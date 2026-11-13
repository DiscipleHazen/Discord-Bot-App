import os
import json
import time
import random
import asyncio
import threading
import copy
import requests

# Import our regex from the config module
from config import MARKDOWN_URL_WRAP_REGEX

# ==============================================================================
# THREAD-SAFE CONNECTION POOLING, ADAPTIVE CIRCUIT BREAKER & ATOMIC FILE ENGINE
# ==============================================================================
_thread_local = threading.local()
_CACHE_LOCK = threading.RLock()
_JSON_MEM_CACHE = {}  # {abs_path: (mtime_ns, file_size, data_copy, serialized_str)}
_MAX_MEM_CACHE_FILES = 64

_CIRCUIT_LOCK = threading.RLock()
_MODEL_CIRCUIT_STATE = {}  # {model_key: {"failures": int, "open_until": float}}
_CIRCUIT_FAILURE_THRESHOLD = 2
_CIRCUIT_COOLDOWN_SECONDS = 45.0

RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 520, 522, 524})
SOCKET_RESET_STATUSES = frozenset({502, 503, 504, 520, 522, 524})


def sanitize_url(url: str) -> str:
    """
    Strips any accidental Markdown link formatting like '[https://...](https://...)'
    so requests never fails with InvalidSchema if a URL was auto-linked on copy.
    """
    if not url or not isinstance(url, str):
        return ""
    cleaned = MARKDOWN_URL_WRAP_REGEX.sub(r'\2', url.strip())
    return cleaned.strip("<> ")


def get_http_session(reset: bool = False) -> requests.Session:
    """
    Returns a thread-local persistent requests.Session with connection pooling
    and warm keep-alive headers so concurrent asyncio.to_thread calls never
    contend on shared Session state.
    """
    session = getattr(_thread_local, "session", None)
    if reset and session is not None:
        try:
            session.close()
        except Exception:
            pass
        session = None

    if session is None:
        session = requests.Session()
        session.headers.update({
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "DiscordMultiAgentFramework/3.0"
        })
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=30,
            pool_maxsize=30,
            max_retries=1,
            pool_block=False
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def is_model_circuit_open(model_key: str) -> bool:
    """Returns True if `model_key` is currently in a fast-fail circuit-breaker cooldown window."""
    if not model_key:
        return False
    now_m = time.monotonic()
    with _CIRCUIT_LOCK:
        st = _MODEL_CIRCUIT_STATE.get(model_key)
        if not st:
            return False
        if now_m < st.get("open_until", 0.0):
            return True
        return False


