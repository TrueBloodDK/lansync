import hashlib
import os
from pathlib import Path


def default_state(root):
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return base / "lansync" / key

