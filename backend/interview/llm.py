"""
Interview LLM helpers.

Supports single-key and multi-key configs for interview-related calls.
"""

import os
import threading
from itertools import count
from typing import Iterable

from openai import OpenAI

_KEY_COUNTER = count()
_KEY_LOCK = threading.Lock()


def _split_keys(raw: str) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _candidate_key_sets() -> Iterable[list[str]]:
    yield _split_keys(os.environ.get("INTERVIEW_LLM_API_KEYS", ""))
    single_interview_key = os.environ.get("INTERVIEW_LLM_API_KEY", "").strip()
    yield [single_interview_key] if single_interview_key else []
    yield _split_keys(os.environ.get("LLM_API_KEYS", ""))
    single_global_key = os.environ.get("LLM_API_KEY", "").strip()
    yield [single_global_key] if single_global_key else []


def get_llm_model() -> str:
    return (
        os.environ.get("INTERVIEW_LLM_MODEL")
        or os.environ.get("LLM_MODEL")
        or "deepseek-chat"
    ).split("/")[-1]


def get_llm_base_url() -> str:
    return (
        os.environ.get("INTERVIEW_LLM_API_BASE")
        or os.environ.get("LLM_API_BASE")
        or "https://api.deepseek.com"
    )


def get_llm_keys() -> list[str]:
    for keys in _candidate_key_sets():
        if keys:
            return keys
    return []


def get_next_llm_key() -> str:
    keys = get_llm_keys()
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]
    with _KEY_LOCK:
        idx = next(_KEY_COUNTER) % len(keys)
    return keys[idx]


def make_llm_client(timeout: int | float = 60) -> tuple[OpenAI, str]:
    return (
        OpenAI(
            api_key=get_next_llm_key(),
            base_url=get_llm_base_url(),
            timeout=timeout,
        ),
        get_llm_model(),
    )

