import re
import time
import discord

from config import ERROR_REPLY_STRIP_REGEX, IMAGE_DESC_STRIP_REGEX, URL_REGEX
from core_system import load_json_file, update_json_file, post_with_backoff
from memory_and_context import normalize_tokens

# ==============================================================================
# UNIVERSAL BOT DISCOVERY & DYNAMIC PERSONA EXTRACTION
# ==============================================================================

def build_universal_media_instructions(
    enable_image_gen: bool = True,
    enable_voice: bool = True,
    enable_gifs: bool = True,
    image_style_hint: str = "",
    voice_role_hint: str = "",
    voice_guidance_hint: str = "",
    allowed_voice_styles: str = "(Happy), (Sad), (Angry), (Calm), (Serious), (Playful), (Deep), (Lazy), (Excited)"
) -> str:
    """
    Reusable helper that any bot script can call to generate rich, conflict-free
    media generation instructions tailored to its persona.
    """
    rules = [
        "### CRITICAL MEDIA & TAG RULES",
        "- **STRICT MUTUAL EXCLUSION (NO IMAGE + GIF COMBOS)**: Attach AT MOST ONE visual media tag per message. NEVER output both `[IMAGE_GEN: ...]` and `[GIF: ...]` in the same response.",
        "- **NO PHANTOM MEDIA**: Never claim you generated, rendered, drew, or attached an image, GIF, or voice clip unless you literally include the exact `[IMAGE_GEN: ...]`, `[GIF: ...]`, or `[VOICE_MSG: ...]` tag in your response.",
        "- **EMOTES IN ASTERISKS ONLY (NEVER BRACKETS IN CHAT)**: Never use square brackets like `[chuckle]`, `[sigh]`, `[giggle]`, or `[shrug]` in your regular chat text. Square brackets in normal text are strictly reserved for system media tags (`[IMAGE_GEN:]`, `[GIF:]`, `[VOICE_MSG:]`). If you include a physical action or emote in normal chat, wrap it in single asterisks like `*sighs*` or `*grins*`."
    ]

    style_clause = f" Tailor the aesthetic to your persona ({image_style_hint})." if image_style_hint else ""
    if enable_image_gen:
        rules.append(
            "- **Autonomous & Requested Images (`[IMAGE_GEN: ...]`)**:\n"
            "  1. When a user asks you for an image, OR when showing what you are doing, holding, looking at, or roasting adds strong visual punch, append `[IMAGE_GEN: detailed visual prompt]`.\n"
            "  2. **ORIGINALITY & ANTI-CLONING**: NEVER copy or reuse an existing `[Attached Media Description:]` or another bot's recent image concept. Always invent a completely fresh visual angle, setting, and composition from your own persona's perspective.\n"
            f"  3. **HIGH-QUALITY VISUAL PROMPTS**: Write vivid, concrete prompts (25–50 words) specifying subject, action, setting, camera angle, lighting, and art medium.{style_clause} Avoid asking the image model to render long sentences of written text on signs."
        )
    else:
        rules.append("- **Image Generation**: DISABLED. Do NOT generate images or use `[IMAGE_GEN:]` tags.")

    if enable_gifs:
        rules.append(
            "- **Reaction GIFs (`[GIF: ...]`)**:\n"
            "  1. When a short reaction meme or clip fits better than a custom generated image—and you are NOT using `[IMAGE_GEN:]`—append `[GIF: concise 2-4 word meme search]`.\n"
            "  2. Never combine `[GIF:]` with `[IMAGE_GEN:]`."
        )
    else:
        rules.append("- **GIFs**: DISABLED. Do NOT use `[GIF:]` tags.")

    role_line = voice_role_hint if voice_role_hint else "[Briefly describe your persona's voice timbre, age, gender, and vibe]"
    guidance_line = voice_guidance_hint if voice_guidance_hint else "[1-2 sentences specifying speaking speed, vocal texture, and pauses—do not use audio engineering terms like reverb or EQ]"

    if enable_voice:
        rules.append(
            "- **Autonomous & Requested Voice (`[VOICE_MSG: ...]`)**:\n"
            "  1. Use voice messages when explicitly asked to speak/send a voice clip, or rarely on your own for dramatic, emotional, or comedic effect.\n"
            "  2. CRITICAL TTS FORMAT: You are prompting the MiMo-V2.5-TTS-VoiceDesign engine using Director Mode and Audio Tags. Always write your normal chat text first, then place your `[VOICE_MSG: ...]` block at the very end formatted like this:\n"
            "`[VOICE_MSG:`\n"
            f"`Role: {role_line}`\n"
            "`Scene: [1 concise sentence describing the current situation, who you are talking to, and your emotional state]`\n"
            f"`Guidance: {guidance_line}`\n\n"
            "`(Overall Style Tag) Spoken dialogue goes here [audio tag] with natural spoken cadence.`\n"
            "`]`\n"
            f"  3. *Allowed Overall Style Tags:* {allowed_voice_styles}.\n"
            "  4. *Allowed Inline Audio Tags (ONLY inside `[VOICE_MSG: ...]`, NEVER in normal chat):* `[sigh]`, `[let out a long sigh]`, `[chuckle]`, `[laugh out loud]`, `[sneer]`, `[cough]`, `[take a deep breath]`, `[pauses for a moment]`, `[voice trembling]`, `[whimper]`, `[pant]`.\n"
            "  5. Do NOT put unpronounceable ASCII kaomoji (like `>w<` or `:3`) inside the spoken `[VOICE_MSG: ...]` dialogue. Keep spoken dialogue between 10 and 70 words."
        )
    else:
        rules.append(
            "- **Voice Messaging**: STRICTLY DISABLED. Never use `[VOICE_MSG:]`. If asked to speak, decline in character."
        )

    rules.append(
        "- **Tag Placement**: Always write normal conversational chat text first, and place any `[IMAGE_GEN: ...]`, `[GIF: ...]`, or `[VOICE_MSG: ...]` tag at the very end."
    )
    return "\n".join(rules)

