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
    STOPWORDS
)
from core_system import post_with_backoff, update_json_file
from text_and_parsers import parse_mimotts_voice_block
from media_engine import safe_download_url_bytes

# ==============================================================================
# VISION, EMOJIS, IMAGE & VOICE GENERATION ENGINES
# ==============================================================================

def _normalize_tokens(text: str) -> set[str]:
    """Internal helper for comparing prompt overlap safely."""
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


async def summarize_image_for_memory(image_bytes: bytes, api_key: str, vision_model: str, api_url: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": vision_model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Describe this image, screenshot, meme, or multi-frame video/GIF sequence in 1-2 concise, complete sentences (under 45 words). "
                        "If it shows a 2x2 sequence of frames from a video or GIF (top-left to bottom-right), describe the action across the clip as a single animation rather than calling it a grid. "
                        "State the main subject, any visible text/numbers/UI details verbatim, and the humor or mood."
                    )
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            ]
        }],
        "temperature": 0.25,
        "max_tokens": 120
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
    emoji_reaction_hint: str = ""
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
            cleaned_turn = re.sub(r'\*\(System Note:.*?\)\*', '', content_str).strip()
            cleaned_turn = ERROR_REPLY_STRIP_REGEX.sub('', cleaned_turn).strip()
            if cleaned_turn and not re.fullmatch(r'\[[^\]]+\]:\s*', cleaned_turn):
                clean_recent_turns.append(cleaned_turn)

    clean_recent_turns = clean_recent_turns[-emoji_context_length:]
    history_summary = "RECENT CONVERSATION CONTEXT:\n" + "\n".join(clean_recent_turns) + "\n\n" if clean_recent_turns else ""
    banned_emojis_str = ", ".join(my_recent_emojis[:6]) if my_recent_emojis else "None"

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
        "1. READ ALL MEDIA DETAILS: Pay close attention to `[Attached Media Description: ...]`, `[Contains GIF of: ...]`, `[Contains YouTube Video: ...]`, and `[Replying to ...]`. React to the specific object, joke, game, anime, or fail shown!\n"
        f"2. BANNED RECENT EMOJIS (DO NOT USE THESE): [{banned_emojis_str}]. Never spam the same default emoji.\n"
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
            json_match = JSON_EMOJI_REGEX.search(raw_json)
            if json_match:
                parsed = json.loads(json_match.group(0)).get("emojis", [])
                candidates = [str(e).strip() for e in parsed if str(e).strip()]
                non_repeated = [c for c in candidates if c not in my_recent_emojis[:4]]
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
        emoji_reaction_hint=kwargs.get("emoji_reaction_hint", "")
    )

    existing_reactions = {str(r.emoji) for r in message.reactions}
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


