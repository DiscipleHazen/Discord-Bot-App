# Multi-Agent Discord AI Ecosystem

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Discord.py](https://img.shields.io/badge/Discord.py-Asynchronous-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-Video_Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white)
![Pillow](https://img.shields.io/badge/Pillow-Meme_Engine-3776AB?style=for-the-badge)
![DeepInfra](https://img.shields.io/badge/DeepInfra-Multimodal_AI-000000?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

A production-grade, distributed multi-agent Discord bot framework written in Python. Engineered to run multiple autonomous, distinct AI agents concurrently within the same Discord channels, this system features cross-process atomic state synchronization with OS-level dead-PID lock recovery, an IDF-weighted 3-tier memory verification pipeline, multimodal computer vision (including motion-aware 2x2 video/GIF keyframe storyboards with Laplacian sharpness scoring), luminance-adaptive meme typography, and director-mode neural Text-to-Speech (TTS). (This was made with the help of Gemini Pro/Argon)

---

## Key Engineering Highlights

* **Cross-Process Concurrency, Dead-PID Lock Recovery & Adaptive Circuit Breakers (`core_system.py`, `main_bot.py`):**
  * **OS-Agnostic Atomic File Locking:** Built a cross-process synchronization engine (`FileLock`) using atomic `O_CREAT | O_EXCL` lockfiles, monotonic timeout tracking, and atomic `.tmp` file replacement (`os.replace`). Inspects lockfile `<pid>|<timestamp>` headers against the OS process table (`OpenProcess`/`GetExitCodeProcess` on Windows; `os.kill(pid, 0)` on POSIX) to evict locks held by terminated processes in `0ms`.
  * **Zero-Copy Serialization & Nanosecond RAM Cache:** Enables independent bot processes to read and mutate shared JSON state (`chat_memory.json`, `shared_image_cache.json`, `general_knowledge.json`) concurrently without race conditions. Backed by a thread-safe (`threading.RLock`) `(st_mtime_ns, st_size)` cache that stores pre-serialized JSON payloads to eliminate redundant `copy.deepcopy` calls and skip no-op disk writes, paired with non-blocking `asyncio.to_thread` I/O wrappers.
  * **Two-Phase Turn Coalescing & Bounded Waiter Queue:** Separates pre-snapshot message coalescing windows (`channel_in_coalesce_window`) from in-flight API generation locks (`channel_pending_waiters`) and watermark tracking (`max_hist_msg_id`), ensuring rapid multi-line user bursts coalesce into a single turn while mid-flight `@mentions` queue cleanly without dropping or double-posting.
  * **Per-Model Adaptive Circuit Breaker & HTTP Pooling:** Maintains thread-local `requests.Session` connection pools (`HTTPAdapter`) with automated socket resets and truncated-JSON payload validation. Tracks consecutive gateway failures (`502`, `503`, `504`, `522`, `524`, timeouts) per `(endpoint, model)` pair to trip fast-fail cooldown windows that route directly to backup models without stalling the event loop.
* **Multi-Agent Discovery, Target Attribution & Turn Coordination (`bot_discovery.py`):**
  * **Low-I/O Service Discovery:** Active agents dynamically register their display names, aliases, and trigger keywords into a shared cross-process registry that throttles unchanged heartbeat writes to 30-minute intervals (eliminating 97% of idle disk I/O).
  * **Speaker-to-Target Disambiguation:** Resolves multi-agent conversation collisions by parsing sentence structure, prepositions, and Discord reply trees to annotate shared chat logs (`[User (speaking to Agent_A, NOT to Agent_B)]: ...`), preventing agents from hijacking commands or misattributing questions directed at other bots.
  * **4-Stage Contextual Follow-Up Engine:** Combines `O(1)` persistent tombstone and turn-cap checks (`max_followup_turns = 2` with active speaker tracking, Stage 0), deterministic addressing, AFK/stepping-away, dismissal closure, and third-person side-comment filters (Stage 1), stem-aware lexical/visual overlap and clarification fast-paths (Stage 2), and a zero-temperature 10-token LLM classifier (Stage 3) with atomic per-message claim locks (`_claimed_followup_msgs`).
  * **Cross-Bot Floor Yielding & Dogpile Prevention:** Scales spontaneous engagement multipliers using media cues, open questions, and persona stem alignment while enforcing cross-bot active-exchange yielding (`is_other_bot_in_active_exchange`) and atomic channel-wide cooldown slots (`_last_unprompted_chime`) so unprompted agents never interrupt another bot's active 1-on-1 exchange.
* **IDF-Weighted Memory Retrieval, Hearsay Verification & Loop Prevention (`memory_and_context.py`, `text_and_parsers.py`):**
  * **3-Tier Knowledge Architecture & Superseding Consolidation:** Separates persistent memory into **Per-User Dossiers** (`[LEARN_USER: user | fact]`), **Shared Universal Knowledge** (`[LEARN_GENERAL: fact]`), and **Server-Specific Lore** (`[LEARN: fact]`). Automatically consolidates overlapping facts in-place (`consolidate_and_append_fact`) when more detailed information is learned, and self-heals memory files on startup by pruning ephemeral chat recaps.
  * **IDF-Weighted Specificity Gate:** Scores stored memories against active conversation stems by weighting high-specificity domain and proper nouns (`2.5x`) against a 120+ word low-information conversational lexicon (`0.25x`), requiring a specificity threshold `>= 2.0` before injecting passive memories into the system prompt.
  * **Zero-Latency First-Person Fast-Path & LLM Hearsay Verifier:** Validates unambiguous first-person self-disclosures in `0ms` with zero extra API calls while routing ambiguous or multi-user attributions through an asynchronous zero-temperature LLM verifier that blocks third-party reported speech (*"he said..."*, *"they told me..."*) and temporary chat actions.
  * **Temporal Session Boundaries & Anti-Repetition Engine:** Tracks message creation timestamps to inject `[System Time Marker]` boundaries after `>= 45` minutes of silence, resets topic-fixation counters across sessions, forbids verbatim bio-quote parroting, strips novel-style outer dialogue quotes, and prunes excessive inline stage directions.
* **Multimodal Vision, Luminance-Adaptive Meme Synthesis & Director-Mode TTS (`media_engine.py`, `ai_services.py`):**
  * **Perceptual Motion-Aware 2x2 Video & GIF Storyboarding:** Uses OpenCV (`cv2`) and Pillow (`PIL`) under a bounded hardware semaphore (`threading.BoundedSemaphore(2)`) to sample 4 chronological keyframes (`10%`, `36%`, `64%`, `90%`) from videos, animated GIFs/WebPs, and Discord stickers—scoring contrast, Laplacian sharpness (`cv2.Laplacian`), and 16x16 grayscale frame-to-frame motion delta—and stamps sequence badges (`1`–`4`) onto a unified 2x2 storyboard grid.
  * **Cross-Process Vision Deduplication & Zero-Cost Link Context:** Coordinates atomic `PROCESSING|<timestamp>|<bot_id>` locks in `shared_image_cache.json` and an in-memory SHA-256 digest cache (`_VISION_HASH_CACHE`) so uploaded media is analyzed by the 235B Vision model only once across all processes and reused by `auto_react` on the fast text model. Filters out generic webpage favicons (`is_visual_media_candidate`) while extracting `0ms` article titles and descriptions from embeds (`extract_webpage_embed_context`).
  * **Subject-Faithful Prompt Grounding & Luminance-Adaptive Meme Engine:** Strips conversational filler from bot replies, enforces multi-line and history-aware user subject fidelity (`_extract_explicit_requested_subject`), rewrites overlapping visual compositions, and refreshes stale meme punchlines. Renders classic Impact-style captions via Pillow with automatic clause-pivot setup/punchline splitting, vertical center-subject safe zones (preserving the middle 52% of the canvas), and `ImageStat` background luminance sampling to boost stroke width and shadow alpha over bright regions.
  * **Expressive Neural Voice Synthesis:** Rescues trailing media/memory tags from unclosed voice blocks, validates pronounceable dialogue, and parses Director Mode headers (`Role`, `Scene`, `Guidance`) and inline vocal performance tags (`[sigh]`, `[chuckle]`, `[pauses for a moment]`) for `MiMo-V2.5-tts-voicedesign`.
* **Self-Healing Resilience & In-Character Glitch Awareness:**
  * Automatic model failover chains across primary and backup Vision (`Qwen3-VL-235B`, `Qwen2.5-VL-32B`, `Llama-3.2-90B-Vision`) and Text (`Qwen3-235B`, `Qwen3-30B`, `Llama-3.3-70B`) endpoints.
  * Sanitizes upstream HTML/gateway error strings (`sanitize_api_error_text`) and converts transient API drops into structured `[System Glitch Context]` events, allowing agents to react to lag spikes in-character without echoing raw error strings.

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
|  | Two-phase turn locks, |  | Cross-bot registry,   |  | IDF specificity gate, |  |
|  | atomic vision claims, |  | floor-yielding logic, |  | fact consolidation,   |  |
|  | & coalescing pipeline |  | & 4-stage follow-ups  |  | & hearsay verifier    |  |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|                                                                                   |
|  +-----------------------+  +-----------------------+  +-----------------------+  |
|  |    ai_services.py     |  |    media_engine.py    |  |   core_system.py      |  |
|  | SHA-256 vision cache, |  | Motion/Laplacian 2x2  |  | Dead-PID FileLock,    |  |
|  | subject-faithful FLUX,|  | storyboards & Pillow  |  | circuit breaker, RAM  |  |
|  | emoji & MiMo TTS      |  | luminance meme engine |  | cache & HTTP pooling  |  |
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
multi-agent-discord-framework/
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
│           ├── ai_services.py             # Vision cache, contextual emojis, image/meme & TTS
│           ├── bot_discovery.py           # Multi-agent registry, floor yielding & follow-ups
│           ├── memory_and_context.py      # IDF retrieval, fact consolidation & hearsay filter
│           ├── media_engine.py            # OpenCV/PIL storyboards, luminance memes & Giphy/YT
│           ├── text_and_parsers.py        # MiMo TTS tag sanitizer, quote de-wrapper & parser
│           ├── core_system.py             # Dead-PID FileLock, circuit breaker & HTTP pool
│           ├── config.py                  # Pre-compiled regexes, IDF lexicons & fallbacks
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
git clone https://github.com/disciplehazen/multi-agent-discord-framework.git
cd multi-agent-discord-framework
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
| `@BotName admin learn <fact>` | Manually adds or consolidates a server-specific lore entry. |
| `@BotName admin learngeneral <fact>` | Manually adds or consolidates a shared universal knowledge fact across all agents. |
| `@BotName admin learnuser <user> \| <fact>` | Manually adds or consolidates a fact in a specific user's dossier. |
| `@BotName admin forget <keyword>` | Purges any server lore, general fact, or user dossier entry matching `<keyword>`. |
| `@BotName !clearmemory` | Wipes short-term conversation history and recent generated media cache for the current channel. |

---

## Creating Custom Personas

To add a new autonomous agent with full access to shared vision, custom meme generation, and cross-agent discovery:
1. Copy `universal_copy_paste_bot_template.py` into the `bots/` directory (e.g., `bots/archivist_agent.py`) or use option **`[C]`** in the CLI manager.
2. Customize `BOT_SLUG`, `DISCORD_TOKEN_ENV_VAR`, `TRIGGER_KEYWORDS`, `IMAGE_STYLE_HINT`, and `VOICE_ROLE_HINT`.
3. Use option **`[E]` (Edit Config)** in the CLI manager at any time to live-edit constants or manage trigger keywords via `bot_editor.py`.

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for full details.