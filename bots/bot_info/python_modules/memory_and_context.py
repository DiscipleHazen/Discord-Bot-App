import re
import json
import copy
import time
import random
from collections import Counter

from config import (
    STOPWORDS, FACT_HEARSAY_PREFIX_REGEX, THIRD_PARTY_REPORT_REGEX,
    JSON_EMOJI_REGEX, ERROR_REPLY_STRIP_REGEX, ERROR_REPLY_CAPTURE_REGEX,
    FALLBACK_IMAGE_NOTE_REGEX, IMAGE_DESC_STRIP_REGEX, IMAGE_DESC_EXTRACT_REGEX,
    OVERUSED_MEME_CAPTIONS, SPONTANEOUS_MICRO_MOMENTS
)
from core_system import post_with_backoff, update_json_file
from text_and_parsers import extract_used_emotes, parse_meme_gen_tag

# ==============================================================================
# MEMORY DEDUPLICATION, HEARSAY FILTER & CONVERSATION HISTORY BUILDER
# ==============================================================================

EPHEMERAL_USER_FACT_REGEX = re.compile(
    r'\b(?:asked\s+(?:for|to|about)|requested\s+a|posted\s+a\s+(?:meme|gif|link|pic|screenshot)|'
    r'sent\s+a\s+(?:message|meme|gif|screenshot)|is\s+currently\s+(?:chatting|talking|online|typing)|'
    r'pinged\s+|mentioned\s+bot|replied\s+to\s+)\b',
    re.IGNORECASE
)


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
        s = re.sub(r'\s*Try again in a moment\.?$', '', s, flags=re.IGNORECASE).strip()
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
    if not text:
        return set()
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
    Deterministic filter that catches third-party reported speech / hearsay
    and ephemeral one-off chat actions (like 'asked for an image').
    """
    if not fact or not user_message:
        return False

    if FACT_HEARSAY_PREFIX_REGEX.search(fact) or EPHEMERAL_USER_FACT_REGEX.search(fact):
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

    if re.search(
        r'\bi\s+(?:said|told\s+\w+)\s+(?:that\s+)?(?:they|he|she|someone)\s+(?:said|told|claimed|thinks|thought)\b',
        msg_clean,
        re.IGNORECASE
    ):
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
            print(f"[{my_bot_name}] [MEMORY FILTER] Blocked hearsay/ephemeral fact for {canonical_uname}: '{clean_fact}'")
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
        "1. Mark status as \"VALID\" ONLY if `target_user` directly stated, owned, or confirmed `fact` as a real personal trait, hobby, relationship, preference, or life detail about THEMSELVES.\n"
        "2. If the speaker was quoting or reporting what a third party said (e.g., \"they said...\", \"he told me...\"), mark status as \"REJECT\".\n"
        "3. If the statement is clearly a true fact about a DIFFERENT explicitly named Known Server Member, mark status as \"REATTRIBUTE\" and set `target_user` to that member's exact name.\n"
        "4. If the statement is pure sarcasm, roleplay banter, or a one-time chat command (e.g., 'asked for an image'), mark status as \"REJECT\".\n"
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
                        print(f"[{my_bot_name}] [MEMORY VERIFIER] Rejected misattributed/ephemeral fact: '{f_text}'")
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


def _extract_repeated_phrase_templates(own_texts: list[str]) -> list[str]:
    """
    Detects 4-word to 6-word conversational clichés that the bot has repeated
    across 2+ of its own recent messages (e.g. 'rather be cursed with', 'pinnacle of magical achievement',
    'oh look a peasant') so the system prompt can actively forbid repeating them.
    """
    if len(own_texts) < 2:
        return []

    ngram_doc_counts = Counter()
    for txt in own_texts[-6:]:
        # Strip *emotes* before scanning for repeated verbal stock templates
        cleaned = re.sub(r'\*[^*]+\*', ' ', txt.lower())
        words = re.findall(r"[a-z']+", cleaned)
        seen_in_msg = set()
        for n in (6, 5, 4):
            if len(words) >= n:
                for i in range(len(words) - n + 1):
                    gram = " ".join(words[i:i + n])
                    if len(gram) >= 15:
                        seen_in_msg.add(gram)
        for g in seen_in_msg:
            ngram_doc_counts[g] += 1

    candidates = [
        (count, len(gram.split()), len(gram), gram)
        for gram, count in ngram_doc_counts.items()
        if count >= 2
    ]
    candidates.sort(reverse=True)

    filtered = []
    for _, _, _, g in candidates:
        g_words = set(g.split())
        if any(
            g in other or other in g or (len(g_words & set(other.split())) / float(min(len(g_words), len(other.split()))) >= 0.65)
            for other in filtered
        ):
            continue
        filtered.append(g)
        if len(filtered) >= 4:
            break
    return filtered


def build_api_messages_from_history(
    dynamic_system: dict,
    raw_history: list[dict],
    bot_user_id: int,
    bot_display_name: str
) -> list[dict]:
    """
    Builds clean conversation history for the LLM, annotating when a user message
    was addressed to a DIFFERENT bot, tracking the bot's own recent emotes, openers,
    stock phrases, and meme captions to prevent repetitive loops, and injecting
    organic human-like spontaneity cues.
    """
    api_messages = [dynamic_system]
    seen_msg_ids = set()
    own_recent_openers = []
    own_recent_texts = []
    own_emote_counter = Counter()
    recent_meme_captions = []
    active_human_speakers = []

    # Identify the index of the most recent self-generated media turn so older turns
    # don't flood the assistant history with `[MEME_GEN: ...]` tags on every message.
    last_self_media_idx = -1
    for idx in range(len(raw_history) - 1, -1, -1):
        m = raw_history[idx]
        is_self_chk = (
            m.get("author_id") == bot_user_id
            or (m.get("role") == "assistant" and m.get("author_name") == bot_display_name)
        )
        if is_self_chk and (m.get("meme_spec") or m.get("img_prompt") or m.get("gif_query")):
            last_self_media_idx = idx
            break

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
            saved_meme_spec = msg.get("meme_spec")
            saved_gif_query = msg.get("gif_query")

            if saved_meme_spec:
                _, t_cap, b_cap = parse_meme_gen_tag(saved_meme_spec)
                for cap in (t_cap, b_cap):
                    if cap and cap.lower() not in recent_meme_captions:
                        recent_meme_captions.append(cap.upper())

            own_text = ERROR_REPLY_STRIP_REGEX.sub('', own_text).strip()
            own_text = re.sub(r'\*\(System Note:.*?\)\*', '', own_text, flags=re.IGNORECASE).strip()
            own_text = re.sub(r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*', '', own_text, flags=re.IGNORECASE).strip()
            own_text = IMAGE_DESC_STRIP_REGEX.sub('', own_text).strip()

            if not own_text and not saved_img_prompt and not saved_meme_spec and not saved_gif_query:
                if glitch_summary and idx >= len(raw_history) - 10:
                    api_messages.append({
                        "role": "system",
                        "content": (
                            f"[System Glitch Event: {bot_display_name} experienced a glitch ({glitch_summary}). "
                            "You may acknowledge or joke about it in-character, but NEVER output raw '*(API Error...)*' text.]"
                        )
                    })
                continue

            if idx >= len(raw_history) - 12 and own_text:
                own_recent_texts.append(own_text)
                for em in extract_used_emotes(own_text):
                    own_emote_counter[em] += 1
                # Strip leading emote to also capture the actual spoken sentence opener
                spoken_only = re.sub(r'^\s*\*[^*]+\*\s*', '', own_text).strip()
                words_raw = own_text.split()
                words_spoken = spoken_only.split()
                if len(words_raw) >= 3:
                    own_recent_openers.append(" ".join(words_raw[:4]).lower())
                if len(words_spoken) >= 3:
                    sp_opener = " ".join(words_spoken[:4]).lower()
                    if sp_opener not in own_recent_openers:
                        own_recent_openers.append(sp_opener)

            # Only attach the literal media tag on the most recent media turn so the LLM
            # doesn't assume it must attach a [MEME_GEN:] tag to 100% of its responses,
            # and redact the old caption text in history so it never copy-pastes old captions.
            if idx == last_self_media_idx:
                if saved_meme_spec and "[MEME_GEN:" not in own_text:
                    v_scene, _, _ = parse_meme_gen_tag(saved_meme_spec)
                    own_text = f"{own_text} [IMAGE_GEN: {v_scene}]".strip()
                elif saved_img_prompt and "[IMAGE_GEN:" not in own_text:
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
            if not msg.get("author_is_bot") and author_name and author_name not in active_human_speakers:
                active_human_speakers.append(author_name)

            addressed_bot = msg.get("addressed_bot")
            glitch_summary = extract_glitch_summary(other_text)
            other_text = ERROR_REPLY_STRIP_REGEX.sub('', other_text).strip()
            other_text = re.sub(
                r'\*\(System Note:\s*([^\)]+?)\s+successfully attached a(?:n image| meme) of [^\)]+\)\*',
                r'[\1 attached a generated image]',
                other_text,
                flags=re.IGNORECASE
            )
            other_text = re.sub(r'\*\(System Note:.*?\)\*', '', other_text, flags=re.IGNORECASE).strip()
            other_text = re.sub(r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*', '', other_text, flags=re.IGNORECASE).strip()

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

            if addressed_bot and addressed_bot.lower() != bot_display_name.lower():
                other_text = re.sub(
                    rf'(?m)^\[{re.escape(author_name)}\]:',
                    f"[{author_name} (speaking to {addressed_bot}, NOT to {bot_display_name})]:",
                    other_text,
                    count=1
                )
            elif addressed_bot and addressed_bot.lower() == bot_display_name.lower():
                other_text = re.sub(
                    rf'(?m)^\[{re.escape(author_name)}\]:',
                    f"[{author_name} (speaking to YOU, {bot_display_name})]:",
                    other_text,
                    count=1
                )

            api_messages.append({"role": "user", "content": other_text})
            if glitch_summary and idx >= len(raw_history) - 10:
                api_messages.append({
                    "role": "system",
                    "content": f"[System Glitch Event: {author_name} also hit a media glitch ({glitch_summary}) on that message.]"
                })

    # Build a rich, dynamic Variety, Anti-Loop & Human Spontaneity guidance block
    variety_rules = []
    if len(own_recent_openers) >= 2:
        recent_openers_str = ", ".join(f"'{op}...'" for op in own_recent_openers[-4:])
        variety_rules.append(
            f"- **VARY YOUR SENTENCE OPENINGS**: Do NOT start your message with any of your recent openers ({recent_openers_str})."
        )

    overused_emotes = [f"*{em}*" for em, cnt in own_emote_counter.most_common(5) if cnt >= 1]
    if overused_emotes:
        variety_rules.append(
            f"- **NO REPEATED STAGE DIRECTIONS**: You recently used {', '.join(overused_emotes[:4])}. "
            "Do NOT reuse those exact physical emotes on this turn—either use completely fresh body language or skip asterisk stage directions entirely and let your dialogue carry the attitude!"
        )

    repeated_templates = _extract_repeated_phrase_templates(own_recent_texts)
    if repeated_templates:
        tpl_str = ", ".join(f'"{t}"' for t in repeated_templates)
        variety_rules.append(
            f"- **BREAK FORMULAIC TEMPLATES**: You have already used the phrase pattern(s) {tpl_str} in recent turns. "
            "Do NOT reuse that sentence structure or closing punchline—invent a fresh angle!"
        )

    banned_captions = list(dict.fromkeys(
        recent_meme_captions[-6:] + [c.upper() for c in sorted(OVERUSED_MEME_CAPTIONS)]
    ))
    if banned_captions:
        cap_str = "; ".join(f'"{c}"' for c in banned_captions[:6])
        variety_rules.append(
            f"- **BANNED MEME CAPTIONS (NEVER REUSE THESE)**: {cap_str}. "
            "If you generate a meme (`[MEME_GEN: scene | TOP | BOTTOM]`), both captions MUST be 100% brand-new and specific to the exact joke in the latest message."
        )

    micro_impulse = random.choice(SPONTANEOUS_MICRO_MOMENTS)
    variety_rules.append(f"- **SPONTANEOUS CONVERSATIONAL IMPULSE**: {micro_impulse}")

    if variety_rules:
        api_messages[0]["content"] += (
            "\n\n### Dynamic Spontaneity, Variety & Anti-Repetition Rules (Active This Turn):\n"
            + "\n".join(variety_rules)
        )

    return api_messages


def upsert_chat_memory_entry(chat_memory_file: str, channel_id: int, entry: dict, max_history_length: int = 200):
    """
    Atomically inserts or updates a message in chat_memory.json by message_id,
    preserving rich media descriptions and target-bot attribution tags.
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
                        if not entry.get("addressed_bot") and merged.get("addressed_bot"):
                            entry["addressed_bot"] = merged["addressed_bot"]
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
                            desc_match = IMAGE_DESC_EXTRACT_REGEX.search(new_content)
                            if desc_match:
                                merged["content"] = f"{old_content}\n[Attached Media Description: {desc_match.group(1).strip()}]".strip()
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