async def ensure_unique_image_prompt(
    img_prompt: str,
    recent_gen_records: list[dict],
    raw_history: list[dict],
    bot_name: str,
    persona_summary: str,
    user_prompt: str,
    api_key: str,
    text_model: str,
    api_url: str
) -> str:
    """
    Rewrites proposed prompts if they overlap heavily with recently generated images
    or chat descriptions, ensuring a completely distinct visual concept is rendered.
    """
    proposed_tokens = _normalize_tokens(img_prompt)
    if not proposed_tokens:
        return img_prompt

    conflicting_prompts = []

    for rec in (recent_gen_records or [])[-8:]:
        prev_prompt = rec.get("prompt", "")
        prev_tokens = _normalize_tokens(prev_prompt)
        if not prev_tokens:
            continue
        overlap = len(proposed_tokens & prev_tokens) / float(min(len(proposed_tokens), len(prev_tokens)))
        if overlap >= 0.34:
            conflicting_prompts.append(f"- {rec.get('bot_name', 'Another bot')}: '{prev_prompt}'")

    for msg in (raw_history or [])[-12:]:
        content_str = msg.get("content", "")
        if not isinstance(content_str, str):
            continue
        for desc_match in IMAGE_DESC_EXTRACT_REGEX.findall(content_str):
            desc_tokens = _normalize_tokens(desc_match)
            if not desc_tokens:
                continue
            overlap = len(proposed_tokens & desc_tokens) / float(min(len(proposed_tokens), len(desc_tokens)))
            if overlap >= 0.36:
                conflicting_prompts.append(f"- Recent image in chat: '{desc_match}'")

    if not conflicting_prompts:
        return img_prompt

    print(f"[{bot_name}] [IMAGE DIVERGENCE] Rewriting prompt to guarantee unique visual output...")
    conflict_str = "\n".join(conflicting_prompts[:6])
    rewrite_payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are crafting a unique visual prompt for '{bot_name}'.\n"
                    f"Persona summary: {persona_summary[:350]}\n"
                    f"Recent images in chat already depicted these scenes:\n{conflict_str}\n\n"
                    "TASK: Rewrite the proposed image prompt so it depicts a COMPLETELY DIFFERENT visual metaphor, setting, subject pose, camera angle, and art style that fits your persona, without repeating the animals, objects, or signs from the previous images. "
                    "Do NOT include text signs with written sentences. Output ONLY the new visual prompt (25-50 words) with no quotes or tags."
                )
            },
            {
                "role": "user",
                "content": f"User message: {user_prompt}\nProposed prompt to make unique: {img_prompt}"
            }
        ],
        "temperature": 0.85,
        "max_tokens": 90
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
            new_p = re.sub(r'^\[?\s*IMAGE_GEN\s*:\s*|\s*\]$', '', new_p, flags=re.IGNORECASE).strip(' "\'')
            if len(new_p) > 15:
                return new_p
        except Exception:
            pass

    return f"{img_prompt}, distinct cinematic perspective by {bot_name}, dramatic lighting, unique composition"


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
    recent_avoid = "\n".join(f"- {r.get('prompt', '')}" for r in (recent_gen_records or [])[-5:])
    avoid_clause = f"\nAvoid copying these recent prompts:\n{recent_avoid}" if recent_avoid else ""

    payload = {
        "model": text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are the visual imagination engine for '{bot_name}' ({persona_summary[:300]}). "
                    "Based on the user's request and your character's text reply, write a vivid, concrete image generation prompt (25-50 words) describing the exact scene, subject, lighting, and art style your character is presenting. "
                    "Avoid putting long written sentences on signs."
                    f"{avoid_clause}\n"
                    "Output ONLY the raw visual description with no tags or commentary."
                )
            },
            {
                "role": "user",
                "content": f"User asked: {user_prompt}\nYour text reply: {reply_text}"
            }
        ],
        "temperature": 0.75,
        "max_tokens": 85
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
            synth = re.sub(r'^\[?\s*IMAGE_GEN\s*:\s*|\s*\]$', '', synth, flags=re.IGNORECASE).strip(' "\'')
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
    shared_image_cache_file: str
) -> tuple[BytesIO | None, str | None]:
    print(f"[{bot_name}] [IMAGE GEN] Requesting: '{img_prompt}'")
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
        return None, "*(Image generation timed out)*"

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
                image_file = BytesIO(raw_img_bytes)
                image_file.seek(0)

                def _record_gen(cache_data):
                    gen_hist = cache_data.get("_generated_images", [])
                    if not isinstance(gen_hist, list):
                        gen_hist = []
                    gen_hist.append({
                        "bot_id": bot_id,
                        "bot_name": bot_name,
                        "channel_id": channel_id,
                        "prompt": img_prompt,
                        "trigger_msg_id": trigger_msg_id,
                        "timestamp": time.time()
                    })
                    cache_data["_generated_images"] = gen_hist[-35:]
                    return cache_data

                update_json_file(shared_image_cache_file, {}, _record_gen)
                return image_file, None
        except Exception as e:
            print(f"[{bot_name}] [ERROR] Image Gen decode failed: {e}")
            return None, "*(Image generation decode error)*"
    else:
        err_txt = "Blocked or unavailable"
        try:
            err_txt = img_res.json().get("error", {}).get("message", err_txt)
        except Exception:
            pass
        print(f"[{bot_name}] [ERROR] Image Gen failed: {img_res.status_code}")
        return None, f"*(Image generation failed | {img_res.status_code}: {err_txt})*"
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