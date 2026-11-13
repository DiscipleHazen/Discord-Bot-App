import re
from config import (
    VOICE_START_REGEX, MIMO_AUDIO_TAG_MAP, URL_REGEX, IMAGE_GEN_REGEX,
    UNCLOSED_IMAGE_GEN_REGEX, GIF_STRIP_REGEX, TTS_KAOMOJI_STRIP_REGEX,
    BRACKET_EMOTE_REGEX, ASTERISK_EMOTE_EXTRACT_REGEX, ERROR_REPLY_STRIP_REGEX,
    IMAGE_DESC_STRIP_REGEX, MEME_GEN_REGEX, UNCLOSED_MEME_GEN_REGEX,
    LEARN_GENERAL_STRIP_REGEX, LEARN_USER_STRIP_REGEX, LEARN_STRIP_REGEX,
    MEME_PLACEHOLDER_REGEX, INTENTIONAL_NO_RESPONSE_REGEX
)

# ==============================================================================
# PRE-COMPILED VOICE, TEXT, CADENCE & EMOTE PARSING REGEXES
# ==============================================================================

EMPHASIS_NON_EMOTE_WORDS = frozenset({
    "not", "never", "really", "very", "so", "actually", "literally",
    "always", "all", "you", "me", "this", "that", "what", "why", "how",
    "everything", "nothing", "everyone", "no", "yes", "now", "here"
})

VALID_MIMO_PREFIXES = (
    "inhale", "take a deep breath", "sigh", "let out a long sigh", "pant",
    "hold one's breath", "nervous", "scared", "excited", "tired", "wronged",
    "coquettish", "guilty", "shocked", "impatient", "trembling", "voice trembling",
    "pitch change", "cracked voice", "nasal voice", "breathiness", "hoarseness",
    "smile", "chuckle", "laugh out loud", "sneer", "sob", "whimper", "choke",
    "wail", "pause", "pauses for a moment", "cough", "softly", "whisper"
)

LEADING_BOT_HEADER_REGEX = re.compile(
    r'^\s*\[[a-zA-Z0-9_\-\.\s]{1,40}(?:\(speaking to [^\)]+\))?\]:\s*',
    re.IGNORECASE
)

LEAKED_CHAT_PREFIX_REGEX = re.compile(
    r'(?m)^\s*\[[a-zA-Z0-9_\-\.\s]{1,40}(?:\(speaking to [^\)]+\))?\]:\s*',
    re.IGNORECASE
)

# Detects when the LLM hallucinates a second speaker turn in the middle of its reply
HALLUCINATED_MULTI_TURN_CUTOFF_REGEX = re.compile(
    r'(?:'
    r'\n\s*\[[a-zA-Z0-9_\-\.\s]{2,40}(?:\(speaking to [^\)]+\))?\]:'
    r'|\s+\[[a-zA-Z0-9_\-\.\s]{2,40}\(speaking to [^\)]+\)\]:'
    r'|\s+\*?[A-Z][a-zA-Z0-9_\-]{1,25}:\s+[^:\n]{2,80}?\*?\s*:\s+'
    r'|\s+:\s+(?:Oh\b|Clearly\b|Honestly\b|Wait\b|Well\b|Look\b|Please\b|No\b|Yes\b|I\b|You\b|\*[a-zA-Z])'
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
    r'Linked Webpage:[^\]\n]*|'
    r'System (?:Visual|Context|Glitch|Time)[^\]\n]*|'
    r'Generated Attached (?:Image|Meme|Voice Audio)[^\]\n]*|'
    r'Sent (?:GIF|Image|Meme|Audio) of[^\]\n]*)\]\n?',
    re.IGNORECASE
)

TRAILING_META_NOTE_REGEX = re.compile(
    r'(?:\s*\((?:Note|OOC|Meta|Author\'?s?\s*Note|System|Internal\s*Thought)\s*:[^\)]*\)\s*'
    r'|\s*\[(?:Note|OOC|Meta|Internal\s*Thought)\s*:[^\]]*\]\s*)+$',
    re.IGNORECASE
)

