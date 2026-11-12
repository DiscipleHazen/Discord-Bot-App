#!/usr/bin/env bash

# ANSI Colors
CYAN='\033[36m'
MAGENTA='\033[35m'
GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
GRAY='\033[90m'
NC='\033[0m'
BOLD='\033[1m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/bots" ]; then
    BOTS_DIR="$SCRIPT_DIR/bots"
else
    BOTS_DIR="$SCRIPT_DIR"
fi

mkdir -p "$BOTS_DIR/bot_info/bot_logs"
mkdir -p "$BOTS_DIR/bot_info/bot_startup_scripts"
mkdir -p "$BOTS_DIR/bot_info/python_modules"

if command -v python3 >/dev/null 2>&1; then PY_BIN="$(command -v python3)"; else PY_BIN="$(command -v python)"; fi

get_bot_pid() {
    ps aux | grep -E "[p]ython3? .*$BOTS_DIR/$1" | awk '{print $2}' | head -n 1
}

menu() {
    clear
    echo -e "${CYAN}===================================================================${NC}"
    echo -e "                     ${BOLD}${MAGENTA}DISCORD BOT MANAGER${NC} ${GRAY}(POSIX)${NC}      "
    echo -e "${CYAN}===================================================================${NC}\n"
    
    echo -e "${CYAN}[AVAILABLE BOTS]${NC}"
    bot_names=()
    count=0
    for file in "$BOTS_DIR"/*.py; do
        [ -e "$file" ] || continue
        count=$((count + 1))
        bname="$(basename "$file")"
        bot_names+=("$bname")
        
        if pgrep -f "[p]ython3? .*$BOTS_DIR/$bname" > /dev/null; then
            status="${GREEN}[ ONLINE  ]${NC}"
        else
            status="${GRAY}[ OFFLINE ]${NC}"
        fi
        printf "  ${MAGENTA}%d.${NC} %-30s %b\n" "$count" "$bname" "$status"
    done
    if [ "$count" -eq 0 ]; then echo -e "  ${YELLOW}No .py scripts found in: $BOTS_DIR${NC}"; fi

    echo -e "\n${CYAN}===================================================================${NC}\n"
    echo -e "Options:"
    echo -e "  ${MAGENTA}[1-$count]${NC} Toggle Bot   (Starts bot if offline / Stops bot if online)"
    echo -e "  ${MAGENTA}[C]${NC}     Create Bot   (Generate a new bot with a custom prompt)"
    echo -e "  ${MAGENTA}[E]${NC}     Edit Config  (Change variables, models, or manage keywords)"
    echo -e "  ${MAGENTA}[H]${NC}     History      (Set the global max history length)"
    echo -e "  ${MAGENTA}[A]${NC}     API Keys     (Add or update Discord/DeepInfra/Giphy keys)"
    echo -e "  ${MAGENTA}[K]${NC}     Kill All     (Force close every running bot process)"
    echo -e "  ${MAGENTA}[R]${NC}     Refresh      (Reload the menu and online statuses)"
    echo -e "  ${MAGENTA}[Q]${NC}     Quit         (Close this manager window)\n"
    read -rp "Select an option: " choice

    case "$choice" in
        [qQ]) exit 0 ;;
        [rR]) menu ;;
        [kK]) kill_all; menu ;;
        [cC]) create_bot; menu ;;
        [aA]) add_key; menu ;;
        [eE]) edit_config; menu ;;
        [hH]) set_history; menu ;;
        *)
            if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$count" ]; then
                selected_idx=$((choice - 1))
                toggle_bot "${bot_names[$selected_idx]}"
                menu
            else
                echo -e "${RED}[ERROR] Invalid choice.${NC}"
                sleep 2; menu
            fi
            ;;
    esac
}

toggle_bot() {
    local target_name="$1"
    local script_path="$BOTS_DIR/$target_name"
    local log_file="$BOTS_DIR/bot_info/bot_logs/debug_${target_name}.log"
    local sh_script="$BOTS_DIR/bot_info/bot_startup_scripts/headless_start_${target_name}.sh"
    local env_file="$BOTS_DIR/bot_info/.env"

    clear
    echo -e "${CYAN}Managing: $target_name${NC}"
    echo -e "${CYAN}-------------------------------------------------------------------${NC}"

    local current_pid=$(get_bot_pid "$target_name")
    if [ -n "$current_pid" ]; then
        echo -e "${YELLOW}[STOPPING] Terminating PID $current_pid...${NC}"
        kill -15 "$current_pid" 2>/dev/null || kill -9 "$current_pid" 2>/dev/null
        sleep 1
        echo -e "${RED}[SUCCESS] Bot process stopped.${NC}"
    else
        # Env Check
        if grep -q "TOKEN = os.getenv(" "$script_path"; then
            tok_var=$(grep "TOKEN = os.getenv(" "$script_path" | awk -F'"' '{print $2}')
            if [ -z "$tok_var" ]; then tok_var=$(grep "TOKEN = os.getenv(" "$script_path" | awk -F"'" '{print $2}'); fi
            if ! grep -q "^${tok_var}=" "$env_file" 2>/dev/null; then
                echo -e "${RED}[ERROR] Missing $tok_var in bot_info/.env${NC}"
                read -rp "Press Enter to continue..."; return
            fi
        fi

        echo -e "${CYAN}[STARTING] Launching detached background process...${NC}"
        cat <<EOF > "$sh_script"
#!/usr/bin/env bash
cd "$BOTS_DIR"
exec "$PY_BIN" "$script_path" > "$log_file" 2>&1
EOF
        chmod +x "$sh_script"
        nohup "$sh_script" >/dev/null 2>&1 &
        sleep 2
        local new_pid=$(get_bot_pid "$target_name")
        if [ -n "$new_pid" ]; then echo -e "${GREEN}[SUCCESS] Bot started successfully in background (PID: $new_pid)${NC}"
        else echo -e "${RED}[ERROR] Bot crashed or failed to start. Check $log_file${NC}"; fi
    fi
    echo ""; read -rp "Press Enter to continue..."
}

edit_config() {
    clear
    echo -e "${CYAN}=== SELECT BOT TO EDIT ===${NC}\n"
    bot_names=()
    count=0
    for file in "$BOTS_DIR"/*.py; do
        [ -e "$file" ] || continue
        count=$((count + 1))
        bname="$(basename "$file")"
        bot_names+=("$bname")
        echo -e "  ${MAGENTA}$count.${NC} $bname"
    done
    echo -e "  ${MAGENTA}0.${NC} Go Back\n"
    read -rp "Choice (number): " choice
    if [ "$choice" == "0" ] || [ -z "$choice" ]; then return; fi
    
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$count" ]; then
        if [ ! -f "$BOTS_DIR/bot_info/python_modules/bot_editor.py" ]; then
            echo -e "${RED}[ERROR] bot_editor.py not found in bot_info/python_modules${NC}"
            sleep 3; return
        fi
        "$PY_BIN" "$BOTS_DIR/bot_info/python_modules/bot_editor.py" "$BOTS_DIR/${bot_names[$((choice-1))]}"
    fi
}

set_history() {
    clear
    echo -e "${CYAN}=== SET GLOBAL HISTORY LENGTH ===${NC}\n"
    global_hist=200
    if [ -f "$BOTS_DIR/bot_info/.env" ]; then
        env_hist=$(grep "^MAX_HISTORY_LENGTH=" "$BOTS_DIR/bot_info/.env" | cut -d'=' -f2)
        [ -n "$env_hist" ] && global_hist="$env_hist"
    fi
    echo -e "${YELLOW}Current Max History Length: $global_hist${NC}\n"
    read -rp "Enter new global history length (e.g. 200) or 0 to Go Back: " new_hist
    [ -z "$new_hist" ] || [ "$new_hist" == "0" ] && return
    
    env_file="$BOTS_DIR/bot_info/.env"
    mkdir -p "$BOTS_DIR/bot_info"
    touch "$env_file"
    if grep -q "^MAX_HISTORY_LENGTH=" "$env_file"; then
        sed -i.bak "s|^MAX_HISTORY_LENGTH=.*|MAX_HISTORY_LENGTH=$new_hist|" "$env_file"
    else
        echo "MAX_HISTORY_LENGTH=$new_hist" >> "$env_file"
    fi
    echo -e "\n${GREEN}[SUCCESS] Set global history length to $new_hist${NC}"
    read -rp "Press Enter to continue..."
}

kill_all() {
    clear
    echo -e "${YELLOW}[INFO] Terminating all running bot processes...${NC}"
    local pids=$(ps aux | grep -E "[p]ython3? .*$BOTS_DIR/.*\.py" | awk '{print $2}')
    if [ -n "$pids" ]; then
        echo "$pids" | while read -r pid; do
            kill -9 "$pid" 2>/dev/null
            echo -e "${RED}[KILLED] Stopped PID $pid${NC}"
        done
    else echo -e "${YELLOW}No running bot processes to kill.${NC}"; fi
    echo ""; read -rp "Press Enter to continue..."
}

create_bot() {
    clear
    echo -e "${CYAN}=== CREATE A NEW BOT ===${NC}\n"
    read -rp "Enter Bot Name (e.g. ServerBot) or press Enter to cancel: " bname
    [ -z "$bname" ] && return
    read -rp "Enter Discord API Token: " btok
    [ -z "$btok" ] && return
    read -rp "Enter System Prompt Identity: " bprompt
    [ -z "$bprompt" ] && return

    safe_name=$(echo "$bname" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | tr -cd 'a-z0-9_')
    tok_key=$(echo "$safe_name" | tr '[:lower:]' '[:upper:]')_DISCORD_TOKEN
    env_file="$BOTS_DIR/bot_info/.env"
    
    mkdir -p "$BOTS_DIR/bot_info"
    touch "$env_file"
    if grep -q "^$tok_key=" "$env_file"; then
        sed -i.bak "s|^$tok_key=.*|$tok_key=$btok|" "$env_file"
    else
        echo "$tok_key=$btok" >> "$env_file"
    fi

    cat <<EOF > "$BOTS_DIR/$safe_name.py"
import sys, os
from dotenv import load_dotenv
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, 'bot_info', 'python_modules'))
import manage_discord_bots as mdb
load_dotenv(os.path.join(current_dir, 'bot_info', '.env'))

SYSTEM_PROMPT = {'role': 'system', 'content': """$bprompt"""}

# --- BOT CONFIGURATION CONSTANTS ---
TOKEN = os.getenv('$tok_key')
API_KEY = os.getenv('DEEPINFRA_TOKEN')
GIPHY_KEY = os.getenv('GIPHY_TOKEN')

CHAT_MEMORY_FILE = os.path.join(current_dir, 'bot_info', 'short_term_memory', 'chat_memory.json')
SHARED_IMAGE_CACHE_FILE = os.path.join(current_dir, 'bot_info', 'short_term_memory', 'shared_image_cache.json')
LONG_TERM_MEMORY_FILE = os.path.join(current_dir, 'bot_info', 'long_term_memory', '${safe_name}_long_term_memory.json')
CHANNEL_CONFIG_FILE = os.path.join(current_dir, 'bot_info', 'config_files', '${safe_name}_channel_configs.json')

TEXT_MODEL = 'Qwen/Qwen3-235B-A22B-Instruct-2507'
VISION_MODEL = 'Qwen/Qwen3-VL-235B-A22B-Instruct'
ENABLE_VISION = True
ENABLE_GIFS = True
TEMPERATURE = 0.65
TOP_P = 0.8
OUTPUT_LENGTH = 500
CONTEXT_WINDOW = 50
EMOJI_CONTEXT_LENGTH = 6
CHANCE_TO_RESPOND = 0.05
RANDOM_EMOJI_CHANCE = 0.25
TRIGGER_KEYWORDS = ['$safe_name']

if __name__ == '__main__':
    mdb.run_bot(token=TOKEN, api_key=API_KEY, giphy_key=GIPHY_KEY, system_prompt=SYSTEM_PROMPT, chat_memory_file=CHAT_MEMORY_FILE, shared_image_cache_file=SHARED_IMAGE_CACHE_FILE, long_term_memory_file=LONG_TERM_MEMORY_FILE, channel_config_file=CHANNEL_CONFIG_FILE, text_model=TEXT_MODEL, vision_model=VISION_MODEL, enable_vision=ENABLE_VISION, enable_gifs=ENABLE_GIFS, temperature=TEMPERATURE, top_p=TOP_P, output_length=OUTPUT_LENGTH, context_window=CONTEXT_WINDOW, emoji_context_length=EMOJI_CONTEXT_LENGTH, chance_to_respond=CHANCE_TO_RESPOND, random_emoji_chance=RANDOM_EMOJI_CHANCE, keywords=TRIGGER_KEYWORDS)
EOF
    echo -e "${GREEN}[SUCCESS] Created $safe_name.py and saved token!${NC}"
    echo ""; read -rp "Press Enter to continue..."
}

add_key() {
    clear
    echo -e "${CYAN}=== ADD / UPDATE API KEYS ===${NC}\n"
    echo -e "  ${MAGENTA}1.${NC} DeepInfra API Token"
    echo -e "  ${MAGENTA}2.${NC} Giphy API Token"
    echo -e "  ${MAGENTA}3.${NC} Enter custom key name..."
    echo -e "  ${MAGENTA}0.${NC} Go Back\n"
    read -rp "Select an option: " choice
    
    if [ "$choice" == "0" ] || [ -z "$choice" ]; then return; fi
    if [ "$choice" == "1" ]; then key_name="DEEPINFRA_TOKEN"
    elif [ "$choice" == "2" ]; then key_name="GIPHY_TOKEN"
    elif [ "$choice" == "3" ]; then read -rp "Enter Key Name: " key_name
    else return; fi
    
    [ -z "$key_name" ] && return
    read -rp "Enter Token Value for $key_name: " key_val
    [ -z "$key_val" ] && return
    
    env_file="$BOTS_DIR/bot_info/.env"
    mkdir -p "$BOTS_DIR/bot_info"
    touch "$env_file"
    if grep -q "^$key_name=" "$env_file"; then
        sed -i.bak "s|^$key_name=.*|$key_name=$key_val|" "$env_file"
    else
        echo "$key_name=$key_val" >> "$env_file"
    fi
    echo -e "${GREEN}[SUCCESS] Saved $key_name to bot_info/.env${NC}"
    echo ""; read -rp "Press Enter to continue..."
}

menu