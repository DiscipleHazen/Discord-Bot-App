import re
from config import (
    VOICE_START_REGEX, MIMO_AUDIO_TAG_MAP, URL_REGEX, IMAGE_GEN_REGEX,
    UNCLOSED_IMAGE_GEN_REGEX, GIF_STRIP_REGEX, TTS_KAOMOJI_STRIP_REGEX,
    BRACKET_EMOTE_REGEX, ASTERISK_EMOTE_EXTRACT_REGEX, ERROR_REPLY_STRIP_REGEX,
    IMAGE_DESC_STRIP_REGEX, MEME_GEN_REGEX, UNCLOSED_MEME_GEN_REGEX,
    LEARN_GENERAL_STRIP_REGEX, LEARN_USER_STRIP_REGEX, LEARN_STRIP_REGEX
)

# ==============================================================================
# VOICE, TEXT, CADENCE & EMOTE PARSING UTILITIES
# ==============================================================================

EMPHASIS_NON_EMOTE_WORDS = {
    "not", "never", "really", "very", "so", "actually", "literally",
    "always", "all", "you", "me", "this", "that", "what", "why", "how",
    "everything", "nothing", "everyone", "no", "yes", "now", "here"
}

LEAKED_CHAT_PREFIX_REGEX = re.compile(
    r'(?m)^\s*\[[a-zA-Z0-9_\-\.\s]{1,40}(?:\(speaking to [^\)]+\))?\]:\s*',
    re.IGNORECASE
)

# Detects when the LLM hallucinates a second speaker turn in the middle of its reply
# (e.g., `[silly7722 (speaking to Guild Assistant, NOT to Malfoid)]: ...`,
# `*Carzen: yeah they worked again* : ...`, `: Oh, come off it...`, or `[No response—...]`)
HALLUCINATED_MULTI_TURN_CUTOFF_REGEX = re.compile(
    r'(?:'
    r'\n\s*\[[a-zA-Z0-9_\-\.\s]{2,40}(?:\(speaking to [^\)]+\))?\]:'
    r'|\s+\[[a-zA-Z0-9_\-\.\s]{2,40}\(speaking to [^\)]+\)\]:'
    r'|\s+\*?[A-Z][a-zA-Z0-9_\-]{1,25}:\s+[^:\n]{2,80}?\*?\s*:\s+'
    r'|\s+:\s+(?:Oh\b|Clearly\b|Honestly\b|Wait\b|Well\b|Look\b|Please\b| No\b|Yes\b|I\b|You\b|\*[a-zA-Z])'
    r'|\s*\[?\*?\[?No response[\—\-\:][^\n\]]*\]?\*?'
    r')',
    re.IGNORECASE
)

# Strips echoed short user inputs at the very start of the bot's reply (e.g. `erm : *glances up*` or `um : *sighs*`)
ECHOED_INPUT_PREFIX_REGEX = re.compile(
    r'^\s*(?:[a-zA-Z0-9_\-]{1,20}\s*:\s*)+(?=\*|[A-Z"\'])'
)

LEAKED_CONTEXT_TAG_REGEX = re.compile(
    r'\[(?:Replying to [^\]\n]+|Contains (?:GIF|YouTube Video)[^\]\n]*|'
    r'System (?:Visual|Context|Glitch)[^\]\n]*|'
    r'Generated Attached (?:Image|Meme|Voice Audio)[^\]\n]*|'
    r'Sent (?:GIF|Image|Meme|Audio) of[^\]\n]*)\]\n?',
    re.IGNORECASE
)

PROMPT_BAKED_TEXT_STRIP_REGEX = re.compile(
    r'(?:,\s*|\s+)(?:with\s+(?:bold\s+|white\s+|impact\s+|top\s+|bottom\s+)*(?:text|caption|captions|words|lettering)\s+'
    r'(?:reading|saying|that\s+says)\s*[\'"][^\'"]+[\'"])',
    re.IGNORECASE
)


def extract_and_strip_voice_msg(text: str) -> tuple[str | None, str]:
    """
    Safely extracts the full content of [VOICE_MSG: ...] even when nested
    brackets like [sigh] or [chuckle] are inside the voice script, and returns
    (voice_content, cleaned_reply_text).
    """
    if not text:
        return None, ""

    match = VOICE_START_REGEX.search(text)
    if not match:
        return None, text

    start_idx = match.start()
    content_start = match.end()
    depth = 1
    i = content_start

    while i < len(text) and depth > 0:
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
        i += 1

    if depth == 0:
        voice_content = text[content_start:i - 1].strip()
        cleaned_text = (text[:start_idx] + text[i:]).strip()
    else:
        voice_content = text[content_start:].strip()
        cleaned_text = text[:start_idx].strip()

    cleaned_text = re.sub(r'`+\s*`+', '', cleaned_text).strip()
    cleaned_text = re.sub(r'`+$', '', cleaned_text).strip()
    return voice_content, cleaned_text