# Matches lines formatted like novel manuscripts where all spoken segments are wrapped in quotes around *emotes*
NOVEL_QUOTED_LINE_REGEX = re.compile(
    r'^(?:\s*(?:\*[^*]+\*|"[^"\n]+"|“[^”\n]+”)[,.;!]?\s*)+$'
)
STRAIGHT_QUOTED_SEGMENT_REGEX = re.compile(r'"([^"\n]+)"')
CURLY_QUOTED_SEGMENT_REGEX = re.compile(r'“([^”\n]+)”')
UNCLOSED_TRAILING_EMOTE_REGEX = re.compile(r'(?<!\*)\*([a-zA-Z][a-zA-Z\s,\'\-]{2,35})$')

PROMPT_BAKED_TEXT_STRIP_REGEX = re.compile(
    r'(?:,\s*|\s+)(?:with\s+(?:bold\s+|white\s+|impact\s+|top\s+|bottom\s+)*(?:text|caption|captions|words|lettering)\s+'
    r'(?:reading|saying|that\s+says)\s*[\'"][^\'"]+[\'"])',
    re.IGNORECASE
)

# Detects non-voice media or learning tags accidentally nested inside or after an unclosed [VOICE_MSG: ...] block
RESCUABLE_EMBEDDED_TAG_REGEX = re.compile(
    r'\[\s*(?:IMAGE_GEN|MEME_GEN|GIF|LEARN_USER|LEARN_GENERAL(?:_FACT|_KNOWLEDGE)?|LEARN(?:_SERVER|_LORE|_FACT)?)\s*[|:]\s*[^\]]+\]?',
    re.IGNORECASE | re.DOTALL
)

HTML_TAG_STRIP_REGEX = re.compile(r'<[^>]+>')
SYSTEM_NOTE_STRIP_REGEX = re.compile(r'\*\s*\(System Note:.*?\)\s*\*?\n?', re.IGNORECASE)
LEARNED_NOTE_STRIP_REGEX = re.compile(
    r'\*\s*\([^)]*learned (?:server fact|general fact|about).*?\)\s*\*?\n?',
    re.IGNORECASE
)
EMPTY_CODEBLOCK_REGEX = re.compile(r'```[a-zA-Z]*\s*```')
EMPTY_BLOCKQUOTE_REGEX = re.compile(r'(?m)^>\s*$')
STRAY_MARKDOWN_LINE_REGEX = re.compile(r'(?m)^\s*(?:\*{1,3}|_{1,3}|~{1,3})\s*$')
TRAILING_ASTERISKS_REGEX = re.compile(r'(?:\s*\*{2,}\s*)+$')
STRUCTURED_LIST_LINE_REGEX = re.compile(r'^(?:\d+\.|[\-\*]\s+|```)')
LEADING_ACTION_EMOTE_LINE_REGEX = re.compile(r'^\*[a-zA-Z][^*\n]{2,55}\*\s+\S+')
DISCORD_CUSTOM_EMOJI_REGEX = re.compile(r'<a?:[a-zA-Z0-9_]+:\d+>')
MULTI_SPACE_REGEX = re.compile(r'\s{2,}')
SPACE_BEFORE_PUNCT_REGEX = re.compile(r'\s+([,.;!?])')
INLINE_ASTERISK_ACTION_REGEX = re.compile(r'\*([^*]{1,40})\*')
INLINE_BRACKET_ACTION_REGEX = re.compile(r'\[([^\]]{1,45})\]')

