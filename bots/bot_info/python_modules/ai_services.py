import re
import json
import time
import base64
import random
import asyncio
from io import BytesIO

import discord

from config import (
    FALLBACK_VISION_MODELS, FALLBACK_TEXT_MODELS, JSON_EMOJI_REGEX,
    REACT_NOTE_EXTRACT_REGEX, ERROR_REPLY_STRIP_REGEX, IMAGE_DESC_EXTRACT_REGEX,
    STOPWORDS, USER_SUBJECT_EXTRACT_REGEX, OVERUSED_MEME_CAPTIONS
)
from core_system import post_with_backoff, update_json_file
from text_and_parsers import (
    parse_mimotts_voice_block, parse_meme_gen_tag, PROMPT_BAKED_TEXT_STRIP_REGEX
)
from media_engine import safe_download_url_bytes, render_meme_captions

# ==============================================================================
# VISION, EMOJIS, IMAGE, MEME & VOICE GENERATION ENGINES
# ==============================================================================

UNICODE_EMOJI_FALLBACK_REGEX = re.compile(
    r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF]'
)


def _normalize_tokens(text: str) -> set[str]:
    """Internal helper for comparing prompt overlap safely."""
    if not text:
        return set()
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


async def summarize_image_for_memory(image_bytes: bytes, api_key: str, vision_model: str, api_url: str) -> str:
    """
    Summarizes an attached image, meme, doodle, or numbered 2x2 video/GIF storyboard
    into a concise, detail-rich memory tag so bots can reference specific visual gags,
    expressions, background details, and text later in conversation.
    """
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
            desc = re.sub(r'\s+', ' ', desc)
            if desc:
                return f"[Attached Media Description: {desc}]"
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
    if re.fullmatch(r'<a?:[a-zA-Z0-9_]+:\d+>', c):
        return c
    short_match = re.fullmatch(r':?([a-zA-Z0-9_]{2,32}):?', c)
    if short_match and server_custom_emojis:
        target_name = short_match.group(1).lower()
        for se in server_custom_emojis:
            m = re.match(r'<a?:([a-zA-Z0-9_]+):\d+>', se)
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

    if chat_history:
        for msg in reversed(chat_history[-35:]):
            content_str = msg.get("content", "")
            if not isinstance(content_str, str):
                continue
            react_match = REACT_NOTE_EXTRACT_REGEX.search(content_str)
            if react_match:
                reactor_name = react_match.group(1).strip()
                emoji_used = react_match.group(2).strip()
                if bot_name and reactor_name.lower() == bot_name.lower():
                    if emoji_used not in my_recent_emojis:
                        my_recent_emojis.append(emoji_used)

        for msg in chat_history[-(emoji_context_length * 2):]:
            content_str = msg.get("content", "")
            if not isinstance(content_str, str) or content_str.startswith("System Note:"):
                continue
            cleaned_turn = re.sub(r'\*\(System Note:.*?\)\*', '', content_str, flags=re.IGNORECASE).strip()
            cleaned_turn = re.sub(r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*', '', cleaned_turn, flags=re.IGNORECASE).strip()
            cleaned_turn = ERROR_REPLY_STRIP_REGEX.sub('', cleaned_turn).strip()
            if cleaned_turn and not re.fullmatch(r'\[[^\]]+\]:\s*', cleaned_turn):
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
    user_content = [{"type": "text", "text": prompt_text}]
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

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
    if not message.guild or message.author == message.guild.me:
        return None
    bot_name = kwargs.get("bot_name", message.guild.me.display_name)
    server_emojis = [str(emoji) for emoji in message.guild.emojis]
    active_model = kwargs["vision_model"] if kwargs.get("image_bytes") else kwargs["text_model"]
    text_to_use = kwargs.get("text", message.content.strip())
    existing_reactions = {str(r.emoji) for r in getattr(message, "reactions", [])}

    context_emojis = await get_contextual_emojis(
        api_key=kwargs["api_key"],
        text=text_to_use,
        image_bytes=kwargs.get("image_bytes"),
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


def _extract_explicit_requested_subject(user_prompt: str, bot_name: str) -> str:
    """
    If the user explicitly asked for an image/meme of a specific subject
    (e.g. 'generate an image of a bag of hint of lime tortilla chips...'),
    extracts that exact subject description unless the user asked for a picture of the bot itself.
    """
    if not user_prompt:
        return ""
    clean_u = re.sub(r'^\[[^\]]+\]:\s*', '', user_prompt.strip().split("\n")[-1]).strip()
    m = USER_SUBJECT_EXTRACT_REGEX.search(clean_u)
    if not m:
        return ""
    subj = m.group(1).strip().rstrip(".!?")
    if not subj or len(subj) < 3:
        return ""
    subj_lower = subj.lower()
    self_refs = {"you", "yourself", "urself", bot_name.lower(), bot_name.split()[0].lower()}
    if subj_lower in self_refs or any(subj_lower.startswith(f"{sr} ") for sr in self_refs):
        return ""
    return subj


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
    1. Ensures that if a user explicitly requested a specific visual subject
       (e.g. 'generate an image of X with Y'), the generated prompt faithfully depicts
       all key elements of X and Y instead of replacing them with a generic self-portrait.
    2. Rewrites the composition/camera angle if the prompt is a near-identical clone
       (>= 0.68 token overlap) of another recent image in the channel.
    """
    img_prompt = PROMPT_BAKED_TEXT_STRIP_REGEX.sub('', (img_prompt or "")).strip()
    proposed_tokens = _normalize_tokens(img_prompt)
    if not proposed_tokens:
        return img_prompt

    requested_subject = _extract_explicit_requested_subject(user_prompt, bot_name)
    req_tokens = _normalize_tokens(requested_subject)
    missing_requested_subject = False
    if req_tokens and len(req_tokens) >= 2:
        subj_overlap = len(proposed_tokens & req_tokens) / float(len(req_tokens))
        if subj_overlap < 0.45:
            missing_requested_subject = True

    conflicting_prompts = []
    for rec in (recent_gen_records or [])[-5:]:
        prev_prompt = rec.get("prompt", "").split("[Meme Caption:")[0].strip()
        prev_tokens = _normalize_tokens(prev_prompt)
        if not prev_tokens:
            continue
        overlap = len(proposed_tokens & prev_tokens) / float(min(len(proposed_tokens), len(prev_tokens)))
        if overlap >= 0.68:
            conflicting_prompts.append(f"- {rec.get('bot_name', 'Bot')}: '{prev_prompt}'")

    if not missing_requested_subject and not conflicting_prompts:
        return img_prompt

    if missing_requested_subject:
        print(f"[{bot_name}] [SUBJECT FIDELITY] Restoring user's explicitly requested subject into image prompt: '{requested_subject}'")
    else:
        print(f"[{bot_name}] [IMAGE DIVERGENCE] Adjusting camera/composition while preserving exact reply subject...")

    conflict_str = "\n".join(conflicting_prompts[:4]) if conflicting_prompts else "None"
    subject_directive = (
        f"CRITICAL USER REQUEST FIDELITY: The user explicitly asked for: '{requested_subject}'. "
        "You MUST depict ALL specific subjects, people, items, food, clothing, and actions requested by the user! "
        "Do NOT replace the user's requested characters or items with the bot's own character or random wizards!\n"
        if missing_requested_subject
        else (
            "CRITICAL RULE: You MUST keep the EXACT character, subject, props, and action that the bot just described in its visible chat reply! "
            "Only change the camera angle, lighting, or framing so it isn't an identical copy.\n"
        )
    )

    rewrite_payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are refining an image generation prompt for '{bot_name}'.\n"
                    f"User's request: {user_prompt}\n"
                    f"Bot's visible chat reply: {reply_text}\n"
                    f"Previously generated prompts to avoid cloning:\n{conflict_str}\n\n"
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
        "temperature": 0.55,
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
            new_p = re.sub(r'^\[?\s*(?:IMAGE_GEN|MEME_GEN)\s*:\s*|\s*\]$', '', new_p, flags=re.IGNORECASE).strip(' "\'')
            new_p = new_p.split("|")[0].strip()
            if len(new_p) > 15:
                return new_p
        except Exception:
            pass

    return img_prompt


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
    Checks if `top_text` or `bottom_text` repeats a banned stock caption
    (like 'ABSOLUTELY UNACCEPTABLE' or 'WHEN A PEASANT COMPARES YOU TO...')
    or matches any recently generated meme caption in the channel. If so,
    synthesizes fresh, context-specific captions tied to the current message.
    """
    t_clean = (top_text or "").strip()
    b_clean = (bottom_text or "").strip()
    if not t_clean and not b_clean:
        return "", ""

    recent_captions = set(OVERUSED_MEME_CAPTIONS)
    for rec in (recent_gen_records or [])[-10:]:
        p_str = rec.get("prompt", "")
        cap_m = re.search(r"\[Meme Caption:\s*'([^']*)'\]", p_str)
        if cap_m:
            raw_pair = cap_m.group(1)
            for part in raw_pair.split("/"):
                p_norm = part.strip().lower()
                if p_norm:
                    recent_captions.add(p_norm)

    def _is_stale(caption: str) -> bool:
        c_low = caption.strip().lower()
        if not c_low:
            return False
        if c_low in recent_captions:
            return True
        c_toks = _normalize_tokens(c_low)
        if not c_toks:
            return False
        for old_c in recent_captions:
            old_toks = _normalize_tokens(old_c)
            if old_toks and len(c_toks) >= 2 and len(old_toks) >= 2:
                overlap = len(c_toks & old_toks) / float(min(len(c_toks), len(old_toks)))
                if overlap >= 0.72:
                    return True
        return False

    if not _is_stale(t_clean) and not _is_stale(b_clean):
        return t_clean, b_clean

    print(f"[{bot_name}] [MEME CAPTION REFRESH] Replacing repeated/stock meme caption ('{t_clean} / {b_clean}') with fresh joke...")
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
                    "1. Write a brand-new TOP CAPTION (setup, 3-8 words) and BOTTOM CAPTION (punchline, 2-7 words) directly mocking or reacting to the specific details in the user's message and your reply.\n"
                    "2. NEVER use 'ABSOLUTELY UNACCEPTABLE' or generic stock catchphrases.\n"
                    "3. Output ONLY `TOP CAPTION | BOTTOM CAPTION` on a single line with a single pipe separator."
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
            raw_out = re.sub(r'^\[?\s*MEME_GEN\s*:\s*|\s*\]$', '', raw_out, flags=re.IGNORECASE).strip(' "\'')
            parts = [p.strip().strip('"\'*') for p in raw_out.split("|") if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1]
            elif len(parts) == 1 and parts[0]:
                return parts[0], ""
        except Exception:
            pass

    return t_clean, b_clean


async def synthesize_missing_image_prompt(
    reply_text: str,
    user_prompt: str,
    recent_gen_records: list[dict],
    bot_name: str,
    persona_summary: str,
    api_key: str,
    text_model: str,
    api_url: str
) -> str | None:
    requested_subject = _extract_explicit_requested_subject(user_prompt, bot_name)
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
                "content": f"User asked: {user_prompt}\nBot's text reply: {reply_text}"
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
            synth = re.sub(r'^\[?\s*(?:IMAGE_GEN|MEME_GEN)\s*:\s*|\s*\]$', '', synth, flags=re.IGNORECASE).strip(' "\'')
            synth = synth.split("|")[0].strip()
            if len(synth) > 10:
                return synth
        except Exception:
            pass
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
        print(f"[{bot_name}] [ERROR] {err_prefix} Gen failed: {img_res.status_code}")
        return None, f"*({err_prefix} generation failed | {img_res.status_code}: {err_txt})*"
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
    print(f"[{bot_name}] [VOICE] Generating speech...")
    user_prompt_content, assistant_speech_content = parse_mimotts_voice_block(
        raw_voice_content, default_role_desc, default_style
    )

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
        err_txt = voice_res.text[:120]
        try:
            err_txt = voice_res.json().get("error", {}).get("message", err_txt)
        except Exception:
            pass
        print(f"[{bot_name}] [ERROR] Voice API returned {voice_res.status_code}: {err_txt}")
        return None, None, f"*(Voice generation error {voice_res.status_code}: {err_txt})*"
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
        f"NEVER use these banned clichés: {banned_caps}"
        if recent_avoid
        else f"\nNEVER use these banned clichés: {banned_caps}"
    )

    payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are the meme generator engine for '{bot_name}' ({persona_summary[:300]}). "
                    "Create a hilarious, punchy internet meme based directly on the latest conversation and your reply. "
                    "Output EXACTLY 3 pipe-separated parts on a single line:\n"
                    "visual scene description (20-40 words, expressive funny reaction/situation, NO written words inside the scene) | TOP CAPTION (short punchy setup) | BOTTOM CAPTION (short punchy punchline)"
                    f"{avoid_clause}\n"
                    "Output ONLY `visual scene | TOP TEXT | BOTTOM TEXT` with no brackets."
                )
            },
            {
                "role": "user",
                "content": f"User asked: {user_prompt}\nYour text reply: {reply_text}"
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
