"""Revision-based reconciliation, persistent client baseline and safe recovery copies."""
import hashlib
import errno
import json
import os
from pathlib import Path
import tempfile
import uuid
from contextlib import contextmanager

from .manifest import export_manifest
from .scanner import hash_file, scan_tree


def fingerprint(entry):
    if not entry or entry.get('deleted'):
        return None
    return (entry['kind'], entry.get('sha256') if entry['kind'] == 'file' else None)


def revision_manifest(store, manifest):
    """Persist revisions independently of scan generations, including tombstones."""
    connection = store.connection
    with connection:
        connection.execute('CREATE TABLE IF NOT EXISTS revisions (path TEXT PRIMARY KEY, fingerprint TEXT, revision INTEGER)')
        rows = {path: (value, revision) for path, value, revision in connection.execute('SELECT * FROM revisions')}
        counter = max((r[1] for r in rows.values()), default=0)
        for entry in manifest['entries']:
            value = json.dumps(fingerprint(entry))
            old = rows.get(entry['path'])
            if old is None or old[0] != value:
                counter += 1
                connection.execute('INSERT OR REPLACE INTO revisions VALUES (?, ?, ?)', (entry['path'], value, counter))
                revision = counter
            else:
                revision = old[1]
            entry['revision'] = revision
    manifest['sync_protocol'] = 1
    return manifest


