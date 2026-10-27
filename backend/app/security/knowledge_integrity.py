"""Authenticate ingestion, independently of mutable database content/hash pairs.

The signing key is a deployment secret or a private persistent file, never a DB
value. Sign only reconstructed, approved ingestion output, never arbitrary rows.
"""
import hashlib
import hmac
import json
import os
import stat
import tempfile
from pathlib import Path

from .agent_guard import security_event
from .injection_detector import detect_injection

SIGNATURE = "ingestion_signature"


def _key() -> bytes:
    configured = os.getenv("AGENT_KNOWLEDGE_SIGNING_KEY")
    if configured is not None:
        if len(configured.encode()) < 32:
            raise ValueError("KNOWLEDGE_SIGNING_KEY_INVALID")
        return hashlib.sha256(configured.encode()).digest()
    return private_file_key('knowledge-signing.key')


def private_file_key(filename: str) -> bytes:
    if filename not in {'knowledge-signing.key', 'jwt-signing.key'}:
        raise ValueError('SECURITY_KEY_NAME_INVALID')
    root = Path(os.getenv("AGENT_SECURITY_DIR", str(Path(__file__).resolve().parents[3] / "var" / "agent-security")))
    if root.is_symlink():
        raise ValueError("SECURITY_KEY_PATH_INVALID")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / filename
    if not path.exists():
        # Publish the complete key atomically so concurrent workers cannot read
        # another worker's half-written file. A losing creator uses the winner.
        with tempfile.NamedTemporaryFile(dir=root, prefix='.key-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(os.urandom(32))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
            raise ValueError("SECURITY_KEY_PERMISSIONS_INVALID")
        value = stream.read(33)
    if len(value) != 32:
        raise ValueError("SECURITY_KEY_INVALID")
    return value


def _payload(document_id: str, content: str, metadata: dict) -> bytes:
    return json.dumps({"format": "knowledge-attestation-v1", "document_id": document_id,
                       "content": content, "metadata": {k: v for k, v in metadata.items() if k != SIGNATURE}},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def attest(document_id: str, content: str, metadata: dict) -> dict:
    payload = _payload(document_id, content, metadata)
    if len(payload) > 128 * 1024 or detect_injection(payload.decode()):
        security_event("KNOWLEDGE_INGESTION_REJECTED")
        raise ValueError("KNOWLEDGE_INGESTION_REJECTED")
    signature = hmac.new(_key(), payload, hashlib.sha256).hexdigest()
    return {**metadata, SIGNATURE: "v1:" + signature}


def verified(document_id: str, content: str, metadata: dict) -> bool:
    if not isinstance(metadata, dict) or not isinstance(content, str):
        return False
    signature = metadata.get(SIGNATURE)
    if (not isinstance(signature, str) or not signature.isascii()
            or len(signature) != 67 or not signature.startswith("v1:")):
        return False
    try:
        payload = _payload(document_id, content, metadata)
    except (ValueError, TypeError):
        return False
    if len(payload) > 128 * 1024 or detect_injection(payload.decode()):
        return False
    expected = "v1:" + hmac.new(_key(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
