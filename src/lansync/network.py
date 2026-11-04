"""Read-only server and verified, non-overwriting one-way downloads."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import stat
import tempfile
import unicodedata
from threading import Lock
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .policy import excluded, portability_errors, validate_patterns
from .scanner import hash_file, scan_tree, scan_paths, signature
from .storage import Store

BLOCK = 1024 * 1024
MAX_MANIFEST = 16 * BLOCK


def read_token(path):
    token = Path(path).read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('Token must contain 32–256 ASCII letters, digits, underscores or hyphens')
    return token


def safe_path(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or any(p in {'', '.', '..'} for p in relative.split('/'))
            or portability_errors([{'path': relative, 'deleted': False}])):
        raise ValueError('Invalid or non-portable relative path')
    current = root
    for part in [None, *relative.split('/')]:
        if part is not None:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links and reparse points are not allowed')
    return current


def check_live_spelling(root, relative):
    """Detect new sibling collisions without hashing unrelated contents."""
    parent = root
    for component in relative.split('/'):
        normalized = unicodedata.normalize('NFC', component).casefold()
        try:
            with os.scandir(parent) as siblings:
                for sibling in siblings:
                    if sibling.name != component and unicodedata.normalize('NFC', sibling.name).casefold() == normalized:
                        raise ValueError('Live filesystem contains a case/Unicode name collision')
        except FileNotFoundError:
            return
        parent = parent / component


def create_app(root, database, token, exclude_patterns=(), allow_write=False):
    from .sync import apply_change, fingerprint, revision_manifest
    root = Path(root).resolve(strict=True)
    database = Path(database).resolve()
    if not root.is_dir() or root == database or root in database.parents:
        raise ValueError('Database must be outside the shared directory')
    database.parent.mkdir(parents=True, exist_ok=True)
    if allow_write and database.parent.stat().st_dev != root.stat().st_dev:
        raise ValueError('Writable server state and root must be on the same volume')
    patterns = validate_patterns(exclude_patterns)
    lock = Lock()
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    published = {}
    initialized = False

    def authorize(authorization: str | None = Header(default=None)):
        expected = ('Bearer ' + token).encode('utf-8')
        if not hmac.compare_digest((authorization or '').encode('utf-8'), expected):
            raise HTTPException(401, 'Unauthorized')

    def refresh(paths=None):
        nonlocal initialized
        if list((database.parent / 'recovery').glob('*.pending.json')):
            published.clear()
            raise HTTPException(409, 'Unfinished file replacement; inspect server recovery pending records')
        store = Store(database, root)
        try:
            result = store.update(scan_tree(root, patterns) if paths is None or not initialized
                                  else scan_paths(root, paths, patterns))
            if not result['complete']:
                published.clear()
                raise HTTPException(409, 'Source manifest is incomplete; run scan to inspect errors')
            result = revision_manifest(store, result)
            initialized = True
        finally:
            store.close()
        result['writable'] = allow_write
        published.clear()
        published.update({e['path']: e for e in result['entries'] if not e['deleted']})
        return result

    @app.get('/v1/manifest', dependencies=[Depends(authorize)])
    def manifest():
        with lock:
            return refresh()

    @app.put('/v1/change', dependencies=[Depends(authorize)])
    async def change(request: Request, path: str, base_revision: int, kind: str, sha256: str = '', size: int = 0):
        if not allow_write:
            raise HTTPException(403, 'Server is read-only')
        try:
            target = safe_path(root, path)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, 'Invalid path') from exc
        if excluded(path, patterns):
            raise HTTPException(403, 'Excluded path')
        if (base_revision < 0 or kind not in {'file', 'directory', 'deleted'} or size < 0
                or (kind == 'file' and not re.fullmatch('[0-9a-f]{64}', sha256))
                or (kind != 'file' and size != 0)):
            raise HTTPException(400, 'Invalid change metadata')
        database.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=database.parent, prefix='upload-', delete=False) as stream:
                temporary = Path(stream.name)
                digest, received = hashlib.sha256(), 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > size:
                        raise HTTPException(400, 'Upload exceeds advertised size')
                    digest.update(chunk)
                    stream.write(chunk)
                if received != size or (kind == 'file' and digest.hexdigest() != sha256):
                    raise HTTPException(400, 'Upload size/hash mismatch')
                stream.flush()
                os.fsync(stream.fileno())

            def commit():
                with lock:
                    try:
                        safe_path(root, path)
                        check_live_spelling(root, path)
                    except (OSError, ValueError) as exc:
                        raise HTTPException(409, 'Filesystem path changed or name collision') from exc
                    latest = refresh([target])
                    old = next((e for e in latest['entries'] if e['path'] == path), None)
                    if (old or {}).get('revision', 0) != base_revision:
                        raise HTTPException(409, 'Stale base revision')
                    desired = None if kind == 'deleted' else {'path': path, 'kind': kind,
                              'sha256': sha256 if kind == 'file' else None, 'size': size, 'deleted': False}
                    candidates = [e for e in latest['entries'] if e['path'] != path]
                    if desired:
                        candidates.append(desired)
                    if portability_errors(candidates):
                        raise HTTPException(409, 'Name collision')
                    try:
                        apply_change(root, path, desired, temporary, old, database.parent / 'recovery')
                    except (OSError, ValueError) as exc:
                        raise HTTPException(409, 'Filesystem changed or incompatible object; retry after inspection') from exc
                    updated = refresh([target])
                    entry = next((e for e in updated['entries'] if e['path'] == path), None)
                    if entry is None:
                        raise HTTPException(409, 'Unknown deletion')
                    return {'entry': entry}

            return await run_in_threadpool(commit)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @app.get('/v1/file', dependencies=[Depends(authorize)])
    def file(path: str, sha256: str):
        try:
            target = safe_path(root, path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with lock:
            entry = published.get(path)
            if (not entry or entry['kind'] != 'file' or excluded(path, patterns)):
                raise HTTPException(404, 'File is not in the published manifest')
            if sha256 != entry['sha256']:
                raise HTTPException(409, 'Manifest changed; fetch it again')
            expected_size = entry['size']
        # A disk snapshot ensures that a file changing during transmission
        # cannot silently yield content different from the advertised hash.
        snapshot = tempfile.TemporaryFile(dir=database.parent)
        try:
            before = target.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('Source is not a regular file')
            digest = hashlib.sha256()
            size = 0
            with target.open('rb') as source:
                if signature(before) != signature(os.fstat(source.fileno())):
                    raise ValueError('Source changed')
                for chunk in iter(lambda: source.read(BLOCK), b''):
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError('Source changed')
                    digest.update(chunk)
                    snapshot.write(chunk)
                if signature(before) != signature(os.fstat(source.fileno())):
                    raise ValueError('Source changed')
            if signature(before) != signature(target.lstat()) or size != expected_size or digest.hexdigest() != sha256:
                raise ValueError('Source changed')
            snapshot.seek(0)
        except (OSError, ValueError) as exc:
            snapshot.close()
            raise HTTPException(409, 'Source unavailable or changed; fetch manifest again') from exc

        def chunks():
            try:
                yield from iter(lambda: snapshot.read(BLOCK), b'')
            finally:
                snapshot.close()

        return StreamingResponse(chunks(), media_type='application/octet-stream',
                                 headers={'Content-Length': str(size)}, background=BackgroundTask(snapshot.close))

    return app


def validate_manifest(document):
    if not isinstance(document, dict) or document.get('schema_version') != 2 or document.get('complete') is not True:
        raise ValueError('Unsupported or incomplete manifest')
    entries = document.get('entries')
    if not isinstance(entries, list) or document.get('errors') or document.get('portability_errors'):
        raise ValueError('Invalid manifest')
    seen = {}
    for item in entries:
        if not isinstance(item, dict) or type(item.get('deleted')) is not bool or item.get('kind') not in {'file', 'directory'}:
            raise ValueError('Invalid manifest entry')
        path = item.get('path')
        # Syntax check only; filesystem checks happen separately.
        if not isinstance(path, str) or not path or '\\' in path or any(p in {'', '.', '..'} for p in path.split('/')):
            raise ValueError('Invalid manifest path')
        if path in seen:
            raise ValueError('Duplicate manifest path')
        seen[path] = item
        if item['kind'] == 'file' and not item['deleted']:
            if type(item.get('size')) is not int or item['size'] < 0 or not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
                raise ValueError('Invalid file size or hash')
    if portability_errors(entries):
        raise ValueError('Manifest contains incompatible names')
    for path, item in seen.items():
        if item['deleted']:
            continue
        for parent in Path(path).parents:
            key = parent.as_posix()
            if key in seen and (seen[key]['deleted'] or seen[key]['kind'] != 'directory'):
                raise ValueError('Invalid manifest hierarchy')
    return [e for e in entries if not e['deleted']]


def pull(client, root):
    """Import absent files only; different existing files are conflicts."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Destination must be an existing directory')
    with client.stream('GET', '/v1/manifest') as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(BLOCK):
            data.extend(chunk)
            if len(data) > MAX_MANIFEST:
                raise ValueError('Manifest exceeds 16 MiB limit')
    entries = validate_manifest(json.loads(data))
    # Detect conflicts before writing any files.
    for item in entries:
        target = safe_path(root, item['path'])
        if target.exists():
            if item['kind'] == 'directory':
                if not target.is_dir():
                    raise ValueError(f"Local conflict: {item['path']}")
            elif not target.is_file() or hash_file(target)[1] != item['sha256']:
                raise ValueError(f"Local conflict: {item['path']}")
    downloaded = unchanged = 0
    for item in sorted(entries, key=lambda e: (e['path'].count('/'), e['path'])):
        target = safe_path(root, item['path'])
        if item['kind'] == 'directory':
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            if not target.is_file() or hash_file(target)[1] != item['sha256']:
                raise ValueError(f"Local conflict: {item['path']}")
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.lansync-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                digest = hashlib.sha256()
                size = 0
                with client.stream('GET', '/v1/file', params={'path': item['path'], 'sha256': item['sha256']}) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(BLOCK):
                        size += len(chunk)
                        if size > item['size']:
                            raise ValueError('Received file exceeds advertised size')
                        digest.update(chunk)
                        output.write(chunk)
                if size != item['size'] or digest.hexdigest() != item['sha256']:
                    raise ValueError('Downloaded file failed size/hash verification')
                output.flush()
                os.fsync(output.fileno())
            safe_path(root, item['path'])
            # Atomic publish without replacement. Never overwrite a file
            # created by the user while the download was in progress.
            if os.name == 'nt':
                os.rename(temporary, target)  # Windows rename refuses an existing target.
            else:
                os.link(temporary, target)
            downloaded += 1
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {'downloaded': downloaded, 'unchanged': unchanged}


def make_client(url, token, ca_file=None, allow_http=False):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise ValueError('Use a server origin URL without credentials, path or query')
    if not parsed.hostname or parsed.scheme not in {'http', 'https'}:
        raise ValueError('Invalid server URL')
    if parsed.scheme != 'https' and not (allow_http and parsed.hostname in {'127.0.0.1', '::1'}):
        raise ValueError('HTTPS required; test HTTP is allowed only on literal loopback addresses')
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    return httpx.Client(base_url=url, headers={'Authorization': 'Bearer ' + token},
                        verify=context, timeout=60, follow_redirects=False, trust_env=False)
