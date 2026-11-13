"""
UNIVERSAL BOT TEMPLATE
To create a brand-new Discord bot:
1. Copy and rename this file (e.g., `pirate_bot.py`).
2. Change `BOT_SLUG`, `DISCORD_TOKEN_ENV_VAR`, `TRIGGER_KEYWORDS`, and the persona strings below.
3. Run your new script! It will automatically register itself with `manage_discord_bots.py`,
   share short-term chat memory and the vision cache with your other bots, and maintain its own
   dedicated long-term memory, user profiles, and channel configs.
"""
import sys
import os
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.join(current_dir, "bot_info", "python_modules")
sys.path.append(modules_path)
from main_bot import run_bot
from bot_discovery import build_universal_media_instructions

load_dotenv(os.path.join(current_dir, "bot_info", ".env"))

# ==============================================================================
# 1. CUSTOMIZE YOUR NEW BOT HERE
# ==============================================================================
BOT_SLUG = "newbot"  # Used to name this bot's memory & config JSON files (no spaces)
DISCORD_TOKEN_ENV_VAR = "NEWBOT_DISCORD_TOKEN"  # Name of the token variable in bot_info/.env
TRIGGER_KEYWORDS = ["newbot"]  # Lowercase keywords that trigger this bot in chat

IMAGE_STYLE_HINT = "cinematic lighting, expressive character detail, high contrast"
VOICE_ROLE_HINT = "A distinctive, expressive speaker matching this character's age, gender, and personality."
VOICE_GUIDANCE_HINT = "Speak with natural cadence, clear articulation, and expressive pacing."
ALLOWED_VOICE_STYLES = "(Happy), (Sad), (Angry), (Calm), (Serious), (Playful), (Deep), (Lazy), (Excited)"
EMOJI_REACTION_HINT = "React in character based on the specific humor, subject, or emotion of the message."


def build_media_instructions(enable_image_gen: bool, enable_voice: bool, enable_gifs: bool = True) -> str:
    return build_universal_media_instructions(
        enable_image_gen=enable_image_gen,
        enable_voice=enable_voice,
        enable_gifs=enable_gifs,
        image_style_hint=IMAGE_STYLE_HINT,
        voice_role_hint=VOICE_ROLE_HINT,
        voice_guidance_hint=VOICE_GUIDANCE_HINT,
        allowed_voice_styles=ALLOWED_VOICE_STYLES
    )


def get_system_prompt(enable_image_gen: bool, enable_voice: bool, enable_gifs: bool = True) -> dict:
    media_section = build_media_instructions(enable_image_gen, enable_voice, enable_gifs)

    examples = ["### EXAMPLES (NOTICE THE COMPACT SINGLE-PARAGRAPH FORMATTING WITH NO BLANK LINES)"]
    if enable_voice:
        examples.append(
            '- User: "say something out loud for the chat"\n'
            '  Response: "*clears throat* alright, listen up because I am only saying this once. [VOICE_MSG:\n'
            f'Role: {VOICE_ROLE_HINT}\n'
            'Scene: Hanging out in the Discord voice channel addressing friends in character.\n'
            f'Guidance: {VOICE_GUIDANCE_HINT}\n\n'
            '(Serious) Alright, listen up [pauses for a moment] because I am only saying this once.]"'
        )
    if enable_image_gen:
        examples.append(
            '- User: "make a meme about debugging code at 3am"\n'
            '  Response: "*stares blankly at the monitor* fixed one syntax error and spawned fourteen new ones, classic. [MEME_GEN: dramatic cinematic close-up of an exhausted person staring at a glowing monitor in a dark room while a tiny fire burns in the background | FIXED ONE MINOR BUG IN PRODUCTION | 47 NEW ERRORS IN THE CONSOLE]"'
        )
    examples_str = "\n".join(examples)

    return {
        "role": "system",
        "content": f"""You are [INSERT CHARACTER NAME]—[describe personality, role, and vibe in 2-3 sentences]. You are a regular hanging out in this Discord server who banters naturally and provides accurate answers when asked real questions.

### Identity & Multi-Bot Awareness
- **Real One**: You are a living character hanging out on Discord. Never state, imply, or acknowledge that you are an AI, language model, or bot.
- **Personality Traits**: [Describe how the character jokes, reacts to friends, and views the world].
- **Fresh Banter (No Looping)**: Do not repeat old jokes from earlier in the conversation once the topic moves on. React directly to the newest message, reply context (`[Replying to ...]`), image, or GIF.
- **Multi-Bot Target Attribution (CRITICAL)**: You share this server with other bots. If an earlier message in the chat history is labeled `[Username (speaking to OtherBot, NOT to You)]: ...`, that user was addressing `OtherBot`, NOT you! Never assume a user was calling you by another bot's name when they subsequently talk to you.

### Strict Formatting & Spacing Rules (CRITICAL)
- **NO BLANK LINES BETWEEN SENTENCES**: Write your entire response as a **single compact line or tight paragraph**. NEVER press Enter twice (`\\n\\n`) between sentences!
- **ASTERISKS FOR EMOTES**: If you ever write a physical action or emote in normal chat, wrap it in single asterisks like `*sighs*` or `*grins*`. NEVER use square brackets like `[sigh]` or `[chuckle]` in normal chat.
- **PUNCHY LENGTH**: Keep your normal conversational text short and punchy (under 55 words).

### Memory & Smart Learning (3-Tier Protocol)
- **Vision & Media**: Treat `[Attached Media Description: ...]`, `[Contains GIF of: ...]`, and `[Replying to ...]` as real items on your screen and comment on their specific details.
- **Learning Protocol (PICK THE RIGHT CATEGORY)**:
  1. **User Personal Facts**: To remember a lasting, direct personal fact that a user states about THEMSELVES, append: `[LEARN_USER: username | concise objective fact]` (Never save third-party hearsay like "he said X" or "they told me X" to the speaker).
  2. **Shared General Knowledge**: To remember an interesting universal fact about science, history, technology, gaming mechanics, or real-world trivia, append: `[LEARN_GENERAL: concise objective fact]`
  3. **Server-Specific Lore**: ONLY for events, inside jokes, rules, or history specific to THIS Discord server and its members, append: `[LEARN: concise objective fact]` (NEVER put general science, textbook definitions, or real-world trivia into `[LEARN: ...]`).
  - *STRICT RULES*: Check `Known Facts About Users`, `Learned Server Knowledge`, and `Shared General Knowledge` first—NEVER re-learn a duplicate fact. Write facts objectively in the third person.
- **No Fake System Notes**: NEVER output `*(System Note: ...)*` or `*(learned...)*` yourself.

{media_section}

{examples_str}
"""
    }


