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
    OVERUSED_MEME_CAPTIONS, SPONTANEOUS_MICRO_MOMENTS, TOPIC_CHANGE_REQUEST_REGEX,
    FIRST_PERSON_SELF_REF_REGEX, LOW_INFORMATION_RETRIEVAL_WORDS
)
from core_system import post_with_backoff, update_json_file
from text_and_parsers import extract_used_emotes, parse_meme_gen_tag

# ==============================================================================
# PRE-COMPILED REGEXES & CONSTANTS FOR HIGH-THROUGHPUT MEMORY PROCESSING
# ==============================================================================

EPHEMERAL_USER_FACT_REGEX = re.compile(
    r'\b(?:asked\s+(?:if|whether|how|why|what|when|where|for|to|about)|'
    r'wondered\s+(?:if|whether|how|why|about)|curious\s+(?:if|whether|about)|'
    r'wants\s+to\s+know\s+(?:if|whether|how|why|what)|questioned\s+(?:if|whether)|'
    r'requested\s+a|posted\s+a\s+(?:meme|gif|link|pic|screenshot)|'
    r'sent\s+a\s+(?:message|meme|gif|screenshot)|is\s+currently\s+(?:chatting|talking|online|typing)|'
    r'pinged\s+|mentioned\s+bot|replied\s+to\s+|wiped\s+memory|memory\s+wiped|'
    r'memeing|joking\s+about|talking\s+about|chatting\s+about|discussing|'
    r'adulting\s+cris[ie]s|gpu[\s\-]fueled|turned\s+off\s+vsync|game\s+crashed)\b',
    re.IGNORECASE
)

EPHEMERAL_CHAT_RECAP_REGEX = re.compile(
    r'\b(?:memeing|meme\s+about|memes\s+about|joking\s+about|joked\s+about|'
    r'talking\s+about|talked\s+about|chatting\s+about|discussing|discussed|'
    r'adulting\s+cris[ie]s|gpu[\s\-]fueled|in[\s\-]joke|amnesia\s+ritual|'
    r'memory[\s\-]clearing|memory\s+wipe|posted\s+a\s+meme|shared\s+a\s+meme|'
    r'were\s+busy|was\s+busy|just\s+said|currently\s+talking)\b',
    re.IGNORECASE
)

MEMORY_RECALL_QUERY_REGEX = re.compile(
    r'\b(?:what\s+do\s+you\s+(?:remember|know)|do\s+you\s+(?:remember|know)|'
    r'who\s+is\s+|tell\s+me\s+about\s+|what\s+facts|server\s+lore|my\s+profile)\b',
    re.IGNORECASE
)

BIO_EXAMPLE_QUOTE_REGEX = re.compile(r'["“]([^"”\n]{18,140})["”]')

ALPHANUM_TOKEN_REGEX = re.compile(r'[a-z0-9]+')
WORD_APOSTROPHE_REGEX = re.compile(r"[a-z']+")
INLINE_EMOTE_STRIP_REGEX = re.compile(r'\*[^*]+\*')
LEADING_EMOTE_STRIP_REGEX = re.compile(r'^\s*\*[^*]+\*\s*')
SYSTEM_PREFIX_STRIP_REGEX = re.compile(r'^System:\s*', re.IGNORECASE)
TRY_AGAIN_STRIP_REGEX = re.compile(r'\s*Try again in a moment\.?$', re.IGNORECASE)
REPLYING_TO_HEADER_STRIP_REGEX = re.compile(r'^\[Replying to [^\]]+\]\s*', re.IGNORECASE)
SPEAKER_HEADER_STRIP_REGEX = re.compile(r'^\[[^\]]+\]:\s*')
EMPTY_SPEAKER_TAG_REGEX = re.compile(r'\[[^\]]+\]:\s*')
PARENTHETICAL_SUFFIX_REGEX = re.compile(r'\s*\([^\)]*\)')
INDIRECT_HEARSAY_CHAIN_REGEX = re.compile(
    r'\bi\s+(?:said|told\s+\w+)\s+(?:that\s+)?(?:they|he|she|someone)\s+(?:said|told|claimed|thinks|thought)\b',
    re.IGNORECASE
)
SYSTEM_NOTE_INLINE_REGEX = re.compile(r'\*\(System Note:.*?\)\*', re.IGNORECASE)
LEARNED_INLINE_REGEX = re.compile(
    r'\*\([^)]*learned (?:server fact|general fact|about).*?\)\*',
    re.IGNORECASE
)
OTHER_BOT_ATTACHED_IMG_NOTE_REGEX = re.compile(
    r'\*\(System Note:\s*([^\)]+?)\s+successfully attached a(?:n image| meme) of [^\)]+\)\*',
    re.IGNORECASE
)
OTHER_BOT_MEDIA_DESC_REGEX = re.compile(
    r'\[Attached Media Description:\s*([^\n]+)\]',
    re.IGNORECASE
)