def register_active_bot(
    shared_image_cache_file: str,
    bot_id: int,
    display_name: str,
    keywords: list[str] | None
):
    clean_name = display_name.strip()
    base_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', clean_name, flags=re.IGNORECASE).strip()
    aliases = {clean_name.lower(), base_name.lower()}
    for kw in (keywords or []):
        if kw and kw.strip():
            aliases.add(kw.strip().lower())
            aliases.add(f"{kw.strip().lower()} bot")

    first_word = base_name.split()[0].lower() if base_name.split() else ""
    if len(first_word) >= 3 and first_word not in ("the", "bot", "discord"):
        aliases.add(first_word)
        aliases.add(f"{first_word} bot")

    def _mutate(cache_data):
        registry = cache_data.get("_active_bots", {})
        if not isinstance(registry, dict):
            registry = {}
        registry[str(bot_id)] = {
            "bot_id": bot_id,
            "display_name": clean_name,
            "aliases": sorted(list(aliases)),
            "keywords": [k.lower() for k in (keywords or []) if k],
            "last_seen": time.time()
        }
        cache_data["_active_bots"] = registry
        return cache_data

    update_json_file(shared_image_cache_file, {}, _mutate)


def get_other_active_bots(shared_image_cache_file: str, my_bot_id: int, guild_members: list = None) -> list[dict]:
    cache_data = load_json_file(shared_image_cache_file, {})
    registry = cache_data.get("_active_bots", {})
    other_bots = []
    seen_ids = {my_bot_id}

    if isinstance(registry, dict):
        for b_id_str, info in registry.items():
            if not isinstance(info, dict):
                continue
            b_id = info.get("bot_id")
            if b_id and b_id != my_bot_id:
                seen_ids.add(b_id)
                other_bots.append(info)

    if guild_members:
        for member in guild_members:
            if getattr(member, "bot", False) and member.id not in seen_ids:
                seen_ids.add(member.id)
                c_name = member.display_name.strip()
                b_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', c_name, flags=re.IGNORECASE).strip()
                other_bots.append({
                    "bot_id": member.id,
                    "display_name": c_name,
                    "aliases": list({c_name.lower(), b_name.lower()}),
                    "keywords": []
                })

    return other_bots


