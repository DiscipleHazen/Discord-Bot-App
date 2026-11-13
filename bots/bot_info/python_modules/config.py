import re

# ==============================================================================
# PRE-COMPILED REGEXES & CONSTANTS
# ==============================================================================
YT_REGEX = re.compile(
    r'(https?://)?((?:www|m|music)\.)?'
    r'(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/|youtube\.com/embed/)'
    r'([\w-]+)',
    re.IGNORECASE
)
TENOR_REGEX = re.compile(r'tenor\.com/(?:view|[a-z]{2}/view)/([a-zA-Z0-9-]+)-gif', re.IGNORECASE)
GIPHY_REGEX = re.compile(r'giphy\.com/(?:gifs|stickers)/(?:[a-zA-Z0-9]+-)*([a-zA-Z0-9-]+)', re.IGNORECASE)
KLIPY_REGEX = re.compile(r'klipy\.com/gifs/([a-zA-Z0-9-]+)', re.IGNORECASE)
DIRECT_GIF_URL_REGEX = re.compile(
    r'https?://\S+/([a-zA-Z0-9_\-]{3,})\.(?:gif|webp|mp4|webm)(?:\?\S*)?',
    re.IGNORECASE
)
URL_REGEX = re.compile(r'https?://\S+')
MARKDOWN_URL_WRAP_REGEX = re.compile(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)')

OG_VIDEO_REGEX_1 = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:video|og:video:secure_url|og:video:url|twitter:player:stream)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
OG_VIDEO_REGEX_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:video|og:video:secure_url|og:video:url|twitter:player:stream)["\']',
    re.IGNORECASE
)
OG_IMAGE_REGEX_1 = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image|og:image:url|og:image:secure_url)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
OG_IMAGE_REGEX_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image|og:image:url|og:image:secure_url)["\']',
    re.IGNORECASE
)

# ==============================================================================
# CONTEXT, MEMORY & HEARSAY EXTRACTION
# ==============================================================================
LEARN_USER_EXTRACT_REGEX = re.compile(
    r'(?:!LEARN_USER|\[LEARN_USER)\s*[|:]\s*([^\|\n]+)\|\s*([^\]\n]+)\]?',
    re.IGNORECASE
)
LEARN_USER_STRIP_REGEX = re.compile(
    r'(?:!LEARN_USER|\[LEARN_USER)\s*[|:]\s*[^\|\n]+\|\s*[^\]\n]*\]?',
    re.IGNORECASE
)
LEARN_GENERAL_EXTRACT_REGEX = re.compile(
    r'(?:!LEARN_GENERAL(?:_FACT|_KNOWLEDGE)?|\[LEARN_GENERAL(?:_FACT|_KNOWLEDGE)?)\s*[|:]\s*([^\]\n]+)\]?',
    re.IGNORECASE
)
LEARN_GENERAL_STRIP_REGEX = re.compile(
    r'(?:!LEARN_GENERAL(?:_FACT|_KNOWLEDGE)?|\[LEARN_GENERAL(?:_FACT|_KNOWLEDGE)?)\s*[|:]\s*[^\]\n]*\]?',
    re.IGNORECASE
)
LEARN_EXTRACT_REGEX = re.compile(
    r'(?:!LEARN(?:_SERVER|_LORE|_FACT)?|\[LEARN(?:_SERVER|_LORE|_FACT)?)(?!_USER|_GENERAL)\s*[|:]\s*([^\]\n]+)\]?',
    re.IGNORECASE
)
LEARN_STRIP_REGEX = re.compile(
    r'(?:!LEARN(?:_SERVER|_LORE|_FACT)?|\[LEARN(?:_SERVER|_LORE|_FACT)?)(?!_USER|_GENERAL)\s*[|:]\s*[^\]\n]*\]?',
    re.IGNORECASE
)

