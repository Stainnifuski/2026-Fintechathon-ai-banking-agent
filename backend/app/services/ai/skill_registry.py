"""Server-owned business instructions used by the unified agent."""
from functools import lru_cache
from ...security.release_integrity import load_verified_skill, NAMES


@lru_cache(maxsize=16)
def load_skill(name: str) -> str:
    if name not in NAMES:
        raise ValueError("未知业务 Skill")
    return load_verified_skill(name)
