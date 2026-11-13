# Multi-Agent Discord AI Ecosystem

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Discord.py](https://img.shields.io/badge/Discord.py-Asynchronous-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-Video_Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white)
![Pillow](https://img.shields.io/badge/Pillow-Meme_Engine-3776AB?style=for-the-badge)
![DeepInfra](https://img.shields.io/badge/DeepInfra-Multimodal_AI-000000?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A production-grade, distributed multi-agent Discord bot framework written in Python. Engineered to run multiple autonomous, distinct AI agents concurrently within the same Discord channels, this system features cross-process atomic state synchronization, a 3-tier memory verification pipeline, multimodal computer vision (including chronological 2x2 video/GIF keyframe storyboards with Laplacian sharpness scoring), subject-faithful image/meme synthesis, and director-mode neural Text-to-Speech (TTS). (This was made with the help of Gemini Pro/Argon)

---

## Key Engineering Highlights

* **Cross-Process Concurrency & Nanosecond State Synchronization (`core_system.py`):**
  * Built an OS-agnostic, cross-process file-locking engine (`FileLock`) using atomic `O_CREAT | O_EXCL` lockfiles with automatic stale-lock eviction and atomic `.tmp` file replacement (`os.replace`).
  * Enables multiple independent bot processes to read and mutate shared JSON state (`chat_memory.json`, `shared_image_cache.json`, `general_knowledge.json`) concurrently with zero race conditions or file corruption, backed by a thread-safe (`threading.RLock`) nanosecond timestamp and byte-size (`(st_mtime_ns, st_size)`) in-memory cache.
  * Implements thread-local HTTP connection pooling (`threading.local` + `requests.Session` + `HTTPAdapter`) with warm keep-alive headers and automated socket pool resets across Cloudflare/gateway edge drops (`408`, `429`, `500`, `502`, `503`, `504`, `520`, `522`, `524`).
* **Multi-Agent Discovery, Target Attribution & Spontaneous Agency (`bot_discovery.py`):**
  * **Autonomous Service Discovery:** Active agents dynamically register their display names, IDs, and trigger keywords into a shared cross-process registry with automatic stale-agent pruning.
  * **Speaker-to-Target Disambiguation:** Resolves multi-agent conversation collisions by parsing sentence structure, prepositions, and Discord reply trees to annotate shared chat logs (`[User (speaking to Agent_A, NOT to Agent_B)]: ...`), preventing agents from hijacking commands or misattributing questions directed at other bots.
  * **Visual-Aware 3-Stage Contextual Follow-Up Engine:** Combines deterministic addressing and AFK/stepping-away filters (Stage 1), sliding-window lexical and visual-prompt token overlap (Stage 2), and a zero-temperature 10-token LLM classifier (Stage 3) to maintain natural multi-turn conversations—even when users comment directly on background details inside an agent's generated image—without requiring repeated `@mentions`.
  * **Dynamic Engagement & Dogpile Prevention:** Computes real-time conversational interest multipliers based on media uploads, open questions, and persona topic alignment, paired with an atomic cross-process cooldown lock (`_last_unprompted_chime`) so multiple agents never dogpile the same unprompted message.
* **3-Tier Fact Extraction, Hearsay Verification & Loop Prevention (`memory_and_context.py`):**
  * Separates long-term knowledge into three distinct tiers: **Per-User Dossiers** (`[LEARN_USER: user | fact]`), **Shared Universal Knowledge** (`[LEARN_GENERAL: fact]`), and **Server-Specific Lore** (`[LEARN: fact]`).
  * **Two-Stage Hearsay & Ephemeral Action Guard:** Combines deterministic regex/token-overlap heuristics with an asynchronous LLM verification pass to block third-party reported speech (*"he said..."*, *"they told me..."*), sarcasm, and one-off chat commands from polluting user profiles, while automatically routing universal scientific or world trivia into shared general memory.
  * **N-Gram & Stage-Direction Anti-Repetition Engine:** Dynamically analyzes each agent's recent turns using sliding 4-to-6-word n-gram frequency counters, opener tracking, and emote extraction (`text_and_parsers.py`) to actively prevent repetitive phrasing, cliché loops, and stage-direction overload.
* **Multimodal Vision, Subject-Faithful Image/Meme Synthesis & Director-Mode TTS (`media_engine.py`, `ai_services.py`):**
  * **2x2 Video, GIF & Sticker Storyboarding:** Uses OpenCV (`cv2`) and Pillow (`PIL`) to sample 4 chronological keyframes (`10%`, `36%`, `64%`, `90%`) from `.mp4`/`.webm` videos, animated GIFs/WebPs, Discord stickers, and OpenGraph embeds—scoring both contrast and Laplacian variance (`cv2.Laplacian`) to skip motion blur and black fade frames—and stamps sequence badges (`1`–`4`) onto a unified 2x2 storyboard grid.
  * **Cross-Process Vision Deduplication:** Coordinates `PROCESSING|<timestamp>` locks in `shared_image_cache.json` so when multiple agents see the same uploaded media simultaneously, only one Vision API call is executed and shared across all processes.
  * **Subject-Faithful Prompt Divergence & Auto-Fitting Meme Engine:** Enforces user-requested subject fidelity (`_extract_explicit_requested_subject`) while rewriting overlapping compositions (`ensure_unique_image_prompt`) and refreshing stale meme captions (`ensure_unique_meme_captions`). Renders classic Impact-style top/bottomcaptions via Pillow with dynamic font scaling, stroke outlines, and drop shadows.
  * **Expressive Neural Voice Synthesis:** Parses structured Director Mode scripts (`Role`, `Scene`, `Guidance`) and inline vocal performance tags (`[sigh]`, `[chuckle]`, `[pauses for a moment]`) for `MiMo-V2.5-tts-voicedesign`.
* **Self-Healing Resilience & In-Character Glitch Awareness:**
  * Automatic model fallback chains across primary and backup Vision (`Qwen3-VL-235B`, `Qwen2.5-VL-32B`, `Llama-3.2-90B-Vision`) and Text (`Qwen3-235B`, `Qwen3-30B`, `Llama-3.3-70B`) endpoints.
  * Non-mutating retry context injection converts transient HTTP/network drops into structured `[System Glitch Context]` events, allowing agents to acknowledge lag spikes naturally while stripping raw error strings to prevent mimicry loops.

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                                  DISCORD SERVER                                   |
|      User Messages, @Mentions, Replies, Images, Videos, Stickers, & Commands      |
+-----------------------------------------+-----------------------------------------+
                                          |
                   +----------------------+----------------------+
                   |                      |                      |
                   v                      v                      v
        +--------------------+ +--------------------+ +--------------------+
        |  guild_assistant   | |  custom_agent_01   | |  custom_agent_02   |
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
|  | suite, async locks,   |  | target disambiguation,|  | hearsay filter, n-gram|  |
|  | & coalescing pipeline |  | & 3-stage follow-ups  |  | anti-loop & verifier  |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|                                                                                   |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|  |    ai_services.py     |  |    media_engine.py    |  |   core_system.py      |  |
|  | Vision cache, emoji   |  | OpenCV Laplacian 2x2  |  | Cross-process atomic  |  |
|  | reactor, FLUX subject |  | storyboards, Pillow   |  | FileLock, nanosecond  |  |
|  | fidelity & MiMo TTS   |  | auto-fit meme engine  |  | RAM cache, HTTP pool  |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                  ATOMIC SHARED & PER-BOT PERSISTENCE (JSON + FileLock)            |
|                                                                                   |
|  [Shared Across All Agents]                 [Isolated Per-Agent Persona]          |
|  - chat_memory.json (Short-Term Context)    - <agent>_long_term_memory.json       |
|  - shared_image_cache.json (Vision & Reg)   - <agent>_user_profiles.json          |
|  - general_knowledge.json (Universal Facts) - <agent>_channel_configs.json        |
+-----------------------------------------------------------------------------------+
```

---

## Repository Structure

```text
discord-bot-app/
├── bots/
│   ├── guild_assistant.py                 # Reference steward & quartermaster AI agent
│   ├── custom_agent_alpha.py              # Additional autonomous persona script #1
│   ├── custom_agent_beta.py               # Additional autonomous persona script #2
│   └── bot_info/
│       ├── .env                           # API keys & Discord tokens (gitignored)
│       ├── bot_logs/                      # Per-agent background execution logs
│       ├── bot_startup_scripts/           # Auto-generated headless process runners
│       ├── config_files/                  # Per-channel runtime settings JSONs
│       ├── long_term_memory/              # Per-agent lore, user dossiers & shared general facts
│       ├── short_term_memory/             # Shared channel history & vision cache JSONs
│       └── python_modules/
│           ├── main_bot.py                # Core Discord client lifecycle & turn orchestration
│           ├── ai_services.py             # Vision, contextual emojis, image/meme & TTS engines
│           ├── bot_discovery.py           # Multi-agent registry, target attribution & follow-ups
│           ├── memory_and_context.py      # Hearsay verifier, n-gram anti-loop & history builder
│           ├── media_engine.py            # OpenCV/PIL storyboards, meme overlay & Giphy/YT
│           ├── text_and_parsers.py        # MiMo TTS tag sanitizer, emote pruner & text parser
│           ├── core_system.py             # Thread-local HTTP pool & cross-process FileLock
│           ├── config.py                  # Pre-compiled regexes & fallback model constants
│           └── bot_editor.py              # Interactive CLI for editing agent constants & keywords
├── manage_bots.bat                        # Windows CLI Manager (PowerShell/WMI process control)
├── manage_bots.sh                         # POSIX/Linux/macOS CLI Manager (Bash process control)
├── universal_copy_paste_bot_template.py   # Drop-in template for spinning up new AI personas
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
You can manage the entire lifecycle of your agents—including adding API keys, creating new personas, editing model parameters/keywords, and toggling background processes—using the included interactive CLI manager:

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
2. Add your Discord bot tokens to `bots/bot_info/.env` (e.g., `GUILDBOT_DISCORD_TOKEN=...`, `AGENT_ALPHA_DISCORD_TOKEN=...`) or press **`[C]` (Create Bot)** to generate a new agent script and register its token automatically.
3. Press **`[1-N]` (Toggle Bot)** to launch or stop any agent as a detached background process with automatic log redirection to `bots/bot_info/bot_logs/`.

---

## In-Chat Command Suite

### User Commands
| Command | Description |
| :--- | :--- |
| `@BotName !help` | Displays the complete in-chat command reference. |
| `@BotName !whoami` | Displays the permanent dossier facts this agent has learned about you. |
| `@BotName !forgetme <keyword\|all>` | Removes specific matching facts (or wipes `all` facts) from your personal profile. |

### Administrator Channel & Memory Controls
| Command | Description |
| :--- | :--- |
| `@BotName admin status` | Shows channel configuration, active media flags, and memory counts. |
| `@BotName admin enable` / `disable` | Enables or disables the agent in the current channel. |
| `@BotName admin reactonly on` / `off` | Toggles silent emoji-reaction-only mode in the current channel. |
| `@BotName admin chances <resp> <emoji>` | Sets baseline spontaneous chime-in (`0.0–1.0`) and emoji reaction (`0.0–1.0`) probabilities. |
| `@BotName admin random <minutes>` | Sets the unprompted periodic post timer in minutes (`0` = disabled). |
| `@BotName admin temp <0.1-1.5>` | Adjusts the LLM creativity temperature for the current channel. |
| `@BotName admin features <type> <on\|off>` | Toggles `vision`, `gifs`, `image` (images & memes), or `voice` per channel. |
| `@BotName admin memory` | Displays a snapshot of learned Server Lore, Shared General Facts, and User Profiles. |
| `@BotName admin learn <fact>` | Manually adds a server-specific lore entry. |
| `@BotName admin learngeneral <fact>` | Manually adds a shared universal knowledge fact across all agents. |
| `@BotName admin learnuser <user> \| <fact>` | Manually adds a fact to a specific user's dossier. |
| `@BotName admin forget <keyword>` | Purges any server lore, general fact, or user dossier entry matching `<keyword>`. |
| `@BotName !clearmemory` | Wipes short-term conversation history for the current channel. |

---

## Creating Custom Personas

To add a new autonomous agent with full access to shared vision, custom meme generation, and cross-agent discovery:
1. Copy `universal_copy_paste_bot_template.py` into the `bots/` directory (e.g., `bots/archivist_agent.py`) or use option **`[C]`** in the CLI manager.
2. Customize `BOT_SLUG`, `DISCORD_TOKEN_ENV_VAR`, `TRIGGER_KEYWORDS`, `IMAGE_STYLE_HINT`, and `VOICE_ROLE_HINT`.
3. Use option **`[E]` (Edit Config)** in the CLI manager at any time to live-edit constants or manage trigger keywords via `bot_editor.py`.

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for full details.