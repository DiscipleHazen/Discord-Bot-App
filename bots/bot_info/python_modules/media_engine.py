import os
import re
import html
import asyncio
import tempfile
import threading
import urllib.parse
import random
from io import BytesIO

import discord
import cv2
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from config import (
    MAX_DOWNLOAD_BYTES, YT_REGEX, TENOR_REGEX, GIPHY_REGEX,
    KLIPY_REGEX, DIRECT_GIF_URL_REGEX, DIRECT_MEDIA_URL_REGEX,
    KNOWN_MEDIA_HOST_REGEX, URL_REGEX,
    OG_VIDEO_REGEX_1, OG_VIDEO_REGEX_2, OG_IMAGE_REGEX_1, OG_IMAGE_REGEX_2,
    STOPWORDS, MEME_PLACEHOLDER_REGEX
)
from core_system import get_http_session, sanitize_url

# ==============================================================================
# PRE-COMPILED REGEXES, CACHES & HARDWARE CONCURRENCY GUARDS
# ==============================================================================

NON_QUERY_CHAR_REGEX = re.compile(r'[^\w\s\-]')
ALPHANUM_TOKEN_REGEX = re.compile(r'[a-z0-9]+')
SLUG_NUMERIC_ID_REGEX = re.compile(r'\b\d{4,}\b')
MULTI_SPACE_REGEX = re.compile(r'\s{2,}')
WHITESPACE_NORM_REGEX = re.compile(r'\s+')
YOUTUBE_THUMB_DOMAIN_REGEX = re.compile(r'(?:ytimg\.com|youtube\.com|youtu\.be)', re.IGNORECASE)
CLAUSE_SPLIT_PIVOT_REGEX = re.compile(
    r'\b(?:BUT|AND THEN|WHILE|BECAUSE|UNTIL|ONLY TO|MEANWHILE|EXCEPT|INSTEAD|SO NOW|AND IT)\b'
)

VALID_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".apng")
VALID_VID_EXTS = (".mp4", ".mov", ".webm", ".avi")

GIPHY_SPAM_MISMATCH_TERMS = frozenset({
    "birthday", "bday", "voting", "ballot", "election", "hanukkah",
    "christmas", "new year", "anniversary", "graduation", "mothers day",
    "fathers day", "good morning", "happy friday", "happy monday",
    "happy tuesday", "happy wednesday", "happy thursday", "happy saturday",
    "happy sunday", "baby shower", "wedding", "crypto", "nft", "subscribe",
    "real estate", "valentines day", "easter", "thanksgiving"
})

GENERIC_GIF_FILENAMES = frozenset({
    "giphy", "tenor", "klipy", "image", "media", "output", "animation", "video"
})

MEME_CAPTION_PLACEHOLDER_GUARD = frozenset({
    "top text", "bottom text", "top caption", "bottom caption",
    "short top caption", "short bottom punchline", "bottom punchline",
    "short top text", "short bottom text", "caption 1", "caption 2",
    "text 1", "text 2", "setup", "punchline", "insert top text",
    "insert bottom text", "insert caption", "none", "n/a", "null",
    "empty", "no text", "no caption", "blank"
})

FONT_CANDIDATES = (
    "impact.ttf", "Impact.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "arialbd.ttf"
)

_RESOLVED_FONT_PATH: str | None = None
_FONT_SIZE_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

# Hardware & network efficiency caches
_DECODE_THREAD_SEMAPHORE = threading.BoundedSemaphore(2)
_CACHE_LOCK = threading.RLock()
_YT_OEMBED_CACHE: dict[str, str] = {}            # {video_id: formatted_metadata_str}
_MEDIA_BYTES_CACHE: dict[str, bytes] = {}        # {clean_url: processed_png_bytes}
_GIPHY_QUERY_CACHE: dict[str, list[str]] = {}    # {norm_query: [top_gif_urls]}
_MAX_YT_CACHE_ITEMS = 64
_MAX_MEDIA_CACHE_ITEMS = 12
_MAX_GIPHY_CACHE_ITEMS = 48