SCENE_PREFIX_REGEX = re.compile(r'^(?:scene|image|visual|prompt|visual\s+scene)\s*:\s*', re.IGNORECASE)
TOP_CAPTION_PREFIX_REGEX = re.compile(
    r'^(?:short\s+)?(?:top\s*text|top\s*caption|top|caption\s*1|text\s*1|setup)\s*:\s*',
    re.IGNORECASE
)
BOTTOM_CAPTION_PREFIX_REGEX = re.compile(
    r'^(?:short\s+)?(?:bottom\s*text|bottom\s*caption|bottom\s*punchline|bottom|caption\s*2|text\s*2|punchline)\s*:\s*',
    re.IGNORECASE
)

MEME_PLACEHOLDER_SET = frozenset({
    "top text", "bottom text", "top caption", "bottom caption",
    "short top caption", "short bottom punchline", "bottom punchline",
    "short top text", "short bottom text", "caption 1", "caption 2",
    "text 1", "text 2", "setup", "punchline", "insert top text",
    "insert bottom text", "insert caption", "none", "n/a", "null",
    "empty", "no text", "no caption", "blank"
})


def sanitize_api_error_text(raw_err: str, fallback: str = "Provider busy") -> str:
    """
    Cleans raw upstream HTTP/API error messages (stripping HTML tags from Cloudflare
    502/504 pages and replacing inner parentheses) so wrapped error notices like
    `*(Voice generation error 502: ...)*` never leak raw HTML or break ERROR_REPLY_STRIP_REGEX.
    """
    if not raw_err or not isinstance(raw_err, str):
        return fallback
    cleaned = HTML_TAG_STRIP_REGEX.sub(' ', raw_err)
    cleaned = cleaned.replace("(", "[").replace(")", "]").replace("*", "")
    cleaned = MULTI_SPACE_REGEX.sub(' ', cleaned.replace("\n", " ").replace("\r", " ")).strip()
    if not cleaned or len(cleaned) < 2:
        return fallback
    return cleaned[:95].strip()


def _strip_outer_novel_quotes(line: str) -> str:
    """
    Removes novel-style dialogue quotation marks when an LLM wraps its entire
    spoken response (or every spoken segment around *emotes*) in double quotes,
    while preserving legitimate inline quotes inside normal sentences.
    """
    if not line or ('"' not in line and '“' not in line):
        return line
    stripped = line.strip()
    if NOVEL_QUOTED_LINE_REGEX.match(stripped):
        dequoted = STRAIGHT_QUOTED_SEGMENT_REGEX.sub(r'\1', stripped)
        dequoted = CURLY_QUOTED_SEGMENT_REGEX.sub(r'\1', dequoted)
        return MULTI_SPACE_REGEX.sub(' ', dequoted).strip()
    return line


def _heal_unclosed_trailing_emote(line: str) -> str:
    """Closes a trailing unclosed single-asterisk emote at the end of a line (e.g. `*tilts head` -> `*tilts head*`)."""
    if not line or "*" not in line:
        return line
    single_star_count = len(re.findall(r'(?<!\*)\*(?!\*)', line))
    if single_star_count % 2 == 1:
        if UNCLOSED_TRAILING_EMOTE_REGEX.search(line):
            return f"{line.rstrip()}*"
    return line


