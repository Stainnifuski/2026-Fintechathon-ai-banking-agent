"""No public, predictable signing key, including local deployments."""
import os

from .knowledge_integrity import private_file_key

PLACEHOLDERS = frozenset({'dev-secret-key-change-in-production',
                          'your-jwt-secret-change-in-production',
                          'your-secret-key', 'changeme', 'secret'})


def jwt_signing_secret() -> str:
    value = os.getenv('JWT_SECRET', '').strip()
    if value:
        if value.lower() in PLACEHOLDERS or len(value.encode()) < 32:
            raise ValueError('JWT_SECRET_UNSAFE')
        return value
    # Stable across workers and restarts; independent from the knowledge key.
    return private_file_key('jwt-signing.key').hex()
