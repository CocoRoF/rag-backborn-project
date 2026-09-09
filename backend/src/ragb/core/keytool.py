"""Fernet key helpers: `python -m ragb.core.keytool generate|legacy`."""
from __future__ import annotations

import sys

from cryptography.fernet import Fernet

from ragb.core.security import legacy_fernet_key


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "generate"
    if cmd == "generate":
        print(Fernet.generate_key().decode())
    elif cmd == "legacy":
        # The key currently in use on an install that never set RAGB_ENCRYPTION_KEY. Pin this
        # BEFORE rotating RAGB_SECRET_KEY, or every stored secret becomes undecryptable.
        print(legacy_fernet_key())
    else:
        print("usage: keytool [generate|legacy]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
