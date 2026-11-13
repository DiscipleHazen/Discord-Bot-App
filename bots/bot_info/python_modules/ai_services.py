import re
import json
import time
import base64
import random
import asyncio
import hashlib
import threading
from io import BytesIO

import discord

from config import (
    FALLBACK_VISION_MODELS, FALLBACK_TEXT_MODELS, JSON_EMOJI_REGEX,
    REACT_NOTE_EXTRACT_REGEX, ERROR_REPLY_STRIP_REGEX, IMAGE_DESC_EXTRACT_REGEX,
    STOPWORDS, USER_SUBJECT_EXTRACT_REGEX, OVERUSED_MEME_CAPTIONS,
    TOPIC_CHANGE_REQUEST_REGEX
)
from core_system import post_with_backoff, update_json_file, is_model_circuit_open
from text_and_parsers import (
    parse_mimotts_voice_block, parse_meme_gen_tag, PROMPT_BAKED_TEXT_STRIP_REGEX,
    _is_placeholder_caption, sanitize_api_error_text
)
from media_engine import safe_download_url_bytes, render_meme_captions

# ==============================================================================
# PRE-COMPILED REGEXES & CACHES FOR VISION, EMOJIS, IMAGE, MEME & VOICE ENGINES
# ==============================================================================

UNICODE_EMOJI_FALLBACK_REGEX = re.compile(
    r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]'
)
ALPHANUM_WORD_REGEX = re.compile(r'[a-z0-9]+')
WHITESPACE_COLLAPSE_REGEX = re.compile(r'\s+')
CUSTOM_DISCORD_EMOJI_FULL_REGEX = re.compile(r'<a?:([a-zA-Z0-9_]+):\d+>')
EMOJI_SHORTCODE_REGEX = re.compile(r':?([a-zA-Z0-9_]{2,32}):?')
SYSTEM_NOTE_INLINE_STRIP_REGEX = re.compile(r'\*\(System Note:.*?\)\*', re.IGNORECASE)
LEARNED_INLINE_STRIP_REGEX = re.compile(
    r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*',
    re.IGNORECASE
)
EMPTY_SPEAKER_LINE_REGEX = re.compile(r'\[[^\]]+\]:\s*')
LEADING_SPEAKER_TAG_REGEX = re.compile(r'^\[[^\]]+\]:\s*')
MEDIA_TAG_WRAP_STRIP_REGEX = re.compile(
    r'^\[?\s*(?:IMAGE_GEN|MEME_GEN)\s*:\s*|\s*\]$',
    re.IGNORECASE
)
RECORDED_MEME_CAPTION_EXTRACT_REGEX = re.compile(r"\[Meme Caption:\s*'([^']*)'\]")
INLINE_EMOTE_STRIP_REGEX = re.compile(r'\*[^*]+\*')

# Strips conversational meta-chatter from a bot's visible text reply before using it to ground an image prompt
CONVERSATIONAL_FILLER_STRIP_REGEX = re.compile(
    r'\b(?:oopsie!?|one\s+sec(?:ond)?\s+while\s+i[^.!\n]*|let\s+me\s+fix\s+that\s+for\s+you[^.!\n]*|'
    r'sometimes\s+my\s+[^.!\n]*glitch[^.!\n]*|coming\s+right\s+up!?)\b',
    re.IGNORECASE
)

# SHA-256 Vision cache so reposted images/GIFs/stickers resolve in 0ms with 0 API calls
_VISION_CACHE_LOCK = threading.RLock()
_VISION_HASH_CACHE: dict[str, str] = {}
_MAX_VISION_HASH_ITEMS = 128