BASE_SERVER_INDICATORS = frozenset({
    "server", "guild", "channel", "discord", "admin", "mod", "mods", "vc",
    "voice", "chat", "bot", "bots", "raid", "estate", "ledger", "steward",
    "member", "members", "user", "users", "role", "roles", "ping", "ban", "kick"
})

META_BANTER_PHRASES = (
    "memory-clearing", "memory clearing", "memory wipe", "memory wipes",
    "existential recursion", "amnesia ritual", "in-joke", "in-jokes",
    "claims to have archived", "discord server members include"
)

COMMON_CONVERSATIONAL_WORDS = frozenset({
    "people", "person", "someone", "anyone", "everyone", "something", "anything",
    "nothing", "everything", "things", "thing", "stuff", "time", "times", "today",
    "tonight", "right", "maybe", "probably", "actually", "honestly", "literally",
    "really", "pretty", "little", "always", "never", "again", "still", "even",
    "going", "getting", "making", "looking", "trying", "saying", "talking",
    "thinking", "working", "playing", "watching", "reading", "writing", "know",
    "mean", "sure", "good", "great", "better", "best", "well", "much", "many",
    "way", "ways", "day", "night", "hour", "hours", "minute", "minutes", "here",
    "there", "where", "when", "what", "which", "who", "whom", "whose", "why", "how",
    "related", "item", "items", "topic", "topics", "subject", "subjects"
})

SESSION_GAP_SECONDS = 2700.0  # 45 minutes marks a new conversation session


def _extract_base_username(uname: str) -> str:
    """
    Extracts the clean base handle from a display name that may contain
    parenthetical pronouns or tags (e.g., 'Sam (Tam/Tamself)' -> 'sam').
    """
    if not uname:
        return ""
    cleaned = str(uname).strip().lstrip('@').strip()
    base = PARENTHETICAL_SUFFIX_REGEX.sub('', cleaned).strip()
    return (base if len(base) >= 2 else cleaned).lower()


def format_duration_gap(seconds: float) -> str:
    """Formats a duration in seconds into a concise human-readable string (e.g., '2h 15m', '48 minutes')."""
    secs = max(0, int(seconds))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    hours = mins // 60
    rem_mins = mins % 60
    if hours < 24:
        return f"{hours}h {rem_mins}m" if rem_mins else f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h" if rem_hours else f"{days} day{'s' if days != 1 else ''}"


def build_temporal_context_block(raw_history: list[dict], now_str: str, current_ts: float | None = None) -> str:
    """
    Builds a rich Current Time & Conversation Pace block so bots know both the
    wall-clock time and whether the channel is in rapid live banter or returning
    after a long silence gap.
    """
    now_val = current_ts if current_ts is not None else time.time()
    lines = [f"\n\n### Current Time & Session Context:\n- Local Server Time: {now_str}"]

    recent_timestamps = [
        float(m["timestamp"])
        for m in (raw_history or [])
        if isinstance(m, dict) and m.get("role") != "system" and isinstance(m.get("timestamp"), (int, float))
    ]
    if len(recent_timestamps) >= 2:
        gap = max(0.0, recent_timestamps[-1] - recent_timestamps[-2])
        if gap >= SESSION_GAP_SECONDS:
            lines.append(
                f"- Conversation Pace: Returning after a **{format_duration_gap(gap)}** quiet gap. "
                "Treat this as a fresh conversation session—do NOT randomly resurrect stale jokes from hours ago!"
            )
        elif gap <= 45.0:
            lines.append("- Conversation Pace: Active, real-time back-and-forth chat.")
    elif len(recent_timestamps) == 1:
        elapsed = max(0.0, now_val - recent_timestamps[-1])
        if elapsed >= SESSION_GAP_SECONDS:
            lines.append(
                f"- Last Channel Activity: **{format_duration_gap(elapsed)}** ago (fresh session)."
            )

    return "\n".join(lines)


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
        s = SYSTEM_PREFIX_STRIP_REGEX.sub('', m.strip())
        s = TRY_AGAIN_STRIP_REGEX.sub('', s).strip()
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
    words = ALPHANUM_TOKEN_REGEX.findall(text.lower())
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


def consolidate_and_append_fact(
    new_fact: str,
    existing_facts: list[str],
    max_items: int = 35,
    overlap_threshold: float = 0.55
) -> bool:
    """
    Smart fact consolidation:
    - If `new_fact` is already covered by `existing_facts`, returns False.
    - If `new_fact` overlaps >= `overlap_threshold` with an older fact and `new_fact`
      is more detailed (longer), supersedes/replaces the older fact in-place and returns True.
    - Otherwise appends `new_fact` (trimming to `max_items`) and returns True.
    """
    clean_new = (new_fact or "").strip().rstrip(".")
    if not clean_new or len(clean_new) < 4 or is_ephemeral_or_banter_fact(clean_new):
        return False

    new_low = clean_new.lower()
    new_tokens = normalize_tokens(new_low)
    if not new_tokens:
        return False

    for idx, existing in enumerate(existing_facts):
        ex_clean = str(existing).strip().rstrip(".")
        ex_low = ex_clean.lower()
        if new_low == ex_low or new_low in ex_low:
            return False
        if ex_low in new_low and len(clean_new) > len(ex_clean):
            existing_facts[idx] = clean_new
            return True
        ex_tokens = normalize_tokens(ex_low)
        if ex_tokens:
            overlap = len(new_tokens & ex_tokens) / float(min(len(new_tokens), len(ex_tokens)))
            if overlap >= overlap_threshold:
                if len(clean_new) > len(ex_clean) + 6:
                    existing_facts[idx] = clean_new
                    return True
                return False

    existing_facts.append(clean_new)
    if len(existing_facts) > max_items:
        del existing_facts[:-max_items]
    return True


