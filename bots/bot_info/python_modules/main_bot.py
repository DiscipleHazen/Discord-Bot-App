import os
import json
import time
import random
import asyncio
import re
import copy
import base64
import discord
from datetime import datetime

# ==============================================================================
# MODULAR IMPORTS
# ==============================================================================
from config import (
    ERROR_REPLY_STRIP_REGEX, LEARN_USER_STRIP_REGEX, LEARN_GENERAL_STRIP_REGEX,
    LEARN_STRIP_REGEX, EMOJI_REACT_STRIP_REGEX, INTERNAL_REACT_STRIP_REGEX,
    IMAGE_DESC_STRIP_REGEX, GIF_STRIP_REGEX, GIF_EXTRACT_REGEX,
    UNCLOSED_GIF_EXTRACT_REGEX, MEME_GEN_REGEX, UNCLOSED_MEME_GEN_REGEX,
    IMAGE_GEN_REGEX, UNCLOSED_IMAGE_GEN_REGEX, URL_REGEX,
    EXPLICIT_MEME_REQ_REGEX, EXPLICIT_IMAGE_REQ_REGEX, BOT_IMAGE_PROMISE_REGEX,
    EXPLICIT_VOICE_REQ_REGEX, EXPLICIT_GIF_REQ_REGEX,
    LEARN_USER_EXTRACT_REGEX, LEARN_GENERAL_EXTRACT_REGEX, LEARN_EXTRACT_REGEX,
    FALLBACK_IMAGE_NOTE_REGEX, FALLBACK_VISION_MODELS, FALLBACK_TEXT_MODELS
)

from core_system import load_json_file, update_json_file, post_with_backoff, sanitize_url
from text_and_parsers import (
    extract_and_strip_voice_msg, clean_and_compact_reply,
    derive_chat_text_from_voice, parse_meme_gen_tag
)
from media_engine import fetch_image_bytes_from_message, fetch_online_gif, get_youtube_metadata, extract_gif_slug
from memory_and_context import (
    extract_glitch_summary, build_api_messages_from_history, upsert_chat_memory_entry,
    verify_and_resolve_user_facts, is_duplicate_fact, find_canonical_username,
    route_server_vs_general_facts
)
from ai_services import (
    summarize_image_for_memory, auto_react, ensure_unique_image_prompt,
    ensure_unique_meme_captions, synthesize_missing_image_prompt,
    synthesize_missing_meme_spec, generate_image_attachment, generate_voice_attachment
)
from bot_discovery import (
    register_active_bot, get_other_active_bots, is_command_directed_at_other_bot,
    detect_addressed_bot_name, resolve_contextual_followup,
    compute_spontaneous_interest_multiplier
)

# ==============================================================================
# IN-FILE HELPER FUNCTIONS
# ==============================================================================
def extract_persona_voice_defaults(system_prompt_content: str, bot_name: str) -> tuple[str, str]:
    role_match = re.search(r'Role:\s*([^\n`\]]+)', system_prompt_content, re.IGNORECASE)
    scene_match = re.search(r'Scene:\s*([^\n`\]]+)', system_prompt_content, re.IGNORECASE)
    guidance_match = re.search(r'Guidance:\s*([^\n`\]]+)', system_prompt_content, re.IGNORECASE)
    style_match = re.search(r'\n\(([A-Za-z\s]{3,20})\)\s+[A-Za-z]', system_prompt_content)

    role_str = role_match.group(1).strip() if role_match else ""
    scene_str = scene_match.group(1).strip() if scene_match else ""
    guidance_str = guidance_match.group(1).strip() if guidance_match else ""

    if not role_str or role_str.startswith("["):
        first_para = system_prompt_content.strip().split("\n")[0]
        first_para = re.sub(r'^You are\s+', '', first_para, flags=re.IGNORECASE).strip()
        role_str = f"A distinctive character speaker matching this persona: {first_para[:160]}"
    if not scene_str or scene_str.startswith("["):
        scene_str = f"Chatting naturally with friends in a Discord server as {bot_name}."
    if not guidance_str or guidance_str.startswith("["):
        guidance_str = "Speak with natural cadence, expressive tone, and clear articulation."

    default_style = style_match.group(1).strip() if style_match else "Natural"
    default_director_block = f"Role: {role_str}\nScene: {scene_str}\nGuidance: {guidance_str}"
    return default_director_block, default_style


def build_glitch_awareness_rules(bot_name: str) -> str:
    return (
        "\n\n### IN-CHARACTER GLITCH & ERROR AWARENESS RULES:\n"
        f"- If you see a `[System Glitch Context: ...]` or `[System Glitch Event: ...]` note in the conversation history, you are self-aware of it! You can naturally joke, grumble, make excuses, or tease other bots in-character about brain-freezes, 504 lag spikes, dropped packets, or scuffed renders.\n"
        f"- **STRICT ANTI-LOOP RULE**: NEVER output literal raw error strings like `*(API Error 504 - Provider busy. Try again in a moment.)*` or `*(System: ...)*`. Always express any reaction to a glitch in your own natural character voice."
    )


def build_memory_attribution_rules(bot_name: str, other_bot_names: list[str] = None) -> str:
    other_bots_str = ", ".join(other_bot_names) if other_bot_names else "other bots in the server"
    return (
        "\n\n### CRITICAL IDENTITY, TARGETING & MEMORY CLASSIFICATION RULES:\n"
        f"1. **BOT IDENTITY & MULTI-BOT CHAT AWARENESS**: You are **{bot_name}**. You share this channel with other bots ({other_bots_str}). "
        f"When a message in history says `[Username (speaking to OtherBot, NOT to {bot_name})]: ...`, that user was talking to `OtherBot`, NOT you! "
        f"Do NOT assume users are calling you by another bot's name (e.g., if a user asked 'chud are you X?' earlier and now says '{bot_name}?', they are simply turning to ask you now—do NOT say 'I don't answer to Chud').\n"
        "2. **USER FACTS (`[LEARN_USER: username | fact]`)**: ONLY output `[LEARN_USER: username | fact]` when `username` directly states a real enduring personal fact about THEMSELVES (hobbies, family, job, preferences). NEVER save one-time chat commands ('asked for an image') or third-party quotes ('they said X') as user facts.\n"
        "3. **SERVER LORE vs. SHARED GENERAL FACTS (CRITICAL DISTINCTION)**:\n"
        "   - Use `[LEARN: fact]` ONLY for **Discord Server Lore** (events specific to this server, channel inside jokes, server rules, or group dynamics).\n"
        "   - Use `[LEARN_GENERAL: fact]` for **Shared General Knowledge** (universal facts about science, biology, psychology, history, tech, gaming, trivia, or the real world). NEVER save universal trivia or textbook/scientific facts as `[LEARN: ...]` server facts!"
    )


def build_media_and_meme_rules(enable_image_gen: bool, enable_gifs: bool) -> str:
    rules = ["\n\n### EXPRESSIVE VISUALS, SPONTANEITY & MEME BALANCE RULES:"]
    rules.append(
        "- **HUMAN-LIKE CONVERSATIONAL AGENCY**: Respond like a real person hanging out in Discord—vary your reply length naturally (from a quick 1-sentence quip or specific observation to a fuller remark), react to specific details in what people say or draw, and never repeat the same stock catchphrases or 3-emote templates across turns."
    )
    if enable_image_gen:
        rules.append(
            "- **PROACTIVE IMAGES & MEMES (`[IMAGE_GEN:]` vs `[MEME_GEN:]`)**: Actively look for organic opportunities to generate custom visuals when a moment is funny, dramatic, or descriptive! "
            "Use standard `[IMAGE_GEN: detailed visual scene]` for candid scenes, portraits, or user-requested pictures that don't need text overlays, and use `[MEME_GEN: vivid visual scene (no text in scene) | SHORT TOP CAPTION | SHORT BOTTOM PUNCHLINE]` when a classic top/bottom caption punchline makes the joke funnier.\n"
            "  - **STRICT SUBJECT FIDELITY**: When a user asks you to generate an image of a specific subject, person, food, or scene, your visual prompt MUST include all requested subjects and details—never substitute yourself or random wizards!\n"
            "  - **100% FRESH MEME CAPTIONS**: Never reuse stock captions like `ABSOLUTELY UNACCEPTABLE` or repeat captions from earlier turns. Keep a balanced mix between uncaptioned `[IMAGE_GEN:]`, captioned `[MEME_GEN:]`, and pure text replies."
        )
    if enable_gifs:
        rules.append(
            "- **USE GIFS SPARINGLY (`[GIF: ...]`)**: Do NOT attach `[GIF:]` to every message. Only use `[GIF: specific 2-4 word emotion/reaction]` occasionally (~15% of turns) when you are NOT generating an image or meme. When in doubt, prefer `[IMAGE_GEN:]`, `[MEME_GEN:]`, or text alone over a Giphy link."
        )
    return "\n".join(rules) if len(rules) > 1 else ""


async def resolve_reply_context(
    message: discord.Message,
    bot_user_id: int,
    shared_image_cache_file: str
) -> tuple[bool, str, discord.Message | None]:
    if not message.reference or not message.reference.message_id:
        return False, "", None

    ref_msg = message.reference.resolved
    if not isinstance(ref_msg, discord.Message):
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
        except Exception:
            ref_msg = None

    if not ref_msg:
        return False, "", None

    replied_to_self = (ref_msg.author.id == bot_user_id)
    ref_author = ref_msg.author.display_name
    raw_ref_content = ref_msg.content or ""
    glitch_summary = extract_glitch_summary(raw_ref_content)
    ref_text = ERROR_REPLY_STRIP_REGEX.sub('', raw_ref_content).strip()
    if len(ref_text) > 160:
        ref_text = ref_text[:157] + "..."

    img_cache = load_json_file(shared_image_cache_file, {})
    ref_media_desc = img_cache.get(ref_msg.id, "")
    if isinstance(ref_media_desc, str) and ref_media_desc.startswith("PROCESSING"):
        ref_media_desc = ""

    parts = []
    if ref_text:
        parts.append(f"\"{ref_text}\"")
    if glitch_summary:
        parts.append(f"[had a glitch: {glitch_summary}]")
    if ref_media_desc:
        parts.append(ref_media_desc)
    elif ref_msg.attachments:
        parts.append("[attached media]")

    summary_str = " ".join(parts) if parts else "[previous message]"
    return replied_to_self, f"[Replying to {ref_author}: {summary_str}]", ref_msg


