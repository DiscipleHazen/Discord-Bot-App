import re
from config import (
    VOICE_START_REGEX, MIMO_AUDIO_TAG_MAP, URL_REGEX, IMAGE_GEN_REGEX,
    UNCLOSED_IMAGE_GEN_REGEX, GIF_STRIP_REGEX, TTS_KAOMOJI_STRIP_REGEX,
    BRACKET_EMOTE_REGEX, ERROR_REPLY_STRIP_REGEX, IMAGE_DESC_STRIP_REGEX,
    MEME_GEN_REGEX, UNCLOSED_MEME_GEN_REGEX, LEARN_GENERAL_STRIP_REGEX,
    LEARN_USER_STRIP_REGEX, LEARN_STRIP_REGEX
)

# ==============================================================================
# VOICE, TEXT & EMOTE PARSING UTILITIES
# ==============================================================================

def extract_and_strip_voice_msg(text: str) -> tuple[str | None, str]:
    """
    Safely extracts the full content of [VOICE_MSG: ...] even when nested
    brackets like [sigh] or [chuckle] are inside the voice script, and returns
    (voice_content, cleaned_reply_text).
    """
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

    speech_text = re.sub(r'\*([^*]{1,35})\*', r'[\1]', speech_text)
    speech_text = re.sub(r'\[([^\]]{1,40})\]', _replace_tag, speech_text)
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
    lines = [line.strip() for line in raw_voice_content.split('\n') if line.strip()]
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
        elif in_header_section and len(director_instructions) > 0 and not clean_line.startswith("(") and len(spoken_text_lines) == 0 and line.startswith("-"):
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
        assistant_speech_content = raw_voice_content.strip()

    assistant_speech_content = URL_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = MEME_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = UNCLOSED_MEME_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = IMAGE_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = UNCLOSED_IMAGE_GEN_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = GIF_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = sanitize_mimo_inline_tags(assistant_speech_content)
    assistant_speech_content = TTS_KAOMOJI_STRIP_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = re.sub(r'<a?:[a-zA-Z0-9_]+:\d+>', '', assistant_speech_content)
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
            "attached", "contains", "system", "replying", "addressed", "directed", "learn"
        )):
            return match.group(0)
        return f"*{inner}*"

    return BRACKET_EMOTE_REGEX.sub(_replace, text)


def clean_and_compact_reply(reply: str) -> str:
    """
    Cleans stray markdown artifacts, converts bracket emotes to asterisks,
    and removes blank line spacing so bot outputs are compact and natural.
    """
    if not reply:
        return ""

    reply = ERROR_REPLY_STRIP_REGEX.sub('', reply)
    reply = LEARN_USER_STRIP_REGEX.sub('', reply)
    reply = LEARN_GENERAL_STRIP_REGEX.sub('', reply)
    reply = LEARN_STRIP_REGEX.sub('', reply)
    reply = re.sub(
        r'\[(?:Generated Attached (?:Image\vert{}Meme\vert{}Voice Audio)\vert{}Sent (?:GIF\vert{}Image\vert{}Meme\vert{}Audio) of\vert{}System Glitch (?:Event\vert{}Context)):[^\n]*\]\n?',
        '',
        reply,
        flags=re.IGNORECASE
    )
    reply = re.sub(r'\*\s*\(System Note:.*?\)\s*\*?\n?', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'\*\s*\([^)]*learned (?:server fact\vert{}general fact\vert{}about).*?\)\s*\*?\n?', '', reply, flags=re.IGNORECASE)

    reply = convert_bracket_emotes_to_asterisks(reply)

    reply = re.sub(r'```[a-zA-Z]*\s*```', '', reply).strip()
    reply = re.sub(r'(?m)^>\s*$', '', reply).strip()
    reply = re.sub(r'(?m)^\s*(?:\*{1,3}|_{1,3}|~{1,3})\s*$', '', reply).strip()
    reply = re.sub(r'(?:\s*\*{2,}\s*)+$', '', reply).strip()

    lines = [line.strip() for line in reply.split('\n') if line.strip()]
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
    (visual_prompt, top_text, bottom_text), gracefully handling 2-part tags
    and accidental newlines.
    """
    cleaned_raw = re.sub(r'\s+', ' ', raw_meme_str.strip()).rstrip(']')
    parts = [p.strip().strip('"\'') for p in cleaned_raw.split("|") if p.strip()]

    visual_prompt = parts[0] if len(parts) >= 1 else ""
    top_text = parts[1] if len(parts) >= 2 else ""
    bottom_text = parts[2] if len(parts) >= 3 else ""

    visual_prompt = re.sub(r'^(?:scene|image|visual|prompt)\s*:\s*', '', visual_prompt, flags=re.IGNORECASE).strip()
    top_text = re.sub(r'^(?:top\s*text|top|caption\s*1)\s*:\s*', '', top_text, flags=re.IGNORECASE).strip()
    bottom_text = re.sub(r'^(?:bottom\s*text|bottom|caption\s*2)\s*:\s*', '', bottom_text, flags=re.IGNORECASE).strip()

    # If the LLM only provided 1 caption block (2 pipe parts total) and it has a natural setup/punchline split
    if top_text and not bottom_text and len(top_text) > 32:
        if " / " in top_text:
            sp = top_text.split(" / ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif " - " in top_text:
            sp = top_text.split(" - ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()

    return visual_prompt, top_text, bottom_text