def _normalize_tokens(text: str) -> set[str]:
    """Internal helper for comparing prompt overlap safely."""
    if not text:
        return set()
    words = ALPHANUM_WORD_REGEX.findall(text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def _strip_conversational_filler_from_reply(reply_text: str) -> str:
    """
    Removes physical `*emotes*` and meta-apology/delay chatter ('oopsie', 'one sec while I sketch',
    'magic crayons glitch') from `reply_text` so visual prompt synthesis focuses strictly
    on the actual visual scene being described.
    """
    if not reply_text:
        return ""
    cleaned = INLINE_EMOTE_STRIP_REGEX.sub(' ', reply_text)
    cleaned = CONVERSATIONAL_FILLER_STRIP_REGEX.sub(' ', cleaned)
    return WHITESPACE_COLLAPSE_REGEX.sub(' ', cleaned).strip()


async def summarize_image_for_memory(image_bytes: bytes, api_key: str, vision_model: str, api_url: str) -> str:
    """
    Summarizes an attached image, meme, doodle, or numbered 2x2 video/GIF storyboard
    into a concise, detail-rich memory tag, using an in-memory SHA-256 digest cache
    so identical reposted images/stickers/GIFs never trigger redundant Vision API calls.
    """
    if not image_bytes:
        return ""

    img_hash = hashlib.sha256(image_bytes).hexdigest()
    with _VISION_CACHE_LOCK:
        cached_summary = _VISION_HASH_CACHE.get(img_hash)
    if cached_summary:
        return cached_summary

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": vision_model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Describe this image, drawing, screenshot, meme, or video/GIF sequence in 1-2 vivid, concrete sentences (under 55 words). "
                        "If it shows a 2x2 storyboard with small 1-2-3-4 corner badges, describe the chronological action across the clip as a single animation rather than calling it a grid. "
                        "Specify the art style/medium (e.g., MS Paint doodle, anime reaction sketch, photo, 3D render), main subject and facial expression, funny background details/props, and any visible text/numbers verbatim."
                    )
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            ]
        }],
        "temperature": 0.25,
        "max_tokens": 135
    }

    endpoint_label = api_url.rsplit("/", 1)[-1] if api_url else "completions"
    primary_circuit_open = is_model_circuit_open(f"{endpoint_label}:{vision_model}")
    res = None
    if not primary_circuit_open:
        res = await post_with_backoff(
            api_url,
            headers={"Authorization": f"Bearer {api_key}"},
            json_payload=payload,
            timeout=25,
            max_retries=2,
            bot_name="VisionCache"
        )
    if res is None or res.status_code in (429, 500, 502, 503, 504):
        for fb_v_model in FALLBACK_VISION_MODELS:
            if fb_v_model == vision_model:
                continue
            fb_payload = {**payload, "model": fb_v_model}
            res = await post_with_backoff(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                json_payload=fb_payload,
                timeout=25,
                max_retries=1,
                bot_name="VisionCache"
            )
            if res is not None and res.status_code == 200:
                break

    if res and res.status_code == 200:
        try:
            desc = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            desc = WHITESPACE_COLLAPSE_REGEX.sub(' ', desc)
            if desc:
                formatted_desc = f"[Attached Media Description: {desc}]"
                with _VISION_CACHE_LOCK:
                    if len(_VISION_HASH_CACHE) >= _MAX_VISION_HASH_ITEMS:
                        _VISION_HASH_CACHE.pop(next(iter(_VISION_HASH_CACHE)), None)
                    _VISION_HASH_CACHE[img_hash] = formatted_desc
                return formatted_desc
        except Exception as e:
            print(f"[ERROR] Vision JSON parse error: {e}")
    return ""


def _resolve_emoji_candidate(candidate: str, server_custom_emojis: list[str]) -> str:
    """
    Normalizes an LLM emoji candidate, resolving shortcodes like `:pepe:` to
    the full Discord `<:pepe:123456>` format if available in `server_custom_emojis`.
    """
    c = candidate.strip()
    if not c:
        return ""
    if CUSTOM_DISCORD_EMOJI_FULL_REGEX.fullmatch(c):
        return c
    short_match = EMOJI_SHORTCODE_REGEX.fullmatch(c)
    if short_match and server_custom_emojis:
        target_name = short_match.group(1).lower()
        for se in server_custom_emojis:
            m = CUSTOM_DISCORD_EMOJI_FULL_REGEX.match(se)
            if m and m.group(1).lower() == target_name:
                return se
        return ""
    return c


