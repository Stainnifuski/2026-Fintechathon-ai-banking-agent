"""Runtime loads immutable final prices, including bank fee adjustments; never generates."""
import hashlib
import json
from pathlib import Path

DIRECTORY=Path(__file__).resolve().parents[3]/'config/wealth/data'
DATASET_VERSION='20261003-cn-fixed-v1'


def load_file(filename: str,directory=DIRECTORY):
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    entry=manifest['files'].get(filename)
    if not entry:raise ValueError('FIXED_PRICE_FILE_MISSING')
    raw=(directory/filename).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('FIXED_PRICE_FINGERPRINT_CHANGED')
    return json.loads(raw)


def synthetic_payload(directory=DIRECTORY):
    return load_file('synthetic.json',directory)


def bank_products(directory=DIRECTORY):
    return load_file('bank_products.json',directory)