def sanitize_mimo_inline_tags(speech_text: str) -> str:
    """
    Normalizes inline [action] or *action* tags inside spoken TTS text into
    valid MiMo-V2.5-TTS audio control tags, removing non-audio roleplay actions
    (like *tail flicks* or *adjusts glasses*) so the TTS actor doesn't glitch.
    """
    valid_mimo_prefixes = (
        "inhale", "take a deep breath", "sigh", "let out a long sigh", "pant",
        "hold one's breath", "nervous", "scared", "excited", "tired", "wronged",
        "coquettish", "guilty", "shocked", "impatient", "trembling", "voice trembling",
        "pitch change", "cracked voice", "nasal voice", "breathiness", "hoarseness",
        "smile", "chuckle", "laugh out loud", "sneer", "sob", "whimper", "choke",
        "wail", "pause", "pauses for a moment", "cough", "softly", "whisper"
    )

    def _replace_tag(match):
        raw_tag = match.group(1).strip().lower()
        if raw_tag in MIMO_AUDIO_TAG_MAP:
            return f"[{MIMO_AUDIO_TAG_MAP[raw_tag]}]"
        if any(raw_tag.startswith(p) for p in valid_mimo_prefixes):
            return f"[{raw_tag}]"
        return ""

    speech_text = re.sub(r'\*([^*]{1,40})\*', r'[\1]', speech_text)
    speech_text = re.sub(r'\[([^\]]{1,45})\]', _replace_tag, speech_text)
    return speech_text


def parse_mimotts_voice_block(
    raw_voice_content: str,
    default_role_desc: str,
    default_style: str = "Natural"
) -> tuple[str, str]:
    """
    Separates Director Mode headers (Role, Scene, Guidance) from the spoken dialogue
    for MiMo-V2.5-TTS-VoiceDesign, cleaning out unpronounceable chat kaomoji and URLs.
    """
    cleaned_raw = raw_voice_content.strip().strip('`')
    lines = [line.strip().strip('`') for line in cleaned_raw.split('\n') if line.strip().strip('`')]
    director_instructions = []
    spoken_text_lines = []
    in_header_section = True

    for line in lines:
        clean_line = re.sub(r'^[\*\-\s]+', '', line)
        clean_line = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_line)
        lower_line = clean_line.lower()

        if lower_line.startswith(("role:", "character:", "scene:", "guidance:")):
            director_instructions.append(clean_line)
            in_header_section = True
        elif in_header_section and clean_line.startswith("(") and ")" in clean_line:
            in_header_section = False
            spoken_text_lines.append(clean_line)
        elif (
            in_header_section
            and len(director_instructions) > 0
            and not clean_line.startswith("(")
            and len(spoken_text_lines) == 0
            and line.startswith("-")
        ):
            director_instructions.append(clean_line)
        else:
            in_header_section = False
            spoken_text_lines.append(clean_line)

    user_prompt_content = "\n".join(director_instructions).strip()
    assistant_speech_content = " ".join(spoken_text_lines).strip()

    if not assistant_speech_content and director_instructions:
        assistant_speech_content = director_instructions[-1]
        director_instructions = director_instructions[:-1]
        user_prompt_content = "\n".join(director_instructions).strip()

    if not user_prompt_content:
        user_prompt_content = default_role_desc
    if not assistant_speech_content:
        assistant_speech_content = cleaned_raw

    assistant_speech_content = URL_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = LEARN_USER_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = LEARN_GENERAL_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = LEARN_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = MEME_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = UNCLOSED_MEME_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = IMAGE_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = UNCLOSED_IMAGE_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = GIF_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = sanitize_mimo_inline_tags(assistant_speech_content)
    assistant_speech_content = TTS_KAOMOJI_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = re.sub(r'<a?:[a-zA-Z0-9_]+:\d+>', '', assistant_speech_content)
    assistant_speech_content = re.sub(r'[\*_`~]+', '', assistant_speech_content)
    assistant_speech_content = re.sub(r'\s{2,}', ' ', assistant_speech_content).strip()

    if assistant_speech_content and not assistant_speech_content.startswith("("):
        assistant_speech_content = f"({default_style}) {assistant_speech_content}"

    if len(assistant_speech_content) > 450:
        clipped = assistant_speech_content[:450]
        last_punct = max(clipped.rfind('.'), clipped.rfind('!'), clipped.rfind('?'))
        if last_punct > 150:
            assistant_speech_content = clipped[:last_punct + 1].strip()
        else:
            assistant_speech_content = clipped.strip()

    return user_prompt_content, assistant_speech_content


