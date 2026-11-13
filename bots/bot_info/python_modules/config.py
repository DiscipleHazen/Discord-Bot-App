import re

# ==============================================================================
# PRE-COMPILED REGEXES & CONSTANTS
# ==============================================================================
YT_REGEX = re.compile(r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)')
TENOR_REGEX = re.compile(r'tenor\.com/view/([a-zA-Z0-9-]+)-gif', re.IGNORECASE)
GIPHY_REGEX = re.compile(r'giphy\.com/gifs/(?:[a-zA-Z0-9]+-)*([a-zA-Z0-9-]+)', re.IGNORECASE)
KLIPY_REGEX = re.compile(r'klipy\.com/gifs/([a-zA-Z0-9-]+)', re.IGNORECASE)
DIRECT_GIF_URL_REGEX = re.compile(r'https?://\S+/([a-zA-Z0-9_\-]{3,})\.(?:gif|webp)(?:\?\S*)?', re.IGNORECASE)
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
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image|og:image:url)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
OG_IMAGE_REGEX_2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image|og:image:url)["\']',
    re.IGNORECASE
)

# Context & Memory Extraction
LEARN_USER_EXTRACT_REGEX = re.compile(
    r'(?:!LEARN_USER|\[LEARN_USER)\s*[|:]\s*([^\|]+)\|\s*([^\]\n]+)\]?',
    re.IGNORECASE
)
LEARN_USER_STRIP_REGEX = re.compile(
    r'(?:!LEARN_USER|\[LEARN_USER)\s*[|:]\s*[^\|]+\|\s*[^\]\n]*\]?',
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
    r'my\s+(?:friend|friends|coworker|boss|brother|sister|mom|dad|ex|roommate|partner|wife|husband|bf|gf)|'
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

# Media Extraction & Cleansing
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

# Bracket emote conversion
BRACKET_EMOTE_REGEX = re.compile(r'\[([a-zA-Z][a-zA-Z0-9\s,\'\-\.~!]{1,45})\](?!\()')

# Explicit user media intent detection
EXPLICIT_MEME_REQ_REGEX = re.compile(
    r'\b(generate|make|create|cook|drop|send|give|show|render|draw)\b[^\n]{0,45}\b(meme|memes|shitpost)\b'
    r'|\b(meme\s+(?:of|about|for|when))\b',
    re.IGNORECASE
)
EXPLICIT_IMAGE_REQ_REGEX = re.compile(
    r'\b(generate|draw|render|make|create|show|give|send|paint|sketch|wheres|where is|still no)\b[^\n]{0,45}\b(image|pic|picture|art|photo|render|drawing|painting|portrait|loading screen|illustration|wallpaper)\b'
    r'|\b(image|pic|picture|portrait|drawing)\s+of\b',
    re.IGNORECASE
)
BOT_IMAGE_PROMISE_REGEX = re.compile(
    r'\b(i\'ll generate|i generated|one rendering of|here\'s the (?:image|pic|picture|render|drawing|portrait|meme)|loading screen i generated|pull it out of the aether|give you one that\'s technically accurate|let\'s see what we\'re working with)\b',
    re.IGNORECASE
)
EXPLICIT_VOICE_REQ_REGEX = re.compile(
    r'\b(generate|send|make|record|do|give)\b[^\n]{0,45}\b(voice|audio|voice clip|voice message|speech|tts|voiceline)\b'
    r'|\b(say out loud|speak to me|talk to me|voice clip|hear your voice)\b',
    re.IGNORECASE
)
EXPLICIT_GIF_REQ_REGEX = re.compile(
    r'\b(send|post|drop|show|give)\b[^\n]{0,30}\b(gif|reaction gif|meme clip)\b',
    re.IGNORECASE
)

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