async def get_contextual_emojis(
    api_key: str,
    text: str,
    image_bytes: bytes | None,
    server_custom_emojis: list[str],
    personality_prompt: str,
    api_url: str,
    model: str,
    chat_history: list[dict],
    emoji_context_length: int,
    bot_name: str,
    other_bot_keywords: set[str],
    emoji_reaction_hint: str = "",
    existing_reactions: set[str] | None = None
) -> list[str]:
    filtered_custom_emojis = []
    for em in (server_custom_emojis or []):
        em_lower = em.lower()
        if any(len(ob_kw) >= 3 and ob_kw in em_lower for ob_kw in other_bot_keywords):
            continue
        filtered_custom_emojis.append(em)

    custom_pool_str = ", ".join(filtered_custom_emojis) if filtered_custom_emojis else "None"
    my_recent_emojis = []
    clean_recent_turns = []
    bot_name_lower = bot_name.lower() if bot_name else ""

    if chat_history:
        for msg in reversed(chat_history[-35:]):
            content_str = msg.get("content", "")
            if not isinstance(content_str, str):
                continue
            react_match = REACT_NOTE_EXTRACT_REGEX.search(content_str)
            if react_match:
                reactor_name = react_match.group(1).strip()
                emoji_used = react_match.group(2).strip()
                if bot_name_lower and reactor_name.lower() == bot_name_lower:
                    if emoji_used not in my_recent_emojis:
                        my_recent_emojis.append(emoji_used)

        for msg in chat_history[-(emoji_context_length * 2):]:
            content_str = msg.get("content", "")
            if not isinstance(content_str, str) or content_str.startswith("System Note:"):
                continue
            cleaned_turn = SYSTEM_NOTE_INLINE_STRIP_REGEX.sub('', content_str).strip()
            cleaned_turn = LEARNED_INLINE_STRIP_REGEX.sub('', cleaned_turn).strip()
            cleaned_turn = ERROR_REPLY_STRIP_REGEX.sub('', cleaned_turn).strip()
            if cleaned_turn and not EMPTY_SPEAKER_LINE_REGEX.fullmatch(cleaned_turn):
                clean_recent_turns.append(cleaned_turn)

    clean_recent_turns = clean_recent_turns[-emoji_context_length:]
    history_summary = "RECENT CONVERSATION CONTEXT:\n" + "\n".join(clean_recent_turns) + "\n\n" if clean_recent_turns else ""
    banned_pool = list(dict.fromkeys(my_recent_emojis[:6] + list(existing_reactions or set())))
    banned_emojis_str = ", ".join(banned_pool[:8]) if banned_pool else "None"

    style_guidance = (
        f"REACTION LENS FOR {bot_name.upper()}: {emoji_reaction_hint}\n\n"
        if emoji_reaction_hint
        else (
            f"REACTION LENS FOR {bot_name.upper()}: React strictly through your character persona described above. "
            "Pick an emoji that matches both the specific detail/joke of the message (text, image, meme, or GIF) and your character's attitude.\n\n"
        )
    )

    system_message = (
        f"YOUR PERSONA:\n{personality_prompt[:950]}\n\n"
        f"{style_guidance}"
        f"{history_summary}"
        "TASK: Select 3 contextually sharp emojis ranked from best to backup that react directly to the specific content, image description, GIF, or punchline of the latest message.\n\n"
        "STRICT REACTION RULES:\n"
        "1. READ ALL MEDIA DETAILS: Pay close attention to `[Attached Media Description: ...]`, `[Contains GIF of: ...]`, `[Contains YouTube Video: ...]`, and `[Replying to ...]`. React to the specific object, joke, food, game, anime, or fail shown!\n"
        f"2. BANNED / ALREADY USED EMOJIS (DO NOT USE THESE): [{banned_emojis_str}]. Never spam the same default emoji.\n"
        f"3. SERVER CUSTOM EMOJIS: [{custom_pool_str}]. Only pick a custom server emoji if it specifically fits the joke AND is NOT in the banned list above. Prioritize expressive standard Unicode emojis for high variety.\n"
        "4. Return ONLY valid JSON: {\"emojis\": [\"<best_emoji>\", \"<backup_1>\", \"<backup_2>\"]}"
    )

    prompt_text = f"Latest Message to React To:\n{text}" if text else "React to the attached media in character."
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        user_content = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ]
    else:
        user_content = prompt_text

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.85,
        "max_tokens": 65
    }

    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=20,
        max_retries=2,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            raw_json = res.json()["choices"][0]["message"]["content"].strip()
            candidates = []
            json_match = JSON_EMOJI_REGEX.search(raw_json)
            if json_match:
                parsed = json.loads(json_match.group(0)).get("emojis", [])
                for e in parsed:
                    resolved = _resolve_emoji_candidate(str(e), filtered_custom_emojis)
                    if resolved and resolved not in candidates:
                        candidates.append(resolved)
            if not candidates:
                for uni_em in UNICODE_EMOJI_FALLBACK_REGEX.findall(raw_json):
                    if uni_em not in candidates:
                        candidates.append(uni_em)
            non_repeated = [c for c in candidates if c not in banned_pool[:5]]
            return non_repeated if non_repeated else candidates
        except Exception as e:
            print(f"[{bot_name}] [ERROR] Emoji JSON parse failed: {e}")
    return []


