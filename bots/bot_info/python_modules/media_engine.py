import os
import re
import html
import asyncio
import tempfile
import urllib.parse
import random
from io import BytesIO

import discord
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import (
    MAX_DOWNLOAD_BYTES, YT_REGEX, TENOR_REGEX, GIPHY_REGEX,
    KLIPY_REGEX, DIRECT_GIF_URL_REGEX, URL_REGEX,
    OG_VIDEO_REGEX_1, OG_VIDEO_REGEX_2, OG_IMAGE_REGEX_1, OG_IMAGE_REGEX_2,
    STOPWORDS
)
from core_system import get_http_session, sanitize_url

# ==============================================================================
# VISION, MULTI-FRAME VIDEO/GIF STORYBOARDS, STICKERS & MEDIA FETCHING
# ==============================================================================

GIPHY_SPAM_MISMATCH_TERMS = {
    "birthday", "bday", "voting", "ballot", "election", "hanukkah",
    "christmas", "new year", "anniversary", "graduation", "mothers day",
    "fathers day", "good morning", "happy friday", "happy monday",
    "happy tuesday", "happy wednesday", "happy thursday", "happy saturday",
    "happy sunday", "baby shower", "wedding", "crypto", "nft", "subscribe",
    "real estate", "valentines day", "easter", "thanksgiving"
}


async def fetch_online_gif(search_query: str, giphy_key: str) -> str:
    """
    Searches Giphy for a reaction GIF matching `search_query`, filtering out
    unrelated holiday/greeting/election spam and scoring candidates by token,
    phrase, and aspect-ratio quality.
    """
    if not giphy_key or not search_query:
        return ""
    clean_q = re.sub(r'[^\w\s\-]', '', search_query).strip()
    if not clean_q:
        return ""

    q_words = [w.lower() for w in clean_q.split() if len(w) > 1]
    meaningful_words = [w for w in q_words if w not in STOPWORDS]
    if not meaningful_words:
        meaningful_words = q_words

    allowed_spam = {t for t in GIPHY_SPAM_MISMATCH_TERMS if t in clean_q.lower()}

    searches = [clean_q]
    if len(meaningful_words) > 3:
        short_q = " ".join(meaningful_words[:3])
        if short_q not in searches:
            searches.append(short_q)

    for query in searches:
        try:
            url = sanitize_url(
                f"https://api.giphy.com/v1/gifs/search?api_key={giphy_key}&q={urllib.parse.quote(query)}&limit=15&rating=pg-13&lang=en"
            )
            res = await asyncio.to_thread(lambda u=url: get_http_session().get(u, timeout=6))
            if res.status_code != 200:
                continue
            data = res.json().get("data", [])
            if not data:
                continue

            scored_candidates = []
            for rank_idx, item in enumerate(data):
                orig = item.get("images", {}).get("original", {})
                gif_url = orig.get("url", "")
                if not gif_url:
                    continue

                title_slug = f"{item.get('title', '')} {item.get('slug', '')}".lower().replace("-", " ")

                # Reject unrelated birthday/voting/holiday cards unless the query asked for them
                if any(bad in title_slug for bad in (GIPHY_SPAM_MISMATCH_TERMS - allowed_spam)):
                    continue

                # Avoid ultra-skinny banner GIFs if dimensions are available
                try:
                    gw = int(orig.get("width", 0) or 0)
                    gh = int(orig.get("height", 0) or 0)
                    if gw > 0 and gh > 0:
                        aspect = gw / float(gh)
                        if aspect > 3.2 or aspect < 0.30:
                            continue
                except Exception:
                    pass

                title_tokens = set(re.findall(r'[a-z0-9]+', title_slug))
                overlap = len(set(meaningful_words) & title_tokens)
                phrase_bonus = 1.5 if " ".join(meaningful_words[:2]) in title_slug else 0.0
                rank_bonus = max(0.0, (15 - rank_idx) * 0.05)
                total_score = overlap + phrase_bonus + rank_bonus
                scored_candidates.append((total_score, overlap, gif_url))

            if scored_candidates:
                scored_candidates.sort(key=lambda x: x[0], reverse=True)
                best_overlap = scored_candidates[0][1]
                top_matches = [u for sc, ov, u in scored_candidates if ov == best_overlap][:3]
                return random.choice(top_matches if top_matches else [scored_candidates[0][2]])
        except Exception:
            continue
    return ""