def _cache_processed_media(url_key: str, data: bytes | None) -> bytes | None:
    if not url_key or not data:
        return data
    with _CACHE_LOCK:
        if len(_MEDIA_BYTES_CACHE) >= _MAX_MEDIA_CACHE_ITEMS and url_key not in _MEDIA_BYTES_CACHE:
            oldest = next(iter(_MEDIA_BYTES_CACHE))
            _MEDIA_BYTES_CACHE.pop(oldest, None)
        _MEDIA_BYTES_CACHE[url_key] = data
    return data


def _get_cached_processed_media(url_key: str) -> bytes | None:
    if not url_key:
        return None
    with _CACHE_LOCK:
        return _MEDIA_BYTES_CACHE.get(url_key)


def _get_cached_meme_font(sz: int):
    """Loads and caches the Impact/bold meme font by pixel size to avoid repeated disk probes."""
    global _RESOLVED_FONT_PATH
    cached_font = _FONT_SIZE_CACHE.get(sz)
    if cached_font is not None:
        return cached_font

    if _RESOLVED_FONT_PATH:
        try:
            f_obj = ImageFont.truetype(_RESOLVED_FONT_PATH, sz)
            _FONT_SIZE_CACHE[sz] = f_obj
            return f_obj
        except Exception:
            _RESOLVED_FONT_PATH = None

    for fc in FONT_CANDIDATES:
        try:
            f_obj = ImageFont.truetype(fc, sz)
            _RESOLVED_FONT_PATH = fc
            _FONT_SIZE_CACHE[sz] = f_obj
            return f_obj
        except Exception:
            continue

    try:
        f_obj = ImageFont.load_default(size=sz)
    except TypeError:
        f_obj = ImageFont.load_default()
    _FONT_SIZE_CACHE[sz] = f_obj
    return f_obj


def is_visual_media_candidate(message: discord.Message) -> bool:
    """
    Determines whether a Discord message actually contains visual media
    (attachments, stickers, direct image/video/GIF URLs, or media embeds)
    rather than a plain webpage/article link, preventing wasted Vision API calls
    on website favicons or OpenGraph social banners.
    """
    if getattr(message, "attachments", None) or getattr(message, "stickers", None):
        return True

    content_str = getattr(message, "content", "") or ""
    for url in URL_REGEX.findall(content_str):
        clean_u = url.strip().rstrip(")>.,")
        if YT_REGEX.search(clean_u):
            continue
        if DIRECT_MEDIA_URL_REGEX.search(clean_u) or KNOWN_MEDIA_HOST_REGEX.search(clean_u):
            return True

    for embed in getattr(message, "embeds", []):
        embed_url = str(getattr(embed, "url", "") or "")
        if YT_REGEX.search(embed_url):
            continue
        embed_type = str(getattr(embed, "type", "") or "").lower()
        if embed_type in ("image", "gifv", "video"):
            return True
        if getattr(embed, "video", None) or getattr(embed, "image", None):
            # If it's a normal article/link embed with a title, only treat as visual media
            # if the URL is a known media host or direct media file
            if embed_type in ("link", "article", "rich") and getattr(embed, "title", None):
                if KNOWN_MEDIA_HOST_REGEX.search(embed_url) or DIRECT_MEDIA_URL_REGEX.search(embed_url):
                    return True
                img_obj = getattr(embed, "image", None)
                img_u = str(getattr(img_obj, "url", "") or "") if img_obj else ""
                if KNOWN_MEDIA_HOST_REGEX.search(img_u) or DIRECT_GIF_URL_REGEX.search(img_u):
                    return True
                continue
            return True
        thumb_obj = getattr(embed, "thumbnail", None)
        if thumb_obj:
            thumb_u = str(getattr(thumb_obj, "url", "") or "")
            if KNOWN_MEDIA_HOST_REGEX.search(embed_url) or KNOWN_MEDIA_HOST_REGEX.search(thumb_u) or DIRECT_GIF_URL_REGEX.search(thumb_u):
                return True

    return False


