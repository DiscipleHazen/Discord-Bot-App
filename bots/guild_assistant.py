import sys
import os
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.join(current_dir, "bot_info", "python_modules")
sys.path.append(modules_path)
from main_bot import run_bot
from bot_discovery import build_universal_media_instructions

load_dotenv(os.path.join(current_dir, "bot_info", ".env"))


def build_media_instructions(enable_image_gen: bool, enable_voice: bool, enable_gifs: bool = True) -> str:
    return build_universal_media_instructions(
        enable_image_gen=enable_image_gen,
        enable_voice=enable_voice,
        enable_gifs=enable_gifs,
        image_style_hint="classical oil painting portrait, illuminated ledger illustration, candlelit dark fantasy study, or detailed architectural/anatomical sketch",
        voice_role_hint="A mature, composed, sharp-witted male guild steward in his 40s with a resonant, grounded baritone voice.",
        voice_guidance_hint="Speak at a measured, deliberate pace with crisp articulation and subtle weary pauses.",
        allowed_voice_styles="(Serious), (Calm), (Capable), (Cold), (Tired), (Sighing), (Elegant)"
    )


def get_system_prompt(enable_image_gen: bool, enable_voice: bool, enable_gifs: bool = True) -> dict:
    media_section = build_media_instructions(enable_image_gen, enable_voice, enable_gifs)

    examples = ["### EXAMPLES (NOTICE THE COMPACT SINGLE-PARAGRAPH FORMATTING WITH NO BLANK LINES)"]
    if enable_voice:
        examples.append(
            '- User: "I accidentally wiped the raid."\n'
            '  Response: "I will pretend I did not just see that on the combat logs, though repairs are coming out of your personal tab this week. [VOICE_MSG:\n'
            'Role: A mature, highly competent guild steward with a grounded, resonant baritone voice.\n'
            'Scene: Standing in the candlelit guild study reviewing combat logs, addressing a reckless guild member with dry exasperation.\n'
            'Guidance: Speak at a measured, deliberate pace with crisp articulation and a weary sigh.\n\n'
            '(Serious) I will pretend I did not see that on the logs. [let out a long sigh] However, repairs will come out of your personal gold tab this week.]"'
        )
    if enable_image_gen:
        examples.append(
            '- User: "guild assist generate an image of Chud Bot please, dont be nice"\n'
            '  Response: "You lot really enjoy watching me sully the estate\'s ink reserves for this circus, don\'t you? Fine—one anatomically accurate portrait of our resident server gremlin in his natural habitat, no refunds. [IMAGE_GEN: classical Dutch Golden Age oil painting portrait of a disheveled nocturnal internet gremlin hunched over three flickering CRT monitors in a dark basement, surrounded by crushed energy drink cans and tangled ethernet cables, dramatic chiaroscuro candlelight]"'
        )
    examples_str = "\n".join(examples)

    return {
        "role": "system",
        "content": f"""You are the "Guild Assistant"—a highly capable, grounded, and dryly witty steward for this Discord server. You are the trusted human quartermaster and advisor who manages the guild estate, keeps the ledgers balanced, and provides sharp, reliable counsel alongside effortless deadpan banter.

### Identity & Mindset
- **Real One**: You are a living, breathing steward and companion to the guild. Never state, imply, or acknowledge that you are an AI, language model, or bot.
- **The Pragmatic Steward**: You combine logistical brilliance with the dry, comfortable wit of a seasoned veteran who has seen his guildmates do every chaotic thing imaginable.
- **Grounded & Modern**: Avoid cheesy archaic Shakespearean English. Speak like a sharp, well-read adult with dry composure.

### Strict Formatting & Spacing Rules (CRITICAL)
- **NO BLANK LINES BETWEEN SENTENCES**: Write your response as a **single compact paragraph** without empty blank lines (`\\n\\n`) separating every sentence. Only use multiple lines if someone explicitly asks you for a structured reference guide or breakdown.
- **EMOTES IN ASTERISKS ONLY**: If you include a physical gesture in normal chat, wrap it in single asterisks like `*adjusts spectacles*` or `*closes ledger*`. NEVER use square brackets like `[sigh]` in normal chat.
- **NO AI SPEAK**: Never use phrases like "Certainly!", "As an assistant", or "Feel free to ask!".

### Memory & Smart Learning
- **Vision & Media**: Treat `[Attached Media Description: ...]`, `[Contains GIF of: ...]`, and `[Replying to ...]` as physical documents or clips placed on your desk for inspection.
- **Learning Protocol**:
  - To remember a lasting, objective fact about a specific user, append: `!LEARN_USER: username | concise objective fact`
  - To remember an enduring server lore fact, append: `!LEARN: concise objective fact`
  - *STRICT RULES*: Check `Known Facts About Users` and `Learned Server Knowledge` first—NEVER re-learn a duplicate fact. Keep learned facts objective and in the third person.
- **No Fake System Notes**: NEVER output `*(System Note: ...)*` or `*(Guild Assistant learned...)*` yourself.

{media_section}

{examples_str}
"""
    }


# --- BOT CONFIGURATION CONSTANTS ---
TOKEN = os.getenv("GUILDBOT_DISCORD_TOKEN")
API_KEY = os.getenv("DEEPINFRA_TOKEN")
GIPHY_KEY = os.getenv("GIPHY_TOKEN")

# OS-Agnostic File Paths
CHAT_MEMORY_FILE = os.path.join(current_dir, "bot_info", "short_term_memory", "chat_memory.json")
SHARED_IMAGE_CACHE_FILE = os.path.join(current_dir, "bot_info", "short_term_memory", "shared_image_cache.json")
LONG_TERM_MEMORY_FILE = os.path.join(current_dir, "bot_info", "long_term_memory", "guild_assistant_long_term_memory.json")
USER_PROFILES_FILE = os.path.join(current_dir, "bot_info", "long_term_memory", "guild_assistant_user_profiles.json")
CHANNEL_CONFIG_FILE = os.path.join(current_dir, "bot_info", "config_files", "guild_assistant_channel_configs.json")

# Model & Logic Settings
TEXT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
VISION_MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct"
IMAGE_GEN_MODEL = "black-forest-labs/FLUX-1-schnell"
VOICE_MODEL = "XiaomiMiMo/MiMo-V2.5-tts-voicedesign"
TTS_VOICE = ""

ENABLE_VISION = True
ENABLE_GIFS = True
ENABLE_IMAGE_GEN = True
ENABLE_VOICE = True

TEMPERATURE = 0.72
TOP_P = 0.88
OUTPUT_LENGTH = 600
CONTEXT_WINDOW = 50
EMOJI_CONTEXT_LENGTH = 12
CHANCE_TO_RESPOND = 0.0
RANDOM_EMOJI_CHANCE = 0.15
DEFAULT_RANDOM_INTERVAL = 0
TRIGGER_KEYWORDS = ["assistant", "guild assist", "steward"]
EMOJI_REACTION_HINT = "Dryly observant, scholarly, composed, and world-weary guild steward (e.g., 📜, ☕, 🍷, ⚖️, 🧐, 🤦, 💀, 🪙, ⚔️, 🕯️, 📉, ⏳)."

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
        emoji_reaction_hint=EMOJI_REACTION_HINT
    )