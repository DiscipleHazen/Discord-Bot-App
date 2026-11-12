import re
import json
import copy
import time

from config import (
    STOPWORDS, FACT_HEARSAY_PREFIX_REGEX, THIRD_PARTY_REPORT_REGEX,
    JSON_EMOJI_REGEX, ERROR_REPLY_STRIP_REGEX, ERROR_REPLY_CAPTURE_REGEX,
    FALLBACK_IMAGE_NOTE_REGEX, IMAGE_DESC_STRIP_REGEX
)
from core_system import post_with_backoff, update_json_file

# ==============================================================================
# MEMORY DEDUPLICATION, HEARSAY FILTER & CONVERSATION HISTORY BUILDER
# ==============================================================================

def extract_glitch_summary(raw_text: str) -> str:
    """
    Extracts a clean human-readable description from a raw *(API Error ...)* or
    *(System: ...)* notice so it can be logged as a structured System Glitch Event
    without keeping the raw mimic-prone string.
    """
    if not raw_text:
        return ""
    matches = ERROR_REPLY_CAPTURE_REGEX.findall(raw_text)
    if not matches:
        return ""
    cleaned_parts = []
    for m in matches:
        s = re.sub(r'^System:\s*', '', m.strip(), flags=re.IGNORECASE)
        s = re.sub(r'\s*Try again in a moment\.?', '', s, flags=re.IGNORECASE).strip()
        if s:
            cleaned_parts.append(s)
    return "; ".join(cleaned_parts)