# Hearsay & Reported Speech Detection
THIRD_PARTY_REPORT_REGEX = re.compile(
    r'\b(?:'
    r'(?:i\s+(?:said|told\s+\w+|mentioned|heard|read|saw)\s+(?:that\s+)?)?'
    r'(?:they|them|he|him|she|her|someone|somebody|people|everyone|bro|dude|guy|girl|chat|'
    r'my\s+(?:friend|friends|coworker|boss|brother|sister|mom|dad|ex|roommate|partner|wife|husband|bf|gf|cousin|uncle|aunt)|'
    r'a\s+(?:friend|guy|girl|person|user)|@[a-zA-Z0-9_\-\.]+)\s+'
    r'(?:said|says|say|told\s+(?:me|us|him|her|them)|claimed|claims|mentioned|thinks|thought|asked|wrote|posted|kept\s+saying|was\s+saying|were\s+saying)'
    r'|according\s+to\s+(?:them|him|her|someone|@[a-zA-Z0-9_\-\.]+)'
    r'|quoting\s+(?:them|him|her|someone)'
    r')\b',
    re.IGNORECASE
)
FACT_HEARSAY_PREFIX_REGEX = re.compile(
    r'\b(?:said\s+that\s+(?:they|he|she|someone)|told\s+\w+\s+that\s+(?:they|he|she)|'
    r'(?:they|he|she|someone)\s+(?:said|told|claimed|thinks|thought))\b',
    re.IGNORECASE
)

# ==============================================================================
# MEDIA EXTRACTION, CLEANSING & ANTI-REPETITION PATTERNS
# ==============================================================================
MEME_GEN_REGEX = re.compile(r'\[\s*MEME_GEN\s*:\s*(.*?)\s*\]', re.IGNORECASE | re.DOTALL)
UNCLOSED_MEME_GEN_REGEX = re.compile(r'\[\s*MEME_GEN\s*:\s*([^\]]{10,})$', re.IGNORECASE | re.DOTALL)
IMAGE_GEN_REGEX = re.compile(r'\[\s*IMAGE_GEN\s*:\s*(.*?)\s*\]', re.IGNORECASE | re.DOTALL)
UNCLOSED_IMAGE_GEN_REGEX = re.compile(r'\[\s*IMAGE_GEN\s*:\s*([^\]]{10,})$', re.IGNORECASE | re.DOTALL)
FALLBACK_IMAGE_NOTE_REGEX = re.compile(
    r'\*\(System Note:[^)]*successfully attached a(?:n image| meme) of [\'"](.+?)[\'"]\)\*',
    re.IGNORECASE | re.DOTALL
)
VOICE_START_REGEX = re.compile(r'\[\s*VOICE_MSG\s*:\s*', re.IGNORECASE)
GIF_EXTRACT_REGEX = re.compile(r'\[\s*GIF\s*:\s*(.*?)\s*\]', re.IGNORECASE)
UNCLOSED_GIF_EXTRACT_REGEX = re.compile(r'\[\s*GIF\s*:\s*([^\]]{2,40})$', re.IGNORECASE)
GIF_STRIP_REGEX = re.compile(r'\[\s*GIF\s*:\s*.*?(?:\]|$)', re.IGNORECASE)
JSON_EMOJI_REGEX = re.compile(r'\{.*?\}', re.DOTALL)
EMOJI_REACT_STRIP_REGEX = re.compile(r'\[Emoji reaction:[^\n]*\]\n?', re.IGNORECASE)
IMAGE_DESC_STRIP_REGEX = re.compile(r'\[(?:Image|Attached Media|Media)\s*description[^\n]*\]\n?', re.IGNORECASE)
IMAGE_DESC_EXTRACT_REGEX = re.compile(r'\[(?:Image|Attached Media|Media)\s*description[^\:]*:\s*([^\n]+)\]', re.IGNORECASE)
INTERNAL_REACT_STRIP_REGEX = re.compile(
    r'(?:\[|\*|\b)?internally reacts? (?:to|with)[\s\S]*?(?:\]|\*|\n|$)',
    re.IGNORECASE
)
REACT_NOTE_EXTRACT_REGEX = re.compile(
    r'System Note:\s*(.+?)\s*silently reacted to .+ with (.+?)\.\s*Do not announce',
    re.IGNORECASE
)

# Strips any saved or echoed API/System error notices
ERROR_REPLY_STRIP_REGEX = re.compile(
    r'\*\((?:API Error [^\)]+|System:[^\)]+|Brain empty[^\)]*|(?:Image|Meme|Voice) generation [^\)]+)\)\*',
    re.IGNORECASE
)
ERROR_REPLY_CAPTURE_REGEX = re.compile(
    r'\*\(((?:API Error [^\)]+|System:[^\)]+|Brain empty[^\)]*|(?:Image|Meme|Voice) generation [^\)]+))\)\*',
    re.IGNORECASE
)