async def get_youtube_metadata(text: str) -> str:
    """
    Fetches YouTube video title and channel name via YouTube's oEmbed endpoint
    so bots can understand linked videos without needing an API key.
    """
    if not text:
        return ""
    yt_match = YT_REGEX.search(text)
    if yt_match:
        try:
            video_url = f"https://www.youtube.com/watch?v={yt_match.group(4)}"
            oembed_url = sanitize_url(f"https://www.youtube.com/oembed?url={video_url}&format=json")
            res = await asyncio.to_thread(lambda: get_http_session().get(oembed_url, timeout=4))
            if res.status_code == 200:
                data = res.json()
                title = data.get("title", "Unknown").strip()
                author = data.get("author_name", "Unknown").strip()
                return f"[Contains YouTube Video: '{title}' by '{author}']"
        except Exception:
            pass
    return ""


def _clean_extracted_slug(raw_slug: str) -> str:
    """Removes trailing numeric IDs and hash tokens from GIF URL slugs."""
    slug = raw_slug.replace('-', ' ').replace('_', ' ').strip()
    slug = re.sub(r'\b\d{4,}\b', '', slug).strip()
    slug = re.sub(r'\s{2,}', ' ', slug).strip()
    return slug


def extract_gif_slug(text: str) -> str:
    if not text:
        return ""
    tenor_match = TENOR_REGEX.search(text)
    if tenor_match:
        return _clean_extracted_slug(tenor_match.group(1))
    giphy_match = GIPHY_REGEX.search(text)
    if giphy_match:
        slug_words = giphy_match.group(1).split('-')
        raw = " ".join(slug_words[:-1]) if len(slug_words) > 1 else giphy_match.group(1)
        return _clean_extracted_slug(raw)
    klipy_match = KLIPY_REGEX.search(text)
    if klipy_match:
        return _clean_extracted_slug(klipy_match.group(1))
    direct_gif = DIRECT_GIF_URL_REGEX.search(text)
    if direct_gif:
        slug = _clean_extracted_slug(direct_gif.group(1))
        if slug and not slug.isdigit() and slug.lower() not in ("giphy", "tenor", "image", "media", "output", "animation", "video"):
            return slug
    return ""


