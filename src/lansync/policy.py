"""Explicit exclusion rules and conservative cross-platform name checks."""

from fnmatch import fnmatchcase
import unicodedata


def excluded(path, patterns):
    parts = path.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(any(fnmatchcase(value, pattern) for value in
                   (prefixes if "/" in pattern else parts)) for pattern in patterns)


def validate_patterns(patterns):
    patterns = tuple(patterns)
    for pattern in patterns:
        if (not pattern or pattern.startswith(("/", "!")) or "\\" in pattern
                or any(part in {"", ".", ".."} for part in pattern.split("/"))):
            raise ValueError("Exclude patterns must be relative, use '/', and cannot contain empty, '.' or '..' segments or start with '!'")
    return patterns


def portability_errors(entries):
    """Check active paths, including implicit parent names, on every OS."""
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³")
    errors = []
    groups = {}
    paths = set()
    for entry in entries:
        if entry["deleted"]:
            continue
        parts = entry["path"].split("/")
        paths.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    for path in sorted(paths):
        name = path.rsplit("/", 1)[-1]
        reasons = []
        if not name or name in {".", ".."}:
            reasons.append("invalid path component")
        if any(char in '<>:"\\|?*' or ord(char) < 32 for char in name):
            reasons.append("Windows reserved character")
        if name.endswith((" ", ".")):
            reasons.append("trailing space or dot")
        if name.split(".", 1)[0].rstrip(" ").upper() in reserved:
            reasons.append("Windows reserved device name")
        try:
            if len(name.encode("utf-16-le")) // 2 > 255 or len(name.encode("utf-8")) > 255:
                reasons.append("component exceeds portable length limit")
        except UnicodeEncodeError:
            reasons.append("name is not valid Unicode")
        for reason in reasons:
            errors.append({"path": path, "message": reason})
        normalized = unicodedata.normalize("NFC", path).casefold()
        groups.setdefault(normalized, []).append(path)
    for group in groups.values():
        if len(group) > 1:
            for path in group:
                errors.append({"path": path, "message": "case or Unicode normalization collision", "conflicts_with": [other for other in group if other != path]})
    return errors