def is_command_directed_at_other_bot(
    text: str,
    message: discord.Message,
    my_bot_id: int,
    my_display_name: str,
    my_keywords: list[str] | None,
    shared_image_cache_file: str
) -> bool:
    clean_lower = text.strip().lower()
    if not clean_lower:
        return False

    base_my_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', my_display_name.strip(), flags=re.IGNORECASE).strip().lower()
    my_terms = {my_display_name.strip().lower(), base_my_name}
    for kw in (my_keywords or []):
        if kw and kw.strip():
            my_terms.add(kw.strip().lower())
            my_terms.add(f"{kw.strip().lower()} bot")

    guild_members = getattr(message.guild, "members", None) if message.guild else None
    other_bots = get_other_active_bots(shared_image_cache_file, my_bot_id, guild_members)

    other_bot_terms = set()
    for ob in other_bots:
        for alias in ob.get("aliases", []):
            if alias and alias not in my_terms:
                other_bot_terms.add(alias)

    for mentioned_user in message.mentions:
        if getattr(mentioned_user, "bot", False) and mentioned_user.id != my_bot_id:
            m_name = mentioned_user.display_name.strip().lower()
            m_base = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', m_name, flags=re.IGNORECASE).strip()
            other_bot_terms.add(m_name)
            if m_base:
                other_bot_terms.add(m_base)

    if not other_bot_terms:
        return False

    for my_t in sorted(my_terms, key=len, reverse=True):
        if my_t and re.match(rf'^(?:hey\s+|ok\s+|yo\s+|please\s+)?@?{re.escape(my_t)}\b', clean_lower):
            return False

    for other_alias in sorted(other_bot_terms, key=len, reverse=True):
        if other_alias and re.match(rf'^(?:hey\s+|ok\s+|yo\s+|please\s+)?@?{re.escape(other_alias)}\b', clean_lower):
            return True

    cmd_verbs = r'(?:please\s+)?(?:generate|draw|render|make|create|send|record|say|speak|give|show|paint|roast)'
    for other_alias in sorted(other_bot_terms, key=len, reverse=True):
        if other_alias and re.search(rf'\b{re.escape(other_alias)}[,:\s]+{cmd_verbs}\b', clean_lower):
            if not any(
                my_t and re.search(rf'\b{re.escape(my_t)}[,:\s]+{cmd_verbs}\b', clean_lower)
                for my_t in my_terms
            ):
                return True

    has_other_bot_word = any(
        ob_t and re.search(rf'\b{re.escape(ob_t)}\b', clean_lower)
        for ob_t in other_bot_terms
    )
    if has_other_bot_word:
        my_as_object = any(
            my_t and re.search(rf'\b(?:of|about|for|at|with)\s+@?{re.escape(my_t)}\b', clean_lower)
            for my_t in sorted(my_terms, key=len, reverse=True)
        )
        if my_as_object:
            stripped_check = clean_lower
            for my_t in sorted(my_terms, key=len, reverse=True):
                if my_t:
                    stripped_check = re.sub(rf'\b(?:of|about|for|at|with)\s+@?{re.escape(my_t)}\b', '', stripped_check)
            still_mentions_me = any(
                my_t and re.search(rf'\b{re.escape(my_t)}\b', stripped_check)
                for my_t in my_terms
            )
            if not still_mentions_me:
                return True

    return False


def mentions_other_active_bot(
    text: str,
    message: discord.Message,
    my_bot_id: int,
    my_display_name: str,
    my_keywords: list[str] | None,
    shared_image_cache_file: str
) -> bool:
    clean_lower = text.strip().lower()
    if not clean_lower:
        return False

    base_my_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', my_display_name.strip(), flags=re.IGNORECASE).strip().lower()
    my_terms = {my_display_name.strip().lower(), base_my_name}
    for kw in (my_keywords or []):
        if kw and kw.strip():
            my_terms.add(kw.strip().lower())
            my_terms.add(f"{kw.strip().lower()} bot")

    if any(my_t and re.search(rf'\b{re.escape(my_t)}\b', clean_lower) for my_t in my_terms):
        return False

    for mentioned_user in message.mentions:
        if getattr(mentioned_user, "bot", False) and mentioned_user.id != my_bot_id:
            return True

    guild_members = getattr(message.guild, "members", None) if message.guild else None
    other_bots = get_other_active_bots(shared_image_cache_file, my_bot_id, guild_members)
    for ob in other_bots:
        for term in (ob.get("aliases", []) + ob.get("keywords", [])):
            t_clean = term.strip().lower()
            if len(t_clean) >= 3 and t_clean not in my_terms and t_clean not in ("bot", "the", "app"):
                if re.search(rf'\b{re.escape(t_clean)}\b', clean_lower):
                    return True

    return False


def is_addressing_other_person(text: str, my_terms: set[str]) -> bool:
    """Stage 1 Helper: Detects if the user is explicitly greeting/addressing someone else (e.g., 'hey sam')."""
    match = re.match(r'^(?:hey|hi|hello|yo|ok|okay|please)?\s*@?([a-z0-9_]{3,})\b\s*[:,]?', text.lower())
    if match:
        addressed = match.group(1)
        generic = {"bro", "dude", "chat", "guys", "yall", "everyone", "there", "the", "this", "that", "it", "bot", "someone", "anyone"}
        if addressed not in my_terms and addressed not in generic:
            return True
    return False


async def check_followup_relevance_via_api(
    api_url: str,
    api_key: str,
    text_model: str,
    bot_name: str,
    bot_recent_text: str,
    user_text: str
) -> bool:
    """Stage 3 Helper: 10-token, zero-temperature API call to classify ambiguous follow-ups."""
    prompt = (
        f"Bot '{bot_name}' just said: \"{bot_recent_text}\"\n"
        f"User replied: \"{user_text}\"\n\n"
        "Is the user's reply directly continuing this conversation, answering the bot, or reacting to the bot? "
        "Reply with exactly one word: YES or NO."
    )
    payload = {
        "model": text_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 10
    }
    res = await post_with_backoff(
        api_url, headers={"Authorization": f"Bearer {api_key}"}, 
        json_payload=payload, timeout=8, max_retries=1, bot_name=bot_name
    )
    if res and res.status_code == 200:
        ans = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip().upper()
        return "YES" in ans
    return False