def extract_and_strip_voice_msg(text: str) -> tuple[str | None, str]:
    """
    Safely extracts the full content of [VOICE_MSG: ...] even when nested
    brackets like [sigh] or [chuckle] are inside the voice script, rescues any
    trailing [IMAGE_GEN:], [MEME_GEN:], [GIF:], or [LEARN*:] tags that were
    swallowed by an unclosed [VOICE_MSG: block, and returns (voice_content, cleaned_reply_text).
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

    # Rescue any media or learning tags ([IMAGE_GEN:], [MEME_GEN:], [GIF:], [LEARN*:])
    # that the LLM accidentally placed inside or after an unclosed [VOICE_MSG: ...] block
    rescued_tags = []
    if voice_content:
        for m_tag in RESCUABLE_EMBEDDED_TAG_REGEX.finditer(voice_content):
            tag_str = m_tag.group(0).strip()
            if not tag_str.endswith("]"):
                tag_str += "]"
            rescued_tags.append(tag_str)
        if rescued_tags:
            voice_content = RESCUABLE_EMBEDDED_TAG_REGEX.sub('', voice_content).strip()
            cleaned_text = f"{cleaned_text} {' '.join(rescued_tags)}".strip()

    cleaned_text = re.sub(r'`+\s*`+', '', cleaned_text).strip()
    cleaned_text = re.sub(r'`+$', '', cleaned_text).strip()
    return (voice_content if voice_content else None), cleaned_text


def sanitize_mimo_inline_tags(speech_text: str) -> str:
    """
    Normalizes inline [action] or *action* tags inside spoken TTS text into
    valid MiMo-V2.5-TTS audio control tags, removing non-audio roleplay actions
    (like *tail flicks* or *adjusts glasses*) so the TTS actor doesn't glitch.
    """
    def _replace_tag(match):
        raw_tag = match.group(1).strip().lower()
        if raw_tag in MIMO_AUDIO_TAG_MAP:
            return f"[{MIMO_AUDIO_TAG_MAP[raw_tag]}]"
        if any(raw_tag.startswith(p) for p in VALID_MIMO_PREFIXES):
            return f"[{raw_tag}]"
        return ""

    speech_text = INLINE_ASTERISK_ACTION_REGEX.sub(r'[\1]', speech_text)
    speech_text = INLINE_BRACKET_ACTION_REGEX.sub(_replace_tag, speech_text)
    return speech_text


def parse_mimotts_voice_block(
    raw_voice_content: str,
    default_role_desc: str,
    default_style: str = "Natural"
) -> tuple[str, str]:
    """
    Separates Director Mode headers (Role, Scene, Guidance) from the spoken dialogue
    for MiMo-V2.5-TTS-VoiceDesign, cleaning out unpronounceable chat kaomoji and URLs.
    Returns (user_prompt_content, assistant_speech_content). If no pronounceable spoken
    words remain after stripping tags, `assistant_speech_content` is returned as `""`.
    """
    if not raw_voice_content or not raw_voice_content.strip():
        return default_role_desc, ""

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
        last_dir = director_instructions[-1]
        if not last_dir.lower().startswith(("role:", "character:", "scene:", "guidance:")):
            assistant_speech_content = last_dir
            director_instructions = director_instructions[:-1]
            user_prompt_content = "\n".join(director_instructions).strip()

    if not user_prompt_content:
        user_prompt_content = default_role_desc
    if not assistant_speech_content and not director_instructions:
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
    assistant_speech_content = DISCORD_CUSTOM_EMOJI_REGEX.sub('', assistant_speech_content)
    assistant_speech_content = re.sub(r'[\*_`~]+', '', assistant_speech_content)
    assistant_speech_content = MULTI_SPACE_REGEX.sub(' ', assistant_speech_content).strip()

    # Check if any actual spoken words exist outside of (Style) and [audio] tags
    spoken_words_check = re.sub(r'\([^\)]*\)|\[[^\]]*\]', '', assistant_speech_content).strip()
    if not re.search(r'[a-zA-Z0-9]', spoken_words_check):
        return user_prompt_content, ""

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
    if not spoken:
        return ""
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
            "learn", "no response", "linked webpage", "note:", "ooc:", "meta:"
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
        em = MULTI_SPACE_REGEX.sub(' ', m.group(1).strip().lower())
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
    into a single short line) by keeping at most `max_emotes` distinct actions per turn,
    collapsing back-to-back adjacent emotes, and removing duplicate emote stems.
    """
    if not text or "*" not in text:
        return text

    # Collapse back-to-back adjacent action emotes like `*smirks* *rolls eyes*` -> `*smirks*`
    text = re.sub(
        r'(\*[a-zA-Z][^*\n]{2,45}\*)\s+\*[a-zA-Z][^*\n]{2,45}\*',
        r'\1',
        text
    )

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
    pruned = MULTI_SPACE_REGEX.sub(' ', pruned).strip()
    pruned = SPACE_BEFORE_PUNCT_REGEX.sub(r'\1', pruned)
    return pruned