def extract_webpage_embed_context(message: discord.Message) -> str:
    """
    Extracts clean title and description metadata in 0ms from non-YouTube webpage
    embeds (such as GitHub repos, news articles, or documentation links) so bots
    have full context of shared links without making a Vision API call.
    """
    snippets = []
    for embed in getattr(message, "embeds", []):
        embed_url = str(getattr(embed, "url", "") or "")
        if YT_REGEX.search(embed_url) or KNOWN_MEDIA_HOST_REGEX.search(embed_url):
            continue
        embed_type = str(getattr(embed, "type", "") or "").lower()
        if embed_type in ("image", "gifv", "video"):
            continue
        title = WHITESPACE_NORM_REGEX.sub(' ', str(getattr(embed, "title", "") or "").strip())
        desc = WHITESPACE_NORM_REGEX.sub(' ', str(getattr(embed, "description", "") or "").strip())
        if not title and not desc:
            continue
        if len(desc) > 160:
            desc = desc[:157] + "..."
        if title and desc:
            snippets.append(f"[Linked Webpage: '{title}' — {desc}]")
        elif title:
            snippets.append(f"[Linked Webpage: '{title}']")
        elif desc:
            snippets.append(f"[Linked Webpage: {desc}]")
        if len(snippets) >= 2:
            break
    return "\n".join(snippets)


async def fetch_online_gif(search_query: str, giphy_key: str) -> str:
    """
    Searches Giphy for a reaction GIF matching `search_query`, using an in-memory
    query pool cache for instant zero-network repeat lookups while filtering out
    unrelated holiday/greeting/election spam and scoring aspect-ratio quality.
    """
    if not giphy_key or not search_query:
        return ""
    clean_q = NON_QUERY_CHAR_REGEX.sub('', search_query).strip()
    if not clean_q:
        return ""

    clean_q_lower = clean_q.lower()
    with _CACHE_LOCK:
        cached_pool = _GIPHY_QUERY_CACHE.get(clean_q_lower)
    if cached_pool:
        return random.choice(cached_pool)

    q_words = [w.lower() for w in clean_q.split() if len(w) > 1]
    meaningful_words = [w for w in q_words if w not in STOPWORDS]
    if not meaningful_words:
        meaningful_words = q_words

    allowed_spam = {t for t in GIPHY_SPAM_MISMATCH_TERMS if t in clean_q_lower}
    disallowed_spam = GIPHY_SPAM_MISMATCH_TERMS - allowed_spam
    meaningful_set = set(meaningful_words)
    two_word_phrase = " ".join(meaningful_words[:2])

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
                if any(bad in title_slug for bad in disallowed_spam):
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

                title_tokens = set(ALPHANUM_TOKEN_REGEX.findall(title_slug))
                overlap = len(meaningful_set & title_tokens)
                phrase_bonus = 1.5 if (two_word_phrase and two_word_phrase in title_slug) else 0.0
                rank_bonus = max(0.0, (15 - rank_idx) * 0.05)
                total_score = overlap + phrase_bonus + rank_bonus
                scored_candidates.append((total_score, overlap, gif_url))

            if scored_candidates:
                scored_candidates.sort(key=lambda x: x[0], reverse=True)
                best_overlap = scored_candidates[0][1]
                top_matches = [u for sc, ov, u in scored_candidates if ov == best_overlap][:3]
                pool = top_matches if top_matches else [scored_candidates[0][2]]
                with _CACHE_LOCK:
                    if len(_GIPHY_QUERY_CACHE) >= _MAX_GIPHY_CACHE_ITEMS:
                        _GIPHY_QUERY_CACHE.pop(next(iter(_GIPHY_QUERY_CACHE)), None)
                    _GIPHY_QUERY_CACHE[clean_q_lower] = pool
                return random.choice(pool)
        except Exception:
            continue
    return ""


async def get_youtube_metadata(text: str) -> str:
    """
    Fetches YouTube video title and channel name via YouTube's oEmbed endpoint
    with an in-memory LRU cache so repeated checks for the same video ID are instant.
    """
    if not text:
        return ""
    yt_match = YT_REGEX.search(text)
    if yt_match:
        video_id = yt_match.group(4)
        with _CACHE_LOCK:
            cached_meta = _YT_OEMBED_CACHE.get(video_id)
        if cached_meta:
            return cached_meta

        try:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            oembed_url = sanitize_url(f"https://www.youtube.com/oembed?url={video_url}&format=json")
            res = await asyncio.to_thread(lambda: get_http_session().get(oembed_url, timeout=4))
            if res.status_code == 200:
                data = res.json()
                title = data.get("title", "Unknown").strip()
                author = data.get("author_name", "Unknown").strip()
                meta_str = f"[Contains YouTube Video: '{title}' by '{author}']"
                with _CACHE_LOCK:
                    if len(_YT_OEMBED_CACHE) >= _MAX_YT_CACHE_ITEMS:
                        _YT_OEMBED_CACHE.pop(next(iter(_YT_OEMBED_CACHE)), None)
                    _YT_OEMBED_CACHE[video_id] = meta_str
                return meta_str
        except Exception:
            pass
    return ""