def _convert_pil_frame_to_rgb(img: Image.Image, apply_exif: bool = False) -> Image.Image:
    if apply_exif:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (43, 45, 49, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def _frames_have_meaningful_motion(frames: list[Image.Image], diff_threshold: float = 2.2) -> bool:
    if len(frames) <= 1:
        return False
    thumbs = [list(f.convert("L").resize((16, 16), Image.Resampling.BILINEAR).getdata()) for f in frames]
    base = thumbs[0]
    for other in thumbs[1:]:
        mean_diff = sum(abs(a - b) for a, b in zip(base, other)) / 256.0
        if mean_diff >= diff_threshold:
            return True
    return False


def _stamp_panel_index(panel: Image.Image, index_num: int) -> Image.Image:
    """
    Stamps a subtle, tiny sequence badge ('1', '2', '3', '4') in the top-left
    corner of each storyboard panel so Vision models reliably read temporal order.
    """
    try:
        out = panel.copy()
        draw = ImageDraw.Draw(out)
        badge_w, badge_h = 18, 18
        draw.rectangle([(4, 4), (4 + badge_w, 4 + badge_h)], fill=(20, 22, 25))
        draw.text((10, 6), str(index_num), fill=(235, 235, 240))
        return out
    except Exception:
        return panel


def _build_multi_frame_grid(frames: list[Image.Image], max_dim: int = 1024) -> bytes | None:
    """
    Stitches up to 4 chronological video/GIF frames into a 2x2 storyboard grid
    (or a single image if static) encoded as PNG.
    """
    if not frames:
        return None

    if len(frames) == 1 or not _frames_have_meaningful_motion(frames):
        single = frames[len(frames) // 2].copy()
        if max(single.width, single.height) > max_dim:
            single.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        out_buf = BytesIO()
        single.save(out_buf, format="PNG")
        return out_buf.getvalue()

    fw, fh = frames[0].size
    gap = 4
    bg_color = (30, 32, 36)

    if len(frames) == 2:
        canvas = Image.new("RGB", (fw * 2 + gap, fh), bg_color)
        f0 = _stamp_panel_index(frames[0], 1)
        f1 = _stamp_panel_index(frames[1].resize((fw, fh), Image.Resampling.LANCZOS), 2)
        canvas.paste(f0, (0, 0))
        canvas.paste(f1, (fw + gap, 0))
    else:
        grid_frames = list(frames[:4])
        while len(grid_frames) < 4:
            grid_frames.append(grid_frames[-1])
        canvas = Image.new("RGB", (fw * 2 + gap, fh * 2 + gap), bg_color)
        positions = [
            (0, 0),
            (fw + gap, 0),
            (0, fh + gap),
            (fw + gap, fh + gap)
        ]
        for idx, pos in enumerate(positions):
            f_resized = (
                grid_frames[idx]
                if grid_frames[idx].size == (fw, fh)
                else grid_frames[idx].resize((fw, fh), Image.Resampling.LANCZOS)
            )
            canvas.paste(_stamp_panel_index(f_resized, idx + 1), pos)

    if max(canvas.width, canvas.height) > max_dim:
        canvas.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    out_buf = BytesIO()
    canvas.save(out_buf, format="PNG")
    return out_buf.getvalue()


def preprocess_image_bytes(image_bytes: bytes, max_dim: int = 1024) -> bytes | None:
    """
    Processes both static images and multi-frame animated GIFs/WebPs/APNGs:
    - Static images are EXIF-oriented, resized, and encoded cleanly as PNG.
    - Multi-frame animated GIFs/WebPs sample 4 chronological keyframes (10%, 36%, 64%, 90%)
      into a 2x2 storyboard grid.
    """
    if not image_bytes or len(image_bytes) > MAX_DOWNLOAD_BYTES:
        return None
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            n_frames = getattr(img, "n_frames", 1)
            is_anim = getattr(img, "is_animated", False) and n_frames > 1

            if not is_anim:
                rgb_img = _convert_pil_frame_to_rgb(img, apply_exif=True)
                return _build_multi_frame_grid([rgb_img], max_dim=max_dim)

            if n_frames >= 4:
                raw_indices = [
                    max(0, int(n_frames * 0.10)),
                    int(n_frames * 0.36),
                    int(n_frames * 0.64),
                    min(n_frames - 1, int(n_frames * 0.90))
                ]
            else:
                raw_indices = list(range(n_frames))

            target_indices = []
            for idx in raw_indices:
                if idx not in target_indices:
                    target_indices.append(idx)

            sampled_frames = []
            for f_idx in target_indices:
                try:
                    img.seek(f_idx)
                    sampled_frames.append(_convert_pil_frame_to_rgb(img, apply_exif=False))
                except Exception:
                    continue

            if not sampled_frames:
                img.seek(0)
                sampled_frames.append(_convert_pil_frame_to_rgb(img, apply_exif=False))

            return _build_multi_frame_grid(sampled_frames, max_dim=max_dim)
    except Exception:
        return None


def extract_video_frame_bytes(raw_bytes: bytes, suffix: str = ".mp4") -> bytes | None:
    """
    Samples up to 4 chronological keyframes across the video (~10%, ~36%, ~64%, ~90%),
    scoring both contrast and Laplacian sharpness to avoid black fade-ins or motion blur,
    and builds a 2x2 chronological storyboard grid.
    """
    if not raw_bytes or len(raw_bytes) > MAX_DOWNLOAD_BYTES:
        return None
    if not suffix or not suffix.startswith("."):
        suffix = ".mp4"
    tmp_path = None
    cap = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        cap = cv2.VideoCapture(tmp_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if total_frames >= 8:
            anchor_ratios = [0.10, 0.36, 0.64, 0.90]
            anchors = [max(0, min(total_frames - 1, int(total_frames * r))) for r in anchor_ratios]
        elif total_frames >= 2:
            anchors = list(range(min(4, total_frames)))
        else:
            anchors = [0]

        dedup_anchors = []
        for a in anchors:
            if a not in dedup_anchors:
                dedup_anchors.append(a)

        selected_pil_frames = []
        step_offset = max(1, total_frames // 25) if total_frames > 25 else 1

        for anchor in dedup_anchors:
            probe_positions = [anchor]
            if total_frames > 10:
                alt = min(total_frames - 1, anchor + step_offset) if anchor < total_frames // 2 else max(0, anchor - step_offset)
                if alt != anchor:
                    probe_positions.append(alt)

            best_local_frame = None
            best_local_score = -1.0
            for pos in probe_positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()
                if ret and frame is not None:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    mean_lum = float(gray.mean())
                    std_contrast = float(gray.std())
                    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    lum_penalty = 0.15 if (mean_lum < 14 or mean_lum > 245) else 1.0
                    sharp_bonus = min(25.0, sharpness * 0.04)
                    score = (std_contrast + sharp_bonus) * lum_penalty
                    if score > best_local_score:
                        best_local_score = score
                        best_local_frame = frame
                    if lum_penalty == 1.0 and std_contrast >= 20.0 and sharpness >= 65.0:
                        break

            if best_local_frame is not None:
                rgb_arr = cv2.cvtColor(best_local_frame, cv2.COLOR_BGR2RGB)
                selected_pil_frames.append(Image.fromarray(rgb_arr))

        if selected_pil_frames:
            return _build_multi_frame_grid(selected_pil_frames, max_dim=1024)
    except Exception:
        pass
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return None


async def safe_download_url_bytes(url: str, timeout: int = 8) -> tuple[bytes | None, str]:
    """
    Safely downloads up to MAX_DOWNLOAD_BYTES from a URL and returns (bytes, content_type).
    """
    clean_url = sanitize_url(url)
    if not clean_url:
        return None, ""

    def _fetch():
        with get_http_session().get(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=timeout,
            stream=True
        ) as r:
            if r.status_code != 200:
                return None, ""
            ctype = r.headers.get("Content-Type", "").lower()
            clen = r.headers.get("Content-Length")
            if clen and clen.isdigit() and int(clen) > MAX_DOWNLOAD_BYTES:
                return None, ""
            chunks = []
            downloaded = 0
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        return None, ""
            return b"".join(chunks), ctype

    try:
        return await asyncio.to_thread(_fetch)
    except Exception:
        return None, ""


async def fetch_image_bytes_from_message(message: discord.Message) -> bytes | None:
    valid_img_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".apng")
    valid_vid_exts = (".mp4", ".mov", ".webm", ".avi")

    # 1. Direct message attachments
    for att in getattr(message, "attachments", []):
        if att.size and att.size > MAX_DOWNLOAD_BYTES:
            continue
        url_lower = att.filename.lower()
        is_image = att.content_type and att.content_type.startswith("image/")
        is_video = att.content_type and att.content_type.startswith("video/")

        if is_image or url_lower.endswith(valid_img_exts):
            try:
                raw = await att.read()
                processed = await asyncio.to_thread(preprocess_image_bytes, raw)
                if processed:
                    return processed
            except Exception:
                pass
        elif is_video or url_lower.endswith(valid_vid_exts):
            try:
                raw = await att.read()
                processed = await asyncio.to_thread(extract_video_frame_bytes, raw, ".mp4")
                if processed:
                    return processed
            except Exception:
                pass

    # 2. Discord Stickers (custom PNG/APNG/GIF server stickers)
    for sticker in getattr(message, "stickers", []):
        sticker_url = getattr(sticker, "url", None)
        if sticker_url and not str(sticker_url).lower().endswith(".json"):
            raw_bytes, _ = await safe_download_url_bytes(str(sticker_url), timeout=6)
            if raw_bytes:
                processed = await asyncio.to_thread(preprocess_image_bytes, raw_bytes)
                if processed:
                    return processed

    urls = URL_REGEX.findall(message.content or "")
    if urls and not message.embeds:
        await asyncio.sleep(0.65)

    # 3. Message embeds
    for embed in getattr(message, "embeds", []):
        for embed_media in [getattr(embed, "video", None), embed.image, embed.thumbnail]:
            if not embed_media:
                continue
            media_url = getattr(embed_media, "proxy_url", None) or getattr(embed_media, "url", None)
            if media_url:
                raw_bytes, ctype = await safe_download_url_bytes(media_url, timeout=8)
                if raw_bytes:
                    if ctype.startswith("video/") or media_url.lower().split("?")[0].endswith(valid_vid_exts):
                        processed = await asyncio.to_thread(extract_video_frame_bytes, raw_bytes, ".mp4")
                    else:
                        processed = await asyncio.to_thread(preprocess_image_bytes, raw_bytes)
                    if processed:
                        return processed

    # 4. Direct URLs & HTML OpenGraph fallback
    for url in urls:
        clean_url = url.strip().rstrip(")>.,")
        raw_bytes, content_type = await safe_download_url_bytes(clean_url, timeout=8)
        if not raw_bytes:
            continue

        if content_type.startswith("image/"):
            processed = await asyncio.to_thread(preprocess_image_bytes, raw_bytes)
            if processed:
                return processed
        elif content_type.startswith("video/"):
            processed = await asyncio.to_thread(extract_video_frame_bytes, raw_bytes, ".mp4")
            if processed:
                return processed
        elif "text/html" in content_type:
            html_text = raw_bytes[:35000].decode("utf-8", errors="ignore")
            og_vid_match = OG_VIDEO_REGEX_1.search(html_text) or OG_VIDEO_REGEX_2.search(html_text)
            if og_vid_match:
                og_v_url = html.unescape(og_vid_match.group(1).strip())
                if og_v_url.startswith("//"):
                    og_v_url = "https:" + og_v_url
                og_v_bytes, og_v_ctype = await safe_download_url_bytes(og_v_url, timeout=8)
                if og_v_bytes and (og_v_ctype.startswith("video/") or og_v_url.lower().split("?")[0].endswith(valid_vid_exts)):
                    processed = await asyncio.to_thread(extract_video_frame_bytes, og_v_bytes, ".mp4")
                    if processed:
                        return processed

            og_match = OG_IMAGE_REGEX_1.search(html_text) or OG_IMAGE_REGEX_2.search(html_text)
            if og_match:
                og_url = html.unescape(og_match.group(1).strip())
                if og_url.startswith("//"):
                    og_url = "https:" + og_url
                og_bytes, og_ctype = await safe_download_url_bytes(og_url, timeout=8)
                if og_bytes:
                    if og_ctype.startswith("video/"):
                        processed = await asyncio.to_thread(extract_video_frame_bytes, og_bytes, ".mp4")
                    else:
                        processed = await asyncio.to_thread(preprocess_image_bytes, og_bytes)
                    if processed:
                        return processed
    return None


def render_meme_captions(image_bytes: bytes, top_text: str = "", bottom_text: str = "") -> bytes:
    """
    Overlays classic Impact/bold white meme text with a thick black stroke and
    subtle drop shadow onto the top and/or bottom of generated image bytes,
    dynamically auto-fitting font size so longer punchlines never clip horizontally.
    """
    if not image_bytes or (not top_text.strip() and not bottom_text.strip()):
        return image_bytes

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        draw = ImageDraw.Draw(img)
        w, h = img.size

        font_candidates = [
            "impact.ttf", "Impact.ttf",
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/Impact.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "arialbd.ttf"
        ]

        def _load_font(sz: int):
            for fc in font_candidates:
                try:
                    return ImageFont.truetype(fc, sz)
                except Exception:
                    continue
            try:
                return ImageFont.load_default(size=sz)
            except TypeError:
                return ImageFont.load_default()

        def _wrap_lines(words: list[str], font, max_w: int) -> tuple[list[str], int]:
            lines = []
            current_line = []
            widest_line = 0
            for word in words:
                test_line = " ".join(current_line + [word])
                bbox = draw.textbbox((0, 0), test_line, font=font)
                tw = bbox[2] - bbox[0]
                if tw <= max_w or not current_line:
                    current_line.append(word)
                    widest_line = max(widest_line, tw)
                else:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                    w_bbox = draw.textbbox((0, 0), word, font=font)
                    widest_line = max(widest_line, w_bbox[2] - w_bbox[0])
            if current_line:
                lines.append(" ".join(current_line))
            return lines, widest_line

        def _wrap_and_draw(text_str: str, is_top: bool):
            clean = re.sub(r'\s+', ' ', text_str.strip()).upper()
            if not clean:
                return

            scale_factor = 0.075 if len(clean) <= 38 else (0.062 if len(clean) <= 72 else 0.052)
            font_size = max(20, int(h * scale_factor))
            max_width = int(w * 0.92)
            words = clean.split()

            font = _load_font(font_size)
            lines, widest = _wrap_lines(words, font, max_width)

            # Auto-fit loop: step font size down if too many lines or a long word overflows max_width
            while (len(lines) > 3 or widest > max_width) and font_size > 18:
                font_size = max(18, int(font_size * 0.88))
                font = _load_font(font_size)
                lines, widest = _wrap_lines(words, font, max_width)

            lines = lines[:4]
            line_heights = []
            for ln in lines:
                bbox = draw.textbbox((0, 0), ln, font=font)
                line_heights.append((bbox[3] - bbox[1]) + int(font_size * 0.22))

            total_block_h = sum(line_heights)
            margin_y = int(h * 0.035)
            y_cursor = margin_y if is_top else max(margin_y, h - total_block_h - margin_y)
            stroke_w = max(3, int(font_size * 0.095))
            shadow_offset = max(2, int(font_size * 0.04))

            for idx, ln in enumerate(lines):
                bbox = draw.textbbox((0, 0), ln, font=font)
                lw = bbox[2] - bbox[0]
                x_pos = max(8, (w - lw) // 2 - bbox[0])
                # Subtle drop shadow for high contrast over bright backgrounds
                draw.text(
                    (x_pos + shadow_offset, y_cursor + shadow_offset),
                    ln,
                    font=font,
                    fill=(0, 0, 0, 180),
                    stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, 180)
                )
                draw.text(
                    (x_pos, y_cursor),
                    ln,
                    font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, 255)
                )
                y_cursor += line_heights[idx]

        if top_text:
            _wrap_and_draw(top_text, is_top=True)
        if bottom_text:
            _wrap_and_draw(bottom_text, is_top=False)

        out = BytesIO()
        img.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception as e:
        print(f"[WARN] Meme caption overlay failed: {e}")
        return image_bytes