def clean_and_compact_reply(reply: str) -> str:
    """
    Cleans stray markdown artifacts, leaked context headers, echoed user inputs,
    novel-style outer quotation marks, hallucinated multi-turn scripts, and
    self-cloned second turns, converts bracket emotes to asterisks, prunes
    excessive stage directions, and compacts spacing.
    """
    if not reply:
        return ""

    if INTENTIONAL_NO_RESPONSE_REGEX.search(reply) and len(reply.strip()) < 95:
        return ""

    reply = ERROR_REPLY_STRIP_REGEX.sub('', reply)
    reply = LEARN_USER_STRIP_REGEX.sub('', reply)
    reply = LEARN_GENERAL_STRIP_REGEX.sub('', reply)
    reply = LEARN_STRIP_REGEX.sub('', reply)
    reply = IMAGE_DESC_STRIP_REGEX.sub('', reply)
    reply = LEAKED_CONTEXT_TAG_REGEX.sub('', reply)
    reply = TRAILING_META_NOTE_REGEX.sub('', reply)

    # 1. Strip any leading `[BotName]:` header at the start of the response
    reply = LEADING_BOT_HEADER_REGEX.sub('', reply).strip()

    # 2. Strip echoed short user input prefixes like `erm : *glances up*` or `um : *sighs*`
    reply = ECHOED_INPUT_PREFIX_REGEX.sub('', reply).strip()

    # 3. Truncate any hallucinated multi-turn continuation
    cutoff_match = HALLUCINATED_MULTI_TURN_CUTOFF_REGEX.search(reply)
    if cutoff_match and cutoff_match.start() > 8:
        reply = reply[:cutoff_match.start()].strip()

    reply = LEAKED_CHAT_PREFIX_REGEX.sub('', reply)
    reply = SYSTEM_NOTE_STRIP_REGEX.sub('', reply)
    reply = LEARNED_NOTE_STRIP_REGEX.sub('', reply)

    reply = convert_bracket_emotes_to_asterisks(reply)

    reply = EMPTY_CODEBLOCK_REGEX.sub('', reply).strip()
    reply = EMPTY_BLOCKQUOTE_REGEX.sub('', reply).strip()
    reply = STRAY_MARKDOWN_LINE_REGEX.sub('', reply).strip()
    reply = TRAILING_ASTERISKS_REGEX.sub('', reply).strip()

    raw_lines = [LEAKED_CHAT_PREFIX_REGEX.sub('', line.strip()).strip() for line in reply.split('\n') if line.strip()]
    cleaned_lines = []
    for ln in raw_lines:
        ln = ECHOED_INPUT_PREFIX_REGEX.sub('', ln).strip()
        ln = _strip_outer_novel_quotes(ln)
        ln = _heal_unclosed_trailing_emote(ln)
        if ln:
            cleaned_lines.append(ln)

    if not cleaned_lines:
        return ""

    has_structured_list = any(STRUCTURED_LIST_LINE_REGEX.match(line) for line in cleaned_lines)

    # 4. Prevent "two-turn self-contradiction" (e.g. Line 1 apologizes and pivots topic,
    # and Line 2 starts with a brand-new `*emote*` repeating the old joke as a second turn)
    if not has_structured_list and len(cleaned_lines) >= 2:
        deduped_lines = [cleaned_lines[0]]
        for ln in cleaned_lines[1:]:
            if len(deduped_lines[0]) >= 25 and LEADING_ACTION_EMOTE_LINE_REGEX.match(ln):
                break
            deduped_lines.append(ln)
        cleaned_lines = deduped_lines

    combined = "\n".join(cleaned_lines).strip() if has_structured_list else (
        " ".join(cleaned_lines).strip() if (len(cleaned_lines) <= 4 and sum(len(l) for l in cleaned_lines) < 420) else "\n".join(cleaned_lines).strip()
    )

    combined = _strip_outer_novel_quotes(combined)
    return _prune_excessive_inline_emotes(combined, max_emotes=2)