def find_canonical_username(uname: str, user_profiles: dict, recent_users: set[str]) -> str:
    """
    Resolves a username or nickname (including display names with parenthetical pronouns
    like 'Sam (Tam/Tamself)' vs 'Sam') to its canonical key in `user_profiles` or `recent_users`.
    """
    clean = str(uname or "").strip().lstrip('@').strip()
    if not clean:
        return ""
    clean_lower = clean.lower()
    clean_base = _extract_base_username(clean)

    # 1. Exact case-insensitive match
    for existing in user_profiles.keys():
        if str(existing).lower() == clean_lower:
            return str(existing)
    for ru in recent_users:
        if ru and str(ru).lower() == clean_lower:
            return str(ru)

    # 2. Base-name match (e.g. 'Sam (Tam/Tamself)' <-> 'Sam')
    if len(clean_base) >= 3:
        for existing in user_profiles.keys():
            if _extract_base_username(str(existing)) == clean_base:
                return str(existing)
        for ru in recent_users:
            if ru and _extract_base_username(str(ru)) == clean_base:
                return str(ru)

    # 3. Prefix match
    if len(clean_base) >= 3:
        for existing in user_profiles.keys():
            ex_base = _extract_base_username(str(existing))
            if ex_base.startswith(clean_base) or clean_base.startswith(ex_base):
                return str(existing)
        for ru in recent_users:
            if ru:
                ru_base = _extract_base_username(str(ru))
                if ru_base.startswith(clean_base) or clean_base.startswith(ru_base):
                    return str(ru)

    return clean


def _stem_token(w: str) -> str:
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("in") and len(w) > 5:
        return w[:-2]
    if w.endswith("es") and len(w) > 4 and w[-3] in "sxzho":
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


LOW_INFO_STEMS = frozenset(_stem_token(w) for w in LOW_INFORMATION_RETRIEVAL_WORDS)


def _extract_weighted_stems(text: str) -> dict[str, float]:
    """
    Extracts normalized word stems mapped to their IDF specificity weight:
    - High-specificity domain/proper nouns (not in LOW_INFORMATION_RETRIEVAL_WORDS): 2.5
    - Generic conversational words (in LOW_INFORMATION_RETRIEVAL_WORDS, e.g. 'game', 'play', 'work'): 0.25
    """
    if not text:
        return {}
    weights: dict[str, float] = {}
    for w in ALPHANUM_TOKEN_REGEX.findall(text.lower()):
        if len(w) < 4 or w in STOPWORDS or w in COMMON_CONVERSATIONAL_WORDS or w in BASE_SERVER_INDICATORS:
            continue
        st = _stem_token(w)
        if not st or len(st) < 3:
            continue
        if w in LOW_INFORMATION_RETRIEVAL_WORDS or st in LOW_INFO_STEMS:
            weights[st] = max(weights.get(st, 0.0), 0.25)
        else:
            weights[st] = max(weights.get(st, 0.0), 2.5)
    return weights


def _compute_stem_specificity_score(query_weights: dict[str, float], fact_text: str) -> float:
    """
    Computes the IDF-weighted specificity overlap score between `query_weights` and `fact_text`.
    A single specific keyword match scores 2.5 (>= 2.0 threshold), whereas a single generic
    word like 'game' only scores 0.25 (< 2.0 threshold), preventing false memory triggers!
    """
    if not query_weights or not fact_text:
        return 0.0
    fact_weights = _extract_weighted_stems(fact_text)
    if not fact_weights:
        return 0.0
    shared = set(query_weights.keys()) & set(fact_weights.keys())
    if not shared:
        return 0.0
    return sum(min(query_weights[k], fact_weights[k]) for k in shared)


def is_ephemeral_or_banter_fact(fact_str: str) -> bool:
    """Returns True if a saved string is actually a one-off chat/meme recap rather than an enduring fact."""
    if not fact_str or not isinstance(fact_str, str):
        return True
    f_low = fact_str.strip().lower()
    if len(f_low) < 4:
        return True
    if any(mp in f_low for mp in META_BANTER_PHRASES):
        return True
    if EPHEMERAL_USER_FACT_REGEX.search(f_low) or EPHEMERAL_CHAT_RECAP_REGEX.search(f_low):
        return True
    return False