def record_model_outcome(model_key: str, success: bool):
    """Updates the adaptive circuit-breaker state for `model_key` after an HTTP attempt."""
    if not model_key:
        return
    now_m = time.monotonic()
    with _CIRCUIT_LOCK:
        if success:
            _MODEL_CIRCUIT_STATE.pop(model_key, None)
            return
        st = _MODEL_CIRCUIT_STATE.setdefault(model_key, {"failures": 0, "open_until": 0.0})
        st["failures"] = st.get("failures", 0) + 1
        if st["failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
            st["open_until"] = now_m + _CIRCUIT_COOLDOWN_SECONDS


async def post_with_backoff(
    url: str,
    headers: dict,
    json_payload: dict,
    timeout: float | tuple = 45,
    max_retries: int = 3,
    bot_name: str = "Bot",
    glitch_tracker: list | None = None,
    inject_glitch_on_retry: bool = False
) -> requests.Response | None:
    """
    Executes an HTTP POST request on a worker thread with exponential backoff,
    adaptive per-model circuit-breaker fast-failover, fresh socket recovery on
    502/503/504/522/524/Timeout, Retry-After awareness, and automatic retry
    protection against truncated or empty HTTP 200 payloads.
    """
    clean_url = sanitize_url(url)
    last_res = None
    reset_next = False
    injected_retry_note = False
    active_payload = json_payload
    endpoint_label = clean_url.rsplit("/", 1)[-1] if clean_url else "endpoint"
    model_name = str(json_payload.get("model", "") if isinstance(json_payload, dict) else "")
    circuit_key = f"{endpoint_label}:{model_name}" if model_name else endpoint_label

    # If this specific model endpoint is in an active circuit-breaker cooldown window,
    # cap retries to 1 and tighten timeout so callers fail over to fallback models fast.
    effective_retries = max_retries
    effective_timeout = timeout
    if is_model_circuit_open(circuit_key) and max_retries > 1:
        effective_retries = 1
        if isinstance(timeout, (int, float)) and timeout > 22:
            effective_timeout = 22

    for attempt in range(effective_retries):
        if inject_glitch_on_retry and attempt > 0 and not injected_retry_note and glitch_tracker:
            messages_list = active_payload.get("messages")
            if isinstance(messages_list, list):
                glitch_desc = "; ".join(glitch_tracker[-2:])
                active_payload = dict(active_payload)
                active_payload["messages"] = list(messages_list) + [{
                    "role": "system",
                    "content": (
                        f"[System Glitch Context: Your neural API just hit a brief lag spike / glitch ({glitch_desc}) "
                        f"on attempt 1 before recovering on retry #{attempt + 1}. You may briefly grumble, joke, or react "
                        "in-character to your brain-freeze or lag spike while answering, but NEVER output raw '*(API Error...)*' text.]"
                    )
                }]
                injected_retry_note = True

        try:
            should_reset = reset_next
            reset_next = False
            res = await asyncio.to_thread(
                lambda r=should_reset, p=active_payload, t=effective_timeout: get_http_session(reset=r).post(
                    clean_url, headers=headers, json=p, timeout=t
                )
            )
            last_res = res
            if res.status_code == 200:
                if not res.content:
                    reset_next = True
                    record_model_outcome(circuit_key, success=False)
                    if glitch_tracker is not None:
                        glitch_tracker.append(f"empty HTTP 200 payload on attempt {attempt + 1}")
                    print(f"[{bot_name}] [WARN] Empty HTTP 200 body from {endpoint_label}; retrying ({attempt + 1}/{effective_retries})...")
                    await asyncio.sleep(1.2 * (attempt + 1) + random.uniform(0.08, 0.3))
                    continue

                ctype = res.headers.get("Content-Type", "").lower()
                if "application/json" in ctype:
                    try:
                        res.json()
                    except Exception:
                        reset_next = True
                        record_model_outcome(circuit_key, success=False)
                        if glitch_tracker is not None:
                            glitch_tracker.append(f"truncated JSON on attempt {attempt + 1}")
                        print(f"[{bot_name}] [WARN] Truncated/corrupt JSON in HTTP 200 from {endpoint_label}; retrying ({attempt + 1}/{effective_retries})...")
                        await asyncio.sleep(1.2 * (attempt + 1) + random.uniform(0.08, 0.3))
                        continue

                record_model_outcome(circuit_key, success=True)
                return res

            if res.status_code in RETRYABLE_HTTP_STATUSES:
                if glitch_tracker is not None:
                    glitch_tracker.append(f"HTTP {res.status_code} on attempt {attempt + 1}")
                if res.status_code in SOCKET_RESET_STATUSES:
                    reset_next = True
                    record_model_outcome(circuit_key, success=False)
                if attempt + 1 >= effective_retries:
                    break
                retry_after = res.headers.get("Retry-After")
                parsed_retry = None
                if retry_after is not None:
                    try:
                        parsed_retry = float(retry_after)
                    except (ValueError, TypeError):
                        parsed_retry = None
                wait_time = (
                    parsed_retry
                    if (parsed_retry is not None and parsed_retry >= 0)
                    else (1.5 * (attempt + 1) + random.uniform(0.12, 0.50))
                )
                wait_time = min(wait_time, 10.0)
                print(f"[{bot_name}] [HTTP {res.status_code}] Retrying in {wait_time:.1f}s ({attempt + 1}/{effective_retries})...")
                await asyncio.sleep(wait_time)
            else:
                if glitch_tracker is not None:
                    glitch_tracker.append(f"HTTP {res.status_code}")
                return res
        except requests.exceptions.Timeout:
            reset_next = True
            record_model_outcome(circuit_key, success=False)
            if glitch_tracker is not None:
                glitch_tracker.append(f"API timeout on attempt {attempt + 1}")
            print(f"[{bot_name}] [TIMEOUT] Request to {endpoint_label} timed out ({attempt + 1}/{effective_retries}).")
            if attempt + 1 < effective_retries:
                await asyncio.sleep(1.3 * (attempt + 1) + random.uniform(0.05, 0.25))
        except Exception as e:
            reset_next = True
            record_model_outcome(circuit_key, success=False)
            if glitch_tracker is not None:
                glitch_tracker.append(f"network drop on attempt {attempt + 1}")
            print(f"[{bot_name}] [NETWORK ERROR] {e} ({attempt + 1}/{effective_retries}).")
            if attempt + 1 < effective_retries:
                await asyncio.sleep(1.3 * (attempt + 1) + random.uniform(0.05, 0.25))
    return last_res


def _is_pid_alive(pid: int) -> bool:
    """
    Cross-platform check (Windows & POSIX) to verify whether an OS process ID
    holding a lockfile is still running.
    """
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                err = ctypes.get_last_error()
                # ERROR_ACCESS_DENIED (5) means the process exists
                return err == 5
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    except Exception:
        return True


def _is_lockfile_stale_or_dead(lock_path: str, max_age: float = 3.5) -> bool:
    """
    Checks if a `.lock` file is either older than `max_age` seconds OR owned by
    a process ID that has already terminated (enabling 0ms dead-lock recovery).
    """
    try:
        if not os.path.exists(lock_path):
            return False
        age = time.time() - os.path.getmtime(lock_path)
        if age > max_age:
            return True
        if age > 0.08:
            with open(lock_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read(64).strip()
            if "|" in raw:
                pid_str = raw.split("|", 1)[0].strip()
                if pid_str.isdigit() and not _is_pid_alive(int(pid_str)):
                    return True
    except Exception:
        pass
    return False


class FileLock:
    """
    Windows-safe, OS-agnostic cross-process file lock using atomic O_CREAT | O_EXCL
    lockfiles with monotonic timeout tracking and 0ms dead-PID lock eviction.
    """
    def __init__(self, target_file: str, timeout: float = 3.5):
        self.lock_file = f"{os.path.abspath(target_file)}.lock"
        self.timeout = timeout
        self.fd = None
        self.acquired = False

    def __enter__(self):
        start = time.monotonic()
        while True:
            try:
                self.fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    os.write(self.fd, f"{os.getpid()}|{time.time():.3f}".encode("utf-8"))
                except Exception:
                    pass
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    if _is_lockfile_stale_or_dead(self.lock_file, max_age=3.5):
                        os.remove(self.lock_file)
                        continue
                except Exception:
                    pass
                if (time.monotonic() - start) >= self.timeout:
                    return self
                time.sleep(random.uniform(0.002, 0.010))
            except Exception:
                return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
        if self.acquired:
            for _ in range(3):
                try:
                    if os.path.exists(self.lock_file):
                        os.remove(self.lock_file)
                    break
                except Exception:
                    time.sleep(0.004)
            self.acquired = False


def _get_file_signature(abs_path: str) -> tuple[int, int] | None:
    """Returns (st_mtime_ns, st_size) for high-precision cache validation."""
    try:
        st = os.stat(abs_path)
        return (getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)), st.st_size)
    except Exception:
        return None


