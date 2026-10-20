"""Server-owned business instructions used by the unified agent."""
from functools import lru_cache
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills"


@lru_cache(maxsize=16)
def load_skill(name: str) -> str:
    if name != "transfer":
        raise ValueError("未知业务 Skill")
    return (SKILL_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