async def auto_react(message: discord.Message, **kwargs) -> str | None:
    """
    Selects and applies an in-character emoji reaction to `message`.
    Optimization: If `text` already contains `[Attached Media Description: ...]` from the
    shared Vision cache, uses the fast `text_model` instead of uploading the base64 image
    to the 235B Vision model a second time.
    """
    if not message.guild or message.author == message.guild.me:
        return None
    bot_name = kwargs.get("bot_name", message.guild.me.display_name)
    server_emojis = [str(emoji) for emoji in message.guild.emojis]
    text_to_use = kwargs.get("text", (message.content or "").strip())
    raw_img_bytes = kwargs.get("image_bytes")

    # Only upload raw image bytes to the Vision model if we do NOT already have
    # the rich [Attached Media Description: ...] in text_to_use
    has_cached_vision_desc = "[Attached Media Description:" in (text_to_use or "")
    effective_img_bytes = None if has_cached_vision_desc else raw_img_bytes
    active_model = kwargs["vision_model"] if effective_img_bytes else kwargs["text_model"]
    existing_reactions = {str(r.emoji) for r in getattr(message, "reactions", [])}

    context_emojis = await get_contextual_emojis(
        api_key=kwargs["api_key"],
        text=text_to_use,
        image_bytes=effective_img_bytes,
        server_custom_emojis=server_emojis,
        personality_prompt=kwargs["personality_prompt"],
        api_url=kwargs["api_url"],
        model=active_model,
        chat_history=kwargs["chat_history"],
        emoji_context_length=kwargs.get("emoji_context_length", 12),
        bot_name=bot_name,
        other_bot_keywords=kwargs.get("other_bot_keywords", set()),
        emoji_reaction_hint=kwargs.get("emoji_reaction_hint", ""),
        existing_reactions=existing_reactions
    )

    for emoji in context_emojis:
        candidate = emoji.strip()
        if not candidate:
            continue
        if candidate in existing_reactions and len(context_emojis) > 1:
            continue
        try:
            await message.add_reaction(candidate)
            return candidate
        except Exception:
            continue
    return None


def _extract_explicit_requested_subject(
    user_prompt: str,
    bot_name: str,
    raw_history: list[dict] | None = None
) -> str:
    """
    If a user explicitly asked for an image/meme of a specific subject
    (e.g. 'could you draw me a picture of the clouds?' followed by 'hm' or 'pls'),
    scans all lines in `user_prompt` (and the last 3 user turns in `raw_history` if
    a follow-up like 'uwu pls' is nudging an earlier request) to extract the exact
    subject description unless the user asked for a picture of the bot itself.
    """
    candidate_lines = []
    if user_prompt:
        for ln in reversed(user_prompt.strip().split("\n")):
            clean_ln = LEADING_SPEAKER_TAG_REGEX.sub('', ln.strip()).strip()
            if clean_ln:
                candidate_lines.append(clean_ln)

    if raw_history:
        for m in reversed(raw_history[-4:]):
            if isinstance(m, dict) and m.get("role") == "user" and not m.get("author_is_bot"):
                c_str = m.get("content", "")
                if isinstance(c_str, str):
                    for ln in reversed(c_str.split("\n")):
                        ln_s = ln.strip()
                        if ln_s.startswith(("[Replying to ", "[Attached Media", "[Contains ", "[Linked Webpage:")):
                            continue
                        clean_ln = LEADING_SPEAKER_TAG_REGEX.sub('', ln_s).strip()
                        if clean_ln and clean_ln not in candidate_lines:
                            candidate_lines.append(clean_ln)

    self_refs = {"you", "yourself", "urself", bot_name.lower(), bot_name.split()[0].lower()}
    for line in candidate_lines:
        m = USER_SUBJECT_EXTRACT_REGEX.search(line)
        if not m:
            continue
        subj = m.group(1).strip().rstrip(".!?")
        if not subj or len(subj) < 3:
            continue
        subj_lower = subj.lower()
        if subj_lower in self_refs or any(subj_lower.startswith(f"{sr} ") for sr in self_refs):
            continue
        return subj
    return ""


