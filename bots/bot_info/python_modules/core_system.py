import os
import json
import time
import random
import asyncio
import threading
import copy
import requests

# Import our regex from the new config module
from config import MARKDOWN_URL_WRAP_REGEX

# ==============================================================================
# THREAD-SAFE CONNECTION POOLING & CROSS-PROCESS ATOMIC FILE ENGINE
# ==============================================================================
_thread_local = threading.local()
_JSON_MEM_CACHE = {}  # {abs_path: (mtime, data_copy)}

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
    so concurrent asyncio.to_thread calls never contend on shared Session state.
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
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


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
    fresh socket recovery on 502/503/504/Timeout, and Retry-After header awareness.
    """
    clean_url = sanitize_url(url)
    last_res = None
    reset_next = False
    injected_retry_note = False

    for attempt in range(max_retries):
        if inject_glitch_on_retry and attempt > 0 and not injected_retry_note and glitch_tracker:
            messages_list = json_payload.get("messages")
            if isinstance(messages_list, list):
                glitch_desc = "; ".join(glitch_tracker[-2:])
                messages_list.append({
                    "role": "system",
                    "content": (
                        f"[System Glitch Context: Your neural API just hit a brief lag spike / glitch ({glitch_desc}) "
                        f"on attempt 1 before recovering on retry #{attempt + 1}. You may briefly grumble, joke, or react "
                        "in-character to your brain-freeze or lag spike while answering, but NEVER output raw '*(API Error...)*' text.]"
                    )
                })
                injected_retry_note = True

        try:
            should_reset = reset_next
            reset_next = False
            res = await asyncio.to_thread(
                lambda r=should_reset: get_http_session(reset=r).post(clean_url, headers=headers, json=json_payload, timeout=timeout)
            )
            last_res = res
            if res.status_code == 200:
                return res
            if res.status_code in (429, 500, 502, 503, 504):
                if glitch_tracker is not None:
                    glitch_tracker.append(f"HTTP {res.status_code} on attempt {attempt + 1}")
                if res.status_code in (502, 503, 504):
                    reset_next = True
                retry_after = res.headers.get("Retry-After")
                parsed_retry = None
                if retry_after is not None:
                    try:
                        parsed_retry = float(retry_after)
                    except (ValueError, TypeError):
                        parsed_retry = None
                wait_time = parsed_retry if (parsed_retry is not None and parsed_retry >= 0) else (1.8 * (attempt + 1) + random.uniform(0.1, 0.6))
                wait_time = min(wait_time, 12.0)
                print(f"[{bot_name}] [HTTP {res.status_code}] Retrying in {wait_time:.1f}s ({attempt + 1}/{max_retries})...")
                await asyncio.sleep(wait_time)
            else:
                if glitch_tracker is not None:
                    glitch_tracker.append(f"HTTP {res.status_code}")
                return res
        except requests.exceptions.Timeout:
            reset_next = True
            if glitch_tracker is not None:
                glitch_tracker.append(f"API timeout on attempt {attempt + 1}")
            print(f"[{bot_name}] [TIMEOUT] Request to {clean_url.split('/')[-1]} timed out ({attempt + 1}/{max_retries}).")
            await asyncio.sleep(1.5 * (attempt + 1))
        except Exception as e:
            reset_next = True
            if glitch_tracker is not None:
                glitch_tracker.append(f"network drop on attempt {attempt + 1}")
            print(f"[{bot_name}] [NETWORK ERROR] {e} ({attempt + 1}/{max_retries}).")
            await asyncio.sleep(1.5 * (attempt + 1))
    return last_res


class FileLock:
    """
    Windows-safe, OS-agnostic cross-process file lock using atomic O_CREAT | O_EXCL
    lockfiles with stale-lock eviction so concurrent bots never corrupt shared JSON files.
    """
    def __init__(self, target_file: str, timeout: float = 3.0):
        self.lock_file = f"{os.path.abspath(target_file)}.lock"
        self.timeout = timeout
        self.fd = None
        self.acquired = False

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self.fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    if os.path.exists(self.lock_file) and (time.time() - os.path.getmtime(self.lock_file)) > 4.0:
                        os.remove(self.lock_file)
                        continue
                except Exception:
                    pass
                if (time.time() - start) >= self.timeout:
                    return self
                time.sleep(random.uniform(0.004, 0.015))
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
            try:
                if os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
            except Exception:
                pass
            self.acquired = False


def _read_json_unlocked(abs_path: str, default_val, retries: int = 5):
    if not os.path.exists(abs_path):
        return copy.deepcopy(default_val)

    for _ in range(retries):
        try:
            mtime = os.path.getmtime(abs_path)
            cached = _JSON_MEM_CACHE.get(abs_path)
            if cached and cached[0] == mtime:
                return copy.deepcopy(cached[1])

            with open(abs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(default_val, dict) and isinstance(data, dict):
                    parsed = {(int(k) if str(k).isdigit() else k): v for k, v in data.items()}
                else:
                    parsed = data
                _JSON_MEM_CACHE[abs_path] = (mtime, copy.deepcopy(parsed))
                return parsed
        except Exception:
            time.sleep(random.uniform(0.006, 0.025))
    return copy.deepcopy(default_val)


def load_json_file(file_name: str, default_val, retries: int = 5):
    abs_path = os.path.abspath(file_name)
    if not os.path.exists(abs_path):
        return copy.deepcopy(default_val)

    try:
        mtime = os.path.getmtime(abs_path)
        cached = _JSON_MEM_CACHE.get(abs_path)
        if cached and cached[0] == mtime:
            return copy.deepcopy(cached[1])
    except Exception:
        pass

    with FileLock(abs_path):
        return _read_json_unlocked(abs_path, default_val, retries)


def save_json_file(data, file_name: str, retries: int = 5):
    abs_path = os.path.abspath(file_name)
    dir_name = os.path.dirname(abs_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with FileLock(abs_path):
        for _ in range(retries):
            tmp_file = f"{abs_path}.{os.getpid()}.{threading.get_ident()}.{random.randint(1000, 9999)}.tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_file, abs_path)
                mtime = os.path.getmtime(abs_path)
                _JSON_MEM_CACHE[abs_path] = (mtime, copy.deepcopy(data))
                return
            except Exception:
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception:
                    pass
                time.sleep(random.uniform(0.008, 0.03))


def update_json_file(file_name: str, default_val, mutator_fn):
    """
    Atomically locks, loads, mutates, and saves a shared JSON file across processes.
    """
    abs_path = os.path.abspath(file_name)
    dir_name = os.path.dirname(abs_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with FileLock(abs_path):
        data = _read_json_unlocked(abs_path, default_val)
        updated_data = mutator_fn(data)
        if updated_data is not None:
            data = updated_data

        for _ in range(5):
            tmp_file = f"{abs_path}.{os.getpid()}.{threading.get_ident()}.{random.randint(1000, 9999)}.tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_file, abs_path)
                mtime = os.path.getmtime(abs_path)
                _JSON_MEM_CACHE[abs_path] = (mtime, copy.deepcopy(data))
                break
            except Exception:
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception:
                    pass
                time.sleep(random.uniform(0.008, 0.03))
        return data