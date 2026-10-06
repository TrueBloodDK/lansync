"""Full filesystem scan. Errors are data, never evidence of deletion."""

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat


@dataclass
class ScanResult:
    entries: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    scopes: set | None = None  # None means the whole tree was inspected.


def signature(info):
    # Windows runtimes can expose different ctime semantics for stat/fstat.
    change_time = info.st_ctime_ns if os.name != "nt" else None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            change_time)


def hash_file(path: Path):
    """Hash in bounded memory; reject files changed or replaced while read."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("Object is no longer a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if signature(before) != signature(opened):
            raise OSError("File changed before reading")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after_read = os.fstat(stream.fileno())
    after = path.lstat()
    if signature(before) != signature(after_read) or signature(before) != signature(after):
        raise OSError("File changed during reading; rescan required")
    return before, digest.hexdigest()


def scan_tree(root: Path) -> ScanResult:
    result = ScanResult()

    def visit(directory):
        relative = directory.relative_to(root).as_posix()
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            result.errors.append({"path": relative, "message": str(exc)})
            return
        for child in children:
            path = Path(child.path)
            key = path.relative_to(root).as_posix()
            try:
                info = child.stat(follow_symlinks=False)
                is_junction = getattr(path, "is_junction", lambda: False)()
                is_reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
                if stat.S_ISLNK(info.st_mode) or is_junction or is_reparse:
                    result.skipped.append({"path": key, "reason": "link or reparse point"})
                elif stat.S_ISDIR(info.st_mode):
                    result.entries[key] = {"path": key, "kind": "directory", "size": None,
                                           "mtime_ns": info.st_mtime_ns, "sha256": None, "deleted": False}
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    info, digest = hash_file(path)
                    result.entries[key] = {"path": key, "kind": "file", "size": info.st_size,
                                           "mtime_ns": info.st_mtime_ns, "sha256": digest, "deleted": False}
                else:
                    result.skipped.append({"path": key, "reason": "unsupported object"})
            except OSError as exc:
                result.errors.append({"path": key, "message": str(exc)})

    visit(root)
    return result


def scan_paths(root: Path, paths) -> ScanResult:
    """Inspect event targets only; directory targets include their descendants."""
    keys = set()
    for raw in paths:
        path = Path(os.path.abspath(raw))
        try:
            key = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if key == ".":
            return scan_tree(root)
        keys.add(key)
    scopes = {key for key in keys if not any(key.startswith(other + "/") for other in keys if other != key)}
    result = ScanResult(scopes=scopes)
    for key in sorted(scopes):
        path = root / key
        try:
            # Never follow a link in an event target's ancestor chain.
            ancestors = [root, *[parent for parent in reversed(path.parents) if root in parent.parents]]
            for ancestor in ancestors:
                info = ancestor.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OSError("Event path has a linked ancestor")
                if ancestor != root:
                    parent_key = ancestor.relative_to(root).as_posix()
                    result.entries[parent_key] = {"path": parent_key, "kind": "directory", "size": None,
                                                  "mtime_ns": info.st_mtime_ns, "sha256": None, "deleted": False}
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # Confirmed absence within this scope.
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                result.skipped.append({"path": key, "reason": "link or reparse point"})
            elif stat.S_ISREG(info.st_mode):
                info, digest = hash_file(path)
                result.entries[key] = {"path": key, "kind": "file", "size": info.st_size,
                                       "mtime_ns": info.st_mtime_ns, "sha256": digest, "deleted": False}
            elif stat.S_ISDIR(info.st_mode):
                result.entries[key] = {"path": key, "kind": "directory", "size": None,
                                       "mtime_ns": info.st_mtime_ns, "sha256": None, "deleted": False}
                subtree = scan_tree(path)
                for child, entry in subtree.entries.items():
                    entry["path"] = key + "/" + child
                    result.entries[entry["path"]] = entry
                for attribute in ("errors", "skipped"):
                    for item in getattr(subtree, attribute):
                        item["path"] = key if item["path"] == "." else key + "/" + item["path"]
                        getattr(result, attribute).append(item)
            else:
                result.skipped.append({"path": key, "reason": "unsupported object"})
        except OSError as exc:
            result.errors.append({"path": key, "message": str(exc)})
    return result
