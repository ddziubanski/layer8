"""Values a run needs but nobody may see or keep (a new AD account's first password).

They're encrypted with a key that lives in the secrets folder (not the database), handed to the
runner once when it claims the run, and then cleared, so neither the database nor a backup holds
them in the clear, and they're gone once the run starts.
"""

import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.configstore import write_secret_file

KEY_FILE = "run-seal.key"


def _fernet() -> Fernet:
    path = settings.secrets_dir / KEY_FILE
    if not path.is_file():
        write_secret_file(path, Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def seal(values: dict) -> bytes:
    return _fernet().encrypt(json.dumps(values).encode())


def unseal(token: bytes | None) -> dict:
    """The values, or {} if there are none or they can't be read (e.g. the key was replaced)."""
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(bytes(token)))
    except (InvalidToken, ValueError):
        return {}
