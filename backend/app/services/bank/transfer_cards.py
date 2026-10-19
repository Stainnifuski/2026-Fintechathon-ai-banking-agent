"""Stable, full simulation-only receiving card numbers for ordinary accounts."""

import hmac
import os
from hashlib import sha256


def transfer_card_number(account_id: str) -> str:
    # Account.card_number is masked historical display data, so it cannot identify a receiver.
    # Account IDs appear in transfer APIs. A keyed derivation prevents callers
    # from reconstructing another person's card number from an account ID.
    key = (os.getenv("TRANSFER_CARD_SECRET") or os.getenv("JWT_SECRET") or
           "dev-secret-key-change-in-production").encode()
    digest = hmac.new(key, account_id.encode(), sha256).hexdigest()
    stem = "6214" + str(int(digest, 16) % 10**11).zfill(11)
    total = 0
    for index, digit in enumerate(reversed(stem)):
        value = int(digit) * (2 if index % 2 == 0 else 1)
        total += value // 10 + value % 10
    return stem + str((-total) % 10)