async def resolve_contextual_followup(
    message: discord.Message,
    clean_content: str,
    chat_history_list: list[dict],
    my_bot_id: int,
    my_display_name: str,
    my_keywords: list[str] | None,
    shared_image_cache_file: str,
    channel_followup_state: dict[int, dict],
    followup_window_messages: int = 2,
    max_followup_turns: int = 2,
    followup_timeout: float = 180.0,
    api_key: str = "",
    text_model: str = "",
    api_url: str = ""
) -> tuple[bool, str]:
    """
    3-Stage Hybrid Context Engine with a Non-Looping State Machine.
    """
    # STAGE 1: Deterministic Reject
    if message.author.bot or (message.reference and message.reference.message_id):
        return False, ""

    channel_id = message.channel.id
    state = channel_followup_state.get(channel_id)
    now = time.time()

    if state:
        if not state.get("active", True):
            return False, "" 
        if (now - state.get("timestamp", 0)) > followup_timeout or state.get("turns_used", 0) >= max_followup_turns:
            state["active"] = False
            return False, ""

    if mentions_other_active_bot(clean_content, message, my_bot_id, my_display_name, my_keywords, shared_image_cache_file):
        if state: state["active"] = False
        return False, ""

    base_my_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', my_display_name.strip(), flags=re.IGNORECASE).strip().lower()
    my_terms = {my_display_name.strip().lower(), base_my_name}
    for kw in (my_keywords or []):
        if kw and kw.strip():
            my_terms.add(kw.strip().lower())

    if is_addressing_other_person(clean_content, my_terms):
        if state: state["active"] = False
        return False, ""

    non_system_msgs = [m for m in chat_history_list if m.get("role") != "system" and isinstance(m.get("content"), str) and m.get("content").strip()]
    if not non_system_msgs:
        return False, ""

    recent_window = non_system_msgs[-followup_window_messages:]
    bot_msg_entry = None
    distance_from_end = 0

    for idx, m in enumerate(reversed(recent_window), start=1):
        if (m.get("author_id") == my_bot_id) or (m.get("role") == "assistant" and m.get("author_name") == my_display_name):
            bot_msg_entry = m
            distance_from_end = idx
            break

    if not bot_msg_entry:
        return False, ""

    raw_bot_text = bot_msg_entry.get("content", "")
    clean_bot_text = URL_REGEX.sub('', ERROR_REPLY_STRIP_REGEX.sub('', IMAGE_DESC_STRIP_REGEX.sub('', raw_bot_text))).strip()
    context_header = f"[Replying to {my_display_name}: \"{clean_bot_text[:157]}{'...' if len(clean_bot_text)>160 else ''}\"]"

    # STAGE 2: Deterministic Accept
    content_lower = clean_content.lower()
    is_direct_q = bool(re.search(r'\b(you|your|u|ur|wbu|what about you)\b.*\?', content_lower))
    is_direct_rxn = bool(re.search(r'\b(thanks|thx|glad|love|hate|lmao|lol|haha)\b', content_lower))
    is_yes_no = bool(re.match(r'^(yes|no|yeah|nope|sure|ok|okay|yep|nah)\b', content_lower))
    has_q_mark = '?' in raw_bot_text

    is_stage_2_accept = False
    if distance_from_end == 1 and state and (now - state.get("timestamp", 0)) < 60:
        if is_direct_q or is_direct_rxn or (has_q_mark and is_yes_no):
            is_stage_2_accept = True

    # STAGE 3: Lightweight API Classifier
    if not is_stage_2_accept:
        if distance_from_end == 2 and len(recent_window) >= 2:
            intervening_msg = recent_window[-1]
            if intervening_msg.get("author_is_bot") and intervening_msg.get("author_id") != my_bot_id:
                overlap_me = len(normalize_tokens(clean_content) & normalize_tokens(raw_bot_text))
                overlap_other = len(normalize_tokens(clean_content) & normalize_tokens(intervening_msg.get("content", "")))
                if overlap_me == 0 or overlap_me <= overlap_other:
                    return False, ""

        is_relevant = await check_followup_relevance_via_api(
            api_url, api_key, text_model, my_display_name, raw_bot_text, clean_content
        )
        if not is_relevant:
            return False, ""

    return True, context_header