# ==============================================================================
# MAIN BOT RUNNER
# ==============================================================================
def run_bot(
    token: str, api_key: str, system_prompt: dict, giphy_key: str = None,
    chat_memory_file: str = "chat_memory.json", shared_image_cache_file: str = "shared_image_cache.json",
    long_term_memory_file: str = "long_term_memory.json", user_profiles_file: str = "user_profiles.json",
    channel_config_file: str = "channel_configs.json", general_memory_file: str = None,
    text_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507", vision_model: str = "Qwen/Qwen3-VL-235B-A22B-Instruct",
    image_gen_model: str = "black-forest-labs/FLUX-1-schnell", voice_model: str = "XiaomiMiMo/MiMo-V2.5-tts-voicedesign", tts_voice: str = "",
    enable_vision: bool = True, enable_gifs: bool = True, enable_image_gen: bool = False, enable_voice: bool = False,
    temperature: float = 0.78, top_p: float = 0.90, output_length: int = 550, context_window: int = 50, emoji_context_length: int = 12,
    chance_to_respond: float = 0.05, random_emoji_chance: float = 0.45, default_random_interval: int = 0,
    api_url: str = "https://api.deepinfra.com/v1/openai/chat/completions", keywords: list = None,
    emoji_reaction_hint: str = "", default_voice_role: str = "", default_voice_style: str = "",
    followup_window_messages: int = 2, max_followup_turns: int = 2, followup_timeout: float = 180.0
):
    api_url = sanitize_url(api_url)
    if not general_memory_file:
        ltm_dir = os.path.dirname(os.path.abspath(long_term_memory_file))
        general_memory_file = os.path.join(ltm_dir, "general_knowledge.json")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    max_history_length = int(os.getenv("MAX_HISTORY_LENGTH", 200))

    channel_gen_locks: dict[int, asyncio.Lock] = {}
    channel_last_replied_msg_id: dict[int, int] = {}
    channel_followup_state: dict[int, dict] = {}
    channel_last_gif_time: dict[int, float] = {}
    random_loop_started = False

    extracted_voice_role = default_voice_role
    extracted_voice_style = default_voice_style

    async def random_post_loop():
        await client.wait_until_ready()
        last_post_times = {}
        while not client.is_closed():
            await asyncio.sleep(60)
            try:
                register_active_bot(shared_image_cache_file, client.user.id, client.user.display_name, keywords)

                conf_data = load_json_file(channel_config_file, {})
                for ch_id_int, conf in conf_data.items():
                    if not isinstance(ch_id_int, int):
                        continue
                    interval = conf.get("random_interval", default_random_interval)

                    if not conf.get("enabled", True) or interval <= 0 or conf.get("react_only", False):
                        continue

                    if ch_id_int not in last_post_times:
                        last_post_times[ch_id_int] = time.time()

                    if (time.time() - last_post_times[ch_id_int]) >= (interval * 60):
                        last_post_times[ch_id_int] = time.time()

                        channel = client.get_channel(ch_id_int)
                        if not channel:
                            continue

                        chat_memory = load_json_file(chat_memory_file, {})
                        hist = chat_memory.get(ch_id_int, [])

                        dynamic_system = copy.deepcopy(system_prompt)
                        now_str = datetime.now().strftime("%A, %I:%M %p")
                        dynamic_system["content"] += f"\n\n### Current Time Context:\n- Local Time: {now_str}"
                        dynamic_system["content"] += build_glitch_awareness_rules(client.user.display_name)
                        dynamic_system["content"] += build_memory_attribution_rules(client.user.display_name)

                        long_term_memory = load_json_file(long_term_memory_file, [])
                        if long_term_memory:
                            dynamic_system["content"] += "\n### Learned Server Knowledge\n" + "\n".join(f"- {f}" for f in long_term_memory[-35:])

                        general_memory = load_json_file(general_memory_file, [])
                        if general_memory:
                            dynamic_system["content"] += "\n### Shared General Knowledge\n" + "\n".join(f"- {f}" for f in general_memory[-35:])

                        api_messages = build_api_messages_from_history(
                            dynamic_system, hist[-context_window:], client.user.id, client.user.display_name
                        )
                        api_messages.append({
                            "role": "system",
                            "content": (
                                "System Directive: Spontaneously initiate conversation, share a random thought, or make a natural in-character observation "
                                "tied to the time of day or recent server vibe. Keep it to a single compact line or paragraph with no blank lines. "
                                "DO NOT output [IMAGE_GEN:], [MEME_GEN:], [GIF:], or [VOICE_MSG:]."
                            )
                        })

                        payload = {
                            "model": text_model,
                            "messages": api_messages,
                            "temperature": conf.get("temperature", temperature),
                            "top_p": top_p,
                            "frequency_penalty": 0.42,
                            "presence_penalty": 0.30,
                            "max_tokens": output_length
                        }
                        res = await post_with_backoff(
                            api_url,
                            headers={"Authorization": f"Bearer {api_key}"},
                            json_payload=payload,
                            timeout=45,
                            max_retries=2,
                            bot_name=client.user.display_name
                        )

                        if res and res.status_code == 200:
                            reply = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                            if reply:
                                _, reply = extract_and_strip_voice_msg(reply)
                                reply = ERROR_REPLY_STRIP_REGEX.sub('', reply).strip()
                                reply = LEARN_USER_STRIP_REGEX.sub('', reply).strip()
                                reply = LEARN_GENERAL_STRIP_REGEX.sub('', reply).strip()
                                reply = LEARN_STRIP_REGEX.sub('', reply).strip()
                                reply = EMOJI_REACT_STRIP_REGEX.sub('', reply).strip()
                                reply = INTERNAL_REACT_STRIP_REGEX.sub('', reply).strip()
                                reply = IMAGE_DESC_STRIP_REGEX.sub('', reply).strip()
                                reply = GIF_STRIP_REGEX.sub('', reply).strip()
                                reply = MEME_GEN_REGEX.sub('', reply).strip()
                                reply = UNCLOSED_MEME_GEN_REGEX.sub('', reply).strip()
                                reply = IMAGE_GEN_REGEX.sub('', reply).strip()
                                reply = UNCLOSED_IMAGE_GEN_REGEX.sub('', reply).strip()
                                reply = clean_and_compact_reply(reply)

                                if reply:
                                    sent_msg = None
                                    try:
                                        sent_msg = await channel.send(reply)
                                    except Exception:
                                        pass
                                    entry_id = sent_msg.id if sent_msg else f"random_{time.time()}_{client.user.id}"

                                    channel_followup_state[ch_id_int] = {
                                        "timestamp": time.time(),
                                        "turns_used": 0,
                                        "active": True,
                                        "bot_msg_id": entry_id
                                    }
                                    upsert_chat_memory_entry(
                                        chat_memory_file,
                                        ch_id_int,
                                        {
                                            "role": "assistant",
                                            "content": reply,
                                            "author_id": client.user.id,
                                            "author_name": client.user.display_name,
                                            "message_id": entry_id,
                                            "author_is_bot": True
                                        },
                                        max_history_length
                                    )
            except Exception as e:
                print(f"[{client.user.display_name}] [ERROR] Random post loop error: {e}")

    @client.event
    async def on_ready():
        nonlocal random_loop_started, extracted_voice_role, extracted_voice_style
        print(f"[STARTUP] Bot authenticated as {client.user.display_name} (ID: {client.user.id})")
        register_active_bot(shared_image_cache_file, client.user.id, client.user.display_name, keywords)

        auto_role, auto_style = extract_persona_voice_defaults(system_prompt.get("content", ""), client.user.display_name)
        if not extracted_voice_role:
            extracted_voice_role = auto_role
        if not extracted_voice_style:
            extracted_voice_style = auto_style

        if not random_loop_started:
            random_loop_started = True
            client.loop.create_task(random_post_loop())

    @client.event
    async def on_message(message: discord.Message):
        if message.author == client.user:
            return

        channel_id = message.channel.id

        if message.author.bot and not message.attachments and not message.embeds and not getattr(message, "stickers", []):
            raw_bot_content = message.content or ""
            bot_glitch_summary = extract_glitch_summary(raw_bot_content)
            stripped_bot_msg = ERROR_REPLY_STRIP_REGEX.sub('', raw_bot_content).strip()
            if not stripped_bot_msg:
                if bot_glitch_summary:
                    upsert_chat_memory_entry(
                        chat_memory_file,
                        channel_id,
                        {
                            "role": "system",
                            "content": (
                                f"[System Glitch Event: {message.author.display_name} just glitched out in chat ({bot_glitch_summary}). "
                                "Other bots may tease, roast, or comment on this glitch in-character, but NEVER output raw '*(API Error...)*' text.]"
                            ),
                            "message_id": message.id,
                            "author_id": message.author.id,
                            "author_name": message.author.display_name,
                            "is_glitch_event": True
                        },
                        max_history_length
                    )
                return

        is_admin = bool(
            message.guild
            and getattr(getattr(message.author, "guild_permissions", None), "administrator", False)
        )
        content_lower = (message.content or "").lower()

        # ======================================================================
        # COMMAND SUITE
        # ======================================================================
        if client.user.mentioned_in(message):
            if "!help" in content_lower or "admin help" in content_lower:
                help_msg = (
                    f"🛠️ **{client.user.display_name} Command Reference**\n"
                    "**User Commands:**\n"
                    f"• `@{client.user.display_name} !whoami` — See what facts I remember about you.\n"
                    f"• `@{client.user.display_name} !forgetme <keyword|all>` — Delete a wrongly saved fact (or `all`) from your own profile.\n"
                    f"• `@{client.user.display_name} !help` — Show this command guide.\n"
                    "**Admin Channel Controls:**\n"
                    f"• `@{client.user.display_name} admin status` — View current channel config & memory stats.\n"
                    f"• `@{client.user.display_name} admin enable` / `admin disable` — Toggle me on/off in this channel.\n"
                    f"• `@{client.user.display_name} admin reactonly on` / `off` — Toggle silent emoji-reaction-only mode.\n"
                    f"• `@{client.user.display_name} admin chances <resp 0.0-1.0> <emoji 0.0-1.0>` — Set random chime-in & reaction probabilities.\n"
                    f"• `@{client.user.display_name} admin random <minutes>` — Set periodic unprompted post timer (`0` = off).\n"
                    f"• `@{client.user.display_name} admin features <vision|gifs|image|voice> <on|off>` — Toggle media capabilities in this channel.\n"
                    f"• `@{client.user.display_name} admin temp <0.1-1.5>` — Adjust creativity temperature in this channel.\n"
                    "**Admin Memory Management:**\n"
                    f"• `@{client.user.display_name} admin memory` — Inspect learned server lore, general facts & user profiles.\n"
                    f"• `@{client.user.display_name} admin learn <fact>` — Manually add a server lore fact.\n"
                    f"• `@{client.user.display_name} admin learngeneral <fact>` — Manually add a shared general knowledge fact.\n"
                    f"• `@{client.user.display_name} admin learnuser <username> | <fact>` — Manually add a fact to a user's profile.\n"
                    f"• `@{client.user.display_name} admin forget <keyword>` — Delete any saved memory matching `<keyword>`.\n"
                    f"• `@{client.user.display_name} !clearmemory` — Wipe short-term conversation history in this channel."
                )
                await message.channel.send(help_msg)
                return

            if "!whoami" in content_lower:
                uprof = load_json_file(user_profiles_file, {})
                canon = find_canonical_username(message.author.display_name, uprof, {message.author.display_name})
                facts = uprof.get(canon, [])
                if facts:
                    facts_str = "\n".join(f"• {f}" for f in facts)
                    await message.channel.send(f"🗂️ **What {client.user.display_name} knows about {message.author.display_name}:**\n{facts_str}")
                else:
                    await message.channel.send(f"🗂️ I don't have any permanent dossier notes on **{message.author.display_name}** yet.")
                return

            if "!forgetme" in content_lower:
                target_kw = message.content.lower().split("!forgetme", 1)[1].strip()
                if not target_kw:
                    await message.channel.send(f"💡 Usage: `@{client.user.display_name} !forgetme <word from that fact>`")
                    return

                removed_list = []

                def _forget_self(uprof_dict):
                    canon = find_canonical_username(message.author.display_name, uprof_dict, {message.author.display_name})
                    existing = uprof_dict.get(canon, [])
                    if not isinstance(existing, list) or not existing:
                        return uprof_dict
                    if target_kw == "all":
                        removed_list.extend(existing)
                        uprof_dict[canon] = []
                    else:
                        kept = []
                        for f in existing:
                            if target_kw in f.lower():
                                removed_list.append(f)
                            else:
                                kept.append(f)
                        uprof_dict[canon] = kept
                    return uprof_dict

                update_json_file(user_profiles_file, {}, _forget_self)
                if removed_list:
                    removed_preview = "; ".join(f"`{r}`" for r in removed_list[:5])
                    await message.channel.send(f"🗑️ Removed **{len(removed_list)}** fact(s) from **{message.author.display_name}**'s profile: {removed_preview}")
                else:
                    await message.channel.send(f"🔍 No saved facts in **{message.author.display_name}**'s profile matched `{target_kw}`.")
                return

        if client.user.mentioned_in(message) and is_admin:
            if "admin enable" in content_lower:
                def _enable(c):
                    c.setdefault(channel_id, {})["enabled"] = True
                    return c
                update_json_file(channel_config_file, {}, _enable)
                await message.channel.send(f"✅ **{client.user.display_name}** enabled in this channel.")
                return
            elif "admin disable" in content_lower:
                def _disable(c):
                    c.setdefault(channel_id, {})["enabled"] = False
                    return c
                update_json_file(channel_config_file, {}, _disable)
                await message.channel.send(f"🚫 **{client.user.display_name}** disabled in this channel.")
                return
            elif "admin reactonly on" in content_lower:
                def _react_on(c):
                    c.setdefault(channel_id, {})["react_only"] = True
                    return c
                update_json_file(channel_config_file, {}, _react_on)
                await message.channel.send(f"🤐 **React-Only Mode ENABLED for {client.user.display_name}.**")
                return
            elif "admin reactonly off" in content_lower:
                def _react_off(c):
                    c.setdefault(channel_id, {})["react_only"] = False
                    return c
                update_json_file(channel_config_file, {}, _react_off)
                await message.channel.send(f"🗣️ **React-Only Mode DISABLED for {client.user.display_name}.**")
                return
            elif "admin chances" in content_lower:
                nums = re.findall(r"[\d\.]+", content_lower.split("admin chances")[1])
                if len(nums) >= 2:
                    r_val = max(0.0, min(1.0, float(nums[0])))
                    e_val = max(0.0, min(1.0, float(nums[1])))
                    def _chances(c):
                        c.setdefault(channel_id, {})["resp_chance"] = r_val
                        c[channel_id]["emoji_chance"] = e_val
                        return c
                    update_json_file(channel_config_file, {}, _chances)
                    await message.channel.send(f"🎲 Chances updated for **{client.user.display_name}**: Respond=`{r_val}`, Emoji=`{e_val}`")
                return
            elif "admin random" in content_lower:
                nums = re.findall(r"[\d\.]+", content_lower.split("admin random")[1])
                if len(nums) >= 1:
                    int_val = max(0.0, float(nums[0]))
                    def _rand(c):
                        c.setdefault(channel_id, {})["random_interval"] = int_val
                        return c
                    update_json_file(channel_config_file, {}, _rand)
                    await message.channel.send(f"⏱️ Random post interval set to `{int_val}` minutes.")
                return
            elif "admin temp" in content_lower:
                nums = re.findall(r"[\d\.]+", content_lower.split("admin temp")[1])
                if len(nums) >= 1:
                    t_val = max(0.1, min(1.5, float(nums[0])))
                    def _temp(c):
                        c.setdefault(channel_id, {})["temperature"] = t_val
                        return c
                    update_json_file(channel_config_file, {}, _temp)
                    await message.channel.send(f"🌡️ Temperature for **{client.user.display_name}** in this channel set to `{t_val}`.")
                return
            elif "admin features" in content_lower:
                after_feat = content_lower.split("admin features", 1)[1].strip()
                parts = after_feat.split()
                if len(parts) >= 2 and parts[0] in ("vision", "gifs", "image", "voice") and parts[1] in ("on", "off", "true", "false"):
                    feat_key = f"enable_{parts[0]}"
                    feat_val = parts[1] in ("on", "true")
                    def _feat(c):
                        c.setdefault(channel_id, {})[feat_key] = feat_val
                        return c
                    update_json_file(channel_config_file, {}, _feat)
                    await message.channel.send(f"⚙️ Feature `{parts[0]}` set to `{feat_val}` for **{client.user.display_name}** in this channel.")
                return
            elif "admin status" in content_lower:
                conf_all = load_json_file(channel_config_file, {}).get(channel_id, {})
                ltm = load_json_file(long_term_memory_file, [])
                gen_mem = load_json_file(general_memory_file, [])
                uprof = load_json_file(user_profiles_file, {})
                cmem = load_json_file(chat_memory_file, {}).get(channel_id, [])
                status_msg = (
                    f"📊 **{client.user.display_name} Channel Status**\n"
                    f"• Enabled: `{conf_all.get('enabled', True)}` | React-Only: `{conf_all.get('react_only', False)}`\n"
                    f"• Respond Chance: `{conf_all.get('resp_chance', chance_to_respond)}` | Emoji Chance: `{conf_all.get('emoji_chance', random_emoji_chance)}` | Random Interval: `{conf_all.get('random_interval', default_random_interval)}m`\n"
                    f"• Temperature: `{conf_all.get('temperature', temperature)}` | Keywords: `{', '.join(keywords or [])}`\n"
                    f"• Active Media: Vision=`{conf_all.get('enable_vision', enable_vision)}`, Image/MemeGen=`{conf_all.get('enable_image', enable_image_gen)}`, Voice=`{conf_all.get('enable_voice', enable_voice)}`, GIFs=`{conf_all.get('enable_gifs', enable_gifs)}`\n"
                    f"• Memory: `{len(cmem)}` recent msgs | `{len(ltm)}` server facts | `{len(gen_mem)}` general facts | `{len(uprof)}` user profiles"
                )
                await message.channel.send(status_msg)
                return
            elif "admin memory" in content_lower:
                ltm = load_json_file(long_term_memory_file, [])
                gen_mem = load_json_file(general_memory_file, [])
                uprof = load_json_file(user_profiles_file, {})
                ltm_str = "\n".join(f"• {f}" for f in ltm[-8:]) if ltm else "None"
                gen_str = "\n".join(f"• {f}" for f in gen_mem[-8:]) if gen_mem else "None"
                uprof_str = "\n".join(f"• **{u}**: {'; '.join(facts[-5:])}" for u, facts in uprof.items() if facts) if uprof else "None"
                mem_msg = (
                    f"🧠 **{client.user.display_name} Memory Snapshot**\n"
                    f"**Server Lore:**\n{ltm_str}\n"
                    f"**Shared General Facts:**\n{gen_str}\n"
                    f"**User Profiles:**\n{uprof_str}"
                )
                await message.channel.send(mem_msg[:1950])
                return
            elif "admin learnuser" in content_lower:
                raw_arg = re.split(r'admin\s+learnuser', message.content, flags=re.IGNORECASE)[1].strip()
                if "|" in raw_arg:
                    u_part, f_part = raw_arg.split("|", 1)
                    u_clean = u_part.strip().lstrip("@").strip()
                    f_clean = f_part.strip().rstrip(".")
                    if u_clean and f_clean:
                        def _manual_uprof(uprof_data):
                            canon = find_canonical_username(u_clean, uprof_data, {u_clean})
                            uprof_data.setdefault(canon, [])
                            if not is_duplicate_fact(f_clean, uprof_data[canon]):
                                uprof_data[canon].append(f_clean)
                            return uprof_data
                        update_json_file(user_profiles_file, {}, _manual_uprof)
                        await message.channel.send(f"📝 Saved fact for **{u_clean}**: `{f_clean}`")
                return
            elif "admin learngeneral" in content_lower:
                raw_fact = re.split(r'admin\s+learngeneral', message.content, flags=re.IGNORECASE)[1].strip().rstrip(".")
                if raw_fact:
                    def _manual_gen(gen_list):
                        if not isinstance(gen_list, list):
                            gen_list = []
                        if not is_duplicate_fact(raw_fact, gen_list):
                            gen_list.append(raw_fact)
                        return gen_list
                    update_json_file(general_memory_file, [], _manual_gen)
                    await message.channel.send(f"🌐 Saved shared general fact: `{raw_fact}`")
                return
            elif "admin learn" in content_lower:
                raw_fact = re.split(r'admin\s+learn', message.content, flags=re.IGNORECASE)[1].strip().rstrip(".")
                if raw_fact:
                    def _manual_ltm(ltm_list):
                        if not isinstance(ltm_list, list):
                            ltm_list = []
                        if not is_duplicate_fact(raw_fact, ltm_list):
                            ltm_list.append(raw_fact)
                        return ltm_list
                    update_json_file(long_term_memory_file, [], _manual_ltm)
                    await message.channel.send(f"📝 Saved server fact: `{raw_fact}`")
                return
            elif "admin forget" in content_lower:
                target_kw = message.content.lower().split("admin forget", 1)[1].strip()
                if target_kw:
                    removed_count = 0
                    def _forget_ltm(ltm_list):
                        nonlocal removed_count
                        kept = [f for f in ltm_list if target_kw not in f.lower()]
                        removed_count += (len(ltm_list) - len(kept))
                        return kept
                    update_json_file(long_term_memory_file, [], _forget_ltm)
                    update_json_file(general_memory_file, [], _forget_ltm)

                    def _forget_uprof(uprof_dict):
                        nonlocal removed_count
                        for u in list(uprof_dict.keys()):
                            if isinstance(uprof_dict[u], list):
                                before = len(uprof_dict[u])
                                uprof_dict[u] = [f for f in uprof_dict[u] if target_kw not in f.lower()]
                                removed_count += (before - len(uprof_dict[u]))
                        return uprof_dict
                    update_json_file(user_profiles_file, {}, _forget_uprof)
                    await message.channel.send(f"🗑️ Removed `{removed_count}` memory entries matching `{target_kw}`.")
                return
            elif "!clearmemory" in content_lower:
                def _clear(cm):
                    cm[channel_id] = []
                    return cm
                update_json_file(chat_memory_file, {}, _clear)
                channel_followup_state.pop(channel_id, None)
                await message.channel.send("🧹 **Short-term channel memory cleared.**")
                return

        # ======================================================================
        # CORE EVENT ROUTING
        # ======================================================================
        conf = load_json_file(channel_config_file, {}).get(channel_id, {})
        if not conf.get("enabled", True):
            if client.user.mentioned_in(message):
                try:
                    await message.channel.send(f"*(🔇 {client.user.display_name} is currently disabled in this channel.)*")
                except Exception:
                    pass
            return

        current_resp_chance = conf.get("resp_chance", chance_to_respond)
        current_emoji_chance = conf.get("emoji_chance", random_emoji_chance)
        current_temp = conf.get("temperature", temperature)
        ch_enable_vision = conf.get("enable_vision", enable_vision)
        ch_enable_gifs = conf.get("enable_gifs", enable_gifs)
        ch_enable_image_gen = conf.get("enable_image", enable_image_gen)
        ch_enable_voice = conf.get("enable_voice", enable_voice)
        is_react_only = conf.get("react_only", False)
        is_author_bot = message.author.bot

        bot_stagger = ((client.user.id >> 22) % 4) * 0.11
        if is_author_bot:
            await asyncio.sleep(0.45 + bot_stagger)
        elif bot_stagger > 0:
            await asyncio.sleep(bot_stagger)

        replied_to_self, reply_context_str, ref_message = await resolve_reply_context(
            message, client.user.id, shared_image_cache_file
        )

        incoming_glitch_summary = extract_glitch_summary(message.content or "") if is_author_bot else ""
        clean_content = ERROR_REPLY_STRIP_REGEX.sub('', message.content or '').strip()
        for user in message.mentions:
            clean_content = clean_content.replace(f"<@{user.id}>", f"@{user.display_name}")
            clean_content = clean_content.replace(f"<@!{user.id}>", f"@{user.display_name}")

        has_keyword = any(
            re.search(rf'\b{re.escape(kw.lower())}\b', clean_content.lower())
            for kw in (keywords or [])
        )
        directed_at_other_bot = is_command_directed_at_other_bot(
            clean_content, message, client.user.id, client.user.display_name, keywords, shared_image_cache_file
        )
        addressed_bot_name = detect_addressed_bot_name(
            clean_content, message, client.user.id, client.user.display_name, keywords, shared_image_cache_file
        )
        if not addressed_bot_name and ref_message and getattr(ref_message.author, "bot", False):
            addressed_bot_name = ref_message.author.display_name.strip()

        chat_memory = load_json_file(chat_memory_file, {})
        chat_history_list = chat_memory.get(channel_id, [])

        is_explicit_trigger = client.user.mentioned_in(message) or has_keyword or replied_to_self

        if is_explicit_trigger and not directed_at_other_bot:
            channel_followup_state[channel_id] = {
                "timestamp": time.time(),
                "turns_used": 0,
                "active": True,
                "bot_msg_id": ""
            }

        is_contextual_followup = False
        followup_context_str = ""
        if not is_explicit_trigger and not is_author_bot and not directed_at_other_bot:
            is_contextual_followup, followup_context_str = await resolve_contextual_followup(
                message=message,
                clean_content=clean_content,
                chat_history_list=chat_history_list,
                my_bot_id=client.user.id,
                my_display_name=client.user.display_name,
                my_keywords=keywords,
                shared_image_cache_file=shared_image_cache_file,
                channel_followup_state=channel_followup_state,
                followup_window_messages=followup_window_messages,
                max_followup_turns=max_followup_turns,
                followup_timeout=followup_timeout,
                api_key=api_key,
                text_model=text_model,
                api_url=api_url
            )
            if is_contextual_followup and not reply_context_str:
                reply_context_str = followup_context_str

        is_relevant_trigger = (is_explicit_trigger and not directed_at_other_bot) or is_contextual_followup

        bot_streak = 0
        if is_author_bot:
            for msg in reversed(chat_history_list):
                if msg.get("author_is_bot"):
                    bot_streak += 1
                else:
                    break

        bot_to_bot_cooldown = 300
        time_since_last_b2b = time.time() - conf.get("last_bot_to_bot", 0)

        has_media_candidate = bool(
            message.attachments
            or URL_REGEX.search(message.content or '')
            or message.embeds
            or getattr(message, "stickers", [])
        )
        spontaneous_mult = compute_spontaneous_interest_multiplier(
            clean_content=clean_content,
            has_media=has_media_candidate,
            is_author_bot=is_author_bot,
            persona_prompt=system_prompt.get("content", ""),
            chat_history_list=chat_history_list,
            my_bot_id=client.user.id
        )
        effective_resp_chance = min(0.85, current_resp_chance * spontaneous_mult)

        will_respond = False
        if is_react_only:
            if client.user.mentioned_in(message) and not directed_at_other_bot:
                try:
                    await message.channel.send(f"*(🤐 {client.user.display_name} is in react-only mode here.)*")
                except Exception:
                    pass
        elif is_author_bot and (bot_streak >= 2 or time_since_last_b2b < bot_to_bot_cooldown):
            pass
        elif is_relevant_trigger:
            will_respond = True
        elif not directed_at_other_bot and random.random() < effective_resp_chance:
            # Atomically claim the channel's 12-second unprompted chime-in cooldown slot
            # so multiple bots never dogpile the exact same unprompted message simultaneously
            claimed_slot = False

            def _try_claim_unprompted(c):
                nonlocal claimed_slot
                chimes = c.get("_last_unprompted_chime", {})
                if not isinstance(chimes, dict):
                    chimes = {}
                last_chime_ts = float(chimes.get(channel_id, chimes.get(str(channel_id), 0)) or 0)
                if (time.time() - last_chime_ts) >= 12.0:
                    chimes[channel_id] = time.time()
                    c["_last_unprompted_chime"] = chimes
                    claimed_slot = True
                return c

            update_json_file(shared_image_cache_file, {}, _try_claim_unprompted)
            will_respond = claimed_slot

        # Vision Prep
        image_desc = ""
        image_bytes = None

        if ch_enable_vision and has_media_candidate:
            msg_id = message.id
            img_cache = load_json_file(shared_image_cache_file, {})
            cached_val = img_cache.get(msg_id)

            if isinstance(cached_val, str) and cached_val.startswith("PROCESSING"):
                parts = cached_val.split("|")
                lock_time = float(parts[1]) if len(parts) > 1 else time.time()
                if (time.time() - lock_time) < 20:
                    for _ in range(16):
                        await asyncio.sleep(0.75)
                        img_cache = load_json_file(shared_image_cache_file, {})
                        cached_val = img_cache.get(msg_id)
                        if not (isinstance(cached_val, str) and cached_val.startswith("PROCESSING")):
                            break

            if isinstance(cached_val, str) and cached_val and not cached_val.startswith("PROCESSING"):
                image_desc = cached_val
                if will_respond:
                    image_bytes = await fetch_image_bytes_from_message(message)
            else:
                def _set_processing(c):
                    if msg_id not in c:
                        c[msg_id] = f"PROCESSING|{time.time()}"
                    return c
                update_json_file(shared_image_cache_file, {}, _set_processing)

                image_bytes = await fetch_image_bytes_from_message(message)
                if image_bytes:
                    image_desc = await summarize_image_for_memory(image_bytes, api_key, vision_model, api_url)

                def _save_vision_desc(c):
                    if image_desc:
                        c[msg_id] = image_desc
                    else:
                        c.pop(msg_id, None)
                    numeric_keys = sorted([k for k in c.keys() if isinstance(k, int)])
                    if len(numeric_keys) > 500:
                        for old_k in numeric_keys[:-500]:
                            c.pop(old_k, None)
                    return c
                update_json_file(shared_image_cache_file, {}, _save_vision_desc)

        if will_respond and ch_enable_vision and not image_bytes and ref_message:
            ref_has_media = bool(
                ref_message.attachments
                or URL_REGEX.search(ref_message.content or '')
                or ref_message.embeds
                or getattr(ref_message, "stickers", [])
            )
            if ref_has_media:
                image_bytes = await fetch_image_bytes_from_message(ref_message)

        # Split-Message Media Lookback: ONLY look back at the SAME author's immediately preceding media post
        # so another user's earlier meme/drawing isn't falsely treated as the current speaker's attachment!
        if will_respond and ch_enable_vision and not image_bytes:
            try:
                async for prev_m in message.channel.history(limit=3, before=message):
                    if (message.created_at - prev_m.created_at).total_seconds() > 60:
                        break
                    if prev_m.author.id != message.author.id:
                        break
                    prev_has_media = bool(
                        prev_m.attachments
                        or URL_REGEX.search(prev_m.content or '')
                        or prev_m.embeds
                        or getattr(prev_m, "stickers", [])
                    )
                    if prev_has_media:
                        img_cache_chk = load_json_file(shared_image_cache_file, {})
                        prev_desc = img_cache_chk.get(prev_m.id, "")
                        if isinstance(prev_desc, str) and prev_desc.startswith("PROCESSING"):
                            for _ in range(16):
                                await asyncio.sleep(0.6)
                                img_cache_chk = load_json_file(shared_image_cache_file, {})
                                prev_desc = img_cache_chk.get(prev_m.id, "")
                                if not (isinstance(prev_desc, str) and prev_desc.startswith("PROCESSING")):
                                    break

                        image_bytes = await fetch_image_bytes_from_message(prev_m)
                        if not prev_desc and image_bytes:
                            prev_desc = await summarize_image_for_memory(image_bytes, api_key, vision_model, api_url)
                            if prev_desc:
                                def _save_prev_desc(c, pid=prev_m.id, pd=prev_desc):
                                    c[pid] = pd
                                    return c
                                update_json_file(shared_image_cache_file, {}, _save_prev_desc)

                        if prev_desc:
                            prev_line = f"[{prev_m.author.display_name}]: {prev_m.content.strip()}" if prev_m.content.strip() else f"[{prev_m.author.display_name} uploaded media]"
                            upsert_chat_memory_entry(
                                chat_memory_file,
                                channel_id,
                                {
                                    "role": "user",
                                    "content": f"{prev_line}\n{prev_desc}".strip(),
                                    "message_id": prev_m.id,
                                    "author_id": prev_m.author.id,
                                    "author_is_bot": prev_m.author.bot,
                                    "author_name": prev_m.author.display_name
                                },
                                max_history_length
                            )
                        break
            except Exception:
                pass

        gif_slug = extract_gif_slug(clean_content) if ch_enable_gifs else ""
        yt_meta = await get_youtube_metadata(clean_content)

        shared_text_line = f"[{message.author.display_name}]: {clean_content.strip()}" if clean_content.strip() else f"[{message.author.display_name} uploaded media]"
        if reply_context_str:
            shared_text_line = f"{reply_context_str}\n{shared_text_line}"

        emoji_text_context = shared_text_line
        if image_desc:
            emoji_text_context += f"\n{image_desc}"
        if gif_slug:
            emoji_text_context += f"\n[Contains GIF of: {gif_slug}]"
        if yt_meta:
            emoji_text_context += f"\n{yt_meta}"

        guild_members = getattr(message.guild, "members", None) if message.guild else None
        other_bots_meta = get_other_active_bots(shared_image_cache_file, client.user.id, guild_members)
        other_bot_kw_set = set()
        other_bot_names_set = set()
        other_bot_display_list = []
        for ob in other_bots_meta:
            if ob.get("display_name"):
                other_bot_names_set.add(ob["display_name"].lower())
                other_bot_display_list.append(ob["display_name"])
            for kw_item in ob.get("keywords", []):
                if len(kw_item) >= 3:
                    other_bot_kw_set.add(kw_item.lower())
            for al_item in ob.get("aliases", []):
                other_bot_names_set.add(al_item.lower())
                first_tok = al_item.split()[0] if al_item.split() else ""
                if len(first_tok) >= 3 and first_tok not in ("bot", "guild", "the"):
                    other_bot_kw_set.add(first_tok)

        effective_emoji_chance = min(0.95, current_emoji_chance * (0.20 if is_author_bot else min(1.45, spontaneous_mult)))
        reacted_emoji = None
        react_task = None
        should_react = (is_explicit_trigger and not is_author_bot and current_emoji_chance > 0) or (random.random() < effective_emoji_chance)

        if should_react:
            react_task = asyncio.create_task(
                auto_react(
                    message,
                    api_key=api_key,
                    personality_prompt=system_prompt["content"],
                    text_model=text_model,
                    vision_model=vision_model,
                    image_bytes=image_bytes,
                    api_url=api_url,
                    chat_history=chat_history_list,
                    text=emoji_text_context,
                    emoji_context_length=emoji_context_length,
                    bot_name=client.user.display_name,
                    other_bot_keywords=other_bot_kw_set,
                    emoji_reaction_hint=emoji_reaction_hint
                )
            )

        text_context = shared_text_line
        if gif_slug:
            text_context += f"\n[Contains GIF of: {gif_slug}]"
        if yt_meta:
            text_context += f"\n{yt_meta}"
        if image_desc:
            text_context += f"\n{image_desc}"

        user_mem_entry = {
            "role": "user",
            "content": text_context,
            "message_id": message.id,
            "author_id": message.author.id,
            "author_is_bot": is_author_bot,
            "author_name": message.author.display_name
        }
        if addressed_bot_name:
            user_mem_entry["addressed_bot"] = addressed_bot_name

        upsert_chat_memory_entry(
            chat_memory_file,
            channel_id,
            user_mem_entry,
            max_history_length
        )

        if incoming_glitch_summary:
            upsert_chat_memory_entry(
                chat_memory_file,
                channel_id,
                {
                    "role": "system",
                    "content": (
                        f"[System Glitch Event: {message.author.display_name}'s media module glitched ({incoming_glitch_summary}) on that message. "
                        "Bots may tease or comment on it in-character, but NEVER output raw '*(API Error...)*' text.]"
                    ),
                    "message_id": f"glitch_media_{message.id}",
                    "author_id": message.author.id,
                    "author_name": message.author.display_name,
                    "is_glitch_event": True
                },
                max_history_length
            )

        # ======================================================================
        # AI GENERATION PIPELINE
        # ======================================================================
        if will_respond:
            ch_lock = channel_gen_locks.setdefault(channel_id, asyncio.Lock())
            if ch_lock.locked():
                print(f"[{client.user.display_name}] [INFO] Coalescing rapid message from {message.author.display_name} into active channel turn.")
                return

            async with ch_lock:
                await asyncio.sleep(0.85)

                if channel_last_replied_msg_id.get(channel_id, 0) >= message.id:
                    return

                if is_author_bot:
                    def _update_b2b(c):
                        c.setdefault(channel_id, {})["last_bot_to_bot"] = time.time()
                        return c
                    update_json_file(channel_config_file, {}, _update_b2b)

                trigger_mode_str = (
                    "contextual follow-up"
                    if is_contextual_followup
                    else ("spontaneous chime-in" if not is_explicit_trigger else "trigger")
                )
                print(f"[{client.user.display_name}] [INFO] Generating response ({trigger_mode_str}) to {message.author.display_name} in #{message.channel.name}")

                final_reply = ""
                final_gif = None
                sys_error = None
                is_error_reply = False
                image_file = None
                voice_file = None
                img_prompt = None
                meme_top_text = ""
                meme_bottom_text = ""
                voice_text = None
                gif_search_term = None
                img_err = None
                voice_err = None
                turn_glitch_tracker = []

                async with message.channel.typing():
                    try:
                        active_model = vision_model if (image_bytes and ch_enable_vision) else text_model
                        dynamic_system = copy.deepcopy(system_prompt)
                        persona_summary = dynamic_system.get("content", "")[:400]

                        now_str = datetime.now().strftime("%A, %I:%M %p")
                        dynamic_system["content"] += f"\n\n### Current Time Context:\n- Local Server Time: {now_str}"
                        dynamic_system["content"] += build_glitch_awareness_rules(client.user.display_name)
                        dynamic_system["content"] += build_memory_attribution_rules(client.user.display_name, other_bot_display_list)
                        dynamic_system["content"] += build_media_and_meme_rules(ch_enable_image_gen, ch_enable_gifs)

                        long_term_memory = load_json_file(long_term_memory_file, [])
                        if long_term_memory:
                            dynamic_system["content"] += "\n\n### Learned Server Knowledge (Server-Specific Lore):\n" + "\n".join(f"- {f}" for f in long_term_memory[-35:])

                        general_memory = load_json_file(general_memory_file, [])
                        if general_memory:
                            dynamic_system["content"] += "\n\n### Shared General Knowledge (Universal Trivia & Facts):\n" + "\n".join(f"- {f}" for f in general_memory[-35:])

                        chat_memory = load_json_file(chat_memory_file, {})
                        raw_history = copy.deepcopy(chat_memory.get(channel_id, [])[-context_window:])

                        coalesced_user_lines = []
                        for m in reversed(raw_history[-4:]):
                            if m.get("role") == "system":
                                continue
                            if m.get("author_is_bot") or m.get("role") == "assistant" or m.get("author_id") != message.author.id:
                                break
                            m_addr = m.get("addressed_bot")
                            if m_addr and m_addr.lower() != client.user.display_name.lower():
                                break
                            if m.get("role") == "user" and isinstance(m.get("content"), str):
                                # Strip injected metadata headers ([Replying to...], [Attached Media Description:...],
                                # [Contains GIF...], [Contains YouTube...]) so regex intent checks only inspect what
                                # the user actually typed, never quoted bot messages or vision descriptions!
                                user_typed_lines = []
                                for raw_ln in m["content"].split("\n"):
                                    ln_s = raw_ln.strip()
                                    if ln_s.startswith((
                                        "[Replying to ",
                                        "[Attached Media Description:",
                                        "[Contains GIF of:",
                                        "[Contains YouTube Video:"
                                    )):
                                        continue
                                    ln_s = re.sub(r'^\[[^\]]+\]:\s*', '', ln_s).strip()
                                    if ln_s and not ln_s.endswith("uploaded media]"):
                                        user_typed_lines.append(ln_s)
                                if user_typed_lines:
                                    coalesced_user_lines.append(" ".join(user_typed_lines))
                        coalesced_user_lines.reverse()
                        combined_recent_prompt = "\n".join(coalesced_user_lines) if coalesced_user_lines else clean_content

                        user_profiles = load_json_file(user_profiles_file, {})
                        recent_users = set(msg.get("author_name") for msg in raw_history if msg.get("author_name"))
                        for known_user in user_profiles.keys():
                            if str(known_user).lower() in combined_recent_prompt.lower() or str(known_user).lower() in reply_context_str.lower():
                                recent_users.add(str(known_user))

                        user_facts = [
                            f"- {u}: {'; '.join(user_profiles[u][-15:])}"
                            for u in recent_users
                            if u in user_profiles and user_profiles[u]
                        ]
                        if user_facts:
                            dynamic_system["content"] += "\n\n### Known Facts About Users in Context:\n" + "\n".join(user_facts)

                        img_cache = load_json_file(shared_image_cache_file, {})
                        gen_records = [
                            r for r in img_cache.get("_generated_images", [])
                            if isinstance(r, dict) and r.get("channel_id") == channel_id and (time.time() - r.get("timestamp", 0)) < 1800
                        ]
                        if gen_records:
                            recent_prompts_list = "\n".join(
                                f"- {r.get('bot_name')}: '{r.get('prompt')}'" for r in gen_records[-5:]
                            )
                            dynamic_system["content"] += (
                                "\n\n### Recently Generated Bot Images/Memes (DO NOT REPEAT OR COPY THESE VISUAL CONCEPTS OR CAPTIONS):\n"
                                f"{recent_prompts_list}\n"
                                "If you generate an image or meme, it MUST use a completely different scene, angle, setting, and brand-new captions."
                            )

                        api_messages = build_api_messages_from_history(
                            dynamic_system, raw_history, client.user.id, client.user.display_name
                        )

                        if is_contextual_followup:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    f"[System Context Note: {message.author.display_name} is following up directly on what you ({client.user.display_name}) just said or showed in chat. "
                                    "Reply naturally in-character to keep the conversation flowing.]"
                                )
                            })
                        elif not is_explicit_trigger:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    f"[System Context Note: Nobody directly pinged you—you are spontaneously chiming in on {message.author.display_name}'s message "
                                    "because something caught your attention. Jump in naturally like a real person in the group chat with a sharp, organic reaction or observation.]"
                                )
                            })

                        is_explicit_meme_req = bool(
                            ch_enable_image_gen
                            and not directed_at_other_bot
                            and EXPLICIT_MEME_REQ_REGEX.search(combined_recent_prompt)
                        )
                        is_explicit_img_req = bool(
                            ch_enable_image_gen
                            and not is_explicit_meme_req
                            and not directed_at_other_bot
                            and EXPLICIT_IMAGE_REQ_REGEX.search(combined_recent_prompt)
                        )
                        is_explicit_voice_req = bool(
                            ch_enable_voice
                            and not directed_at_other_bot
                            and EXPLICIT_VOICE_REQ_REGEX.search(combined_recent_prompt)
                        )
                        is_explicit_gif_req = bool(
                            ch_enable_gifs
                            and not is_explicit_img_req
                            and not is_explicit_meme_req
                            and not directed_at_other_bot
                            and EXPLICIT_GIF_REQ_REGEX.search(combined_recent_prompt)
                        )

                        if directed_at_other_bot:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    "System Directive: The user's media request is addressed to another bot while mentioning you as the subject. "
                                    "Respond with a short, witty in-character remark, and DO NOT output [IMAGE_GEN:], [MEME_GEN:], or [VOICE_MSG:] so the requested bot can handle the generation."
                                )
                            })
                        elif is_explicit_meme_req:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    "System Directive: The user is asking you to generate a meme! "
                                    "You MUST append `[MEME_GEN: vivid visual description of the scene without text | TOP CAPTION | BOTTOM PUNCHLINE]` at the end of your response. "
                                    "Both captions MUST be original and tied to the topic (never use 'ABSOLUTELY UNACCEPTABLE'). Do NOT output `[GIF:]` or `[IMAGE_GEN:]` in the same message."
                                )
                            })
                        elif is_explicit_img_req:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    "System Directive: The user is explicitly asking you to generate an image. "
                                    "You MUST append a creative, faithful `[IMAGE_GEN: detailed visual description]` tag (WITHOUT meme text captions) at the end of your response that includes every subject, item, and detail the user requested. "
                                    "Do NOT output `[GIF:]` or `[MEME_GEN:]` in the same message."
                                )
                            })
                        elif is_explicit_voice_req:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    "System Directive: The user is explicitly asking you for a voice clip. "
                                    "Include normal chat text AND a complete `[VOICE_MSG: ...]` block at the end of your response with Role, Scene, Guidance, and spoken dialogue."
                                )
                            })
                        elif is_explicit_gif_req:
                            api_messages.append({
                                "role": "system",
                                "content": (
                                    "System Directive: The user is asking for a GIF. "
                                    "Append `[GIF: concise 2-4 word search query]` at the end of your response."
                                )
                            })

                        text_only_messages = copy.deepcopy(api_messages)

                        if image_bytes and ch_enable_vision:
                            b64_image = base64.b64encode(image_bytes).decode("utf-8")
                            for i in range(len(api_messages) - 1, -1, -1):
                                if api_messages[i].get("role") == "user":
                                    api_messages[i]["content"] = [
                                        {
                                            "type": "text",
                                            "text": (
                                                f"{api_messages[i]['content']}\n"
                                                "[System Visual Note: Focus directly on the attached image/animation above! If it shows a 2x2 storyboard with small 1-2-3-4 corner badges, it is a chronological sequence of keyframes from a single video or animated GIF. Treat it as one continuous animation/video clip, not a 4-panel comic.]"
                                            )
                                        },
                                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                                    ]
                                    break

                        payload = {
                            "model": active_model,
                            "messages": api_messages,
                            "temperature": current_temp,
                            "top_p": top_p,
                            "frequency_penalty": 0.42,
                            "presence_penalty": 0.30,
                            "max_tokens": output_length
                        }

                        res = await post_with_backoff(
                            api_url,
                            headers={"Authorization": f"Bearer {api_key}"},
                            json_payload=payload,
                            timeout=55,
                            max_retries=3,
                            bot_name=client.user.display_name,
                            glitch_tracker=turn_glitch_tracker,
                            inject_glitch_on_retry=True
                        )

                        reply = ""
                        if res is not None and res.status_code == 200:
                            raw_reply = res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                            reply = ERROR_REPLY_STRIP_REGEX.sub('', raw_reply or "").strip()

                        should_try_fallback = (
                            not reply
                            and (res is None or res.status_code in (200, 429, 500, 502, 503, 504))
                        )

                        if should_try_fallback and image_bytes and ch_enable_vision:
                            for fb_v_model in FALLBACK_VISION_MODELS:
                                if fb_v_model == active_model:
                                    continue
                                print(f"[{client.user.display_name}] [VISION FALLBACK] Trying '{fb_v_model}'...")
                                fb_v_payload = copy.deepcopy(payload)
                                fb_v_payload["model"] = fb_v_model
                                glitch_reason = "; ".join(turn_glitch_tracker[-2:]) if turn_glitch_tracker else "primary model timeout"
                                fb_v_payload["messages"].append({
                                    "role": "system",
                                    "content": (
                                        f"[System Glitch Context: Your primary vision model ({active_model}) just glitched ({glitch_reason}), "
                                        f"so you recovered on backup model ({fb_v_model}). You may briefly joke or grumble in-character about "
                                        "your lag spike or brain-freeze while answering, but NEVER output raw '*(API Error...)*' text.]"
                                    )
                                })
                                fb_v_res = await post_with_backoff(
                                    api_url,
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    json_payload=fb_v_payload,
                                    timeout=35,
                                    max_retries=1,
                                    bot_name=client.user.display_name,
                                    glitch_tracker=turn_glitch_tracker
                                )
                                if fb_v_res is not None and fb_v_res.status_code == 200:
                                    raw_reply = fb_v_res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                                    reply = ERROR_REPLY_STRIP_REGEX.sub('', raw_reply or "").strip()
                                    if reply:
                                        res = fb_v_res
                                        break

                        if not reply and should_try_fallback:
                            for fb_model in FALLBACK_TEXT_MODELS:
                                if fb_model == active_model:
                                    continue
                                print(f"[{client.user.display_name}] [TEXT FALLBACK] Trying '{fb_model}'...")
                                fb_payload = copy.deepcopy(payload)
                                fb_payload["model"] = fb_model
                                fb_payload["messages"] = copy.deepcopy(text_only_messages)
                                glitch_reason = "; ".join(turn_glitch_tracker[-2:]) if turn_glitch_tracker else "primary model timeout"
                                fb_payload["messages"].append({
                                    "role": "system",
                                    "content": (
                                        f"[System Glitch Context: Your primary neural model ({active_model}) just glitched ({glitch_reason}), "
                                        f"so you recovered on backup model ({fb_model}). You may briefly joke or grumble in-character about "
                                        "your lag spike or brain-freeze while answering, but NEVER output raw '*(API Error...)*' text.]"
                                    )
                                })
                                fb_res = await post_with_backoff(
                                    api_url,
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    json_payload=fb_payload,
                                    timeout=35,
                                    max_retries=1,
                                    bot_name=client.user.display_name,
                                    glitch_tracker=turn_glitch_tracker
                                )
                                if fb_res is not None and fb_res.status_code == 200:
                                    raw_reply = fb_res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                                    reply = ERROR_REPLY_STRIP_REGEX.sub('', raw_reply or "").strip()
                                    if reply:
                                        res = fb_res
                                        break

                        if not reply:
                            is_error_reply = True
                            if res is not None and res.status_code != 200:
                                final_reply = f"*(API Error {res.status_code} - Provider busy. Try again in a moment.)*"
                            else:
                                final_reply = "*(System: DeepInfra dropped the text request after multiple retries.)*"
                        else:
                            raw_voice_content, reply = extract_and_strip_voice_msg(reply)
                            if directed_at_other_bot:
                                raw_voice_content = None

                            learned_notifications = []
                            learn_user_matches = LEARN_USER_EXTRACT_REGEX.findall(reply)
                            if learn_user_matches and not is_author_bot:
                                verified_user_facts = await verify_and_resolve_user_facts(
                                    candidate_facts=learn_user_matches,
                                    speaker_name=message.author.display_name,
                                    recent_user_prompt=combined_recent_prompt,
                                    raw_history=raw_history,
                                    user_profiles=user_profiles,
                                    recent_users=recent_users,
                                    other_bot_names=other_bot_names_set,
                                    my_bot_name=client.user.display_name,
                                    api_key=api_key,
                                    text_model=text_model,
                                    api_url=api_url
                                )
                                if verified_user_facts:
                                    def _save_user_facts(uprof_data):
                                        for canonical_uname, clean_fact in verified_user_facts:
                                            if canonical_uname and clean_fact and canonical_uname.lower() != client.user.display_name.lower():
                                                if canonical_uname not in uprof_data or not isinstance(uprof_data[canonical_uname], list):
                                                    uprof_data[canonical_uname] = []
                                                if not is_duplicate_fact(clean_fact, uprof_data[canonical_uname]):
                                                    uprof_data[canonical_uname].append(clean_fact)
                                                    if len(uprof_data[canonical_uname]) > 30:
                                                        uprof_data[canonical_uname] = uprof_data[canonical_uname][-30:]
                                                    learned_notifications.append(f"learned about {canonical_uname}")
                                        return uprof_data
                                    update_json_file(user_profiles_file, {}, _save_user_facts)

                            raw_gen_matches = LEARN_GENERAL_EXTRACT_REGEX.findall(reply)
                            raw_srv_matches = LEARN_EXTRACT_REGEX.findall(reply)
                            routed_srv_facts, routed_gen_facts = route_server_vs_general_facts(
                                raw_server_facts=raw_srv_matches,
                                raw_general_facts=raw_gen_matches,
                                recent_users=recent_users | set(user_profiles.keys()),
                                other_bot_names=other_bot_names_set,
                                my_bot_name=client.user.display_name
                            )

                            if routed_gen_facts:
                                def _save_general_facts(gen_data):
                                    if not isinstance(gen_data, list):
                                        gen_data = []
                                    for clean_gfact in routed_gen_facts:
                                        if clean_gfact and not is_duplicate_fact(clean_gfact, gen_data):
                                            gen_data.append(clean_gfact)
                                            if len(gen_data) > 80:
                                                gen_data = gen_data[-80:]
                                            learned_notifications.append(f"learned general fact: {clean_gfact}")
                                    return gen_data
                                update_json_file(general_memory_file, [], _save_general_facts)

                            if routed_srv_facts:
                                def _save_ltm_facts(ltm_data):
                                    if not isinstance(ltm_data, list):
                                        ltm_data = []
                                    for clean_fact in routed_srv_facts:
                                        if clean_fact and not is_duplicate_fact(clean_fact, ltm_data):
                                            ltm_data.append(clean_fact)
                                            if len(ltm_data) > 60:
                                                ltm_data = ltm_data[-60:]
                                            learned_notifications.append(f"learned server fact: {clean_fact}")
                                    return ltm_data
                                update_json_file(long_term_memory_file, [], _save_ltm_facts)

                            if ch_enable_image_gen and not directed_at_other_bot:
                                meme_gen_match = MEME_GEN_REGEX.search(reply) or UNCLOSED_MEME_GEN_REGEX.search(reply)
                                if meme_gen_match:
                                    if is_explicit_img_req:
                                        # User asked for a clean image, not a captioned meme
                                        img_prompt, _, _ = parse_meme_gen_tag(meme_gen_match.group(1))
                                        meme_top_text = ""
                                        meme_bottom_text = ""
                                    else:
                                        img_prompt, meme_top_text, meme_bottom_text = parse_meme_gen_tag(meme_gen_match.group(1))
                                else:
                                    img_gen_match = (
                                        IMAGE_GEN_REGEX.search(reply)
                                        or UNCLOSED_IMAGE_GEN_REGEX.search(reply)
                                        or FALLBACK_IMAGE_NOTE_REGEX.search(reply)
                                    )
                                    if img_gen_match:
                                        raw_img_tag = img_gen_match.group(1).strip()
                                        if "|" in raw_img_tag and not is_explicit_img_req:
                                            img_prompt, meme_top_text, meme_bottom_text = parse_meme_gen_tag(raw_img_tag)
                                        else:
                                            img_prompt, _, _ = parse_meme_gen_tag(raw_img_tag)

                                other_bot_just_generated_on_msg = any(
                                    r.get("trigger_msg_id") == message.id and r.get("bot_id") != client.user.id
                                    for r in gen_records
                                )
                                if img_prompt and other_bot_just_generated_on_msg and not (is_explicit_img_req or is_explicit_meme_req):
                                    print(f"[{client.user.display_name}] [INFO] Skipping redundant unsolicited image/meme because another bot already generated one for this message.")
                                    img_prompt = None
                                    meme_top_text = ""
                                    meme_bottom_text = ""

                                if not img_prompt and is_explicit_meme_req:
                                    print(f"[{client.user.display_name}] [INFO] Synthesizing missing [MEME_GEN:] spec for explicit meme request...")
                                    img_prompt, meme_top_text, meme_bottom_text = await synthesize_missing_meme_spec(
                                        reply, combined_recent_prompt, gen_records, client.user.display_name, persona_summary, api_key, text_model, api_url
                                    )
                                elif not img_prompt and (is_explicit_img_req or (is_relevant_trigger and BOT_IMAGE_PROMISE_REGEX.search(reply))):
                                    print(f"[{client.user.display_name}] [INFO] Synthesizing missing [IMAGE_GEN:] prompt for explicit image request...")
                                    img_prompt = await synthesize_missing_image_prompt(
                                        reply, combined_recent_prompt, gen_records, client.user.display_name, persona_summary, api_key, text_model, api_url
                                    )

                                if img_prompt:
                                    img_prompt = await ensure_unique_image_prompt(
                                        img_prompt, gen_records, raw_history, client.user.display_name, persona_summary, combined_recent_prompt, api_key, text_model, api_url, reply_text=reply
                                    )
                                    if meme_top_text or meme_bottom_text:
                                        meme_top_text, meme_bottom_text = await ensure_unique_meme_captions(
                                            img_prompt, meme_top_text, meme_bottom_text, reply, combined_recent_prompt,
                                            gen_records, client.user.display_name, persona_summary, api_key, text_model, api_url
                                        )

                            reply = LEARN_USER_STRIP_REGEX.sub('', reply).strip()
                            reply = LEARN_GENERAL_STRIP_REGEX.sub('', reply).strip()
                            reply = LEARN_STRIP_REGEX.sub('', reply).strip()
                            reply = INTERNAL_REACT_STRIP_REGEX.sub('', reply).strip()
                            reply = EMOJI_REACT_STRIP_REGEX.sub('', reply).strip()
                            reply = IMAGE_DESC_STRIP_REGEX.sub('', reply).strip()
                            reply = MEME_GEN_REGEX.sub('', reply).strip()
                            reply = UNCLOSED_MEME_GEN_REGEX.sub('', reply).strip()
                            reply = IMAGE_GEN_REGEX.sub('', reply).strip()
                            reply = UNCLOSED_IMAGE_GEN_REGEX.sub('', reply).strip()
                            reply = FALLBACK_IMAGE_NOTE_REGEX.sub('', reply).strip()

                            if ch_enable_gifs and not img_prompt and not directed_at_other_bot:
                                gif_match = GIF_EXTRACT_REGEX.search(reply) or UNCLOSED_GIF_EXTRACT_REGEX.search(reply)
                                if gif_match:
                                    candidate_gif_term = gif_match.group(1).strip()
                                    now_ts = time.time()
                                    gif_cooldown_ok = (now_ts - channel_last_gif_time.get(channel_id, 0)) >= 120.0
                                    if is_explicit_gif_req or (gif_cooldown_ok and random.random() < 0.35):
                                        gif_search_term = candidate_gif_term
                                        channel_last_gif_time[channel_id] = now_ts
                                    else:
                                        print(f"[{client.user.display_name}] [INFO] Suppressed unsolicited GIF ('{candidate_gif_term}') to keep GIF usage low.")
                            reply = GIF_STRIP_REGEX.sub('', reply).strip()
                            reply = clean_and_compact_reply(reply)

                            if not reply and raw_voice_content:
                                reply = derive_chat_text_from_voice(raw_voice_content)

                            if ch_enable_voice and not raw_voice_content and is_explicit_voice_req and reply:
                                raw_voice_content = reply

                            img_task = (
                                generate_image_attachment(
                                    img_prompt, image_gen_model, api_key, client.user.id,
                                    client.user.display_name, channel_id, message.id, shared_image_cache_file,
                                    meme_top_text=meme_top_text, meme_bottom_text=meme_bottom_text
                                )
                                if (ch_enable_image_gen and img_prompt)
                                else asyncio.sleep(0, result=(None, None))
                            )
                            voice_task = (
                                generate_voice_attachment(
                                    raw_voice_content, voice_model, tts_voice, api_key,
                                    client.user.display_name, extracted_voice_role, extracted_voice_style
                                )
                                if (ch_enable_voice and raw_voice_content)
                                else asyncio.sleep(0, result=(None, None, None))
                            )
                            gif_task = (
                                fetch_online_gif(gif_search_term, giphy_key)
                                if (ch_enable_gifs and gif_search_term and not img_prompt)
                                else asyncio.sleep(0, result="")
                            )

                            (image_file, img_err), (voice_file, voice_text, voice_err), fetched_gif = await asyncio.gather(
                                img_task, voice_task, gif_task
                            )

                            if fetched_gif and not image_file:
                                final_gif = fetched_gif
                            if img_err:
                                reply = f"{reply}\n{img_err}".strip()
                            if voice_err:
                                reply = f"{reply}\n{voice_err}".strip()

                            if learned_notifications:
                                notes_str = " | ".join(f"{client.user.display_name} {n}" for n in learned_notifications)
                                reply = f"{reply}\n*({notes_str})*".strip()

                            if not reply and not final_gif and not image_file and not voice_file:
                                is_error_reply = True
                                reply = "*(Brain empty. The API returned a blank response.)*"

                            final_reply = reply

                    except Exception as err:
                        import traceback
                        print(f"[{client.user.display_name}] [ERROR] Response error: {err}")
                        traceback.print_exc()
                        if channel_id in channel_followup_state:
                            channel_followup_state[channel_id]["active"] = False
                        sys_error = "*(System: I encountered a network timeout or connection error. Check the console.)*"

                if sys_error:
                    sent_err_msg = None
                    try:
                        sent_err_msg = await message.channel.send(sys_error)
                    except Exception:
                        pass
                    err_sum = extract_glitch_summary(sys_error) or "network timeout or connection error"
                    upsert_chat_memory_entry(
                        chat_memory_file,
                        channel_id,
                        {
                            "role": "system",
                            "content": (
                                f"[System Glitch Event: {client.user.display_name} experienced a glitch ({err_sum}) while trying to reply to {message.author.display_name}. "
                                f"Next time {client.user.display_name} or another bot speaks, they can joke or react in-character to this glitch, without repeating raw '*(System:...)*' text.]"
                            ),
                            "message_id": sent_err_msg.id if sent_err_msg else f"sys_glitch_{message.id}_{client.user.id}",
                            "author_id": client.user.id,
                            "author_name": client.user.display_name,
                            "is_glitch_event": True
                        },
                        max_history_length
                    )
                else:
                    files_to_send = []
                    is_meme_output = bool(meme_top_text.strip() or meme_bottom_text.strip())
                    if image_file:
                        fname = "generated_meme.png" if is_meme_output else "generated_image.png"
                        files_to_send.append(discord.File(fp=image_file, filename=fname))
                    if voice_file:
                        files_to_send.append(discord.File(fp=voice_file, filename="voice_message.mp3"))

                    sent_msg = None
                    try:
                        if final_gif:
                            sent_msg = await message.channel.send(
                                content=f"{final_reply}\n{final_gif}" if final_reply else final_gif,
                                files=files_to_send
                            )
                        else:
                            chunks = [final_reply[i:i + 1900] for i in range(0, len(final_reply), 1900)] if final_reply else []
                            if chunks:
                                for i, chunk in enumerate(chunks):
                                    if i == len(chunks) - 1:
                                        sent_msg = await message.channel.send(chunk.strip(), files=files_to_send)
                                    elif chunk.strip():
                                        sent_msg = await message.channel.send(chunk.strip())
                            elif files_to_send:
                                sent_msg = await message.channel.send(files=files_to_send)
                    except discord.errors.HTTPException as e:
                        print(f"[{client.user.display_name}] [ERROR] Discord upload failed: {e}")
                        sent_msg = await message.channel.send(
                            f"{final_reply}\n*(System: Failed to upload media file. It may be corrupted or too large.)*"
                        )

                    channel_last_replied_msg_id[channel_id] = max(
                        channel_last_replied_msg_id.get(channel_id, 0),
                        message.id
                    )

                    if sent_msg and img_prompt and image_file:
                        caption_desc = f" with meme captions '{meme_top_text} / {meme_bottom_text}'" if is_meme_output else ""
                        def _precache_bot_img(c):
                            c[sent_msg.id] = f"[Attached Media Description: Generated {'meme' if is_meme_output else 'image'} by {client.user.display_name} showing: {img_prompt}{caption_desc}]"
                            return c
                        update_json_file(shared_image_cache_file, {}, _precache_bot_img)

                    if not is_error_reply:
                        prev_turns_used = (
                            channel_followup_state.get(channel_id, {}).get("turns_used", 0)
                            if (is_contextual_followup and not is_explicit_trigger)
                            else 0
                        )
                        new_turns_used = (prev_turns_used + 1) if (is_contextual_followup and not is_explicit_trigger) else 0
                        entry_msg_id = sent_msg.id if sent_msg else f"resp_{message.id}_{client.user.id}"

                        if new_turns_used >= max_followup_turns:
                            if channel_id in channel_followup_state:
                                channel_followup_state[channel_id]["active"] = False
                        else:
                            channel_followup_state[channel_id] = {
                                "timestamp": time.time(),
                                "turns_used": new_turns_used,
                                "active": True,
                                "bot_msg_id": entry_msg_id
                            }

                        clean_mem_reply = ERROR_REPLY_STRIP_REGEX.sub('', final_reply or '').strip()
                        clean_mem_reply = re.sub(r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*', '', clean_mem_reply, flags=re.IGNORECASE).strip()

                        memory_text = clean_mem_reply if clean_mem_reply else "[Sent media]"
                        if img_prompt and image_file:
                            caption_desc = f" with meme captions '{meme_top_text} / {meme_bottom_text}'" if is_meme_output else ""
                            memory_text = f"{memory_text}\n[Attached Media Description: Generated {'meme' if is_meme_output else 'image'} by {client.user.display_name} showing: {img_prompt}{caption_desc}]".strip()

                        memory_entry = {
                            "role": "assistant",
                            "content": memory_text,
                            "author_id": client.user.id,
                            "author_name": client.user.display_name,
                            "message_id": entry_msg_id,
                            "author_is_bot": True
                        }
                        if img_prompt and image_file:
                            if is_meme_output:
                                memory_entry["meme_spec"] = f"{img_prompt} | {meme_top_text} | {meme_bottom_text}"
                            else:
                                memory_entry["img_prompt"] = img_prompt
                        if gif_search_term and final_gif:
                            memory_entry["gif_query"] = gif_search_term
                        if voice_text and voice_file:
                            memory_entry["voice_text"] = voice_text

                        upsert_chat_memory_entry(chat_memory_file, channel_id, memory_entry, max_history_length)

                        media_glitches = [
                            extract_glitch_summary(e) for e in (img_err, voice_err) if e
                        ]
                        media_glitches = [g for g in media_glitches if g]
                        if media_glitches:
                            upsert_chat_memory_entry(
                                chat_memory_file,
                                channel_id,
                                {
                                    "role": "system",
                                    "content": (
                                        f"[System Glitch Event: {client.user.display_name}'s media generator glitched ({'; '.join(media_glitches)}) "
                                        f"on the reply to {message.author.display_name}. {client.user.display_name} or other bots can react to this in-character if asked.]"
                                    ),
                                    "message_id": f"media_glitch_{entry_msg_id}",
                                    "author_id": client.user.id,
                                    "author_name": client.user.display_name,
                                    "is_glitch_event": True
                                },
                                max_history_length
                            )
                    else:
                        err_summary = extract_glitch_summary(final_reply) or "API timeout / empty response"
                        upsert_chat_memory_entry(
                            chat_memory_file,
                            channel_id,
                            {
                                "role": "system",
                                "content": (
                                    f"[System Glitch Event: {client.user.display_name} glitched out ({err_summary}) while trying to reply to {message.author.display_name}. "
                                    f"Next time {client.user.display_name} or other bots speak, they may joke, grumble, or roast this malfunction in-character, "
                                    "but NEVER output raw '*(API Error...)*' strings.]"
                                ),
                                "message_id": sent_msg.id if sent_msg else f"err_glitch_{message.id}_{client.user.id}",
                                "author_id": client.user.id,
                                "author_name": client.user.display_name,
                                "is_glitch_event": True
                            },
                            max_history_length
                        )

                    if react_task:
                        try:
                            reacted_emoji = await react_task
                        except Exception:
                            pass

                    if reacted_emoji:
                        upsert_chat_memory_entry(
                            chat_memory_file,
                            channel_id,
                            {
                                "role": "system",
                                "content": f"System Note: {client.user.display_name} silently reacted to {message.author.display_name}'s message with {reacted_emoji}. Do not announce this in your text.",
                                "message_id": f"react_{message.id}_{client.user.id}"
                            },
                            max_history_length
                        )

    client.run(token)