def select_relevant_memories(
    facts_list: list[str],
    active_context_text: str,
    max_items: int = 5,
    min_specificity_score: float = 2.0
) -> list[str]:
    """
    IDF-Weighted Memory Gate:
    Filters `long_term_memory` or `general_memory` so ONLY facts that achieve an
    IDF specificity score >= `min_specificity_score` (at least one specific domain/proper
    noun match, never just a generic word like 'game' or 'work') are injected into the prompt.
    """
    if not facts_list or not isinstance(facts_list, list):
        return []

    clean_pool = [str(f).strip() for f in facts_list if f and not is_ephemeral_or_banter_fact(str(f))]
    if not clean_pool:
        return []

    if MEMORY_RECALL_QUERY_REGEX.search(active_context_text or ""):
        return clean_pool[-max_items:]

    query_weights = _extract_weighted_stems(active_context_text)
    if not query_weights:
        return []

    scored = []
    for idx, fact in enumerate(clean_pool):
        score = _compute_stem_specificity_score(query_weights, fact)
        if score >= min_specificity_score:
            scored.append((score, idx, fact))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [f for _, _, f in scored[:max_items]]


def build_active_user_context_block(
    user_profiles: dict,
    raw_history: list[dict],
    current_speaker: str | None = None,
    combined_recent_prompt: str = "",
    reply_context_str: str = "",
    is_spontaneous: bool = False
) -> str:
    """
    Builds a structured, IDF-specificity-filtered User Awareness block for the system prompt:
    1. Filters out ephemeral/meme-recap facts ('memeing about...') so bots never reference stale chat recaps.
    2. Requires an IDF specificity score >= 2.0 (or an explicit recall question) before injecting
       any saved dossier fact so unrelated personal facts are never shoehorned into casual chat.
    """
    if not isinstance(user_profiles, dict):
        user_profiles = {}

    base_to_facts: dict[str, list[str]] = {}
    base_to_display: dict[str, str] = {}

    for prof_user, facts_list in user_profiles.items():
        if not prof_user or not isinstance(facts_list, list) or not facts_list:
            continue
        b_name = _extract_base_username(str(prof_user))
        if not b_name:
            continue
        base_to_display.setdefault(b_name, str(prof_user))
        bucket = base_to_facts.setdefault(b_name, [])
        for f_item in facts_list:
            f_str = str(f_item).strip()
            if f_str and not is_ephemeral_or_banter_fact(f_str) and not is_duplicate_fact(f_str, bucket, threshold=0.75):
                bucket.append(f_str)

    speaker_base = _extract_base_username(current_speaker) if current_speaker else ""
    if speaker_base and current_speaker:
        base_to_display[speaker_base] = current_speaker

    active_blob = f"{combined_recent_prompt or ''} {reply_context_str or ''}".strip()
    query_weights = _extract_weighted_stems(active_blob)
    is_recall_q = bool(MEMORY_RECALL_QUERY_REGEX.search(active_blob))

    def _pick_relevant_user_facts(all_facts: list[str], max_count: int = 4) -> list[str]:
        if not all_facts:
            return []
        if is_recall_q and not is_spontaneous:
            return all_facts[-max_count:]
        scored = []
        for idx, f_str in enumerate(all_facts):
            sc = _compute_stem_specificity_score(query_weights, f_str)
            if sc >= 2.0:
                scored.append((sc, idx, f_str))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [f for _, _, f in scored[:max_count]]

    lines = []
    if current_speaker and speaker_base:
        sp_facts = _pick_relevant_user_facts(base_to_facts.get(speaker_base, []), max_count=(3 if is_spontaneous else 5))
        if sp_facts:
            lines.append(f"- **Current Speaker ({current_speaker})**: {'; '.join(sp_facts)}")
        else:
            lines.append(f"- **Current Speaker**: {current_speaker}")

    # Only include another user's dossier if that user is explicitly mentioned by name in the current prompt
    search_blob_low = active_blob.lower()
    for b_name, disp in list(base_to_display.items()):
        if b_name == speaker_base or len(b_name) < 3:
            continue
        if b_name in search_blob_low or disp.lower() in search_blob_low:
            u_facts = _pick_relevant_user_facts(base_to_facts.get(b_name, []), max_count=3)
            if u_facts:
                lines.append(f"- **{disp}**: {'; '.join(u_facts)}")

    if not lines:
        return ""
    return (
        "\n\n### Active Speaker & Topically Relevant Dossier (PASSIVE REFERENCE ONLY — "
        "NEVER bring up a saved fact unless it directly answers what the speaker is talking about right now!):\n"
        + "\n".join(lines)
    )


