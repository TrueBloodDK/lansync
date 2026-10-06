"""Transactional local state; synchronized server revisions come later."""

import json
from pathlib import Path
import sqlite3
import uuid


class Store:
    def __init__(self, database: Path, root: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        try:
            with self.connection:
                self.connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.connection.execute("CREATE TABLE IF NOT EXISTS entries (path TEXT PRIMARY KEY, data TEXT NOT NULL)")
                existing = dict(self.connection.execute("SELECT key, value FROM meta"))
                if existing and existing.get("root") != str(root):
                    raise ValueError("This state directory belongs to another root folder")
                if not existing:
                    for key, value in {"root": str(root), "device_id": str(uuid.uuid4()),
                                       "folder_id": str(uuid.uuid4()), "generation": "0"}.items():
                        self.connection.execute("INSERT INTO meta VALUES (?, ?)", (key, value))
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def update(self, result):
        with self.connection:
            metadata = dict(self.connection.execute("SELECT key, value FROM meta"))
            entries = {path: json.loads(data) for path, data in self.connection.execute("SELECT path, data FROM entries")}
            before = {path: dict(entry) for path, entry in entries.items()}
            def covered(path):
                return result.scopes is None or any(path == scope or path.startswith(scope + "/") for scope in result.scopes)

            previous = json.loads(metadata.get("last_manifest", "{}"))
            errors = result.errors + [item for item in previous.get("errors", []) if not covered(item["path"])]
            excluded = result.skipped + [item for item in previous.get("skipped", []) if not covered(item["path"])]
            # Conservative policy: a partial scan cannot prove any deletion.
            if not result.errors:
                skipped = [item["path"] for item in result.skipped]
                for path, entry in entries.items():
                    protected = any(path == prefix or path.startswith(prefix + "/") for prefix in skipped)
                    if covered(path) and path not in result.entries and not protected:
                        entry["deleted"] = True
            entries.update(result.entries)
            generation = int(metadata["generation"]) + 1
            self.connection.executemany("INSERT OR REPLACE INTO entries VALUES (?, ?)",
                                        [(path, json.dumps(entry, ensure_ascii=False)) for path, entry in entries.items()
                                         if before.get(path) != entry])
            self.connection.execute("UPDATE meta SET value=? WHERE key='generation'", (str(generation),))
            manifest = {"schema_version": 1, "device_id": metadata["device_id"],
                        "folder_id": metadata["folder_id"], "generation": generation,
                        "complete": not errors and not excluded,
                        "errors": errors, "skipped": excluded,
                        "entries": [entries[key] for key in sorted(entries)]}
            self.connection.execute("INSERT OR REPLACE INTO meta VALUES ('last_manifest', ?)",
                                    (json.dumps(manifest, ensure_ascii=False),))
        return manifest