# Bracket emote conversion & Asterisk emote extraction (for anti-repetition tracking)
BRACKET_EMOTE_REGEX = re.compile(r'\[([a-zA-Z][a-zA-Z0-9\s,\'\-\.~!]{1,45})\](?!\()')
ASTERISK_EMOTE_EXTRACT_REGEX = re.compile(r'\*([a-zA-Z][^*\n]{2,55})\*')

# Explicit user media intent detection (handles natural phrasing & typos like "generate and image of")
EXPLICIT_MEME_REQ_REGEX = re.compile(
    r'\b(generate|make|create|cook|drop|send|give|show|render|draw)\b[^\n]{0,50}\b(meme|memes|shitpost)\b'
    r'|\b(meme\s+(?:of|about|for|when|where))\b',
    re.IGNORECASE
)
EXPLICIT_IMAGE_REQ_REGEX = re.compile(
    r'\b(generate|draw|render|make|create|show|give|send|paint|sketch|conjure|summon|snap|take|wheres|where is|still no)\b'
    r'(?:\s+(?:me|us|a|an|and|some|the|another))?[^\n]{0,50}'
    r'\b(image|pic|picture|art|artwork|photo|photograph|render|drawing|painting|portrait|selfie|loading screen|illustration|wallpaper|visual|scene)\b'
    r'|\b(image|pic|picture|portrait|drawing|painting|selfie|illustration)\s+(?:of|for|showing|with)\b'
    r'|\b(?:draw|paint|sketch)\s+(?:me\s+|us\s+)?(?:a|an|some|the)?\s*[a-z0-9]{3,}',
    re.IGNORECASE
)
USER_SUBJECT_EXTRACT_REGEX = re.compile(
    r'(?:(?:generate|draw|render|make|create|show|give|send|paint|sketch|conjure|summon)'
    r'(?:\s+(?:me|us))?(?:\s+(?:a|an|and|the|another))?\s+'
    r'(?:image|pic|picture|art|photo|render|drawing|painting|portrait|illustration|meme)\s+(?:of|about|for|showing|with)\s+)'
    r'(.+)$',
    re.IGNORECASE | re.DOTALL
)
BOT_IMAGE_PROMISE_REGEX = re.compile(
    r'\b(i\'ll generate|i generated|one rendering of|here\'s the (?:image|pic|picture|render|drawing|portrait|meme)|'
    r'loading screen i generated|pull it out of the aether|give you one that\'s technically accurate|'
    r'let\'s see what we\'re working with|behold[—\-\s]|points\s+at\s+|hologram\s+flickers\s+to\s+life|'
    r'look\s+at\s+this\s+|see\?\s+certified)\b'
    r'|\*(?:raises\s+wand\s+dramatically|conjures\s+a\s+vision|snaps\s+a\s+photo|projects\s+a\s+hologram)\*',
    re.IGNORECASE
)
EXPLICIT_VOICE_REQ_REGEX = re.compile(
    r'\b(generate|send|make|record|do|give|drop)\b[^\n]{0,45}\b(voice|audio|voice clip|voice message|voice note|speech|tts|voiceline)\b'
    r'|\b(say out loud|speak to me|talk to me|voice clip|hear your voice|say that in voice|read that out loud)\b',
    re.IGNORECASE
)
EXPLICIT_GIF_REQ_REGEX = re.compile(
    r'\b(send|post|drop|show|give)\b[^\n]{0,30}\b(gif|reaction gif|meme clip)\b',
    re.IGNORECASE
)

# High-engagement conversational hooks (used to modulate spontaneous human-like chime-ins & reactions)
TOPIC_INTEREST_CUES_REGEX = re.compile(
    r'(?:\?\s*$|\b(?:what do (?:you|yall|you guys) think|thoughts on|hot take|unpopular opinion|rate my|look at this|check this out|anyone else|who here|am i crazy|be honest)\b)',
    re.IGNORECASE
)