def _clean_extracted_slug(raw_slug: str) -> str:
    """Removes trailing numeric IDs and hash tokens from GIF URL slugs."""
    slug = raw_slug.replace('-', ' ').replace('_', ' ').strip()
    slug = SLUG_NUMERIC_ID_REGEX.sub('', slug).strip()
    slug = MULTI_SPACE_REGEX.sub(' ', slug).strip()
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
        if slug and not slug.isdigit() and slug.lower() not in GENERIC_GIF_FILENAMES:
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


def _frame_thumb_signature(img: Image.Image) -> list[int]:
    """Computes a fast 16x16 grayscale thumbnail vector for perceptual motion comparison."""
    return list(img.convert("L").resize((16, 16), Image.Resampling.BILINEAR).getdata())


def _frames_have_meaningful_motion(frames: list[Image.Image], diff_threshold: float = 2.2) -> bool:
    if len(frames) <= 1:
        return False
    thumbs = [_frame_thumb_signature(f) for f in frames]
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
    (or a single image if static) encoded as compressed PNG.
    """
    if not frames:
        return None

    if len(frames) == 1 or not _frames_have_meaningful_motion(frames):
        single = frames[len(frames) // 2].copy()
        if max(single.width, single.height) > max_dim:
            single.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        out_buf = BytesIO()
        single.save(out_buf, format="PNG", compress_level=6)
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
        positions = (
            (0, 0),
            (fw + gap, 0),
            (0, fh + gap),
            (fw + gap, fh + gap)
        )
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
    canvas.save(out_buf, format="PNG", compress_level=6)
    return out_buf.getvalue()


def preprocess_image_bytes(image_bytes: bytes, max_dim: int = 1024) -> bytes | None:
    """
    Processes both static images and multi-frame animated GIFs/WebPs/APNGs under
    a bounded thread semaphore so concurrent decodes never spike CPU or RAM:
    - Static images are EXIF-oriented, resized, and encoded cleanly as PNG.
    - Multi-frame animated GIFs/WebPs sample 4 chronological keyframes (10%, 36%, 64%, 90%)
      into a 2x2 storyboard grid.
    """
    if not image_bytes or len(image_bytes) > MAX_DOWNLOAD_BYTES:
        return None
    with _DECODE_THREAD_SEMAPHORE:
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
    Samples up to 4 chronological keyframes across the video (~10%, ~36%, ~64%, ~90%)
    under a bounded hardware semaphore, scoring contrast, Laplacian sharpness, and
    perceptual difference from the preceding panel so all 4 storyboard panels show
    distinct, sharp moments of action.
    """
    if not raw_bytes or len(raw_bytes) > MAX_DOWNLOAD_BYTES:
        return None
    if not suffix or not suffix.startswith("."):
        suffix = ".mp4"

    with _DECODE_THREAD_SEMAPHORE:
        tmp_path = None
        cap = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name
            cap = cv2.VideoCapture(tmp_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

            if total_frames >= 8:
                anchor_ratios = (0.10, 0.36, 0.64, 0.90)
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
            prev_thumb_sig: list[int] | None = None
            step_offset = max(1, total_frames // 25) if total_frames > 25 else 1

            for anchor in dedup_anchors:
                probe_positions = [anchor]
                if total_frames > 10:
                    alt = min(total_frames - 1, anchor + step_offset) if anchor < total_frames // 2 else max(0, anchor - step_offset)
                    if alt != anchor:
                        probe_positions.append(alt)

                best_local_pil = None
                best_local_sig = None
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

                        rgb_arr = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        cand_pil = Image.fromarray(rgb_arr)
                        cand_sig = _frame_thumb_signature(cand_pil)

                        # Reward perceptual difference from the previous panel so the storyboard shows motion
                        motion_bonus = 0.0
                        if prev_thumb_sig is not None:
                            diff_val = sum(abs(a - b) for a, b in zip(prev_thumb_sig, cand_sig)) / 256.0
                            motion_bonus = min(18.0, diff_val * 0.8)

                        score = (std_contrast + sharp_bonus + motion_bonus) * lum_penalty
                        if score > best_local_score:
                            best_local_score = score
                            best_local_pil = cand_pil
                            best_local_sig = cand_sig
                        if lum_penalty == 1.0 and std_contrast >= 22.0 and sharpness >= 70.0 and motion_bonus >= 4.0:
                            break

                if best_local_pil is not None:
                    selected_pil_frames.append(best_local_pil)
                    prev_thumb_sig = best_local_sig

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


async def _process_downloaded_media_bytes(raw_bytes: bytes, content_type: str, media_url: str) -> bytes | None:
    """Routes downloaded bytes to either video keyframe extraction or PIL image/GIF storyboard processing."""
    if not raw_bytes:
        return None
    url_base = (media_url or "").lower().split("?")[0]
    ctype = (content_type or "").lower()
    if ctype.startswith("video/") or url_base.endswith(VALID_VID_EXTS):
        processed = await asyncio.to_thread(extract_video_frame_bytes, raw_bytes, ".mp4")
        if processed:
            return _cache_processed_media(media_url, processed)
    if ctype.startswith("image/") or url_base.endswith(VALID_IMG_EXTS) or not ctype.startswith("text/"):
        processed = await asyncio.to_thread(preprocess_image_bytes, raw_bytes)
        if processed:
            return _cache_processed_media(media_url, processed)
    return None


async def fetch_image_bytes_from_message(message: discord.Message) -> bytes | None:
    """
    Extracts and preprocesses visual media (attachments, stickers, embeds, or linked
    images/videos/GIFs) from a Discord message into a PNG or 2x2 storyboard grid,
    skipping YouTube links and generic webpage favicons/social cards, and caching
    processed URLs in memory to avoid duplicate downloads.
    """
    # 1. Direct message attachments
    for att in getattr(message, "attachments", []):
        if att.size and att.size > MAX_DOWNLOAD_BYTES:
            continue
        att_url = getattr(att, "url", "") or getattr(att, "proxy_url", "")
        cached = _get_cached_processed_media(att_url)
        if cached:
            return cached

        url_lower = att.filename.lower()
        is_image = att.content_type and att.content_type.startswith("image/")
        is_video = att.content_type and att.content_type.startswith("video/")

        if is_image or url_lower.endswith(VALID_IMG_EXTS):
            try:
                raw = await att.read()
                processed = await asyncio.to_thread(preprocess_image_bytes, raw)
                if processed:
                    return _cache_processed_media(att_url, processed)
            except Exception:
                pass
        elif is_video or url_lower.endswith(VALID_VID_EXTS):
            try:
                raw = await att.read()
                processed = await asyncio.to_thread(extract_video_frame_bytes, raw, ".mp4")
                if processed:
                    return _cache_processed_media(att_url, processed)
            except Exception:
                pass

    # 2. Discord Stickers (custom PNG/APNG/GIF server stickers)
    for sticker in getattr(message, "stickers", []):
        sticker_url = getattr(sticker, "url", None)
        if sticker_url and not str(sticker_url).lower().endswith(".json"):
            s_url_str = str(sticker_url)
            cached = _get_cached_processed_media(s_url_str)
            if cached:
                return cached
            raw_bytes, _ = await safe_download_url_bytes(s_url_str, timeout=6)
            if raw_bytes:
                processed = await asyncio.to_thread(preprocess_image_bytes, raw_bytes)
                if processed:
                    return _cache_processed_media(s_url_str, processed)

    # Filter URLs to only direct media files or known media hosts (Tenor, Giphy, Klipy, Imgur, etc.)
    all_urls = URL_REGEX.findall(message.content or "")
    media_urls = [
        u for u in all_urls
        if not YT_REGEX.search(u) and (DIRECT_MEDIA_URL_REGEX.search(u) or KNOWN_MEDIA_HOST_REGEX.search(u))
    ]
    if media_urls and not message.embeds:
        await asyncio.sleep(0.55)

    # 3. Message embeds (including Klipy, Tenor, Giphy, and video/image embeds; skip normal webpage favicons)
    for embed in getattr(message, "embeds", []):
        embed_page_url = str(getattr(embed, "url", "") or "")
        if YT_REGEX.search(embed_page_url):
            continue
        embed_type = str(getattr(embed, "type", "") or "").lower()
        is_article_card = (
            embed_type in ("link", "article", "rich")
            and bool(getattr(embed, "title", None) or getattr(embed, "description", None))
            and not KNOWN_MEDIA_HOST_REGEX.search(embed_page_url)
            and not DIRECT_MEDIA_URL_REGEX.search(embed_page_url)
        )
        if is_article_card:
            continue

        for embed_media in (getattr(embed, "video", None), embed.image, embed.thumbnail):
            if not embed_media:
                continue
            media_url = getattr(embed_media, "proxy_url", None) or getattr(embed_media, "url", None)
            if media_url:
                m_url_str = str(media_url)
                if YOUTUBE_THUMB_DOMAIN_REGEX.search(m_url_str):
                    continue
                cached = _get_cached_processed_media(m_url_str)
                if cached:
                    return cached
                raw_bytes, ctype = await safe_download_url_bytes(m_url_str, timeout=8)
                processed = await _process_downloaded_media_bytes(raw_bytes, ctype, m_url_str)
                if processed:
                    return processed

    # 4. Direct media URLs & HTML OpenGraph fallback on media hosts (Klipy/Tenor/Giphy/Imgur)
    for url in media_urls:
        clean_url = url.strip().rstrip(")>.,")
        cached = _get_cached_processed_media(clean_url)
        if cached:
            return cached

        raw_bytes, content_type = await safe_download_url_bytes(clean_url, timeout=8)
        if not raw_bytes:
            continue

        if not content_type.startswith("text/html"):
            processed = await _process_downloaded_media_bytes(raw_bytes, content_type, clean_url)
            if processed:
                return _cache_processed_media(clean_url, processed)
        else:
            html_text = raw_bytes[:35000].decode("utf-8", errors="ignore")
            og_vid_match = OG_VIDEO_REGEX_1.search(html_text) or OG_VIDEO_REGEX_2.search(html_text)
            if og_vid_match:
                og_v_url = html.unescape(og_vid_match.group(1).strip())
                if og_v_url.startswith("//"):
                    og_v_url = "https:" + og_v_url
                og_v_bytes, og_v_ctype = await safe_download_url_bytes(og_v_url, timeout=8)
                processed = await _process_downloaded_media_bytes(og_v_bytes, og_v_ctype, og_v_url)
                if processed:
                    return _cache_processed_media(clean_url, processed)

            og_match = OG_IMAGE_REGEX_1.search(html_text) or OG_IMAGE_REGEX_2.search(html_text)
            if og_match:
                og_url = html.unescape(og_match.group(1).strip())
                if og_url.startswith("//"):
                    og_url = "https:" + og_url
                og_bytes, og_ctype = await safe_download_url_bytes(og_url, timeout=8)
                processed = await _process_downloaded_media_bytes(og_bytes, og_ctype, og_url)
                if processed:
                    return _cache_processed_media(clean_url, processed)
    return None


def _sanitize_meme_caption_text(text_str: str) -> str:
    """Strips any literal placeholder labels like 'TOP CAPTION' before rendering onto an image."""
    if not text_str:
        return ""
    clean = WHITESPACE_NORM_REGEX.sub(' ', text_str.strip()).strip(' "\'*[]<>')
    if not clean:
        return ""
    low = clean.lower()
    if low in MEME_CAPTION_PLACEHOLDER_GUARD or MEME_PLACEHOLDER_REGEX.match(low):
        return ""
    return clean.upper()


def _auto_split_single_long_caption(top_clean: str, bottom_clean: str) -> tuple[str, str]:
    """
    If only a single long caption was provided (>= 7 words or > 38 chars) with
    an empty second caption, splits it at a natural clause boundary or balanced
    midpoint into classic setup (top) + punchline (bottom) layout.
    """
    if top_clean and bottom_clean:
        return top_clean, bottom_clean
    single = top_clean or bottom_clean
    words = single.split()
    if len(words) < 7 and len(single) <= 38:
        return top_clean, bottom_clean

    # Look for a natural clause pivot ("BUT", "WHILE", "BECAUSE", "UNTIL", etc.) near the middle
    m = CLAUSE_SPLIT_PIVOT_REGEX.search(single)
    if m and 10 <= m.start() <= len(single) - 8:
        left = single[:m.start()].strip(" ,;-—:")
        right = single[m.start():].strip(" ,;-—:")
        if len(left.split()) >= 2 and len(right.split()) >= 2:
            return left, right

    mid = len(words) // 2
    return " ".join(words[:mid]), " ".join(words[mid:])


def render_meme_captions(image_bytes: bytes, top_text: str = "", bottom_text: str = "") -> bytes:
    """
    Overlays classic Impact/bold white meme text with a thick black stroke and
    contrast-aware drop shadow onto the top and/or bottom of generated image bytes,
    automatically splitting single long captions into setup/punchline and enforcing
    a vertical safe zone so the center subject/face of the image is never obscured.
    """
    clean_top = _sanitize_meme_caption_text(top_text)
    clean_bottom = _sanitize_meme_caption_text(bottom_text)
    if not image_bytes or (not clean_top and not clean_bottom):
        return image_bytes

    clean_top, clean_bottom = _auto_split_single_long_caption(clean_top, clean_bottom)

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        draw = ImageDraw.Draw(img)
        w, h = img.size

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

        def _measure_block_height(lines: list[str], font, font_sz: int) -> tuple[list[int], int]:
            line_heights = []
            for ln in lines:
                bbox = draw.textbbox((0, 0), ln, font=font)
                line_heights.append((bbox[3] - bbox[1]) + int(font_sz * 0.22))
            return line_heights, sum(line_heights)

        def _wrap_and_draw(clean: str, is_top: bool):
            if not clean:
                return

            scale_factor = 0.074 if len(clean) <= 36 else (0.060 if len(clean) <= 70 else 0.050)
            font_size = max(18, int(h * scale_factor))
            max_width = int(w * 0.92)
            # Safe zone: never let top or bottom text block exceed 24% of total image height
            max_block_h = int(h * 0.24)
            words = clean.split()

            font = _get_cached_meme_font(font_size)
            lines, widest = _wrap_lines(words, font, max_width)
            line_heights, total_block_h = _measure_block_height(lines, font, font_size)

            # Auto-fit loop: step font size down if too many lines, overflow width, or exceeds vertical safe zone
            while (len(lines) > 3 or widest > max_width or total_block_h > max_block_h) and font_size > 16:
                font_size = max(16, int(font_size * 0.88))
                font = _get_cached_meme_font(font_size)
                lines, widest = _wrap_lines(words, font, max_width)
                line_heights, total_block_h = _measure_block_height(lines, font, font_size)

            lines = lines[:3]
            line_heights, total_block_h = _measure_block_height(lines, font, font_size)
            margin_y = int(h * 0.032)
            y_cursor = margin_y if is_top else max(margin_y, h - total_block_h - margin_y)

            # Sample background luminance under the caption region to boost stroke over bright skies/snow
            band_top = max(0, y_cursor - 4)
            band_bot = min(h, y_cursor + total_block_h + 4)
            mean_lum = 128.0
            if band_bot > band_top:
                try:
                    crop_l = img.crop((0, band_top, w, band_bot)).convert("L")
                    mean_lum = float(ImageStat.Stat(crop_l).mean[0])
                except Exception:
                    mean_lum = 128.0

            stroke_mult = 0.115 if mean_lum > 185.0 else 0.095
            stroke_w = max(3, int(font_size * stroke_mult))
            shadow_offset = max(2, int(font_size * 0.045))
            shadow_alpha = 215 if mean_lum > 185.0 else 180

            for idx, ln in enumerate(lines):
                bbox = draw.textbbox((0, 0), ln, font=font)
                lw = bbox[2] - bbox[0]
                x_pos = max(8, (w - lw) // 2 - bbox[0])
                draw.text(
                    (x_pos + shadow_offset, y_cursor + shadow_offset),
                    ln,
                    font=font,
                    fill=(0, 0, 0, shadow_alpha),
                    stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, shadow_alpha)
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

        if clean_top:
            _wrap_and_draw(clean_top, is_top=True)
        if clean_bottom:
            _wrap_and_draw(clean_bottom, is_top=False)

        out = BytesIO()
        img.convert("RGB").save(out, format="PNG", compress_level=6)
        return out.getvalue()
    except Exception as e:
        print(f"[WARN] Meme caption overlay failed: {e}")
        return image_bytes
