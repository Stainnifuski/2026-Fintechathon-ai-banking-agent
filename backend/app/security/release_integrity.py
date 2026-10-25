"""Reviewed checksums for server-owned Skills, kept with the application code.

Check: python -m backend.app.security.release_integrity
After reviewing intentional Skill changes: ...release_integrity --write
Never regenerate this manifest automatically during startup or image build.
"""
import hashlib
import json
from pathlib import Path

from .agent_guard import security_event

ROOT = Path(__file__).resolve().parents[1] / 'skills'
MANIFEST = Path(__file__).with_name('skill_hashes.json')
NAMES = frozenset({'transfer', 'collection', 'wealth', 'card', 'subscription', 'calendar', 'shop', 'bills'})


def _read(name: str) -> bytes:
    if name not in NAMES:
        raise ValueError('UNKNOWN_SKILL')
    path = ROOT / name / 'SKILL.md'
    if ROOT.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise ValueError('SKILL_INTEGRITY_REJECTED')
    return path.read_bytes()


def load_verified_skill(name: str) -> str:
    try:
        content = _read(name)
        hashes = json.loads(MANIFEST.read_text(encoding='utf-8'))
        if hashes.get(name) != hashlib.sha256(content).hexdigest():
            raise ValueError('SKILL_INTEGRITY_REJECTED')
        return content.decode('utf-8')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        security_event('SKILL_INTEGRITY_REJECTED')
        raise ValueError('SKILL_INTEGRITY_REJECTED') from None


def verify_skills() -> None:
    for name in sorted(NAMES):
        load_verified_skill(name)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='approve reviewed Skill content')
    args = parser.parse_args()
    if args.write:
        hashes = {name: hashlib.sha256(_read(name)).hexdigest() for name in sorted(NAMES)}
        MANIFEST.write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    verify_skills()
    print('All 8 server-owned Skills match the reviewed manifest.')