def disk_entry(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    if path.is_dir():
        return {'kind': 'directory', 'deleted': False}
    info, digest = hash_file(path)
    return {'kind': 'file', 'sha256': digest, 'size': info.st_size, 'deleted': False}


def publish_new(source, target):
    if os.name == 'nt':
        os.rename(source, target)
    else:
        os.link(source, target)
        source.unlink()


def apply_change(root, key, desired, temporary, expected, recovery):
    from .network import safe_path
    target = safe_path(root, key)
    current = disk_entry(target)
    if fingerprint(current) != fingerprint(expected):
        raise ValueError('Local state changed during transfer')
    if fingerprint(current) == fingerprint(desired):
        return
    if desired and desired['kind'] == 'directory':
        if current:
            raise ValueError('File/directory replacement requires manual resolution')
        target.mkdir(parents=True, exist_ok=False)
        return
    if current and current['kind'] == 'directory':
        if desired:
            raise ValueError('Directory/file replacement requires manual resolution')
        target.rmdir()  # Never remove a non-empty directory recursively.
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if current:
        recovery.mkdir(parents=True, exist_ok=True)
        # Recovery must be on the same volume as the root for atomic rename.
        backup = recovery / (uuid.uuid4().hex + '.bak')
        export_manifest({'path': key, 'previous': current}, backup.with_suffix('.json'))
        os.rename(target, backup)
        try:
            if fingerprint(disk_entry(backup)) != fingerprint(expected):
                raise ValueError('File changed during replacement; preserved in recovery')
            if desired:
                publish_new(temporary, safe_path(root, key))
        except BaseException:
            if not target.exists():
                publish_new(backup, target)
            raise
    elif desired:
        publish_new(temporary, safe_path(root, key))


def fetch_manifest(client):
    from .network import BLOCK, MAX_MANIFEST, validate_manifest
    with client.stream('GET', '/v1/manifest') as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(BLOCK):
            data.extend(chunk)
            if len(data) > MAX_MANIFEST:
                raise ValueError('Manifest too large')
    manifest = json.loads(data)
    validate_manifest(manifest)
    if manifest.get('sync_protocol') != 1:
        raise ValueError('Server does not support revision synchronization')
    for entry in manifest['entries']:
        if type(entry.get('revision')) is not int or entry['revision'] <= 0:
            raise ValueError('Invalid server revision')
    if not isinstance(manifest.get('folder_id'), str):
        raise ValueError('Missing server folder identifier')
    return manifest


@contextmanager
def locked_sync_state(root, state):
    root, state = Path(root).resolve(strict=True), Path(state).resolve()
    if not root.is_dir():
        raise ValueError('Sync root must be a directory')
    if state == root or root in state.parents:
        raise ValueError('Sync state must be outside the synchronized folder')
    state.mkdir(parents=True, exist_ok=True)
    if state.stat().st_dev != root.stat().st_dev:
        raise ValueError('Sync state and root must be on the same volume for recovery renames')
    # Exclusive lock file prevents two local sync processes from racing.
    lock_path = state / 'sync.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        yield root, state
    finally:
        lock_path.unlink(missing_ok=True)


def synchronize(client, root, state, *, dry_run=False):
    if dry_run:
        root, state = Path(root).expanduser().resolve(strict=True), Path(state).expanduser().resolve()
        if not root.is_dir() or state == root or root in state.parents:
            raise ValueError('Root must be a directory and state must be outside it')
        if (state / 'sync.lock').exists():
            raise ValueError('Sync state is locked; stop the running sync first')
        return _synchronize(client, root, state, dry_run=True)
    with locked_sync_state(root, state) as (root, state):
        return _synchronize(client, root, state)


def resolve_conflict(client, root, state, key, choice):
    if choice not in {'local', 'remote'}:
        raise ValueError('Choose local or remote')
    with locked_sync_state(root, state) as (root, state):
        report = json.loads((state / 'last-sync.json').read_text(encoding='utf-8'))
        snapshot = report.get('conflict_versions', {}).get(key)
        if key not in report.get('conflicts', []) or snapshot is None:
            raise ValueError('No recorded conflict snapshot; run sync first')
        return _synchronize(client, root, state, resolution=(key, choice, snapshot))


def _synchronize(client, root, state, resolution=None, dry_run=False):
    from .network import BLOCK, safe_path
    from .policy import excluded, portability_errors
    remote_manifest = fetch_manifest(client)
    if not remote_manifest.get('writable'):
        raise ValueError('Server is read-only; start it with --allow-write')
    binding = {'root': str(root), 'server': str(client.base_url), 'folder_id': remote_manifest['folder_id']}
    baseline_file = state / 'baseline.json'
    saved = json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else {'binding': binding, 'entries': {}}
    if saved.get('binding') != binding:
        raise ValueError('Sync state belongs to another folder or server')
    baseline = saved['entries']
    remote = {e['path']: e for e in remote_manifest['entries']}
    patterns = remote_manifest.get('exclude_patterns', [])
    local_scan = scan_tree(root, patterns)
    if local_scan.errors or any(e['reason'] != 'excluded by rule' for e in local_scan.skipped) or portability_errors(list(local_scan.entries.values())):
        raise ValueError('Local scan is incomplete or has incompatible names')
    local = local_scan.entries
    combined = {e['path']: e for e in [*local.values(), *remote.values()] if not e.get('deleted')}
    name_conflicts = sorted({e['path'] for e in portability_errors(list(combined.values()))})
    report = {'uploaded': 0, 'downloaded': 0, 'deleted_local': 0, 'deleted_remote': 0, 'conflicts': name_conflicts}
    actions = []
    if resolution:
        key, choice, snapshot = resolution
        safe_path(root, key)
        left, right = local.get(key), remote.get(key)
        if (fingerprint(left) != fingerprint(snapshot.get('local')) or
                fingerprint(right) != fingerprint(snapshot.get('remote')) or
                (right or {}).get('revision', 0) != (snapshot.get('remote') or {}).get('revision', 0)):
            raise ValueError('Conflict changed since detection; run sync and review again')
        if excluded(key, patterns) or any(key == p or key.startswith(p + '/') for p in name_conflicts):
            raise ValueError('Excluded paths and name collisions require manual resolution')
        if any(e and not e.get('deleted') and e['kind'] != 'file' for e in (left, right)):
            raise ValueError('Directory conflicts require manual resolution')
        actions.append(('upload' if choice == 'local' else 'download', key, left, right))
    for key in sorted(set(local) | set(remote) | set(baseline)):
        if resolution:
            continue
        if excluded(key, patterns):
            continue
        if any(key == conflict or key.startswith(conflict + '/') for conflict in name_conflicts):
            continue
        left, right, base = local.get(key), remote.get(key), baseline.get(key)
        lf, rf, bf = fingerprint(left), fingerprint(right), fingerprint(base)
        if lf == rf:
            baseline[key] = right
        elif base is None:
            if lf is None and rf is not None and not (right or {}).get('deleted'):
                actions.append(('download', key, left, right))
            elif rf is None and lf is not None and right is None:
                actions.append(('upload', key, left, right))
            else:
                report['conflicts'].append(key)
        elif lf == bf:
            actions.append(('download', key, left, right))
        elif rf == bf:
            actions.append(('upload', key, left, right))
        else:
            report['conflicts'].append(key)
    # Parent creations first; deletions deepest-first. Type swaps are conflicts.
    def order(action):
        direction, key, left, right = action
        desired = left if direction == 'upload' else right
        return (1, -key.count('/'), key) if fingerprint(desired) is None else (0, key.count('/'), key)
    if dry_run:
        plan = []
        for direction, key, left, right in sorted(actions, key=order):
            desired = left if direction == 'upload' else right
            existing = right if direction == 'upload' else left
            if (fingerprint(desired) and fingerprint(existing) and desired['kind'] != existing['kind']) or any(
                    key.startswith(parent + '/') for parent in report['conflicts']):
                report['conflicts'].append(key)
                continue
            operation = direction if fingerprint(desired) else ('delete_remote' if direction == 'upload' else 'delete_local')
            plan.append({'path': key, 'operation': operation, 'kind': (desired or existing or {}).get('kind')})
        report['dry_run'] = True
        report['planned'] = plan
        return report
    for direction, key, left, right in sorted(actions, key=order):
        desired = left if direction == 'upload' else right
        existing = right if direction == 'upload' else left
        if fingerprint(desired) and fingerprint(existing) and desired['kind'] != existing['kind']:
            report['conflicts'].append(key)
            continue
        # Do not operate below a conflicting parent.
        if any(key.startswith(parent + '/') for parent in report['conflicts']):
            report['conflicts'].append(key)
            continue
        temporary = None
        try:
            if direction == 'upload':
                params = {'path': key, 'base_revision': (right or {}).get('revision', 0),
                          'kind': desired['kind'] if fingerprint(desired) else 'deleted',
                          'sha256': (desired or {}).get('sha256') or '', 'size': (desired or {}).get('size') or 0}
                if fingerprint(desired) and desired['kind'] == 'file':
                    source = safe_path(root, key)
                    # Server validates the streamed body against this snapshot hash.
                    with source.open('rb') as stream:
                        response = client.put('/v1/change', params=params, content=iter(lambda: stream.read(BLOCK), b''))
                else:
                    response = client.put('/v1/change', params=params, content=b'')
                if response.status_code == 409:
                    report['conflicts'].append(key)
                    continue
                response.raise_for_status()
                baseline[key] = response.json()['entry']
                report['uploaded' if fingerprint(desired) else 'deleted_remote'] += 1
            else:
                if fingerprint(desired) and desired['kind'] == 'file':
                    target = safe_path(root, key)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.lansync-', suffix='.tmp', delete=False) as stream:
                        temporary = Path(stream.name)
                        digest, size = hashlib.sha256(), 0
                        with client.stream('GET', '/v1/file', params={'path': key, 'sha256': desired['sha256']}) as response:
                            response.raise_for_status()
                            for chunk in response.iter_bytes(BLOCK):
                                size += len(chunk)
                                if size > desired['size']:
                                    raise ValueError('Download exceeds advertised size')
                                digest.update(chunk)
                                stream.write(chunk)
                        if size != desired['size'] or digest.hexdigest() != desired['sha256']:
                            raise ValueError('Download hash mismatch')
                        stream.flush()
                        os.fsync(stream.fileno())
                try:
                    apply_change(root, key, desired if fingerprint(desired) else None, temporary, left, state / 'recovery')
                except ValueError:
                    report['conflicts'].append(key)
                    continue
                except OSError as exc:
                    if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise
                    report['conflicts'].append(key)
                    continue
                baseline[key] = right
                report['downloaded' if fingerprint(desired) else 'deleted_local'] += 1
            export_manifest(saved, baseline_file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    export_manifest(saved, baseline_file)
    if resolution:
        previous = json.loads((state / 'last-sync.json').read_text(encoding='utf-8'))
        remaining = [p for p in previous.get('conflicts', []) if p != resolution[0]]
        report['conflicts'] = sorted(set(remaining + report['conflicts']))
    report['conflict_versions'] = {key: {'local': local.get(key), 'remote': remote.get(key)}
                                   for key in report['conflicts']}
    if resolution:
        for key in remaining:
            if key in previous.get('conflict_versions', {}):
                report['conflict_versions'][key] = previous['conflict_versions'][key]
    export_manifest(report, state / 'last-sync.json')
    return report
