import json
import os
from pathlib import Path
import tempfile
from .durability import make_directory, sync_directory


def export_manifest(manifest: dict, destination: Path):
    """Replace the previous export only after a complete write."""
    make_directory(destination.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".lansync-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