def strip_old_image_data(history: list, keep_images: int = 0) -> list:
    image_count = 0
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if isinstance(msg.get("content"), list):
            has_image = any(isinstance(item, dict) and item.get("type") == "image_url" for item in msg["content"])
            if has_image:
                image_count += 1
                if image_count > keep_images:
                    text_parts = [
                        item.get("text", "")
                        for item in msg["content"]
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    msg["content"] = "\n".join(t for t in text_parts if t).strip()
    return history


def normalize_tokens(text: str) -> set[str]:
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def is_duplicate_fact(new_fact: str, existing_facts: list[str], threshold: float = 0.60) -> bool:
    new_clean = new_fact.strip().lower()
    if not new_clean or len(new_clean) < 4:
        return True
    new_tokens = normalize_tokens(new_clean)
    if not new_tokens:
        return True

    for existing in existing_facts:
        ex_clean = existing.strip().lower()
        if new_clean == ex_clean or new_clean in ex_clean or ex_clean in new_clean:
            return True
        ex_tokens = normalize_tokens(ex_clean)
        if ex_tokens:
            overlap = len(new_tokens & ex_tokens) / float(min(len(new_tokens), len(ex_tokens)))
            if overlap >= threshold:
                return True
    return False


def find_canonical_username(uname: str, user_profiles: dict, recent_users: set[str]) -> str:
    clean = uname.strip().lstrip('@').strip()
    if not clean:
        return ""
    for existing in user_profiles.keys():
        if str(existing).lower() == clean.lower():
            return str(existing)
    for ru in recent_users:
        if ru and str(ru).lower() == clean.lower():
            return str(ru)
    if len(clean) >= 3:
        for existing in user_profiles.keys():
            if str(existing).lower().startswith(clean.lower()):
                return str(existing)
        for ru in recent_users:
            if ru and str(ru).lower().startswith(clean.lower()):
                return str(ru)
    return clean


def is_misattributed_hearsay(fact: str, target_user: str, speaker_name: str, user_message: str) -> bool:
    """
    Deterministic filter that catches third-party reported speech / hearsay.
    """
    if not fact or not user_message:
        return False

    if FACT_HEARSAY_PREFIX_REGEX.search(fact):
        return True

    if target_user.strip().lower() != speaker_name.strip().lower():
        return False

    msg_clean = re.sub(r'^\[Replying to [^\]]+\]\s*', '', user_message.strip(), flags=re.IGNORECASE)
    msg_clean = re.sub(r'^\[[^\]]+\]:\s*', '', msg_clean.strip())

    report_matches = list(THIRD_PARTY_REPORT_REGEX.finditer(msg_clean))
    if not report_matches:
        return False

    fact_tokens = normalize_tokens(fact)
    if not fact_tokens:
        return True

    first_report_start = report_matches[0].start()
    before_report_text = msg_clean[:first_report_start]
    reported_clause_text = msg_clean[first_report_start:]

    reported_tokens = normalize_tokens(reported_clause_text)
    before_tokens = normalize_tokens(before_report_text)

    overlap_with_reported = len(fact_tokens & reported_tokens) / float(len(fact_tokens))
    overlap_with_before = len(fact_tokens & before_tokens) / float(len(fact_tokens)) if before_tokens else 0.0

    if overlap_with_reported >= 0.45 and overlap_with_before < 0.40:
        return True

    if re.search(r'\bi\s+(?:said|told\s+\w+)\s+(?:that\s+)?(?:they|he|she|someone)\s+(?:said|told|claimed|thinks|thought)\b', msg_clean, re.IGNORECASE):
        if overlap_with_reported >= 0.30:
            return True

    return False


async def verify_and_resolve_user_facts(
    candidate_facts: list[tuple[str, str]],
    speaker_name: str,
    recent_user_prompt: str,
    raw_history: list[dict],
    user_profiles: dict,
    recent_users: set[str],
    other_bot_names: set[str],
    my_bot_name: str,
    api_key: str,
    text_model: str,
    api_url: str
) -> list[tuple[str, str]]:
    """
    Verifies candidate (target_user, fact) pairs extracted from [LEARN_USER: user | fact].
    """
    pre_filtered: list[tuple[str, str]] = []
    for raw_uname, raw_fact in candidate_facts:
        canonical_uname = find_canonical_username(raw_uname, user_profiles, recent_users)
        clean_fact = raw_fact.strip().rstrip(".")
        if not canonical_uname or not clean_fact:
            continue
        if canonical_uname.lower() == my_bot_name.lower() or canonical_uname.lower() in other_bot_names:
            continue
        if is_misattributed_hearsay(clean_fact, canonical_uname, speaker_name, recent_user_prompt):
            print(f"[{my_bot_name}] [MEMORY FILTER] Blocked hearsay misattribution to {canonical_uname}: '{clean_fact}'")
            continue
        pre_filtered.append((canonical_uname, clean_fact))

    if not pre_filtered:
        return []

    context_lines = []
    for m in (raw_history or [])[-6:]:
        if m.get("role") == "system":
            continue
        c = m.get("content", "")
        if isinstance(c, str) and c.strip():
            context_lines.append(c.strip()[:260])
    if recent_user_prompt and recent_user_prompt.strip() not in "\n".join(context_lines):
        context_lines.append(f"[{speaker_name}]: {recent_user_prompt.strip()[:260]}")

    context_block = "\n".join(context_lines[-6:])
    known_human_users = sorted({
        u for u in (set(user_profiles.keys()) | set(recent_users))
        if u and str(u).lower() != my_bot_name.lower() and str(u).lower() not in other_bot_names
    })

    candidates_json = json.dumps([
        {"index": idx, "target_user": u, "fact": f}
        for idx, (u, f) in enumerate(pre_filtered)
    ], ensure_ascii=False)

    verify_prompt = (
        "You are a strict memory attribution verifier for a Discord server.\n"
        f"Current Speaker: {speaker_name}\n"
        f"Known Server Members: {', '.join(known_human_users) if known_human_users else speaker_name}\n\n"
        f"Recent Conversation:\n{context_block}\n\n"
        f"Candidate Facts to Verify:\n{candidates_json}\n\n"
        "RULES:\n"
        "1. Mark status as \"VALID\" ONLY if `target_user` directly stated, owned, or confirmed `fact` about THEMSELVES.\n"
        "2. If the speaker was quoting or reporting what a third party said (e.g., \"they said...\", \"he told me...\"), mark status as \"REJECT\".\n"
        "3. If the statement is clearly a true fact about a DIFFERENT explicitly named Known Server Member, mark status as \"REATTRIBUTE\" and set `target_user` to that member's exact name.\n"
        "4. If the statement is pure sarcasm, roleplay banter, or a temporary state, mark status as \"REJECT\".\n"
        "Return ONLY valid JSON in this exact format:\n"
        "{\"results\": [{\"index\": 0, \"status\": \"VALID\", \"target_user\": \"Name\", \"fact\": \"fact text\"}]}"
    )

    payload = {
        "model": text_model,
        "messages": [
            {"role": "system", "content": "Output only valid JSON."},
            {"role": "user", "content": verify_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 180
    }

    res = await post_with_backoff(
        api_url,
        headers={"Authorization": f"Bearer {api_key}"},
        json_payload=payload,
        timeout=15,
        max_retries=1,
        bot_name=my_bot_name
    )

    if res and res.status_code == 200:
        try:
            raw_content = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            json_match = JSON_EMOJI_REGEX.search(raw_content)
            if json_match:
                parsed = json.loads(json_match.group(0)).get("results", [])
                verified_list: list[tuple[str, str]] = []
                for item in parsed:
                    status = str(item.get("status", "")).upper()
                    t_user = str(item.get("target_user", "")).strip()
                    f_text = str(item.get("fact", "")).strip().rstrip(".")
                    if status == "REJECT" or not t_user or not f_text:
                        print(f"[{my_bot_name}] [MEMORY VERIFIER] Rejected misattributed/hearsay fact: '{f_text}'")
                        continue
                    if status in ("VALID", "REATTRIBUTE"):
                        canon_target = find_canonical_username(t_user, user_profiles, recent_users)
                        if canon_target.lower() in ("they", "them", "he", "him", "she", "her", "someone", "friend", "unknown"):
                            continue
                        if canon_target.lower() == my_bot_name.lower() or canon_target.lower() in other_bot_names:
                            continue
                        verified_list.append((canon_target, f_text))
                return verified_list
        except Exception as e:
            print(f"[{my_bot_name}] [WARN] Memory verifier parse fallback: {e}")

    return pre_filtered


def build_api_messages_from_history(
    dynamic_system: dict,
    raw_history: list[dict],
    bot_user_id: int,
    bot_display_name: str
) -> list[dict]:
    """
    Builds clean conversation history for the LLM.
    """
    api_messages = [dynamic_system]
    seen_msg_ids = set()
    own_recent_openers = []

    for idx, msg in enumerate(raw_history):
        msg_id = msg.get("message_id")
        if msg_id and msg_id in seen_msg_ids:
            continue
        if msg_id:
            seen_msg_ids.add(msg_id)

        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue

        if role == "system":
            if idx >= len(raw_history) - 10:
                api_messages.append({"role": "system", "content": content})
            continue

        is_self = (
            msg.get("author_id") == bot_user_id
            or (role == "assistant" and msg.get("author_name") == bot_display_name)
        )

        if is_self:
            own_text = content
            glitch_summary = extract_glitch_summary(own_text)
            img_note_match = FALLBACK_IMAGE_NOTE_REGEX.search(own_text)
            saved_img_prompt = msg.get("img_prompt") or (img_note_match.group(1) if img_note_match else None)
            saved_gif_query = msg.get("gif_query")

            own_text = ERROR_REPLY_STRIP_REGEX.sub('', own_text).strip()
            own_text = re.sub(r'\*\(System Note:.*?\)\*', '', own_text, flags=re.IGNORECASE).strip()
            own_text = re.sub(r'\*\([^)]*learned (?:server fact|about).*?\)\*', '', own_text, flags=re.IGNORECASE).strip()
            own_text = IMAGE_DESC_STRIP_REGEX.sub('', own_text).strip()

            if not own_text and not saved_img_prompt and not saved_gif_query:
                if glitch_summary and idx >= len(raw_history) - 10:
                    api_messages.append({
                        "role": "system",
                        "content": (
                            f"[System Glitch Event: {bot_display_name} experienced a glitch ({glitch_summary}). "
                            "You may acknowledge or joke about it in-character, but NEVER output raw '*(API Error...)*' text.]"
                        )
                    })
                continue

            words = own_text.split()
            if len(words) >= 3:
                own_recent_openers.append(" ".join(words[:3]).lower())

            if saved_img_prompt and "[IMAGE_GEN:" not in own_text:
                own_text = f"{own_text} [IMAGE_GEN: {saved_img_prompt}]".strip()
            elif saved_gif_query and "[GIF:" not in own_text:
                own_text = f"{own_text} [GIF: {saved_gif_query}]".strip()

            if own_text:
                api_messages.append({"role": "assistant", "content": own_text})
            if glitch_summary and idx >= len(raw_history) - 10:
                api_messages.append({
                    "role": "system",
                    "content": f"[System Glitch Event: {bot_display_name}'s media module glitched ({glitch_summary}) on that turn.]"
                })
        else:
            other_text = content
            author_name = msg.get("author_name", "User")
            glitch_summary = extract_glitch_summary(other_text)
            other_text = ERROR_REPLY_STRIP_REGEX.sub('', other_text).strip()
            other_text = re.sub(
                r'\*\(System Note:\s*([^\)]+?)\s+successfully attached an image of [^\)]+\)\*',
                r'[\1 attached a generated image]',
                other_text,
                flags=re.IGNORECASE
            )
            other_text = re.sub(r'\*\(System Note:.*?\)\*', '', other_text, flags=re.IGNORECASE).strip()
            other_text = re.sub(r'\*\([^)]*learned (?:server fact|about).*?\)\*', '', other_text, flags=re.IGNORECASE).strip()

            if not other_text or re.fullmatch(r'\[[^\]]+\]:\s*', other_text):
                if glitch_summary and idx >= len(raw_history) - 10:
                    api_messages.append({
                        "role": "system",
                        "content": (
                            f"[System Glitch Event: {author_name} just glitched out ({glitch_summary}). "
                            "You may tease, roast, or comment on it in-character, but NEVER output raw '*(API Error...)*' text.]"
                        )
                    })
                continue

            if msg.get("author_is_bot"):
                other_text = re.sub(
                    r'\[Attached Media Description:\s*([^\n]+)\]',
                    r'[Attached Media Description (already generated by another bot — DO NOT copy this visual scene): \1]',
                    other_text,
                    flags=re.IGNORECASE
                )

            if not other_text.startswith(f"[{author_name}") and not other_text.startswith("[Replying to"):
                other_text = f"[{author_name}]: {other_text}"
            api_messages.append({"role": "user", "content": other_text})
            if glitch_summary and idx >= len(raw_history) - 10:
                api_messages.append({
                    "role": "system",
                    "content": f"[System Glitch Event: {author_name} also hit a media glitch ({glitch_summary}) on that message.]"
                })

    if len(own_recent_openers) >= 2:
        recent_openers_str = ", ".join(f"'{op}...'" for op in own_recent_openers[-3:])
        api_messages[0]["content"] += (
            f"\n\n### Variety & Anti-Repetition Reminder:\n"
            f"- Vary your sentence openings! Do NOT start your message with any of your recent openers ({recent_openers_str})."
        )

    return api_messages


def upsert_chat_memory_entry(chat_memory_file: str, channel_id: int, entry: dict, max_history_length: int = 200):
    """
    Atomically inserts or updates a message in chat_memory.json by message_id,
    preserving rich media descriptions and automatically converting any legacy
    *(API Error ...)* dialogue lines into structured System Glitch Events.
    """
    def _mutate(chat_memory):
        if channel_id not in chat_memory or not isinstance(chat_memory[channel_id], list):
            chat_memory[channel_id] = []

        healed_history = []
        for existing_item in chat_memory[channel_id]:
            c_val = existing_item.get("content", "")
            if isinstance(c_val, str) and existing_item.get("role") != "system":
                glitch_sum = extract_glitch_summary(c_val)
                cleaned_c = ERROR_REPLY_STRIP_REGEX.sub('', c_val).strip()
                if not cleaned_c or re.fullmatch(r'\[[^\]]+\]:\s*', cleaned_c):
                    if glitch_sum:
                        a_name = existing_item.get("author_name", "A bot")
                        healed_history.append({
                            "role": "system",
                            "content": (
                                f"[System Glitch Event: {a_name} glitched out ({glitch_sum}). "
                                "Bots may react to or joke about this malfunction in-character, but NEVER output raw '*(API Error...)*' strings.]"
                            ),
                            "message_id": existing_item.get("message_id", f"healed_glitch_{time.time()}"),
                            "author_id": existing_item.get("author_id"),
                            "author_name": a_name,
                            "is_glitch_event": True
                        })
                    continue
                if cleaned_c != c_val:
                    existing_item = copy.deepcopy(existing_item)
                    existing_item["content"] = cleaned_c
            healed_history.append(existing_item)
        chat_memory[channel_id] = healed_history

        msg_id = entry.get("message_id")
        updated = False
        if msg_id is not None:
            for i, existing in enumerate(chat_memory[channel_id]):
                if existing.get("message_id") == msg_id:
                    merged = copy.deepcopy(existing)
                    old_content = merged.get("content", "") if isinstance(merged.get("content"), str) else ""
                    new_content = entry.get("content", "") if isinstance(entry.get("content"), str) else ""

                    if entry.get("role") == "assistant" or entry.get("role") == "system":
                        merged.update(entry)
                    elif merged.get("role") != "assistant":
                        merged.update(entry)
                        if old_content and new_content:
                            for meta_line in old_content.split("\n"):
                                meta_stripped = meta_line.strip()
                                if meta_stripped.startswith((
                                    "[Attached Media Description:",
                                    "[Contains GIF of:",
                                    "[Contains YouTube Video:",
                                    "[Replying to "
                                )) and meta_stripped not in new_content:
                                    if meta_stripped.startswith("[Replying to "):
                                        new_content = f"{meta_stripped}\n{new_content}".strip()
                                    else:
                                        new_content = f"{new_content}\n{meta_stripped}".strip()
                            merged["content"] = new_content
                    else:
                        if "[Attached Media Description:" in new_content and "[Attached Media Description:" not in old_content:
                            desc_match = IMAGE_DESC_STRIP_REGEX.search(new_content)
                            if desc_match:
                                merged["content"] = f"{old_content}\n{desc_match.group(0).strip()}".strip()
                    chat_memory[channel_id][i] = merged
                    updated = True
                    break

        if not updated:
            chat_memory[channel_id].append(entry)

        chat_memory[channel_id] = strip_old_image_data(chat_memory[channel_id], keep_images=0)
        if len(chat_memory[channel_id]) > max_history_length:
            chat_memory[channel_id] = chat_memory[channel_id][-max_history_length:]
        return chat_memory

    update_json_file(chat_memory_file, {}, _mutate)