# Stock/cliche meme punchlines that bots must not repeat across turns
OVERUSED_MEME_CAPTIONS = {
    "absolutely unacceptable",
    "bottom text",
    "top text",
    "when a peasant compares you to that pink catchphrase disaster",
    "when someone offers you a green apple after your daily limit"
}

# Dynamic human-like spontaneity micro-impulses (injected lightly to keep bots organic, unpredictable & lifelike)
SPONTANEOUS_MICRO_MOMENTS = [
    "React like a real person hanging out in Discord: jump straight into your point or reaction without a formulaic opening.",
    "Notice a specific, quirky detail in what was just said or shown and riff on that exact detail instead of giving a generic summary.",
    "Keep your cadence punchy and spontaneous—skip repetitive physical stage directions and let your word choice carry your personality.",
    "If the moment calls for it, playfully challenge, tease, or ask a sharp follow-up question back to the speaker like a real friend/rival in chat.",
    "Vary your rhythm: you don't need a three-part monologue every turn; a sharp, natural 1-2 sentence remark or spontaneous visual observation feels much more alive.",
    "Connect the current message to the ongoing vibe or a recent joke in the channel if it fits naturally, without forcing it.",
    "Express a genuine, in-the-moment mood shift (amused, skeptical, intrigued, unimpressed, or hyped) based on the exact topic being discussed."
]

# Kaomoji/Emoticons to strip from TTS
TTS_KAOMOJI_STRIP_REGEX = re.compile(
    r'(?:>w<|>_<|\^w\^|\^_\^|:3|;3|OwO|UwU|owo|uwu|TwT|T_T|-_-|o_O|O_o|~+|٩\(◕‿◕｡\)۶|\(•‿•\)|\(´･ω･`\)|\\\(\^o\^\)/)',
    re.IGNORECASE
)

# Map common roleplay emotes inside [VOICE_MSG:] to official MiMo audio control tags
MIMO_AUDIO_TAG_MAP = {
    "giggle": "chuckle",
    "giggles": "chuckle",
    "snicker": "sneer",
    "snickers": "sneer",
    "chuckles": "chuckle",
    "laugh": "laugh out loud",
    "laughs": "laugh out loud",
    "cackle": "laugh out loud",
    "cackles": "laugh out loud",
    "sighs": "sigh",
    "long sigh": "let out a long sigh",
    "sighs deeply": "let out a long sigh",
    "heavy sigh": "let out a long sigh",
    "groan": "let out a long sigh",
    "groans": "let out a long sigh",
    "yawn": "let out a long sigh",
    "yawns": "let out a long sigh",
    "gasp": "inhale",
    "gasps": "inhale",
    "inhales": "inhale",
    "sniffs": "inhale",
    "deep breath": "take a deep breath",
    "takes a deep breath": "take a deep breath",
    "pause": "pauses for a moment",
    "pauses": "pauses for a moment",
    "beat": "pauses for a moment",
    "hesitates": "pauses for a moment",
    "coughs": "cough",
    "clears throat": "cough",
    "ahem": "cough",
    "scoff": "sneer",
    "scoffs": "sneer",
    "sneers": "sneer",
    "whimpers": "whimper",
    "sobs": "sob",
    "cries": "sob",
    "panting": "pant",
    "pants": "pant",
    "out of breath": "pant",
    "shiver": "voice trembling",
    "shivers": "voice trembling",
    "trembles": "voice trembling",
    "whispers": "softly",
    "mutters": "softly",
    "mumbles": "softly",
}

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "but", "is", "are", "was", "were", "be", "been", "being", "standing", "sitting",
    "holding", "looking", "wearing", "next", "beside", "front", "background", "style",
    "shot", "view", "close", "up", "glowing", "dark", "light", "lighting", "image",
    "photo", "generated", "showing", "attached", "media", "description", "when", "that",
    "this", "from", "into", "over", "under", "bot", "said", "told", "says", "think",
    "thinks", "thought", "about", "what", "they", "them", "their", "have", "has", "had"
}

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB safety cap

FALLBACK_VISION_MODELS = [
    "Qwen/Qwen2.5-VL-32B-Instruct",
    "meta-llama/Llama-3.2-90B-Vision-Instruct"
]

FALLBACK_TEXT_MODELS = [
    "Qwen/Qwen3-30B-A3B",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo"
]