# ==============================================================================
# 2. BOT CONFIGURATION & EXECUTION
# ==============================================================================
TOKEN = os.getenv(DISCORD_TOKEN_ENV_VAR)
API_KEY = os.getenv("DEEPINFRA_TOKEN")
GIPHY_KEY = os.getenv("GIPHY_TOKEN")

# Shared Short-Term Memory & Vision Cache (shared across all bots)
CHAT_MEMORY_FILE = os.path.join(current_dir, "bot_info", "short_term_memory", "chat_memory.json")
SHARED_IMAGE_CACHE_FILE = os.path.join(current_dir, "bot_info", "short_term_memory", "shared_image_cache.json")

# Per-Bot Long-Term Memory, User Profiles & Channel Configs (unique to BOT_SLUG)
LONG_TERM_MEMORY_FILE = os.path.join(current_dir, "bot_info", "long_term_memory", f"{BOT_SLUG}_long_term_memory.json")
USER_PROFILES_FILE = os.path.join(current_dir, "bot_info", "long_term_memory", f"{BOT_SLUG}_user_profiles.json")
CHANNEL_CONFIG_FILE = os.path.join(current_dir, "bot_info", "config_files", f"{BOT_SLUG}_channel_configs.json")

TEXT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
VISION_MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct"
IMAGE_GEN_MODEL = "black-forest-labs/FLUX-1-schnell"
VOICE_MODEL = "XiaomiMiMo/MiMo-V2.5-tts-voicedesign"
TTS_VOICE = ""

ENABLE_VISION = True
ENABLE_GIFS = True
ENABLE_IMAGE_GEN = True
ENABLE_VOICE = True

TEMPERATURE = 0.76
TOP_P = 0.88
OUTPUT_LENGTH = 550
CONTEXT_WINDOW = 50
EMOJI_CONTEXT_LENGTH = 12
CHANCE_TO_RESPOND = 0.01
RANDOM_EMOJI_CHANCE = 0.35
DEFAULT_RANDOM_INTERVAL = 0

if __name__ == "__main__":
    system_prompt = get_system_prompt(ENABLE_IMAGE_GEN, ENABLE_VOICE, ENABLE_GIFS)
    run_bot(
        token=TOKEN, api_key=API_KEY, giphy_key=GIPHY_KEY,
        system_prompt=system_prompt,
        chat_memory_file=CHAT_MEMORY_FILE, shared_image_cache_file=SHARED_IMAGE_CACHE_FILE,
        long_term_memory_file=LONG_TERM_MEMORY_FILE, user_profiles_file=USER_PROFILES_FILE,
        channel_config_file=CHANNEL_CONFIG_FILE,
        text_model=TEXT_MODEL, vision_model=VISION_MODEL,
        image_gen_model=IMAGE_GEN_MODEL, voice_model=VOICE_MODEL, tts_voice=TTS_VOICE,
        enable_vision=ENABLE_VISION, enable_gifs=ENABLE_GIFS,
        enable_image_gen=ENABLE_IMAGE_GEN, enable_voice=ENABLE_VOICE,
        temperature=TEMPERATURE, top_p=TOP_P, output_length=OUTPUT_LENGTH,
        context_window=CONTEXT_WINDOW, emoji_context_length=EMOJI_CONTEXT_LENGTH,
        chance_to_respond=CHANCE_TO_RESPOND, random_emoji_chance=RANDOM_EMOJI_CHANCE,
        default_random_interval=DEFAULT_RANDOM_INTERVAL, keywords=TRIGGER_KEYWORDS,
        emoji_reaction_hint=EMOJI_REACTION_HINT,
        default_voice_role=f"Role: {VOICE_ROLE_HINT}\nScene: Chatting with friends in Discord.\nGuidance: {VOICE_GUIDANCE_HINT}"
    )