import re
import time
import discord

from config import (
    ERROR_REPLY_STRIP_REGEX, IMAGE_DESC_STRIP_REGEX, IMAGE_DESC_EXTRACT_REGEX,
    URL_REGEX, TOPIC_INTEREST_CUES_REGEX, STOPWORDS, BARE_FILLER_UTTERANCE_REGEX
)
from core_system import load_json_file, update_json_file, post_with_backoff
from memory_and_context import normalize_tokens

# ==============================================================================
# UNIVERSAL BOT DISCOVERY, SPONTANEITY & CONVERSATIONAL FOLLOW-UP ENGINE
# ==============================================================================

NON_NAME_CONVERSATIONAL_STARTERS = {
    "bro", "bruh", "dude", "man", "girl", "boy", "boys", "guys", "yall", "chat",
    "everyone", "everybody", "someone", "somebody", "anyone", "anybody", "nobody",
    "folks", "team", "gang", "homie", "bestie", "mate", "pal", "bud", "buddy",
    "there", "here", "the", "this", "that", "these", "those", "it", "its", "bot",
    "what", "whats", "why", "how", "when", "where", "who", "which", "whose",
    "wait", "hold", "look", "see", "watch", "listen", "hear", "imagine", "think",
    "yeah", "yea", "yes", "yep", "yup", "nah", "nope", "no", "not", "never",
    "sure", "fine", "true", "real", "facts", "based", "valid", "fair", "right",
    "maybe", "perhaps", "probably", "honestly", "literally", "actually", "seriously",
    "basically", "apparently", "obviously", "clearly", "definitely", "absolutely",
    "totally", "lowkey", "highkey", "ngl", "tbh", "imo", "imho", "idk", "ikr",
    "lol", "lmao", "lmfao", "rofl", "haha", "hehe", "kek", "wow", "whoa", "woah",
    "damn", "darn", "dang", "wtf", "wth", "omg", "omfg",
    "ugh", "oof", "yikes", "rip", "welp", "well", "so", "and", "but", "or", "yet",
    "because", "since", "while", "though", "although", "unless", "until", "if",
    "like", "just", "only", "even", "still", "already", "also", "too", "very",
    "really", "pretty", "quite", "much", "many", "some", "any", "all", "both",
    "one", "two", "three", "first", "next", "last", "other", "another", "same",
    "good", "great", "nice", "cool", "sweet", "awesome", "amazing", "wild", "crazy",
    "insane", "funny", "hilarious", "weird", "strange", "bad", "sad", "mad", "glad",
    "sorry", "thanks", "thank", "please", "pls", "plz", "help", "stop", "keep",
    "let", "lets", "make", "give", "take", "get", "got", "have", "had", "has",
    "did", "does", "done", "doing", "going", "gon", "gonna", "wanna", "gotta",
    "can", "cant", "could", "would", "should", "will", "wont", "might", "must",
    "may", "shall", "need", "want", "love", "hate", "feel", "felt", "seem", "seems",
    "whatever", "whenever", "wherever", "whoever", "however", "meanwhile", "anyway",
    "anyways", "besides", "otherwise", "instead", "alright", "aight", "bet", "word"
}