def _normalize_top_dict_keys(data):
    """Normalizes top-level numeric string keys to int so RAM cache and disk reads match 100%."""
    if isinstance(data, dict):
        return {(int(k) if isinstance(k, str) and k.isdigit() else k): v for k, v in data.items()}
    return data


def _store_in_mem_cache(abs_path: str, sig: tuple[int, int], data, serialized_str: str | None = None):
    if serialized_str is None:
        try:
            serialized_str = json.dumps(data, indent=2, ensure_ascii=False)
        except Exception:
            serialized_str = ""
    with _CACHE_LOCK:
        if len(_JSON_MEM_CACHE) >= _MAX_MEM_CACHE_FILES and abs_path not in _JSON_MEM_CACHE:
            oldest_key = next(iter(_JSON_MEM_CACHE))
            _JSON_MEM_CACHE.pop(oldest_key, None)
        _JSON_MEM_CACHE[abs_path] = (sig[0], sig[1], copy.deepcopy(data), serialized_str)


def _read_json_unlocked(abs_path: str, default_val, retries: int = 5, return_serialized: bool = False):
    if not os.path.exists(abs_path):
        empty_copy = copy.deepcopy(default_val)
        return (empty_copy, None) if return_serialized else empty_copy

    for _ in range(retries):
        try:
            sig = _get_file_signature(abs_path)
            if sig is not None:
                with _CACHE_LOCK:
                    cached = _JSON_MEM_CACHE.get(abs_path)
                    if cached and cached[0] == sig[0] and cached[1] == sig[1]:
                        data_copy = copy.deepcopy(cached[2])
                        ser_str = cached[3] if len(cached) > 3 else None
                        return (data_copy, ser_str) if return_serialized else data_copy

            with open(abs_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
                data = json.loads(raw_text)
                if isinstance(default_val, dict) and isinstance(data, dict):
                    parsed = _normalize_top_dict_keys(data)
                else:
                    parsed = data

            norm_ser = json.dumps(parsed, indent=2, ensure_ascii=False)
            sig_after = _get_file_signature(abs_path) or sig
            if sig_after is not None:
                _store_in_mem_cache(abs_path, sig_after, parsed, norm_ser)
            return (parsed, norm_ser) if return_serialized else parsed
        except Exception:
            time.sleep(random.uniform(0.004, 0.015))

    fallback_copy = copy.deepcopy(default_val)
    return (fallback_copy, None) if return_serialized else fallback_copy


def _write_json_unlocked(abs_path: str, data, retries: int = 5, pre_serialized: str | None = None) -> bool:
    """
    Serializes JSON once in memory and atomically writes via a unique temp file,
    updating the nanosecond RAM cache with normalized keys.
    """
    normalized_for_cache = _normalize_top_dict_keys(data) if isinstance(data, dict) else data
    if pre_serialized is not None:
        serialized = pre_serialized
    else:
        try:
            serialized = json.dumps(normalized_for_cache, indent=2, ensure_ascii=False)
        except Exception:
            return False

    pid = os.getpid()
    tid = threading.get_ident()

    for attempt in range(retries):
        tmp_file = f"{abs_path}.{pid}.{tid}.{random.randint(1000, 9999)}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
            os.replace(tmp_file, abs_path)
            sig = _get_file_signature(abs_path)
            if sig is not None:
                _store_in_mem_cache(abs_path, sig, normalized_for_cache, serialized)
            return True
        except Exception:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            time.sleep(0.008 * (attempt + 1) + random.uniform(0.002, 0.012))
    return False


def load_json_file(file_name: str, default_val, retries: int = 5):
    abs_path = os.path.abspath(file_name)
    if not os.path.exists(abs_path):
        return copy.deepcopy(default_val)

    sig = _get_file_signature(abs_path)
    if sig is not None:
        with _CACHE_LOCK:
            cached = _JSON_MEM_CACHE.get(abs_path)
            if cached and cached[0] == sig[0] and cached[1] == sig[1]:
                return copy.deepcopy(cached[2])

    with FileLock(abs_path):
        return _read_json_unlocked(abs_path, default_val, retries)


def save_json_file(data, file_name: str, retries: int = 5):
    abs_path = os.path.abspath(file_name)
    dir_name = os.path.dirname(abs_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with FileLock(abs_path):
        _write_json_unlocked(abs_path, data, retries=retries)


def update_json_file(file_name: str, default_val, mutator_fn):
    """
    Atomically locks, loads, mutates, and saves a shared JSON file across processes.
    Compares the pre-serialized JSON string to eliminate redundant `copy.deepcopy`
    calls and skips disk I/O if the mutator made no changes.
    """
    abs_path = os.path.abspath(file_name)
    dir_name = os.path.dirname(abs_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with FileLock(abs_path):
        data, prev_serialized = _read_json_unlocked(abs_path, default_val, return_serialized=True)
        updated_data = mutator_fn(data)
        if updated_data is not None:
            data = updated_data

        normalized_data = _normalize_top_dict_keys(data) if isinstance(data, dict) else data
        try:
            new_serialized = json.dumps(normalized_data, indent=2, ensure_ascii=False)
        except Exception:
            new_serialized = None

        if new_serialized is not None and (new_serialized != prev_serialized or not os.path.exists(abs_path)):
            _write_json_unlocked(abs_path, normalized_data, retries=5, pre_serialized=new_serialized)
        return normalized_data


async def async_load_json_file(file_name: str, default_val, retries: int = 5):
    """
    Event-loop friendly JSON loader: returns in 0ms on RAM cache hit, and offloads
    lock acquisition / disk reads to a worker thread on cache miss.
    """
    abs_path = os.path.abspath(file_name)
    if not os.path.exists(abs_path):
        return copy.deepcopy(default_val)

    sig = _get_file_signature(abs_path)
    if sig is not None:
        with _CACHE_LOCK:
            cached = _JSON_MEM_CACHE.get(abs_path)
            if cached and cached[0] == sig[0] and cached[1] == sig[1]:
                return copy.deepcopy(cached[2])

    return await asyncio.to_thread(load_json_file, file_name, default_val, retries)


async def async_update_json_file(file_name: str, default_val, mutator_fn):
    """Offloads cross-process locked JSON mutation to a worker thread so the Discord heartbeat never blocks."""
    return await asyncio.to_thread(update_json_file, file_name, default_val, mutator_fn)
