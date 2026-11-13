# Multi-Agent Discord AI Ecosystem

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Discord.py](https://img.shields.io/badge/Discord.py-Asynchronous-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-Video_Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white)
![Pillow](https://img.shields.io/badge/Pillow-Meme_Engine-3776AB?style=for-the-badge)
![DeepInfra](https://img.shields.io/badge/DeepInfra-Multimodal_AI-000000?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A production-grade, distributed multi-agent Discord bot framework written in Python. Designed to run multiple autonomous, distinct AI personas concurrently within the same Discord channels, this system features cross-process atomic state synchronization, a 3-tier memory verification pipeline, multimodal vision (including chronological 2x2 video/GIF keyframe storyboards), dynamic image/meme synthesis, and director-mode neural Text-to-Speech (TTS).

---

## Key Engineering Highlights

* **Cross-Process Concurrency & Atomic State Synchronization (`core_system.py`):**
  * Built an OS-agnostic, cross-process file-locking engine (`FileLock`) using atomic `O_CREAT | O_EXCL` lockfiles with automatic stale-lock eviction and atomic `.tmp` file replacement (`os.replace`).
  * Enables multiple independent bot processes to read and mutate shared JSON state (`chat_memory.json`, `shared_image_cache.json`, `general_knowledge.json`) concurrently with zero race conditions or file corruption, backed by an `mtime`-validated in-memory cache.
  * Implements thread-local HTTP connection pooling (`threading.local` + `requests.Session` + `HTTPAdapter`) so concurrent `asyncio.to_thread` worker calls never contend on socket state.
* **Multi-Agent Discovery & Target Attribution (`bot_discovery.py`):**
  * Autonomous cross-process service discovery: active bots dynamically register their display names, IDs, and trigger keywords into a shared registry.
  * **Speaker-to-Target Disambiguation:** Resolves multi-bot conversation collisions by parsing sentence structure, prepositions, and Discord reply trees to annotate shared chat logs (`[User (speaking to BotA, NOT to BotB)]: ...`), preventing bots from hijacking commands or misattributing questions directed at other agents.
  * **3-Stage Contextual Follow-Up Engine:** Combines deterministic addressing filters (Stage 1), sliding-window lexical overlap (Stage 2), and a zero-temperature 10-token LLM classifier (Stage 3) to maintain natural multi-turn conversations without requiring repeated `@mentions`.
* **3-Tier Fact Extraction & Hearsay Verification Pipeline (`memory_and_context.py`):**
  * Separates long-term knowledge into three distinct tiers: **Per-User Dossiers** (`[LEARN_USER: user | fact]`), **Shared Universal Knowledge** (`[LEARN_GENERAL: fact]`), and **Server-Specific Lore** (`[LEARN: fact]`).
  * **Two-Stage Hearsay & Misattribution Guard:** Combines deterministic regex/token-overlap heuristics with an asynchronous LLM verification pass to block third-party reported speech (*"he said..."*, *"they told me..."*) and sarcasm from polluting user profiles, while automatically routing misplaced scientific or world trivia into shared general memory.
* **Multimodal Vision, Custom Meme Synthesis & Director-Mode TTS (`media_engine.py`, `ai_services.py`):**
  * **2x2 Video & Animated GIF Storyboarding:** Uses OpenCV (`cv2`) and Pillow (`PIL`) to sample 4 chronological keyframes (`10%`, `36%`, `64%`, `90%`) from `.mp4`/`.webm` videos, animated GIFs, and OpenGraph embeds—penalizing black fade-in/fade-out frames via luminance/contrast scoring—and stitches them into a single 2x2 storyboard grid.
  * **Cross-Process Vision Deduplication:** Coordinate `PROCESSING|<timestamp>` locks in `shared_image_cache.json` so when 4 bots see the same uploaded image or video simultaneously, only one Vision API call is executed and shared across all processes.
  * **Prompt Divergence & Custom Meme Rendering:** Automatically rewrites overlapping image prompts (`ensure_unique_image_prompt`) so bots never clone each other's visual scenes, and renders classic Impact-style top/bottom stroke-outlined captions via Pillow (`[MEME_GEN: scene | TOP TEXT | BOTTOM TEXT]`).
  * **Expressive Neural Voice Synthesis:** Parses structured Director Mode scripts (`Role`, `Scene`, `Guidance`) and inline vocal performance tags (`[sigh]`, `[chuckle]`, `[pauses for a moment]`) for `MiMo-V2.5-tts-voicedesign`.
* **Self-Healing Resilience & In-Character Glitch Awareness:**
  * Automatic model fallback chains across primary and backup Vision (`Qwen3-VL-235B`, `Qwen2.5-VL-32B`, `Llama-3.2-90B-Vision`) and Text (`Qwen3-235B`, `Qwen3-30B`, `Llama-3.3-70B`) endpoints.
  * Converts HTTP `429`/`502`/`503`/`504` retries and timeouts into structured `[System Glitch Context]` events, allowing bots to acknowledge lag spikes in-character while stripping raw error strings to prevent mimicry loops.

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                                  DISCORD SERVER                                   |
|         User Messages, @Mentions, Replies, Images, Videos, GIFs, & Commands       |
+-----------------------------------------+-----------------------------------------+
                                          |
                   +----------------------+----------------------+
                   |                      |                      |
                   v                      v                      v
        +--------------------+ +--------------------+ +--------------------+
        |  guild_assistant   | |   chud_bot_script  | |   uwu_bot_script   |
        |   (Process #1)     | |    (Process #2)    | |    (Process #3)    |
        +---------+----------+ +----------+---------+ +----------+---------+
                  |                       |                      |
                  +-----------------------+----------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                     MODULAR PYTHON ENGINE (bot_info/python_modules)               |
|                                                                                   |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|  |      main_bot.py      |  |   bot_discovery.py    |  | memory_and_context.py |  |
|  | Event routing, admin  |  | Cross-bot registry,   |  | 3-tier memory router, |  |
|  | suite, async locks,   |  | target disambiguation,|  | hearsay filter, LLM   |  |
|  | & coalescing pipeline |  | & 3-stage follow-ups  |  | fact verifier         |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|                                                                                   |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|  |    ai_services.py     |  |    media_engine.py    |  |   core_system.py      |  |
|  | Vision cache, emoji   |  | OpenCV 2x2 video/GIF  |  | Cross-process atomic  |  |
|  | reactor, FLUX image   |  | storyboards, Pillow   |  | FileLock, mtime JSON  |  |
|  | divergence & MiMo TTS |  | meme caption renderer |  | cache, HTTP backoff   |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                  ATOMIC SHARED & PER-BOT PERSISTENCE (JSON + FileLock)            |
|                                                                                   |
|  [Shared Across All Bots]                   [Isolated Per-Bot Persona]            |
|  - chat_memory.json (Short-Term Context)    - <bot>_long_term_memory.json (Lore)  |
|  - shared_image_cache.json (Vision & Reg)   - <bot>_user_profiles.json (Dossiers) |
|  - general_knowledge.json (Universal Facts) - <bot>_channel_configs.json          |
+-----------------------------------------------------------------------------------+
```

---

## Repository Structure

```text
discord-bot-app/
├── bots/
│   ├── guild_assistant_bot_script.py      # Composed, deadpan guild steward persona
│   ├── chud_bot_script.py                 # Cynical, sharp-witted server regular persona
│   ├── uwu_bot_script.py                  # Bubbly, wholesome anime cat-girl persona
│   └── bot_info/
│       ├── .env                           # API keys & Discord tokens (gitignored)
│       ├── bot_logs/                      # Per-bot background execution logs
│       ├── bot_startup_scripts/           # Auto-generated headless process runners
│       ├── config_files/                  # Per-channel runtime settings JSONs
│       ├── long_term_memory/              # Per-bot lore, user dossiers & shared general facts
│       ├── short_term_memory/             # Shared channel history & vision cache JSONs
│       └── python_modules/
│           ├── main_bot.py                # Core Discord client lifecycle & turn orchestration
│           ├── ai_services.py             # Vision, contextual emojis, image/meme & TTS engines
│           ├── bot_discovery.py           # Multi-bot registry, target attribution & follow-ups
│           ├── memory_and_context.py      # Hearsay verifier, fact router & history builder
│           ├── media_engine.py            # OpenCV/PIL storyboards, meme overlay & Giphy/YT
│           ├── text_and_parsers.py        # MiMo TTS tag sanitizer & compact text parser
│           ├── core_system.py             # Thread-local HTTP pool & cross-process FileLock
│           ├── config.py                  # Pre-compiled regexes & fallback model constants
│           └── bot_editor.py              # Interactive CLI for editing bot constants & keywords
├── manage_bots.bat                        # Windows CLI Manager (PowerShell/WMI process control)
├── manage_bots.sh                         # POSIX/Linux/macOS CLI Manager (Bash process control)
├── universal_copy_paste_bot_template.py   # Drop-in template for spinning up new bot personas
└── LICENSE
```

---

## Getting Started

### 1. Prerequisites
* **Python 3.10+**
* **API Keys:**
  * One or more **Discord Bot Tokens** (with `Message Content Intent` and `Server Members Intent` enabled in the Discord Developer Portal).
  * A **DeepInfra API Key** (for LLM text, Vision, FLUX image generation, and MiMo TTS).
  * *(Optional)* A **Giphy API Key** (for reaction GIF search).

### 2. Installation
Clone the repository and install the required Python dependencies:

```bash
git clone https://github.com/disciplehazen/discord-bot-app.git
cd discord-bot-app
pip install discord.py requests python-dotenv Pillow opencv-python
```

### 3. Configuration & Launching via the CLI Manager
You can manage the entire lifecycle of your bots—including adding API keys, creating new bots, editing model parameters/keywords, and toggling background processes—using the included interactive CLI manager:

* **Windows:**
  ```cmd
  manage_bots.bat
  ```
* **Linux / macOS:**
  ```bash
  chmod +x manage_bots.sh
  ./manage_bots.sh
  ```

From the CLI menu:
1. Press **`[A]` (API Keys)** to save your `DEEPINFRA_TOKEN` and `GIPHY_TOKEN` into `bots/bot_info/.env`.
2. Add your bot tokens to `bots/bot_info/.env` (e.g., `GUILDBOT_DISCORD_TOKEN=...`, `CHUDBOT_DISCORD_TOKEN=...`, `UWUBOT_DISCORD_TOKEN=...`) or press **`[C]` (Create Bot)** to generate a new bot script and register its token automatically.
3. Press **`[1-N]` (Toggle Bot)** to launch or stop any bot as a detached background process with automatic log redirection to `bots/bot_info/bot_logs/`.

---

## In-Chat Command Suite

### User Commands
| Command | Description |
| :--- | :--- |
| `@BotName !help` | Displays the complete in-chat command reference. |
| `@BotName !whoami` | Displays the permanent dossier facts this bot has learned about you. |
| `@BotName !forgetme <keyword\|all>` | Removes specific matching facts (or wipes `all` facts) from your personal profile. |

### Administrator Channel & Memory Controls
| Command | Description |
| :--- | :--- |
| `@BotName admin status` | Shows channel configuration, active media flags, and memory counts. |
| `@BotName admin enable` / `disable` | Enables or disables the bot in the current channel. |
| `@BotName admin reactonly on` / `off` | Toggles silent emoji-reaction-only mode in the current channel. |
| `@BotName admin chances <resp> <emoji>` | Sets random chime-in (`0.0–1.0`) and emoji reaction (`0.0–1.0`) probabilities. |
| `@BotName admin random <minutes>` | Sets the unprompted periodic post timer in minutes (`0` = disabled). |
| `@BotName admin temp <0.1-1.5>` | Adjusts the LLM creativity temperature for the current channel. |
| `@BotName admin features <type> <on\|off>` | Toggles `vision`, `gifs`, `image` (images & memes), or `voice` per channel. |
| `@BotName admin memory` | Displays a snapshot of learned Server Lore, Shared General Facts, and User Profiles. |
| `@BotName admin learn <fact>` | Manually adds a server-specific lore entry. |
| `@BotName admin learngeneral <fact>` | Manually adds a shared universal knowledge fact across all bots. |
| `@BotName admin learnuser <user> \| <fact>` | Manually adds a fact to a specific user's dossier. |
| `@BotName admin forget <keyword>` | Purges any server lore, general fact, or user dossier entry matching `<keyword>`. |
| `@BotName !clearmemory` | Wipes short-term conversation history for the current channel. |

---

## Creating Custom Personas

To add a new character with full access to shared vision, custom meme generation, and cross-bot discovery:
1. Copy `universal_copy_paste_bot_template.py` into the `bots/` directory (e.g., `bots/detective_bot.py`) or use option **`[C]`** in the CLI manager.
2. Customize `BOT_SLUG`, `DISCORD_TOKEN_ENV_VAR`, `TRIGGER_KEYWORDS`, `IMAGE_STYLE_HINT`, and `VOICE_ROLE_HINT`.
3. Use option **`[E]` (Edit Config)** in the CLI manager at any time to live-edit constants or manage trigger keywords via `bot_editor.py`.

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for full details.