def is_misattributed_hearsay(fact: str, target_user: str, speaker_name: str, user_message: str) -> bool:
    """
    Deterministic filter that catches third-party reported speech / hearsay
    and ephemeral one-off chat actions (like 'asked for an image').
    """
    if not fact or not user_message:
        return False

    if FACT_HEARSAY_PREFIX_REGEX.search(fact) or EPHEMERAL_USER_FACT_REGEX.search(fact):
        return True

    msg_clean = REPLYING_TO_HEADER_STRIP_REGEX.sub('', user_message.strip())
    msg_clean = SPEAKER_HEADER_STRIP_REGEX.sub('', msg_clean.strip())

    # If the candidate fact is attributed to the current speaker, the speaker MUST have
    # used at least one first-person pronoun ('I', 'my', 'me', 'I'm', etc.). Pure questions
    # about the bot like 'uwu does it hurt to have your memory wiped?' contain zero self-facts!
    if _extract_base_username(target_user) == _extract_base_username(speaker_name):
        if not FIRST_PERSON_SELF_REF_REGEX.search(msg_clean):
            return True
    else:
        return False

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

    if INDIRECT_HEARSAY_CHAIN_REGEX.search(msg_clean):
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
    my_bot_lower = my_bot_name.lower()
    for raw_uname, raw_fact in candidate_facts:
        canonical_uname = find_canonical_username(raw_uname, user_profiles, recent_users)
        clean_fact = raw_fact.strip().rstrip(".")
        if not canonical_uname or not clean_fact:
            continue
        if canonical_uname.lower() == my_bot_lower or canonical_uname.lower() in other_bot_names:
            continue
        if is_misattributed_hearsay(clean_fact, canonical_uname, speaker_name, recent_user_prompt):
            print(f"[{my_bot_name}] [MEMORY FILTER] Blocked hearsay/ephemeral fact for {canonical_uname}: '{clean_fact}'")
            continue
        pre_filtered.append((canonical_uname, clean_fact))

    if not pre_filtered:
        return []

    # Fast-Path (0 API calls): If every candidate fact is about the current speaker,
    # the user's message contains direct first-person self-reference ("I", "my", "me", "I'm"),
    # there are no third-party report cues ("he said", "they told me"), and the fact shares
    # content tokens with what the speaker actually typed, approve deterministically in 0ms.
    speaker_base = _extract_base_username(speaker_name)
    msg_clean_for_fp = REPLYING_TO_HEADER_STRIP_REGEX.sub('', (recent_user_prompt or "").strip())
    msg_clean_for_fp = SPEAKER_HEADER_STRIP_REGEX.sub('', msg_clean_for_fp.strip())
    has_first_person = bool(FIRST_PERSON_SELF_REF_REGEX.search(msg_clean_for_fp))
    has_third_party = bool(THIRD_PARTY_REPORT_REGEX.search(msg_clean_for_fp))
    prompt_tokens = normalize_tokens(msg_clean_for_fp)

    if has_first_person and not has_third_party and prompt_tokens:
        fast_verified: list[tuple[str, str]] = []
        needs_llm_check = False
        for canon_u, c_fact in pre_filtered:
            if _extract_base_username(canon_u) != speaker_base:
                needs_llm_check = True
                break
            fact_tokens = normalize_tokens(c_fact)
            if not fact_tokens or len(fact_tokens & prompt_tokens) == 0:
                needs_llm_check = True
                break
            fast_verified.append((canon_u, c_fact))
        if not needs_llm_check and fast_verified:
            return fast_verified

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
        if u and str(u).lower() != my_bot_lower and str(u).lower() not in other_bot_names
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
                        if canon_target.lower() == my_bot_lower or canon_target.lower() in other_bot_names:
                            continue
                        verified_list.append((canon_target, f_text))
                return verified_list
        except Exception as e:
            print(f"[{my_bot_name}] [WARN] Memory verifier parse fallback: {e}")

    return []


def _extract_bio_example_quotes(system_prompt_text: str) -> list[str]:
    """
    Scans the bot's base persona prompt for quoted sample dialogue lines
    (e.g., '"Levi-oh-sa, not Levi-o-za..."') so the anti-repetition block can
    explicitly forbid verbatim parroting of sample bio lines in live chat.
    """
    if not system_prompt_text:
        return []
    base_section = system_prompt_text.split("### Current Time")[0]
    found = []
    for m in BIO_EXAMPLE_QUOTE_REGEX.finditer(base_section):
        q = m.group(1).strip()
        if any(tag in q for tag in ("[IMAGE_GEN", "[MEME_GEN", "[VOICE_MSG", "[GIF", "[LEARN", "Role:", "Scene:")):
            continue
        words = q.split()
        if 4 <= len(words) <= 22:
            short_q = " ".join(words[:8])
            if short_q not in found:
                found.append(short_q)
            if len(found) >= 4:
                break
    return found