async def ensure_unique_image_prompt(
    img_prompt: str,
    recent_gen_records: list[dict],
    raw_history: list[dict],
    bot_name: str,
    persona_summary: str,
    user_prompt: str,
    api_key: str,
    text_model: str,
    api_url: str,
    reply_text: str = ""
) -> str:
    """
    1. Ensures that if a user explicitly requested a specific visual subject,
       the generated prompt faithfully depicts all key elements of that subject.
    2. Detects if the bot is stuck in a visual obsession loop (repeating the same
       subject nouns as its own recent images, or ignoring a user's topic-change request)
       and rewrites the visual scene to match the fresh topic.
    """
    img_prompt = PROMPT_BAKED_TEXT_STRIP_REGEX.sub('', (img_prompt or "")).strip()
    proposed_tokens = _normalize_tokens(img_prompt)
    if not proposed_tokens:
        return img_prompt

    requested_subject = _extract_explicit_requested_subject(user_prompt, bot_name, raw_history)
    req_tokens = _normalize_tokens(requested_subject)
    missing_requested_subject = False
    if req_tokens and len(req_tokens) >= 1:
        subj_overlap = len(proposed_tokens & req_tokens) / float(len(req_tokens))
        if subj_overlap < 0.45:
            missing_requested_subject = True

    user_asked_topic_change = bool(TOPIC_CHANGE_REQUEST_REGEX.search(user_prompt or ""))
    conflicting_prompts = []
    stale_self_subject = False
    bot_name_lower = bot_name.lower()

    for rec in (recent_gen_records or [])[-6:]:
        prev_prompt = rec.get("prompt", "").split("[Meme Caption:")[0].strip()
        prev_tokens = _normalize_tokens(prev_prompt)
        if not prev_tokens:
            continue
        is_same_bot = (str(rec.get("bot_name", "")).lower() == bot_name_lower)
        overlap = len(proposed_tokens & prev_tokens) / float(min(len(proposed_tokens), len(prev_tokens)))
        shared_count = len(proposed_tokens & prev_tokens)

        # Stricter threshold for the same bot when unsolicited so it doesn't draw 3 waffles in a row
        if overlap >= 0.65 or (not requested_subject and is_same_bot and (overlap >= 0.38 or shared_count >= 3)):
            conflicting_prompts.append(f"- {rec.get('bot_name', 'Bot')}: '{prev_prompt}'")
            if is_same_bot and not requested_subject:
                stale_self_subject = True

    if not missing_requested_subject and not conflicting_prompts and not user_asked_topic_change:
        return img_prompt

    if missing_requested_subject:
        print(f"[{bot_name}] [SUBJECT FIDELITY] Restoring user's explicitly requested subject into image prompt: '{requested_subject}'")
    elif stale_self_subject or user_asked_topic_change:
        print(f"[{bot_name}] [VISUAL TOPIC PIVOT] Breaking repetitive visual loop in image prompt...")
    else:
        print(f"[{bot_name}] [IMAGE DIVERGENCE] Adjusting camera/composition while preserving reply subject...")

    conflict_str = "\n".join(conflicting_prompts[:4]) if conflicting_prompts else "None"
    clean_reply_scene = _strip_conversational_filler_from_reply(reply_text)
    if missing_requested_subject:
        subject_directive = (
            f"CRITICAL USER REQUEST FIDELITY: The user explicitly asked for: '{requested_subject}'. "
            "You MUST depict ALL specific subjects, people, items, food, clothing, and actions requested by the user! "
            "Do NOT replace the user's requested characters or items with the bot's own character or random wizards!\n"
        )
    elif stale_self_subject or user_asked_topic_change:
        subject_directive = (
            "CRITICAL ANTI-LOOP RULE: The proposed prompt repeats a stale visual gag from earlier turns (listed above). "
            "Completely DROP the old repeated objects/food from the previous prompts and instead illustrate a 100% fresh, "
            "new scene matching the new topic in the bot's visible chat reply and user's latest message!\n"
        )
    else:
        subject_directive = (
            "CRITICAL RULE: Keep the core subject that the bot just described in its visible chat reply, "
            "but change the camera angle, lighting, and framing so it isn't an identical copy.\n"
        )

    rewrite_payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are refining an image generation prompt for '{bot_name}'.\n"
                    f"User's message: {user_prompt}\n"
                    f"Bot's visible chat reply: {clean_reply_scene}\n"
                    f"Previously generated prompts to avoid repeating:\n{conflict_str}\n\n"
                    f"{subject_directive}"
                    "Do NOT include any written text or captions inside the visual scene description. "
                    "Output ONLY the refined visual prompt (25-50 words) with no quotes or tags."
                )
            },
            {
                "role": "user",
                "content": f"Proposed prompt: {img_prompt}"
            }
        ],
        "temperature": 0.60,
        "max_tokens": 95
    }
    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=rewrite_payload,
        timeout=20,
        max_retries=2,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            new_p = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            new_p = MEDIA_TAG_WRAP_STRIP_REGEX.sub('', new_p).strip(' "\'')
            new_p = new_p.split("|")[0].strip()
            if len(new_p) > 15:
                return new_p
        except Exception:
            pass

    return img_prompt