def derive_chat_text_from_voice(raw_voice_content: str) -> str:
    """
    If the LLM placed its entire response inside [VOICE_MSG: ...] and left the visible
    chat reply empty, derives a clean, natural Discord text message from the spoken lines.
    """
    _, spoken = parse_mimotts_voice_block(raw_voice_content, "", "Natural")
    spoken = re.sub(r'^\([^\)]+\)\s*', '', spoken).strip()
    spoken = convert_bracket_emotes_to_asterisks(spoken)
    return clean_and_compact_reply(spoken)


def convert_bracket_emotes_to_asterisks(text: str) -> str:
    """
    Converts any remaining [emote] or [action] tags in normal chat text into *emote*
    after media/system tags have already been stripped.
    """
    def _replace(match):
        inner = match.group(1).strip()
        lower_inner = inner.lower()
        if lower_inner.startswith((
            "http", "image_gen", "meme_gen", "voice_msg", "gif",
            "attached", "contains", "system", "replying", "addressed", "directed",
            "learn", "no response"
        )) or "speaking to" in lower_inner:
            return ""
        return f"*{inner}*"

    return BRACKET_EMOTE_REGEX.sub(_replace, text)


def extract_used_emotes(text: str) -> list[str]:
    """
    Extracts normalized *emote* phrases from a message so memory/context builders
    can prevent bots from repeating identical physical actions every turn.
    """
    if not text:
        return []
    found = []
    for m in ASTERISK_EMOTE_EXTRACT_REGEX.finditer(text):
        em = re.sub(r'\s+', ' ', m.group(1).strip().lower())
        words = em.split()
        if 1 <= len(words) <= 8:
            if len(words) == 1 and words[0] in EMPHASIS_NON_EMOTE_WORDS:
                continue
            if em not in found:
                found.append(em)
    return found


def _prune_excessive_inline_emotes(text: str, max_emotes: int = 2) -> str:
    """
    Prevents robotic 'stage-direction overload' (e.g., 3-4 separate *emotes* stuffed
    into a single short line) by keeping at most `max_emotes` distinct actions per turn
    and removing duplicate emote stems within the same reply.
    """
    if not text or "*" not in text:
        return text

    seen_roots = set()
    kept_count = 0

    def _filter_emote(match):
        nonlocal kept_count
        raw_inner = match.group(1).strip()
        words = re.findall(r'[a-z]+', raw_inner.lower())
        if len(words) == 1 and words[0] in EMPHASIS_NON_EMOTE_WORDS:
            return match.group(0)

        root_key = " ".join(words[:2]) if words else raw_inner.lower()
        if root_key in seen_roots or kept_count >= max_emotes:
            return ""
        seen_roots.add(root_key)
        kept_count += 1
        return f"*{raw_inner}*"

    pruned = ASTERISK_EMOTE_EXTRACT_REGEX.sub(_filter_emote, text)
    pruned = re.sub(r'\s{2,}', ' ', pruned).strip()
    pruned = re.sub(r'\s+([,.;!?])', r'\1', pruned)
    return pruned