def _extract_repeated_phrase_templates(own_texts: list[str]) -> list[str]:
    """
    Detects 4-word to 6-word conversational clichés that the bot has repeated
    across 2+ of its own recent messages OR distinctive clauses from its very last
    message so the bot never echoes its own previous turn verbatim.
    """
    if not own_texts:
        return []

    ngram_doc_counts = Counter()
    for txt in own_texts[-6:]:
        cleaned = INLINE_EMOTE_STRIP_REGEX.sub(' ', txt.lower())
        words = WORD_APOSTROPHE_REGEX.findall(cleaned)
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

    # Also grab 1-2 distinctive 5-word clauses from the very last message so the bot
    # cannot copy-paste its immediately preceding line (as seen in the waffle/syrup loop)
    if len(filtered) < 4 and own_texts:
        last_clean = INLINE_EMOTE_STRIP_REGEX.sub(' ', own_texts[-1].lower())
        last_words = WORD_APOSTROPHE_REGEX.findall(last_clean)
        if len(last_words) >= 5:
            mid_idx = max(0, (len(last_words) // 2) - 2)
            sample_gram = " ".join(last_words[mid_idx:mid_idx + 5])
            if len(sample_gram) >= 15 and not any(sample_gram in f or f in sample_gram for f in filtered):
                filtered.append(sample_gram)

    return filtered[:4]


def _detect_overused_or_banned_topics(
    own_recent_texts: list[str],
    recent_user_texts: list[str],
    bot_display_name: str
) -> tuple[list[str], bool]:
    """
    1. Checks if a human explicitly asked to change the subject or stop talking about X
       (e.g. 'can you bots talk about something besides toaster related items?').
    2. Checks if the bot has fixated on the same specific nouns/keywords across 2+ of its
       last 4 turns (e.g. 'waffle', 'syrup', 'breakfast', 'latin', 'cursed', 'relic').
    Returns (list_of_overused_or_banned_words, user_requested_topic_change).
    """
    banned_words = []
    user_requested_pivot = False

    # 1. Inspect recent human messages for explicit topic-change requests
    for u_txt in recent_user_texts[-4:]:
        m = TOPIC_CHANGE_REQUEST_REGEX.search(u_txt)
        if m:
            user_requested_pivot = True
            for grp in m.groups():
                if grp:
                    for tok in ALPHANUM_TOKEN_REGEX.findall(grp.lower()):
                        if len(tok) >= 3 and tok not in STOPWORDS and tok not in COMMON_CONVERSATIONAL_WORDS:
                            if tok not in banned_words:
                                banned_words.append(tok)

    # 2. Inspect the bot's own last 4 messages (including emotes & captions) for topic fixation
    bot_name_parts = set(ALPHANUM_TOKEN_REGEX.findall(bot_display_name.lower()))
    word_turn_counts = Counter()
    window = own_recent_texts[-4:]
    for b_txt in window:
        tokens = {
            w for w in ALPHANUM_TOKEN_REGEX.findall(b_txt.lower())
            if len(w) >= 4
            and w not in STOPWORDS
            and w not in COMMON_CONVERSATIONAL_WORDS
            and w not in bot_name_parts
        }
        for t in tokens:
            word_turn_counts[t] += 1

    # Prioritize words repeated across >= 2 turns first; if user explicitly asked to pivot,
    # also include top single-turn topic nouns from the bot's most recent turn.
    min_count = 1 if user_requested_pivot else 2
    for word, cnt in word_turn_counts.most_common(10):
        if cnt >= min_count and word not in banned_words:
            banned_words.append(word)

    return banned_words[:8], user_requested_pivot


def build_api_messages_from_history(
    dynamic_system: dict,
    raw_history: list[dict],
    bot_user_id: int,
    bot_display_name: str
) -> list[dict]:
    """
    Builds clean conversation history for the LLM, inserting session-gap time markers
    when >= 45 minutes elapsed between messages, annotating when a user message was
    addressed to a DIFFERENT bot, tracking the bot's own recent emotes, openers,
    stock phrases, bio example quotes, overused topic nouns, and meme captions to
    prevent repetitive loops, and injecting organic human-like spontaneity cues.
    """
    api_messages = [dynamic_system]
    seen_msg_ids = set()
    own_recent_openers = []
    own_recent_texts = []
    recent_human_texts = []
    own_emote_counter = Counter()
    recent_meme_captions = []
    active_human_speakers = []
    bot_name_lower = bot_display_name.lower()
    last_msg_ts: float | None = None

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

        # Detect long silence gaps (>= 45 mins) between turns and inject a session boundary marker
        msg_ts = msg.get("timestamp")
        if isinstance(msg_ts, (int, float)):
            if last_msg_ts is not None and (msg_ts - last_msg_ts) >= SESSION_GAP_SECONDS:
                gap_label = format_duration_gap(msg_ts - last_msg_ts)
                api_messages.append({
                    "role": "system",
                    "content": (
                        f"[System Time Marker: {gap_label} of silence passed — A new conversation session started here. "
                        "Treat earlier messages above as past history and do NOT resume old bits or stale jokes from earlier unless explicitly asked.]"
                    )
                })
                # Clear topic-fixation and emote windows across session boundaries so yesterday's
                # conversation doesn't trigger false anti-obsession warnings in today's session
                recent_human_texts.clear()
                own_recent_texts.clear()
                own_emote_counter.clear()
            last_msg_ts = float(msg_ts)

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
                    if cap and cap.upper() not in recent_meme_captions:
                        recent_meme_captions.append(cap.upper())

            own_text = ERROR_REPLY_STRIP_REGEX.sub('', own_text).strip()
            own_text = SYSTEM_NOTE_INLINE_REGEX.sub('', own_text).strip()
            own_text = LEARNED_INLINE_REGEX.sub('', own_text).strip()
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
                topic_sample = f"{own_text} {saved_meme_spec or ''} {saved_img_prompt or ''}".strip()
                own_recent_texts.append(topic_sample)
                for em in extract_used_emotes(own_text):
                    own_emote_counter[em] += 1
                spoken_only = LEADING_EMOTE_STRIP_REGEX.sub('', own_text).strip()
                words_raw = own_text.split()
                words_spoken = spoken_only.split()
                if len(words_raw) >= 3:
                    own_recent_openers.append(" ".join(words_raw[:4]).lower())
                if len(words_spoken) >= 3:
                    sp_opener = " ".join(words_spoken[:4]).lower()
                    if sp_opener not in own_recent_openers:
                        own_recent_openers.append(sp_opener)

            # Do NOT re-inject `[IMAGE_GEN: ...]` or `[MEME_GEN: ...]` tags into past assistant
            # turns in history—leaving old media tags in assistant turns causes the LLM to
            # spam unsolicited memes on every turn and fixate on the old visual prompt!
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
            if not msg.get("author_is_bot"):
                if author_name and author_name not in active_human_speakers:
                    active_human_speakers.append(author_name)
                if idx >= len(raw_history) - 8:
                    recent_human_texts.append(other_text)

            addressed_bot = msg.get("addressed_bot")
            glitch_summary = extract_glitch_summary(other_text)
            other_text = ERROR_REPLY_STRIP_REGEX.sub('', other_text).strip()
            other_text = OTHER_BOT_ATTACHED_IMG_NOTE_REGEX.sub(
                r'[\1 attached a generated image]',
                other_text
            )
            other_text = SYSTEM_NOTE_INLINE_REGEX.sub('', other_text).strip()
            other_text = LEARNED_INLINE_REGEX.sub('', other_text).strip()

            if not other_text or EMPTY_SPEAKER_TAG_REGEX.fullmatch(other_text):
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
                other_text = OTHER_BOT_MEDIA_DESC_REGEX.sub(
                    r'[Attached Media Description (already generated by another bot — DO NOT copy this visual scene): \1]',
                    other_text
                )

            if not other_text.startswith(f"[{author_name}") and not other_text.startswith("[Replying to"):
                other_text = f"[{author_name}]: {other_text}"

            if addressed_bot and addressed_bot.lower() != bot_name_lower:
                other_text = re.sub(
                    rf'(?m)^\[{re.escape(author_name)}\]:',
                    f"[{author_name} (speaking to {addressed_bot}, NOT to {bot_display_name})]:",
                    other_text,
                    count=1
                )
            elif addressed_bot and addressed_bot.lower() == bot_name_lower:
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

    # Build a rich, dynamic Variety, Anti-Obsession & Human Spontaneity guidance block
    variety_rules = []
    bio_quotes = _extract_bio_example_quotes(dynamic_system.get("content", ""))
    if bio_quotes:
        bio_q_str = ", ".join(f'"{bq}..."' for bq in bio_quotes[:3])
        variety_rules.append(
            f"- **NEVER PARROT BIO EXAMPLE QUOTES**: Example lines in your bio ({bio_q_str}) are strictly tone illustrations—NEVER recite or copy them into live chat!"
        )

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
            f"- **BREAK FORMULAIC TEMPLATES & NEVER ECHO PREVIOUS LINES**: You already used {tpl_str} in recent turns. "
            "NEVER repeat or paraphrase your own earlier sentences—write a completely fresh response!"
        )

    overused_topics, user_asked_topic_change = _detect_overused_or_banned_topics(
        own_recent_texts, recent_human_texts, bot_display_name
    )
    if user_asked_topic_change:
        banned_t_str = ", ".join(f'"{w}"' for w in overused_topics) if overused_topics else "the previous recurring joke"
        variety_rules.append(
            f"- **USER REQUESTED TOPIC CHANGE (MANDATORY PIVOT)**: A user explicitly asked to stop talking about {banned_t_str} and move on! "
            f"Do NOT mention {banned_t_str} even as a joke, apology, or side comment, and do NOT generate any image/meme about it. Pivot 100% to a brand-new subject!"
        )
    elif len(overused_topics) >= 2:
        stale_t_str = ", ".join(f'"{w}"' for w in overused_topics[:6])
        variety_rules.append(
            f"- **ANTI-OBSESSION / DROP STALE JOKES**: You have already joked about {stale_t_str} across multiple recent turns. "
            "That bit is played out—move on to a fresh observation or new angle instead of repeating the same gag!"
        )

    banned_captions = list(dict.fromkeys(
        recent_meme_captions[-6:] + [c.upper() for c in sorted(OVERUSED_MEME_CAPTIONS)]
    ))
    if banned_captions:
        cap_str = "; ".join(f'"{c}"' for c in banned_captions[:6])
        variety_rules.append(
            f"- **BANNED MEME CAPTIONS (NEVER REUSE THESE)**: {cap_str}. "
            "If you generate a meme (`[MEME_GEN: scene | <witty setup> | <witty punchline>]`), both captions MUST be real, funny punchlines (NEVER literal words like 'TOP CAPTION')."
        )

    micro_impulse = random.choice(SPONTANEOUS_MICRO_MOMENTS)
    variety_rules.append(f"- **SPONTANEOUS CONVERSATIONAL IMPULSE**: {micro_impulse}")
    variety_rules.append(
        f"- **SINGLE-TURN DISCORD CHAT FORMAT (CRITICAL)**: Output ONLY your own single response as {bot_display_name}. "
        "Do NOT wrap your spoken sentences in quotation marks (`\"...\"`), NEVER echo the user's message at the start, "
        "NEVER output two separate turns on two lines, and NEVER write meta-stage notes like '[No response—...]'!"
    )

    if variety_rules:
        api_messages[0]["content"] += (
            "\n\n### Dynamic Spontaneity, Variety & Anti-Repetition Rules (Active This Turn):\n"
            + "\n".join(variety_rules)
        )

    return api_messages


def upsert_chat_memory_entry(chat_memory_file: str, channel_id: int, entry: dict, max_history_length: int = 200):
    """
    Atomically inserts or updates a message in chat_memory.json by message_id,
    stamping every entry with a Unix timestamp (`timestamp`) for session-gap awareness
    while preserving rich media descriptions and target-bot attribution tags.
    """
    if isinstance(entry, dict) and "timestamp" not in entry:
        entry = dict(entry)
        entry["timestamp"] = time.time()

    def _mutate(chat_memory):
        if channel_id not in chat_memory or not isinstance(chat_memory[channel_id], list):
            chat_memory[channel_id] = []

        healed_history = []
        for existing_item in chat_memory[channel_id]:
            c_val = existing_item.get("content", "")
            if isinstance(c_val, str) and existing_item.get("role") != "system":
                glitch_sum = extract_glitch_summary(c_val)
                cleaned_c = ERROR_REPLY_STRIP_REGEX.sub('', c_val).strip()
                if not cleaned_c or EMPTY_SPEAKER_TAG_REGEX.fullmatch(cleaned_c):
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
                            "timestamp": existing_item.get("timestamp", time.time()),
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
                    orig_ts = merged.get("timestamp")
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
                                    "[Linked Webpage:",
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
                    if isinstance(orig_ts, (int, float)):
                        merged["timestamp"] = orig_ts
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
    server_indicators = set(BASE_SERVER_INDICATORS)
    for u in (recent_users or set()):
        if u:
            for part in ALPHANUM_TOKEN_REGEX.findall(str(u).lower()):
                if len(part) >= 3:
                    server_indicators.add(part)

    bot_name_tokens = set()
    for b in (other_bot_names or set()):
        if b:
            for part in ALPHANUM_TOKEN_REGEX.findall(str(b).lower()):
                if len(part) >= 3:
                    server_indicators.add(part)
                    if part not in ("bot", "the", "guild"):
                        bot_name_tokens.add(part)
    for part in ALPHANUM_TOKEN_REGEX.findall((my_bot_name or "").lower()):
        if len(part) >= 3:
            server_indicators.add(part)
            if part not in ("bot", "the", "guild"):
                bot_name_tokens.add(part)

    def _is_bot_banter_or_meta(fact_str: str, is_gen: bool) -> bool:
        if is_ephemeral_or_banter_fact(fact_str):
            return True
        f_low = fact_str.lower()
        f_words = set(ALPHANUM_TOKEN_REGEX.findall(f_low))
        if is_gen and (f_words & bot_name_tokens):
            return True
        if (f_words & bot_name_tokens) and any(w in f_low for w in ("claims", "joked", "said", "archived", "ritual", "recursion")):
            return True
        return False

    final_server_facts = []
    final_general_facts = []
    for gf in (raw_general_facts or []):
        clean_gf = gf.strip().rstrip(".")
        if clean_gf and not _is_bot_banter_or_meta(clean_gf, is_gen=True):
            final_general_facts.append(clean_gf)

    for fact in (raw_server_facts or []):
        clean_f = fact.strip().rstrip(".")
        if not clean_f or _is_bot_banter_or_meta(clean_f, is_gen=False):
            continue
        words = set(ALPHANUM_TOKEN_REGEX.findall(clean_f.lower()))
        if words & server_indicators:
            final_server_facts.append(clean_f)
        else:
            print(f"[{my_bot_name}] [FACT ROUTER] Auto-routed universal fact from Server Lore -> General Knowledge: '{clean_f}'")
            final_general_facts.append(clean_f)

    return final_server_facts, final_general_facts