def _derive_fallback_captions_from_reply(reply_text: str) -> tuple[str, str]:
    """
    Deterministic fallback that extracts a clean setup/punchline from the bot's
    chat reply if the LLM outputted a placeholder like 'TOP CAPTION' and the
    caption-refresh API call timed out.
    """
    cleaned = _strip_conversational_filler_from_reply(reply_text)
    if not cleaned:
        return "", ""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if len(s.strip()) >= 6]
    if len(sentences) >= 2:
        t_words = sentences[0].split()[:8]
        b_words = sentences[-1].split()[:8]
        return " ".join(t_words).rstrip(".!?"), " ".join(b_words).rstrip(".!?")
    words = cleaned.split()
    if len(words) >= 6:
        mid = min(7, len(words) // 2)
        return " ".join(words[:mid]), " ".join(words[mid:mid + 8]).rstrip(".!?")
    return " ".join(words[:8]).rstrip(".!?"), ""


async def ensure_unique_meme_captions(
    visual_prompt: str,
    top_text: str,
    bottom_text: str,
    reply_text: str,
    user_prompt: str,
    recent_gen_records: list[dict],
    bot_name: str,
    persona_summary: str,
    api_key: str,
    text_model: str,
    api_url: str
) -> tuple[str, str]:
    """
    Checks if `top_text` or `bottom_text` is a template placeholder (like 'TOP CAPTION'),
    repeats a banned stock caption, or matches any recently generated meme caption in the channel.
    If so, synthesizes fresh, context-specific captions tied to the current message.
    """
    t_clean = "" if _is_placeholder_caption(top_text) else (top_text or "").strip()
    b_clean = "" if _is_placeholder_caption(bottom_text) else (bottom_text or "").strip()

    recent_captions = set(OVERUSED_MEME_CAPTIONS)
    for rec in (recent_gen_records or [])[-10:]:
        p_str = rec.get("prompt", "")
        cap_m = RECORDED_MEME_CAPTION_EXTRACT_REGEX.search(p_str)
        if cap_m:
            raw_pair = cap_m.group(1)
            for part in raw_pair.split("/"):
                p_norm = part.strip().lower()
                if p_norm:
                    recent_captions.add(p_norm)

    def _is_stale_or_invalid(caption: str) -> bool:
        if not caption:
            return False
        if _is_placeholder_caption(caption):
            return True
        c_low = caption.strip().lower()
        if c_low in recent_captions:
            return True
        c_toks = _normalize_tokens(c_low)
        if not c_toks:
            return True
        for old_c in recent_captions:
            old_toks = _normalize_tokens(old_c)
            if old_toks and len(c_toks) >= 2 and len(old_toks) >= 2:
                overlap = len(c_toks & old_toks) / float(min(len(c_toks), len(old_toks)))
                if overlap >= 0.65:
                    return True
        return False

    needs_refresh = (
        (not t_clean and not b_clean)
        or _is_stale_or_invalid(t_clean)
        or _is_stale_or_invalid(b_clean)
    )
    if not needs_refresh:
        return t_clean, b_clean

    print(f"[{bot_name}] [MEME CAPTION REFRESH] Replacing placeholder/stale meme caption ('{top_text} / {bottom_text}') with fresh joke...")
    banned_list_str = "; ".join(f"'{c.upper()}'" for c in list(recent_captions)[:10])

    payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are writing fresh, hilarious TOP and BOTTOM meme captions for '{bot_name}' ({persona_summary[:280]}).\n"
                    f"BANNED / ALREADY USED CAPTIONS (DO NOT USE ANY OF THESE): {banned_list_str}\n\n"
                    "RULES:\n"
                    "1. Write a brand-new setup line (3-8 words) and punchline (2-7 words) directly reacting to the specific details in the user's message and your reply.\n"
                    "2. NEVER output literal placeholder labels like 'TOP CAPTION', 'BOTTOM CAPTION', 'TOP TEXT', or 'ABSOLUTELY UNACCEPTABLE'.\n"
                    "3. Output ONLY the two actual joke lines separated by a single pipe `|` on one line."
                )
            },
            {
                "role": "user",
                "content": (
                    f"User message: {user_prompt}\n"
                    f"Your text reply: {reply_text}\n"
                    f"Visual scene: {visual_prompt}"
                )
            }
        ],
        "temperature": 0.85,
        "max_tokens": 60
    }
    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=18,
        max_retries=2,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            raw_out = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            _, new_t, new_b = parse_meme_gen_tag(f"scene | {raw_out}")
            if new_t or new_b:
                return new_t, new_b
        except Exception:
            pass

    if not t_clean and not b_clean:
        return _derive_fallback_captions_from_reply(reply_text)
    return t_clean, b_clean