def _is_placeholder_caption(text: str) -> bool:
    """Returns True if a meme caption is just a literal template placeholder like 'TOP CAPTION'."""
    if not text:
        return True
    cleaned = text.strip().strip('"\'*[]<>()').strip().lower()
    cleaned = MULTI_SPACE_REGEX.sub(' ', cleaned)
    if not cleaned:
        return True
    if cleaned in MEME_PLACEHOLDER_SET:
        return True
    if MEME_PLACEHOLDER_REGEX.match(cleaned):
        return True
    return False


def parse_meme_gen_tag(raw_meme_str: str) -> tuple[str, str, str]:
    """
    Parses a [MEME_GEN: visual scene | TOP TEXT | BOTTOM TEXT] tag into
    (visual_prompt, top_text, bottom_text), gracefully handling 2-part tags,
    stripping baked-in text instructions from the visual prompt, and blocking
    any template placeholder strings like 'TOP CAPTION' or 'SHORT BOTTOM PUNCHLINE'.
    """
    if not raw_meme_str:
        return "", "", ""

    cleaned_raw = re.sub(r'\s+', ' ', raw_meme_str.strip()).rstrip(']')
    cleaned_raw = re.sub(r'^\[?\s*(?:MEME_GEN|IMAGE_GEN)\s*:\s*', '', cleaned_raw, flags=re.IGNORECASE).strip()
    # Strip any trailing nested [GIF: ...] or [IMAGE_GEN: ...] tag if the LLM concatenated two tags
    cleaned_raw = re.split(r'\[\s*(?:GIF|IMAGE_GEN|VOICE_MSG)\s*:', cleaned_raw, flags=re.IGNORECASE)[0].strip()

    parts = [p.strip().strip('"\'') for p in cleaned_raw.split("|") if p.strip()]

    visual_prompt = parts[0] if len(parts) >= 1 else ""
    top_text = parts[1] if len(parts) >= 2 else ""
    bottom_text = parts[2] if len(parts) >= 3 else ""

    visual_prompt = SCENE_PREFIX_REGEX.sub('', visual_prompt).strip()
    visual_prompt = PROMPT_BAKED_TEXT_STRIP_REGEX.sub('', visual_prompt).strip()

    top_text = TOP_CAPTION_PREFIX_REGEX.sub('', top_text).strip(' "\'*[]<>')
    bottom_text = BOTTOM_CAPTION_PREFIX_REGEX.sub('', bottom_text).strip(' "\'*[]<>')

    if _is_placeholder_caption(top_text):
        top_text = ""
    if _is_placeholder_caption(bottom_text):
        bottom_text = ""

    # If the LLM only provided 1 valid caption block and it has a natural setup/punchline split
    if top_text and not bottom_text and len(top_text) > 26:
        if " / " in top_text:
            sp = top_text.split(" / ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif " - " in top_text:
            sp = top_text.split(" - ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif " — " in top_text:
            sp = top_text.split(" — ", 1)
            top_text, bottom_text = sp[0].strip(), sp[1].strip()
        elif ": " in top_text and len(top_text) > 38:
            sp = top_text.split(": ", 1)
            if len(sp[0]) >= 6 and len(sp[1]) >= 6:
                top_text, bottom_text = sp[0].strip(), sp[1].strip()

    if _is_placeholder_caption(top_text):
        top_text = ""
    if _is_placeholder_caption(bottom_text):
        bottom_text = ""

    if top_text and bottom_text and top_text.lower() == bottom_text.lower():
        bottom_text = ""

    return visual_prompt, top_text, bottom_text
