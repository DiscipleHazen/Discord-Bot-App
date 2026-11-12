DISCLAIMER: IM NOT A SUPER GENIUS, AI HELPED ME GENERATE EVERY PART OF THIS PROJECT (if you couldn't tell by the emojis), I USED GEMINI PRO/ARGON FOR MANY MANY HOURS. 

# 🤖 Multi-Agent Discord AI Ecosystem & Orchestration Framework

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-Asynchronous-5865F2?logo=discord&logoColor=white)
![OpenCV & PIL](https://img.shields.io/badge/Vision-OpenCV%20%7C%20Pillow-5C3EE8)
![DeepInfra API](https://img.shields.io/badge/Inference-DeepInfra%20LLMs%20%7C%20FLUX%20%7C%20TTS-00C7B7)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-4D4D4D)

An asynchronous, multi-agent Discord AI framework built in Python. Designed to run multiple distinct AI personas concurrently within shared servers, this platform coordinates shared short-term context, persistent per-bot user dossiers, multimodal computer vision (static images, animated GIFs, and video keyframe storyboards), dynamic image/audio synthesis, and process-safe atomic file I/O.

---

## 🌟 Core Engineering Highlights

### 1. 8-Layer One-Way Modular Architecture
To eliminate circular dependencies and maintain clean separation of concerns, the engine is architected as a strict **bottom-up dependency pyramid** across 7 core modules and lightweight per-bot launcher scripts:
* **Layer 0 (`config.py`):** Zero-import static configuration containing 28+ pre-compiled regular expressions, model fallback arrays, stopword sets, and TTS audio tag mappings.
* **Layer 1 (`core_system.py`):** Infrastructure layer featuring a custom cross-process atomic `FileLock` (`O_CREAT | O_EXCL` with stale-lock eviction), `mtime`-aware in-memory JSON caching, thread-local `requests.Session` connection pooling (20 connections/pool), and an exponential backoff HTTP wrapper with `Retry-After` compliance.
* **Layer 2 (`media_engine.py` & `text_and_parsers.py`):** Media download/preprocessing pipelines (OpenCV/Pillow) and balanced-bracket AST-style parsers for inline voice synthesis and markdown sanitization.
* **Layer 3 (`ai_services.py`, `bot_discovery.py`, `memory_and_context.py`):** DeepInfra inference engines, multi-bot discovery and de-escalation, anti-hallucination memory verification, and history assembly.
* **Layer 4 (`main_bot.py`):** Asynchronous `discord.Client` event loop orchestrating per-channel generation locks, message coalescing, background tasks, and parallel media synthesis.
* **Layer 5 (`<bot_name>.py`):** Declarative bot launcher scripts defining persona system prompts, model selections, and trigger keywords.

### 2. Multi-Frame Computer Vision & Shared Vision Cache
* **2x2 Chronological Video/GIF Storyboarding:** Using `OpenCV (cv2)` and `Pillow (PIL)`, the media engine samples 4 chronological keyframes (`10%`, `36%`, `64%`, `90%`) across MP4/WebM videos and animated GIFs/WebPs. It evaluates grayscale luminance and contrast standard deviation to bypass black fade-in/out frames and stitches the frames into a single 2x2 storyboard grid for Vision-Language Models (`Qwen3-VL-235B`).
* **Cross-Process Vision Deduplication:** When multiple bots inhabit the same channel, the first bot to process an attachment acquires a `PROCESSING|<timestamp>` lock in `shared_image_cache.json`, summarizes the visual media, and caches the description by Discord `message_id`. Sibling bots await and reuse the cached analysis rather(than firing redundant vision API calls.
* **Deep Media Extraction:** Supports direct Discord attachments, embedded video/image proxies, OpenGraph (`og:video`, `og:image`) HTML scraping, YouTube oEmbed metadata extraction, and Tenor/Giphy/Klipy URL slug parsing.

### 3. Anti-Hearsay Long-Term Memory & User Profiling
* **Two-Stage Fact Verification:** Bots autonomously extract server lore (`[LEARN: fact]`) and user-specific dossier notes (`[LEARN_USER: username | fact]`).
* **Deterministic Hearsay Filter:** Before saving a user fact, a linguistic regex & token-overlap filter (`is_misattributed_hearsay`) catches reported third-party speech (e.g., *"they said X"*, *"he told me X"*), preventing quotes or banter from polluting a speaker's permanent profile.
* **LLM Attribution Verifier:** Candidate facts pass through a secondary `temperature=0.0` JSON verification call that validates self-ownership, rejects sarcasm/roleplay, or re-attributes facts to the correct canonical server member.

### 4. Multi-Bot Coordination & 3-Stage Contextual Follow-Up Engine
* **Autonomous Bot Discovery:** Running bots register their IDs, display names, and trigger keywords in `shared_image_cache.json`. Bots recognize when a prompt is directed at a sibling bot (e.g., *"Bot A, roast Bot B"*) and withhold media generation so the targeted bot takes lead.
* **Loop Prevention:** Enforces bot-to-bot streak limits and a 300-second cooldown on unprompted bot-to-bot banter.
* **3-Stage Hybrid Follow-Up Gatekeeper:** Maintains natural conversation flow for 1–2 turns after a bot speaks without requiring repeated `@mentions`, using:
  1. *Deterministic Rejection:* Blocks messages addressing other users or bots.
  2. *Deterministic Acceptance:* Accepts rapid replies, questions, or direct reactions within 60 seconds.
  3. *LLM Relevance Classifier:* Uses a fast `max_tokens=10` classifier for ambiguous follow-ups.

### 5. Concurrent Multimodal Synthesis (Images, Voice, GIFs, Emojis)
* **Parallel Execution:** Text replies trigger `asyncio.gather` tasks to synthesize images (`FLUX-1-schnell` / `SDXL-Turbo`), voice clips (`MiMo-V2.5-TTS` / `Kokoro-82M`), and Giphy searches simultaneously.
* **Visual Divergence Engine:** Compares proposed `[IMAGE_GEN:]` prompts against recent channel renders; if token overlap exceeds `34%`, the prompt is automatically rewritten to guarantee a fresh visual composition and camera angle.
* **Director-Mode TTS Parsing:** Extracts `[VOICE_MSG:]` blocks, maps roleplay actions (e.g., `*sighs*`, `*giggles*`) to native audio control tags, and strips non-vocal actions/kaomoji.
* **Persona-Driven Auto-Reactions:** Fires non-blocking background tasks (`asyncio.create_task`) to react to messages with contextually ranked Unicode or custom server emojis while banning recently used emojis to prevent repetition.
* **Self-Aware Glitch Recovery:** If a primary model times out or returns HTTP `429/502/503/504`, the engine cascades through fallback Vision/Text models and injects a `[System Glitch Context]` note, allowing the bot to joke about its "brain freeze" in-character rather than outputting raw error strings.

---

## 📁 Repository Structure

```text
discord_AI_appV1.9.3/
├── manage_bots.bat                       # Interactive Windows CLI Process & Config Manager
├── manage_bots.sh                        # Cross-platform Linux/macOS Shell Manager
└── bots/
    ├── .gitignore                        # Protects .env, logs, and JSON user memory
    ├── guild_assistant.py                # Example Bot Launcher Script (Layer 5)
    └── bot_info/
        ├── .env                          # Private API Keys & Tokens (Git-Ignored)
        ├── bot_logs/                     # Per-bot stdout/stderr runtime logs
        ├── bot_startup_scripts/          # Auto-generated headless launcher scripts
        ├── config_files/                 # Per-bot channel configuration JSONs
        ├── long_term_memory/             # Persistent server lore & user profile JSONs
        ├── short_term_memory/            # Shared chat_memory.json & shared_image_cache.json
        └── python_modules/               # Core 7-File Engine (Layers 0–4)
            ├── config.py                 # Layer 0: Regexes, Constants & Fallbacks
            ├── core_system.py            # Layer 1: Atomic FileLock, Cache & HTTP Backoff
            ├── media_engine.py           # Layer 2A: OpenCV/PIL Storyboards & Media Fetch
            ├── text_and_parsers.py       # Layer 2B: Voice Tag Parser & Text Cleaners
            ├── ai_services.py            # Layer 3A: DeepInfra Vision, Image, Voice & Emoji
            ├── bot_discovery.py          # Layer 3B: Multi-Bot Routing & Follow-Up Engine
            ├── memory_and_context.py     # Layer 3C: Hearsay Filter & History Builder
            ├── main_bot.py               # Layer 4: Discord Event Loop & Command Suite
            └── bot_editor.py             # Interactive CLI Configuration Editor
```

---

## 💬 Discord In-Chat Command Reference

All commands are invoked by `@mentioning` the target bot in any Discord channel where it has access.

### 👤 User Commands (Available to All Server Members)
| Command | Description |
| :--- | :--- |
| `@BotName !help` | Displays the complete interactive command reference in chat. |
| `@BotName !whoami` | Outputs all permanent dossier facts the bot has learned and saved about you. |
| `@BotName !forgetme <keyword>` | Deletes any saved personal facts matching `<keyword>` from your user profile. |
| `@BotName !forgetme all` | Completely wipes your personal user dossier from the bot's long-term memory. |

### 🛡️ Administrator Channel Controls (Requires Server Admin Permissions)
| Command | Description |
| :--- | :--- |
| `@BotName admin status` | Displays channel configuration, active media flags, temperature, and memory statistics. |
| `@BotName admin enable` / `disable` | Enables or disables the bot from responding in the current channel. |
| `@BotName admin reactonly on` / `off` | Toggles silent mode (bot will only add emoji reactions and never send text replies). |
| `@BotName admin chances <resp> <emoji>` | Sets random chime-in probability (`0.0–1.0`) and emoji reaction probability (`0.0–1.0`). |
| `@BotName admin random <minutes>` | Configures periodic unprompted channel posts every `<minutes>` (`0` disables timer). |
| `@BotName admin features <type> <on\|off>` | Toggles `vision`, `gifs`, `image`, or `voice` capabilities dynamically for the current channel. |
| `@BotName admin temp <0.1–1.5>` | Adjusts the LLM creativity/sampling temperature in the current channel. |

### 🧠 Administrator Memory Management (Requires Server Admin Permissions)
| Command | Description |
| :--- | :--- |
| `@BotName admin memory` | Displays a snapshot of learned server lore and active user profiles. |
| `@BotName admin learn <fact>` | Manually injects a permanent server lore fact into `long_term_memory.json`. |
| `@BotName admin learnuser <user> \| <fact>` | Manually adds a verified fact to a specific user's permanent profile. |
| `@BotName admin forget <keyword>` | Purges any server lore or user profile entries matching `<keyword>` across all files. |
| `@BotName !clearmemory` | Wipes the short-term conversation context window for the current channel. |

---

## 🖥️ Using `manage_bots.bat` (Windows CLI Control Center)

The included `manage_bots.bat` script provides a color-coded ANSI terminal dashboard for managing multiple bot processes without keeping console windows open.

### Key Capabilities:
* **Live Process Inspection:** Uses WMI (`Get-CimInstance Win32_Process`) to inspect active `python.exe` command lines and display real-time `[ ONLINE ]` or `[ OFFLINE ]` status indicators for every `.py` bot script in `bots/`.
* **Pre-Flight Environment Validation:** Before launching a bot, dynamically parses the bot's Python script to identify required `.env` keys (`DISCORD_TOKEN`, `DEEPINFRA_TOKEN`, `GIPHY_TOKEN`) and halts startup with a clear report if any credentials are missing.
* **Detached Headless Execution:** Generates a startup wrapper in `bot_info/bot_startup_scripts/` and spawns a detached background process via `Win32_Process.Create`, piping all `stdout` and `stderr` output into `bot_info/bot_logs/debug_<bot_name>.log`.
* **Interactive Bot Scaffolding (`[C] Create Bot`):** Prompts for a bot name, Discord token, and system prompt, then automatically writes the token to `bot_info/.env` and generates a pre-configured `.py` launcher script.
* **Live Configuration & Key Management:**
  * `[1-N] Toggle Bot`: Starts an offline bot in the background or cleanly terminates an online bot's PID.
  * `[E] Edit Config`: Launches `bot_editor.py` to modify models, temperatures, media toggles, and keywords.
  * `[H] History`: Updates the global `MAX_HISTORY_LENGTH` variable in `.env`.
  * `[A] API Keys`: Adds or updates `DEEPINFRA_TOKEN`, `GIPHY_TOKEN`, or custom environment keys directly in `.env`.
  * `[K] Kill All`: Force-terminates all running bot processes in a single command.

---

## 🚀 Getting Started & Creating a New Bot

### Prerequisites
1. **Python 3.11+** installed and added to your system `PATH`.
2. Install required dependencies:
   ```bash
   pip install discord.py requests python-dotenv Pillow opencv-python
   ```
3. API Keys:
   * **DeepInfra API Key:** For LLM text, vision, FLUX image generation, and TTS audio synthesis.
   * **Giphy API Key *(Optional)*:** For automated `[GIF:]` search embeds.

### Step 1: Register Your Bot on the Discord Developer Portal
1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Give your bot a name and click **Create**.
3. **Configure Bot Settings & Token:**
   * Select the **Bot** tab on the left sidebar.
   * Click **Reset Token** (or **View Token**) and copy your secret token. *(Save this for Step 2).*
4. **Enable Privileged Gateway Intents (CRITICAL):**
   * Still under the **Bot** tab, scroll down to **Privileged Gateway Intents**.
   * Toggle **ON**:
     * ✅ **Server Members Intent** (`intents.members`)
     * ✅ **Message Content Intent** (`intents.message_content`)
   * Click **Save Changes**. *(Without these intents, the bot cannot read messages or resolve server members).*
5. **Invite the Bot to Your Server:**
   * Select the **OAuth2** tab on the left sidebar.
   * Scroll down to **OAuth2 URL Generator** and check the **`bot`** scope.
   * In the **Bot Permissions** box that appears below, select:
     * `View Channels`
     * `Send Messages`
     * `Attach Files` *(required for AI images & voice clips)*
     * `Embed Links` *(required for GIFs)*
     * `Add Reactions` & `Use External Emojis` *(required for auto-reactions)*
     * `Read Message History`
   * Copy the generated URL at the bottom, paste it into your browser, and authorize the bot on your server.

### Step 2: Create & Launch the Bot Locally
1. Double-click `manage_bots.bat` (or run `./manage_bots.sh` on Linux).
2. Press **`A` (API Keys)** to save your `DEEPINFRA_TOKEN` and optional `GIPHY_TOKEN` into `bots/bot_info/.env`.
3. Press **`C` (Create Bot)** from the main menu:
   * **Enter Bot Name:** e.g., `ButlerBot`
   * **Enter Discord API Token:** Paste the token copied from the Discord Developer Portal.
   * **Enter System Prompt Identity:** Define the character's personality, tone, and behavioral rules.
4. The manager will automatically create `bots/butlerbot.py` and store `BUTLERBOT_DISCORD_TOKEN` inside `bots/bot_info/.env`.
5. *(Optional)* Press **`E` (Edit Config)** to customize the bot's models, trigger keywords, or enable image/voice synthesis.
6. Press the **number key** next to your new bot in the main menu to launch it in the background!

---

## 🔒 Security & Secret Management
* All API tokens and secrets are isolated inside `bots/bot_info/.env` and loaded via `python-dotenv`.
* The `.gitignore` is strictly configured to exclude `.env`, runtime debug logs (`bot_logs/`), and user conversation data (`short_term_memory/` and `long_term_memory/`), while preserving directory structures via `.gitkeep` anchors.