def route_server_vs_general_facts(
    raw_server_facts: list[str],
    raw_general_facts: list[str],
    recent_users: set[str],
    other_bot_names: set[str],
    my_bot_name: str
) -> tuple[list[str], list[str]]:
    """
    Ensures universal science/trivia/world facts accidentally tagged with [LEARN: ...]
    are automatically re-routed to general_facts unless they mention server members,
    bots, or Discord/guild-specific terms.
    """
    server_indicators = {
        "server", "guild", "channel", "discord", "admin", "mod", "mods", "vc",
        "voice", "chat", "bot", "bots", "raid", "estate", "ledger", "steward",
        "member", "members", "user", "users", "role", "roles", "ping", "ban", "kick"
    }
    for u in (recent_users or set()):
        if u:
            for part in re.findall(r'[a-z0-9]+', str(u).lower()):
                if len(part) >= 3:
                    server_indicators.add(part)
    for b in (other_bot_names or set()):
        if b:
            for part in re.findall(r'[a-z0-9]+', str(b).lower()):
                if len(part) >= 3:
                    server_indicators.add(part)
    for part in re.findall(r'[a-z0-9]+', (my_bot_name or "").lower()):
        if len(part) >= 3:
            server_indicators.add(part)

    final_server_facts = []
    final_general_facts = [f.strip().rstrip(".") for f in (raw_general_facts or []) if f and f.strip()]

    for fact in (raw_server_facts or []):
        clean_f = fact.strip().rstrip(".")
        if not clean_f:
            continue
        words = set(re.findall(r'[a-z0-9]+', clean_f.lower()))
        if words & server_indicators:
            final_server_facts.append(clean_f)
        else:
            print(f"[{my_bot_name}] [FACT ROUTER] Auto-routed universal fact from Server Lore -> General Knowledge: '{clean_f}'")
            final_general_facts.append(clean_f)

    return final_server_facts, final_general_facts