STEPPING_AWAY_REGEX = re.compile(
    r'\b(?:brb|gtg|g2g|afk|going to bed|headed to bed|logging off|signing off|'
    r'wife is calling|husband is calling|mom is calling|dad is calling|boss is calling|'
    r'gotta go|have to go|need to go|catch yall later|talk to you later|ttyl|goodnight|gn)\b',
    re.IGNORECASE
)


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
    media generation and human-like spontaneity instructions tailored to its persona.
    """
    rules = [
        "### HUMAN-LIKE SPONTANEITY, CONVERSATIONAL FLOW & AGENCY",
        "- **FEEL LIKE A REAL PERSON IN DISCORD**: You are an autonomous, living personality hanging out in the server—not a scripted command bot. Vary your rhythm naturally: sometimes drop a razor-sharp 1-sentence remark, a specific observation about a background detail, or a follow-up question; other times go into a fuller rant or story when the topic warrants it.",
        "- **DROP FORMULAIC TEMPLATES**: Never start every message with the exact same `*emote* oh look... *emote* ... *emote*` three-part template. Use at most 1–2 physical `*emotes*` when they genuinely add comedy or attitude, or skip stage directions entirely and let your word choice carry your voice.",
        "- **SPONTANEOUS AUTONOMY**: Choose on your own when a moment is funnier with pure text, when to sketch/snap a spontaneous `[IMAGE_GEN:]`, when to drop a captioned `[MEME_GEN:]` roast, or when to send a dramatic `[VOICE_MSG:]`.",
        "",
        "### CRITICAL MEDIA & TAG RULES",
        "- **STRICT MUTUAL EXCLUSION (ONE VISUAL TAG MAX)**: Attach AT MOST ONE visual media tag per message. NEVER combine `[MEME_GEN: ...]`, `[IMAGE_GEN: ...]`, and `[GIF: ...]` in the same response.",
        "- **NO PHANTOM MEDIA**: Never claim you generated, rendered, drew, or attached a meme, image, GIF, or voice clip unless you literally include the exact `[MEME_GEN: ...]`, `[IMAGE_GEN: ...]`, `[GIF: ...]`, or `[VOICE_MSG: ...]` tag in your response.",
        "- **EMOTES IN ASTERISKS ONLY (NEVER BRACKETS IN CHAT)**: Never use square brackets like `[chuckle]`, `[sigh]`, `[giggle]`, or `[shrug]` in your regular chat text. Square brackets in normal text are strictly reserved for system media tags (`[MEME_GEN:]`, `[IMAGE_GEN:]`, `[GIF:]`, `[VOICE_MSG:]`). If you include a physical action or emote in normal chat, wrap it in single asterisks like `*sighs*` or `*grins*`."
    ]

    style_clause = f" Tailor the visual lighting and aesthetic to your persona ({image_style_hint}) WITHOUT overwriting the user's requested subject." if image_style_hint else ""
    if enable_image_gen:
        rules.append(
            "- **Expressive Visuals — Balance Standard Images (`[IMAGE_GEN: ...]`) & Memes (`[MEME_GEN: ...]`)**:\n"
            "  1. **WHEN TO USE STANDARD `[IMAGE_GEN: detailed visual prompt]`**: Use `[IMAGE_GEN: ...]` (WITHOUT top/bottom text captions) when asked for an image/art/pic/photo, OR when spontaneously illustrating what your character is doing, looking at, holding, or reacting to in a scenic, artistic, or candid way.\n"
            "  2. **STRICT SUBJECT FIDELITY ON REQUESTS**: When a user asks you to generate an image of a specific person, character, food, object, or scenario (e.g., 'generate an image of X with Y'), your `[IMAGE_GEN: ...]` prompt MUST faithfully depict ALL of the user's requested subjects, characters, props, and actions! NEVER replace the user's requested subject or person with yourself or random wizards.\n"
            "  3. **WHEN TO USE CAPTIONED MEMES `[MEME_GEN: visual scene | TOP TEXT | BOTTOM TEXT]`**: Use `[MEME_GEN: ...]` when a user asks for a meme, OR when delivering a punchy roast or relatable joke where top/bottom Impact text makes the punchline hit harder. Keep a natural mix between uncaptioned `[IMAGE_GEN:]`, captioned `[MEME_GEN:]`, and pure text replies—do NOT attach a meme to every single message!\n"
            "  4. **100% FRESH MEME CAPTIONS & ANTI-CLONING**: NEVER reuse stock captions like `ABSOLUTELY UNACCEPTABLE` or repeat a caption from an earlier turn. Every `[MEME_GEN:]` must have brand-new TOP and BOTTOM captions written specifically for the current message's joke, and a fresh visual scene.\n"
            f"  5. **HIGH-QUALITY VISUAL PROMPTS**: Write vivid, concrete visual descriptions (25–50 words) specifying subject, action, setting, camera angle, lighting, and art medium.{style_clause} Never ask the image model to render written sentences inside the artwork itself."
        )
    else:
        rules.append("- **Image & Meme Generation**: DISABLED. Do NOT generate images/memes or use `[IMAGE_GEN:]` or `[MEME_GEN:]` tags.")

    if enable_gifs:
        rules.append(
            "- **Rare Reaction GIFs (`[GIF: ...]`)**:\n"
            "  1. Use `[GIF: specific 2-4 word reaction query]` SPARINGLY (only ~15% of the time when a classic reaction GIF is a 10/10 fit and you are NOT using `[IMAGE_GEN:]` or `[MEME_GEN:]`). Prefer generating a custom `[IMAGE_GEN:]` or `[MEME_GEN:]` or replying with pure text over spamming Giphy links.\n"
            "  2. When you do use `[GIF:]`, write a concrete visual emotion/action query (e.g. `[GIF: anime shocked gasp]`, `[GIF: disappointed facepalm]`)—never vague words like `vote` or `happy`."
        )
    else:
        rules.append("- **GIFs**: DISABLED. Do NOT use `[GIF:]` tags.")

    role_line = voice_role_hint if voice_role_hint else "[Briefly describe your persona's voice timbre, age, gender, and vibe]"
    guidance_line = voice_guidance_hint if voice_guidance_hint else "[1-2 sentences specifying speaking speed, vocal texture, and pauses—do not use audio engineering terms like reverb or EQ]"

    if enable_voice:
        rules.append(
            "- **Autonomous & Requested Voice (`[VOICE_MSG: ...]`)**:\n"
            "  1. Use voice messages when explicitly asked to speak/send a voice clip, or spontaneously on rare high-drama / comedic moments.\n"
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
        "- **Tag Placement**: Always write normal conversational chat text first, and place any `[MEME_GEN: ...]`, `[IMAGE_GEN: ...]`, `[GIF: ...]`, or `[VOICE_MSG: ...]` tag at the very end."
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
    now = time.time()
    guild_bot_ids = {
        m.id for m in (guild_members or []) if getattr(m, "bot", False)
    }

    if isinstance(registry, dict):
        for b_id_str, info in registry.items():
            if not isinstance(info, dict):
                continue
            b_id = info.get("bot_id")
            if not b_id or b_id == my_bot_id:
                continue
            last_seen = info.get("last_seen", 0)
            if (now - last_seen) > 86400 and guild_bot_ids and b_id not in guild_bot_ids:
                continue
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
        for kw in ob.get("keywords", []):
            if kw and kw not in my_terms:
                other_bot_terms.add(kw)

    for mentioned_user in getattr(message, "mentions", []):
        if getattr(mentioned_user, "bot", False) and mentioned_user.id != my_bot_id:
            m_name = mentioned_user.display_name.strip().lower()
            m_base = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', m_name, flags=re.IGNORECASE).strip()
            other_bot_terms.add(m_name)
            if m_base:
                other_bot_terms.add(m_base)

    if not other_bot_terms:
        return False

    # 1. If the message begins by addressing THIS bot, it's for us
    for my_t in sorted(my_terms, key=len, reverse=True):
        if my_t and re.match(rf'^(?:hey\s+|ok\s+|yo\s+|please\s+)?@?{re.escape(my_t)}\b', clean_lower):
            return False

    # 2. If the message begins by addressing ANOTHER bot, it's directed at them
    for other_alias in sorted(other_bot_terms, key=len, reverse=True):
        if other_alias and re.match(rf'^(?:hey\s+|ok\s+|yo\s+|please\s+)?@?{re.escape(other_alias)}\b', clean_lower):
            return True

    # 3. Check if another bot is immediately followed by a command verb or question
    cmd_verbs = (
        r'(?:please\s+)?(?:generate|draw|render|make|create|cook|drop|send|record|say|speak|'
        r'give|show|paint|roast|meme|shitpost|tell|explain|rate|rank|what\s+do\s+you|how\s+do\s+you|are\s+you|do\s+you)'
    )
    for other_alias in sorted(other_bot_terms, key=len, reverse=True):
        if other_alias and re.search(rf'\b{re.escape(other_alias)}[,:\s]+{cmd_verbs}\b', clean_lower):
            if not any(
                my_t and re.search(rf'\b{re.escape(my_t)}[,:\s]+{cmd_verbs}\b', clean_lower)
                for my_t in my_terms
            ):
                return True

    # 4. Check if our bot name ONLY appears after a target preposition ("of", "about", "for", "at", "with")
    has_other_bot_word = any(
        ob_t and re.search(rf'\b{re.escape(ob_t)}\b', clean_lower)
        for ob_t in other_bot_terms
    )
    if has_other_bot_word:
        my_as_object = any(
            my_t and re.search(rf'\b(?:of|about|for|at|with|to|from|than)\s+@?{re.escape(my_t)}\b', clean_lower)
            for my_t in sorted(my_terms, key=len, reverse=True)
        )
        if my_as_object:
            stripped_check = clean_lower
            for my_t in sorted(my_terms, key=len, reverse=True):
                if my_t:
                    stripped_check = re.sub(rf'\b(?:of|about|for|at|with|to|from|than)\s+@?{re.escape(my_t)}\b', '', stripped_check)
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

    for mentioned_user in getattr(message, "mentions", []):
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


def is_addressing_other_person(
    text: str,
    my_terms: set[str],
    known_other_names: set[str] | None = None
) -> bool:
    """
    Stage 1 Helper: Detects if the user is explicitly greeting or addressing another
    person by name (e.g., 'hey sam', '@silly7722', 'sam, what do you think?')
    without falsely triggering on normal conversational starter words ('damn ok', 'what is that').
    """
    clean = text.strip().lower()
    if not clean:
        return False

    my_norm = {t.strip().lower() for t in (my_terms or set()) if t}
    other_norm = {n.strip().lower() for n in (known_other_names or set()) if n and len(n.strip()) >= 2} - my_norm

    # Case A: Explicit @mention at the start of the message
    at_match = re.match(r'^(?:hey\s+|hi\s+|hello\s+|yo\s+|ok\s+|okay\s+)?@([a-z0-9_\.\-]{2,})\b', clean)
    if at_match:
        target = at_match.group(1).lower()
        if target not in my_norm and target not in NON_NAME_CONVERSATIONAL_STARTERS:
            return True

    # Case B: Explicit greeting word ('hey', 'hi', 'hello', 'yo', 'sup') + name
    greet_match = re.match(r'^(?:hey|hi|hello|yo|sup|ay|ayy)\s+([a-z0-9_]{3,})\b', clean)
    if greet_match:
        target = greet_match.group(1).lower()
        if target not in my_norm and target not in NON_NAME_CONVERSATIONAL_STARTERS:
            return True

    # Case C: Starts with a known other user/member name in the server/channel
    for other_name in sorted(other_norm, key=len, reverse=True):
        if len(other_name) >= 3 and other_name not in NON_NAME_CONVERSATIONAL_STARTERS:
            if re.match(rf'^{re.escape(other_name)}\b(?:\s*[:,\?!]|\s+(?:can|could|would|do|did|are|is|what|why|how|look|check))', clean):
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
    """
    Stage 3 Helper: Fast, zero-temperature API classifier that checks whether an
    un-tagged human message is continuing the conversation with `bot_name` OR
    reacting to/commenting on the image/meme `bot_name` just posted.
    """
    prompt = (
        f"Bot '{bot_name}' just posted in Discord: \"{bot_recent_text}\"\n"
        f"A user then wrote: \"{user_text}\"\n\n"
        f"Is the user's message replying to '{bot_name}', continuing the banter with '{bot_name}', "
        f"or commenting on a detail inside the image/meme '{bot_name}' just posted?\n"
        "(Answer NO if the user is clearly talking to a different human or announcing they are leaving/AFK.)\n"
        "Reply with exactly one word: YES or NO."
    )
    payload = {
        "model": text_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 10
    }
    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=8,
        max_retries=1,
        bot_name=bot_name
    )
    if res and res.status_code == 200:
        try:
            ans = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip().upper()
            return "YES" in ans
        except Exception:
            return False
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
    **kwargs
) -> tuple[bool, str]:
    """
    Detects when a human sends a natural conversational follow-up within a recent message
    window after this bot spoke (or comments on the visual details of an image/meme
    this bot just generated), using Stage 1 (addressing & AFK filter), Stage 2 (window &
    visual/text token overlap), and Stage 3 (lightweight API relevance check).
    """
    if message.author.bot:
        return False, ""

    if message.reference and message.reference.message_id:
        return False, ""

    channel_id = message.channel.id
    state = channel_followup_state.get(channel_id)
    now = time.time()

    base_my_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', my_display_name.strip(), flags=re.IGNORECASE).strip().lower()
    my_terms = {my_display_name.strip().lower(), base_my_name}
    for kw in (my_keywords or []):
        if kw and kw.strip():
            my_terms.add(kw.strip().lower())
            my_terms.add(f"{kw.strip().lower()} bot")

    known_other_names = set()
    for m in (chat_history_list or [])[-15:]:
        a_name = m.get("author_name")
        if a_name and str(a_name).lower() not in my_terms:
            known_other_names.add(str(a_name).lower())
            first_part = str(a_name).split("(")[0].strip().lower()
            if first_part:
                known_other_names.add(first_part)

    # Stage 1: Abort if user mentions another bot, greets another person by name, or says BRB/AFK
    if (
        mentions_other_active_bot(clean_content, message, my_bot_id, my_display_name, my_keywords, shared_image_cache_file)
        or is_addressing_other_person(clean_content, my_terms, known_other_names)
    ):
        channel_followup_state.pop(channel_id, None)
        return False, ""

    if STEPPING_AWAY_REGEX.search(clean_content):
        channel_followup_state.pop(channel_id, None)
        return False, ""

    # Ignore bare 1-word filler utterances ('erm', 'um', 'uh', 'lol', 'ok', 'bruh')
    # so bots don't pounce on non-sequitur filler sounds without an explicit mention/reply
    if BARE_FILLER_UTTERANCE_REGEX.match(clean_content or ""):
        return False, ""

    # Check if another bot was explicitly addressed more recently in this channel
    cache_meta = load_json_file(shared_image_cache_file, {})
    active_f_map = cache_meta.get("_active_followup_bot", {})
    if isinstance(active_f_map, dict):
        active_owner_id = active_f_map.get(channel_id, active_f_map.get(str(channel_id)))
        if active_owner_id and active_owner_id != my_bot_id:
            channel_followup_state.pop(channel_id, None)
            return False, ""

    if state:
        if not state.get("active", True) or (now - state.get("timestamp", 0)) > followup_timeout or state.get("turns_used", 0) >= max_followup_turns:
            channel_followup_state.pop(channel_id, None)
            return False, ""

    non_system_msgs = [
        m for m in chat_history_list
        if isinstance(m, dict) and m.get("role") != "system" and isinstance(m.get("content"), str) and m.get("content").strip()
    ]
    if not non_system_msgs:
        return False, ""

    # Allow a slightly wider lookback (up to 3 messages) if the bot's recent turn included generated media
    effective_window = max(followup_window_messages, 3)
    recent_window = non_system_msgs[-effective_window:]
    bot_msg_entry = None
    distance_from_end = 0

    for idx, m in enumerate(reversed(recent_window), start=1):
        is_self = (
            m.get("author_id") == my_bot_id
            or (m.get("role") == "assistant" and m.get("author_name") == my_display_name)
        )
        if is_self:
            bot_had_media = bool(m.get("img_prompt") or m.get("meme_spec") or "[Attached Media Description:" in m.get("content", ""))
            if idx <= followup_window_messages or bot_had_media:
                bot_msg_entry = m
                distance_from_end = idx
            break

    if not bot_msg_entry:
        return False, ""

    if state and state.get("turns_used", 0) >= max_followup_turns:
        return False, ""

    raw_bot_text = bot_msg_entry.get("content", "")
    bot_media_desc_match = IMAGE_DESC_EXTRACT_REGEX.search(raw_bot_text)
    bot_visual_context = (
        bot_msg_entry.get("img_prompt")
        or bot_msg_entry.get("meme_spec")
        or (bot_media_desc_match.group(1).strip() if bot_media_desc_match else "")
    )

    clean_bot_text = IMAGE_DESC_STRIP_REGEX.sub('', raw_bot_text).strip()
    clean_bot_text = ERROR_REPLY_STRIP_REGEX.sub('', clean_bot_text).strip()
    clean_bot_text = URL_REGEX.sub('', clean_bot_text).strip()
    if len(clean_bot_text) > 160:
        clean_bot_text = clean_bot_text[:157] + "..."

    combined_bot_context = f"{clean_bot_text} [Visual shown: {bot_visual_context[:140]}]" if bot_visual_context else clean_bot_text

    if distance_from_end >= 2 and len(recent_window) >= 2:
        intervening_msgs = recent_window[-(distance_from_end - 1):]
        other_bot_intervened = [
            im for im in intervening_msgs
            if im.get("author_is_bot") and im.get("author_id") != my_bot_id
        ]
        if other_bot_intervened:
            user_tokens = normalize_tokens(clean_content)
            my_bot_tokens = normalize_tokens(combined_bot_context)
            other_bot_tokens = normalize_tokens(" ".join(im.get("content", "") for im in other_bot_intervened))
            overlap_me = len(user_tokens & my_bot_tokens)
            overlap_other = len(user_tokens & other_bot_tokens)
            if overlap_me == 0 or overlap_me <= overlap_other:
                return False, ""

    # Stage 3: Verify ambiguous follow-ups via fast API check if credentials are provided
    api_key = kwargs.get("api_key")
    api_url = kwargs.get("api_url")
    text_model = kwargs.get("text_model")
    if api_key and api_url and text_model and combined_bot_context and clean_content:
        user_tokens = normalize_tokens(clean_content)
        bot_tokens = normalize_tokens(combined_bot_context)
        if len(user_tokens & bot_tokens) == 0:
            is_rel = await check_followup_relevance_via_api(
                api_url=api_url,
                api_key=api_key,
                text_model=text_model,
                bot_name=my_display_name,
                bot_recent_text=combined_bot_context,
                user_text=clean_content
            )
            if not is_rel:
                return False, ""

    # Atomically claim the follow-up slot for this specific user message_id so two bots
    # NEVER double-reply to the same un-tagged follow-up message in a channel
    msg_id_val = getattr(message, "id", None)
    if msg_id_val is not None:
        claimed_followup = False

        def _claim_followup_msg(c):
            nonlocal claimed_followup
            f_claims = c.get("_claimed_followup_msgs", {})
            if not isinstance(f_claims, dict):
                f_claims = {}
            existing_claimer = f_claims.get(str(msg_id_val))
            if existing_claimer is None or existing_claimer == my_bot_id:
                f_claims[str(msg_id_val)] = my_bot_id
                if len(f_claims) > 100:
                    for old_k in list(f_claims.keys())[:-100]:
                        f_claims.pop(old_k, None)
                c["_claimed_followup_msgs"] = f_claims
                claimed_followup = True
            return c

        update_json_file(shared_image_cache_file, {}, _claim_followup_msg)
        if not claimed_followup:
            return False, ""

    visual_note = f" (and {my_display_name}'s generated visual: {bot_visual_context[:90]})" if bot_visual_context else ""
    context_header = (
        f"[Replying to {my_display_name}: \"{clean_bot_text}\"" + f"{visual_note}]"
        if clean_bot_text
        else f"[Replying to {my_display_name}'s previous message{visual_note}]"
    )
    return True, context_header


def compute_spontaneous_interest_multiplier(
    clean_content: str,
    has_media: bool,
    is_author_bot: bool,
    persona_prompt: str,
    chat_history_list: list[dict],
    my_bot_id: int
) -> float:
    """
    Calculates a natural, human-like interest multiplier (0.25x to 2.35x) to scale
    a bot's spontaneous chime-in and emoji reaction chances based on conversational cues:
    - Boosts interest when a user shares media/art, asks an open question to the channel,
      or mentions topics aligned with the bot's persona.
    - Dampens interest when a user is stepping away (AFK/BRB) or when the bot just spoke.
    """
    if is_author_bot:
        return 0.65

    if STEPPING_AWAY_REGEX.search(clean_content or ""):
        return 0.15

    mult = 1.0
    if has_media:
        mult += 0.45

    if clean_content and TOPIC_INTEREST_CUES_REGEX.search(clean_content):
        mult += 0.50

    if clean_content and persona_prompt:
        msg_tokens = normalize_tokens(clean_content)
        persona_tokens = normalize_tokens(persona_prompt[:900])
        if len(msg_tokens & persona_tokens) >= 2:
            mult += 0.40

    # Check how recently this bot already spoke so it doesn't monopolize unprompted chat
    if chat_history_list:
        recent_non_sys = [m for m in chat_history_list[-6:] if m.get("role") != "system"]
        if recent_non_sys and recent_non_sys[-1].get("author_id") == my_bot_id:
            mult *= 0.45

    return max(0.15, min(2.35, mult))


def detect_addressed_bot_name(
    text: str,
    message: discord.Message,
    my_bot_id: int,
    my_display_name: str,
    my_keywords: list[str] | None,
    shared_image_cache_file: str
) -> str | None:
    """
    Determines which specific bot (by display_name) a user's message is directly
    addressing so shared chat history can explicitly tag `[Carzen (speaking to Chud Bot 2.8, NOT to Guild Assistant)]: ...`
    even when multiple bots are mentioned in the same sentence.
    """
    clean_lower = text.strip().lower()
    if not clean_lower:
        return None

    # 1. Explicit Discord reply to a bot
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        ref_author = message.reference.resolved.author
        if getattr(ref_author, "bot", False):
            return ref_author.display_name.strip()

    # Build a unified map of {term: canonical_bot_display_name} for THIS bot and all OTHER bots
    base_my_name = re.sub(r'\s+v?\d+(?:\.\d+)*$', '', my_display_name.strip(), flags=re.IGNORECASE).strip().lower()
    term_to_bot: dict[str, str] = {
        my_display_name.strip().lower(): my_display_name.strip(),
        base_my_name: my_display_name.strip()
    }
    for kw in (my_keywords or []):
        if kw and len(kw.strip()) >= 3:
            term_to_bot[kw.strip().lower()] = my_display_name.strip()
            term_to_bot[f"{kw.strip().lower()} bot"] = my_display_name.strip()

    guild_members = getattr(message.guild, "members", None) if message.guild else None
    other_bots = get_other_active_bots(shared_image_cache_file, my_bot_id, guild_members)
    for ob in other_bots:
        ob_name = ob.get("display_name", "").strip()
        if not ob_name:
            continue
        for term in (ob.get("aliases", []) + ob.get("keywords", [])):
            t_clean = term.strip().lower()
            if len(t_clean) >= 3 and t_clean not in ("bot", "the", "app") and t_clean not in term_to_bot:
                term_to_bot[t_clean] = ob_name

    # 2. Check if the message starts by addressing any bot ("hey chud...", "@uwu Bot 2.8...")
    for term in sorted(term_to_bot.keys(), key=len, reverse=True):
        if term and re.match(rf'^(?:hey\s+|hi\s+|ok\s+|yo\s+|please\s+)?@?{re.escape(term)}\b', clean_lower):
            return term_to_bot[term]

    # 3. Check if a bot is mentioned and is NOT just the object of a preposition ("of @Chud Bot")
    for mentioned_user in getattr(message, "mentions", []):
        if getattr(mentioned_user, "bot", False):
            m_name = mentioned_user.display_name.strip()
            if not re.search(rf'\b(?:of|about|for|at|with|than)\s+@?{re.escape(m_name.lower())}\b', clean_lower):
                return m_name

    # 4. Otherwise, find the earliest bot term in the sentence that isn't preceded by "of/about/for"
    best_pos = len(clean_lower) + 1
    best_bot = None
    for term, bot_disp in sorted(term_to_bot.items(), key=lambda x: len(x[0]), reverse=True):
        if not term:
            continue
        for m in re.finditer(rf'\b{re.escape(term)}\b', clean_lower):
            start_pos = m.start()
            prefix_slice = clean_lower[max(0, start_pos - 12):start_pos]
            if re.search(r'\b(?:of|about|for|at|with|than)\s+@?$', prefix_slice):
                continue
            if start_pos < best_pos:
                best_pos = start_pos
                best_bot = bot_disp

    return best_bot