def clean_and_compact_reply(reply: str) -> str:
    """
    Cleans stray markdown artifacts, leaked context headers, echoed user inputs,
    and hallucinated multi-turn scripts, converts bracket emotes to asterisks,
    prunes excessive stage directions, and removes blank line spacing.
    """
    if not reply:
        return ""

    reply = ERROR_REPLY_STRIP_REGEX.sub('', reply)
    reply = LEARN_USER_STRIP_REGEX.sub('', reply)
    reply = LEARN_GENERAL_STRIP_REGEX.sub('', reply)
    reply = LEARN_STRIP_REGEX.sub('', reply)
    reply = IMAGE_DESC_STRIP_REGEX.sub('', reply)
    reply = LEAKED_CONTEXT_TAG_REGEX.sub('', reply)

    # 1. Strip any leading `[BotName]:` header at the start of the response
    reply = re.sub(
        r'^\s*\[[a-zA-Z0-9_\-\.\s]{1,40}(?:\(speaking to [^\)]+\))?\]:\s*',
        '',
        reply,
        flags=re.IGNORECASE
    ).strip()

    # 2. Strip echoed short user input prefixes like `erm : *glances up*` or `um : *sighs*`
    reply = ECHOED_INPUT_PREFIX_REGEX.sub('', reply).strip()

    # 3. Truncate any hallucinated multi-turn continuation (e.g. `[silly7722 (speaking to...)]: ...`,
    # `*Carzen: yeah they worked again* : ...`, `: Oh, come off it...`, or `[No response—...]`)
    cutoff_match = HALLUCINATED_MULTI_TURN_CUTOFF_REGEX.search(reply)
    if cutoff_match and cutoff_match.start() > 8:
        reply = reply[:cutoff_match.start()].strip()

    reply = LEAKED_CHAT_PREFIX_REGEX.sub('', reply)
    reply = re.sub(r'\*\s*\(System Note:.*?\)\s*\*?\n?', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'\*\s*\([^)]*learned (?:server fact|general fact|about).*?\)\s*\*?\n?', '', reply, flags=re.IGNORECASE)

    reply = convert_bracket_emotes_to_asterisks(reply)
    reply = _prune_excessive_inline_emotes(reply, max_emotes=2)

    reply = re.sub(r'```[a-zA-Z]*\s*```', '', reply).strip()
    reply = re.sub(r'(?m)^>\s*$', '', reply).strip()
    reply = re.sub(r'(?m)^\s*(?:\*{1,3}|_{1,3}|~{1,3})\s*$', '', reply).strip()
    reply = re.sub(r'(?:\s*\*{2,}\s*)+$', '', reply).strip()

    lines = [LEAKED_CHAT_PREFIX_REGEX.sub('', line.strip()).strip() for line in reply.split('\n') if line.strip()]
    lines = [ECHOED_INPUT_PREFIX_REGEX.sub('', line).strip() for line in lines if line]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    has_structured_list = any(
        re.match(r'^(?:\d+\.|[\-\*]\s+|```)', line) for line in lines
    )

    if has_structured_list:
        return "\n".join(lines).strip()

    if len(lines) <= 4 and sum(len(l) for l in lines) < 420:
        return " ".join(lines).strip()
    return "\n".join(lines).strip()


def parse_meme_gen_tag(raw_meme_str: str) -> tuple[str, str, str]:
    """
    Parses a [MEME_GEN: visual scene | TOP TEXT | BOTTOM TEXT] tag into
    (visual_prompt, top_text, bottom_text), gracefully handling 2-part tags,
    stripping baked-in text instructions from the visual prompt, and preventing
    placeholder/duplicate captions.
    """
    if not raw_meme_str:
        return "", "", ""

    cleaned_raw = re.sub(r'\s+', ' ', raw_meme_str.strip()).rstrip(']')
    cleaned_raw = re.sub(r'^\[?\s*(?:MEME_GEN|IMAGE_GEN)\s*:\s*', '', cleaned_raw, flags=re.IGNORECASE).strip()
    parts = [p.strip().strip('"\'') for p in cleaned_raw.split("|") if p.strip()]

    visual_prompt = parts[0] if len(parts) >= 1 else ""
    top_text = parts[1] if len(parts) >= 2 else ""
    bottom_text = parts[2] if len(parts) >= 3 else ""

    visual_prompt = re.sub(r'^(?:scene|image|visual|prompt|visual\s+scene)\s*:\s*', '', visual_prompt, flags=re.IGNORECASE).strip()
    visual_prompt = PROMPT_BAKED_TEXT_STRIP_REGEX.sub('', visual_prompt).strip()

    top_text = re.sub(r'^(?:top\s*text|top\s*caption|top|caption\s*1|text\s*1|setup)\s*:\s*', '', top_text, flags=re.IGNORECASE).strip(' "\'*')
    bottom_text = re.sub(r'^(?:bottom\s*text|bottom\s*caption|bottom|caption\s*2|text\s*2|punchline)\s*:\s*', '', bottom_text, flags=re.IGNORECASE).strip(' "\'*')

    placeholder_values = {"top text", "bottom text", "none", "n/a", "null", "empty", "no text", "blank"}
    if top_text.lower() in placeholder_values:
        top_text = ""
    if bottom_text.lower() in placeholder_values:
        bottom_text = ""

    if top_text and not bottom_text and len(top_text) > 30:
        if " / " in top_text:
            sp = top_text.split(" / ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif " - " in top_text:
            sp = top_text.split(" - ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif ": " in top_text and len(top_text) > 42:
            sp = top_text.split(": ", 1)
            if len(sp[0]) >= 8 and len(sp[1]) >= 8:
                top_text, bottom_text = sp[0].strip(), sp[1].strip()

    if top_text and bottom_text and top_text.lower() == bottom_text.lower():
        bottom_text = ""

    return visual_prompt, top_text, bottom_text