async def synthesize_missing_image_prompt(
    reply_text: str,
    user_prompt: str,
    recent_gen_records: list[dict],
    bot_name: str,
    persona_summary: str,
    api_key: str,
    text_model: str,
    api_url: str,
    raw_history: list[dict] | None = None
) -> str | None:
    requested_subject = _extract_explicit_requested_subject(user_prompt, bot_name, raw_history)
    clean_reply_scene = _strip_conversational_filler_from_reply(reply_text)
    subject_clause = (
        f"CRITICAL: The user explicitly asked for an image of: '{requested_subject}'. "
        "Your visual prompt MUST depict that exact subject and all its requested details! "
        if requested_subject
        else "Depict the EXACT subject, character, props, and details the bot is showing or describing in its reply. "
    )

    payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are the visual imagination engine for '{bot_name}'. "
                    f"{subject_clause}"
                    "Do NOT replace the requested subject with a generic picture of the bot itself! "
                    "Output ONLY the raw visual description (25-50 words) with no tags, no written text inside the scene, and no commentary."
                )
            },
            {
                "role": "user",
                "content": f"User asked: {user_prompt}\nBot's visual description: {clean_reply_scene}"
            }
        ],
        "temperature": 0.55,
        "max_tokens": 90
    }
    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=20,
        max_retries=2,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            synth = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            synth = MEDIA_TAG_WRAP_STRIP_REGEX.sub('', synth).strip(' "\'')
            synth = synth.split("|")[0].strip()
            if len(synth) > 10:
                return synth
        except Exception:
            pass
    if requested_subject:
        return f"High quality, vivid illustration of {requested_subject}, detailed lighting and atmospheric composition"
    return None


async def generate_image_attachment(
    img_prompt: str,
    image_gen_model: str,
    api_key: str,
    bot_id: int,
    bot_name: str,
    channel_id: int,
    trigger_msg_id: int,
    shared_image_cache_file: str,
    meme_top_text: str = "",
    meme_bottom_text: str = ""
) -> tuple[BytesIO | None, str | None]:
    # Final hard guard: never render template placeholders onto an image
    if _is_placeholder_caption(meme_top_text):
        meme_top_text = ""
    if _is_placeholder_caption(meme_bottom_text):
        meme_bottom_text = ""

    is_meme = bool(meme_top_text.strip() or meme_bottom_text.strip())
    if is_meme:
        img_prompt = PROMPT_BAKED_TEXT_STRIP_REGEX.sub('', img_prompt).strip()
    mode_label = "MEME GEN" if is_meme else "IMAGE GEN"
    err_prefix = "Meme" if is_meme else "Image"
    print(f"[{bot_name}] [{mode_label}] Requesting: '{img_prompt}' (Top='{meme_top_text}', Bottom='{meme_bottom_text}')")

    img_res = await post_with_backoff(
        "https://api.deepinfra.com/v1/openai/images/generations",
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload={
            "prompt": img_prompt,
            "model": image_gen_model,
            "seed": random.randint(1, 2_000_000_000)
        },
        timeout=45,
        max_retries=2,
        bot_name=bot_name
    )
    if img_res is None:
        return None, f"*({err_prefix} generation timed out)*"

    if img_res.status_code == 200:
        try:
            img_data = img_res.json()
            img_url = img_data.get("data", [{}])[0].get("url")
            img_b64 = img_data.get("data", [{}])[0].get("b64_json")
            raw_img_bytes = None
            if img_url:
                raw_img_bytes, _ = await safe_download_url_bytes(img_url, timeout=15)
            elif img_b64:
                raw_img_bytes = base64.b64decode(img_b64)

            if raw_img_bytes and len(raw_img_bytes) > 256:
                if is_meme:
                    raw_img_bytes = await asyncio.to_thread(
                        render_meme_captions, raw_img_bytes, meme_top_text, meme_bottom_text
                    )
                image_file = BytesIO(raw_img_bytes)
                image_file.seek(0)

                def _record_gen(cache_data):
                    gen_hist = cache_data.get("_generated_images", [])
                    if not isinstance(gen_hist, list):
                        gen_hist = []
                    caption_note = f" [Meme Caption: '{meme_top_text} / {meme_bottom_text}']" if is_meme else ""
                    gen_hist.append({
                        "bot_id": bot_id,
                        "bot_name": bot_name,
                        "channel_id": channel_id,
                        "prompt": f"{img_prompt}{caption_note}",
                        "trigger_msg_id": trigger_msg_id,
                        "timestamp": time.time()
                    })
                    cache_data["_generated_images"] = gen_hist[-35:]
                    return cache_data

                update_json_file(shared_image_cache_file, {}, _record_gen)
                return image_file, None
        except Exception as e:
            print(f"[{bot_name}] [ERROR] {err_prefix} Gen decode failed: {e}")
            return None, f"*({err_prefix} generation decode error)*"
    else:
        err_txt = "Blocked or unavailable"
        try:
            err_txt = img_res.json().get("error", {}).get("message", err_txt)
        except Exception:
            pass
        clean_err_txt = sanitize_api_error_text(str(err_txt), "Blocked or unavailable")
        print(f"[{bot_name}] [ERROR] {err_prefix} Gen failed: {img_res.status_code} ({clean_err_txt})")
        return None, f"*({err_prefix} generation failed | {img_res.status_code}: {clean_err_txt})*"
    return None, None


