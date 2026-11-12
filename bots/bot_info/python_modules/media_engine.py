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
from PIL import Image

from config import (
    MAX_DOWNLOAD_BYTES, YT_REGEX, TENOR_REGEX, GIPHY_REGEX,
    KLIPY_REGEX, DIRECT_GIF_URL_REGEX, URL_REGEX,
    OG_VIDEO_REGEX_1, OG_VIDEO_REGEX_2, OG_IMAGE_REGEX_1, OG_IMAGE_REGEX_2,
    STOPWORDS
)
from core_system import get_http_session, sanitize_url

# ==============================================================================
# VISION, MULTI-FRAME VIDEO/GIF STORYBOARDS & MEDIA FETCHING
# ==============================================================================

async def fetch_online_gif(search_query: str, giphy_key: str) -> str:
    if not giphy_key or not search_query:
        return ""
    clean_q = re.sub(r'[^\w\s\-]', '', search_query).strip()
    if not clean_q:
        return ""

    meaningful_words = [w for w in clean_q.split() if w.lower() not in STOPWORDS and len(w) > 1]
    searches = [clean_q]
    if len(meaningful_words) > 2:
        short_q = " ".join(meaningful_words[:3])
        if short_q not in searches:
            searches.append(short_q)
    if len(meaningful_words) >= 2:
        pair_q = " ".join(meaningful_words[:2])
        if pair_q not in searches:
            searches.append(pair_q)
    elif len(meaningful_words) == 1 and meaningful_words[0] not in searches:
        searches.append(meaningful_words[0])

    for query in searches:
        try:
            url = sanitize_url(
                f"https://api.giphy.com/v1/gifs/search?api_key={giphy_key}&q={urllib.parse.quote(query)}&limit=10&rating=pg-13&lang=en"
            )
            res = await asyncio.to_thread(lambda u=url: get_http_session().get(u, timeout=6))
            data = res.json()
            if data.get("data"):
                top_pool = data["data"][:5]
                return random.choice(top_pool)["images"]["original"]["url"]
        except Exception:
            continue
    return ""


async def get_youtube_metadata(text: str) -> str:
    yt_match = YT_REGEX.search(text)
    if yt_match:
        try:
            video_url = f"https://www.youtube.com/watch?v={yt_match.group(4)}"
            oembed_url = sanitize_url(f"https://www.youtube.com/oembed?url={video_url}&format=json")
            res = await asyncio.to_thread(lambda: get_http_session().get(oembed_url, timeout=4))
            if res.status_code == 200:
                data = res.json()
                return f"[Contains YouTube Video: '{data.get('title', 'Unknown')}' by '{data.get('author_name', 'Unknown')}']"
        except Exception:
            pass
    return ""


def extract_gif_slug(text: str) -> str:
    tenor_match = TENOR_REGEX.search(text)
    if tenor_match:
        return tenor_match.group(1).replace('-', ' ')
    giphy_match = GIPHY_REGEX.search(text)
    if giphy_match:
        slug_words = giphy_match.group(1).split('-')
        return " ".join(slug_words[:-1]) if len(slug_words) > 1 else giphy_match.group(1).replace('-', ' ')
    klipy_match = KLIPY_REGEX.search(text)
    if klipy_match:
        slug = klipy_match.group(1).replace('-', ' ')
        slug = re.sub(r'\s+\d{4,}$', '', slug).strip()
        return slug
    direct_gif = DIRECT_GIF_URL_REGEX.search(text)
    if direct_gif:
        slug = direct_gif.group(1).replace('-', ' ').replace('_', ' ')
        if not slug.isdigit() and slug.lower() not in ("giphy", "tenor", "image", "media", "output"):
            return slug
    return ""


def _convert_pil_frame_to_rgb(img: Image.Image) -> Image.Image:
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
        canvas.paste(frames[0], (0, 0))
        canvas.paste(frames[1].resize((fw, fh), Image.Resampling.LANCZOS), (fw + gap, 0))
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
            f_resized = grid_frames[idx] if grid_frames[idx].size == (fw, fh) else grid_frames[idx].resize((fw, fh), Image.Resampling.LANCZOS)
            canvas.paste(f_resized, pos)

    if max(canvas.width, canvas.height) > max_dim:
        canvas.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    out_buf = BytesIO()
    canvas.save(out_buf, format="PNG")
    return out_buf.getvalue()


def preprocess_image_bytes(image_bytes: bytes, max_dim: int = 1024) -> bytes | None:
    """
    Processes both static images and multi-frame animated GIFs/WebPs:
    - Static images are resized and encoded cleanly as PNG.
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
                rgb_img = _convert_pil_frame_to_rgb(img)
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
                    sampled_frames.append(_convert_pil_frame_to_rgb(img))
                except Exception:
                    continue

            if not sampled_frames:
                img.seek(0)
                sampled_frames.append(_convert_pil_frame_to_rgb(img))

            return _build_multi_frame_grid(sampled_frames, max_dim=max_dim)
    except Exception:
        return None


def extract_video_frame_bytes(raw_bytes: bytes, suffix: str = ".mp4") -> bytes | None:
    """
    Samples up to 4 chronological keyframes across the video (~10%, ~36%, ~64%, ~90%),
    avoiding black fade-in/fade-out frames, and builds a 2x2 chronological storyboard grid.
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
                    lum_penalty = 0.15 if (mean_lum < 14 or mean_lum > 245) else 1.0
                    score = std_contrast * lum_penalty
                    if score > best_local_score:
                        best_local_score = score
                        best_local_frame = frame
                    if lum_penalty == 1.0 and std_contrast >= 18.0:
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
    valid_img_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    valid_vid_exts = (".mp4", ".mov", ".webm", ".avi")

    # 1. Direct message attachments
    for att in message.attachments:
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

    urls = URL_REGEX.findall(message.content)
    if urls and not message.embeds:
        await asyncio.sleep(0.65)

    # 2. Message embeds
    for embed in message.embeds:
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

    # 3. Direct URLs & HTML OpenGraph fallback
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
            html_text = raw_bytes[:30000].decode("utf-8", errors="ignore")
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