async def generate_voice_attachment(
    raw_voice_content: str,
    voice_model: str,
    tts_voice: str,
    api_key: str,
    bot_name: str,
    default_role_desc: str,
    default_style: str = "Natural"
) -> tuple[BytesIO | None, str | None, str | None]:
    user_prompt_content, assistant_speech_content = parse_mimotts_voice_block(
        raw_voice_content, default_role_desc, default_style
    )
    if not assistant_speech_content or not assistant_speech_content.strip():
        print(f"[{bot_name}] [VOICE] Skipping TTS API call because spoken dialogue was empty after tag stripping.")
        return None, None, None

    print(f"[{bot_name}] [VOICE] Generating speech...")
    voice_url = "https://api.deepinfra.com/v1/openai/audio/speech"
    active_voice_val = tts_voice.strip() if tts_voice.strip() else user_prompt_content

    voice_payload = {
        "model": voice_model,
        "voice": active_voice_val,
        "input": assistant_speech_content,
        "response_format": "mp3"
    }

    voice_res = await post_with_backoff(
        voice_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=voice_payload,
        timeout=(10, 65),
        max_retries=3,
        bot_name=bot_name
    )

    if voice_res is None:
        return None, None, "*(Voice generation timed out)*"

    if voice_res.status_code == 200:
        try:
            content_type = voice_res.headers.get("Content-Type", "")
            raw_audio_bytes = None
            if "application/json" in content_type:
                v_data = voice_res.json()
                if "audio" in v_data and v_data["audio"]:
                    raw_audio_bytes = base64.b64decode(v_data["audio"])
            else:
                raw_audio_bytes = voice_res.content

            if raw_audio_bytes and len(raw_audio_bytes) > 256:
                voice_bio = BytesIO(raw_audio_bytes)
                voice_bio.seek(0)
                return voice_bio, assistant_speech_content, None
        except Exception as e:
            print(f"[{bot_name}] [ERROR] Voice decode error: {e}")
            return None, None, "*(Voice audio decode error)*"
    else:
        err_txt = voice_res.text[:160]
        try:
            err_txt = voice_res.json().get("error", {}).get("message", err_txt)
        except Exception:
            pass
        clean_err_txt = sanitize_api_error_text(str(err_txt), "Provider busy")
        print(f"[{bot_name}] [ERROR] Voice API returned {voice_res.status_code}: {clean_err_txt}")
        return None, None, f"*(Voice generation error {voice_res.status_code}: {clean_err_txt})*"
    return None, None, None


async def synthesize_missing_meme_spec(
    reply_text: str,
    user_prompt: str,
    recent_gen_records: list[dict],
    bot_name: str,
    persona_summary: str,
    api_key: str,
    text_model: str,
    api_url: str
) -> tuple[str | None, str, str]:
    recent_avoid = "\n".join(f"- {r.get('prompt', '')}" for r in (recent_gen_records or [])[-6:])
    banned_caps = ", ".join(f"'{c.upper()}'" for c in sorted(OVERUSED_MEME_CAPTIONS))
    avoid_clause = (
        f"\nAvoid copying any of these recent scenes or captions:\n{recent_avoid}\n"
        f"NEVER use these banned clichés or placeholder labels: {banned_caps}"
        if recent_avoid
        else f"\nNEVER use these banned clichés or placeholder labels: {banned_caps}"
    )

    clean_reply_scene = _strip_conversational_filler_from_reply(reply_text)
    payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are the meme generator engine for '{bot_name}' ({persona_summary[:300]}). "
                    "Create a hilarious, punchy internet meme based directly on the latest conversation and your reply. "
                    "Output EXACTLY 3 pipe-separated parts on a single line:\n"
                    "visual scene description (20-40 words, expressive funny reaction/situation, NO written words inside the scene) | setup caption (3-8 words) | punchline caption (2-7 words)"
                    f"{avoid_clause}\n"
                    "Output ONLY the 3 pipe-separated parts with actual funny joke captions (never literal words like 'TOP CAPTION')."
                )
            },
            {
                "role": "user",
                "content": f"User asked: {user_prompt}\nYour text reply: {clean_reply_scene}"
            }
        ],
        "temperature": 0.85,
        "max_tokens": 110
    }
    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=20,
        max_retries=2,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            synth = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            v_prompt, t_text, b_text = parse_meme_gen_tag(synth)
            if len(v_prompt) > 10 and (t_text or b_text):
                return v_prompt, t_text, b_text
        except Exception:
            pass
    return None, "", ""
