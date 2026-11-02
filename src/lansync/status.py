"""Read-only inspection of persisted client reports."""
import json
import math
from pathlib import Path


def read_status(state):
    state = Path(state).expanduser().resolve(strict=True)
    if not state.is_dir():
        raise ValueError('State path must be a directory')
    result = {'state_dir': str(state), 'lock_present': (state / 'sync.lock').exists(),
              'loop': None, 'last_sync': None}
    for key, name in (('loop', 'loop-status.json'), ('last_sync', 'last-sync.json')):
        path = state / name
        try:
            with path.open(encoding='utf-8') as stream:
                data = json.load(stream)
        except FileNotFoundError:
            continue
        if not isinstance(data, dict):
            raise ValueError(f'{name}: expected a JSON object')
        if key == 'loop':
            if data.get('status') not in ('starting', 'retrying', 'conflicts', 'synced', 'stopped', 'failed'):
                raise ValueError(f'{name}: invalid status')
            for field in ('updated_utc', 'last_success_utc', 'error'):
                if data.get(field) is not None and not isinstance(data[field], str):
                    raise ValueError(f'{name}: invalid {field}')
            if 'failures' in data and (type(data['failures']) is not int or data['failures'] < 0):
                raise ValueError(f'{name}: invalid failures')
            delay = data.get('retry_in_seconds')
            if delay is not None and (type(delay) not in (int, float) or
                                      not math.isfinite(delay) or delay < 0):
                raise ValueError(f'{name}: invalid retry_in_seconds')
        if key == 'last_sync':
            if not isinstance(data.get('conflicts'), list) or not all(isinstance(p, str) for p in data['conflicts']):
                raise ValueError(f'{name}: invalid conflicts')
            for field in ('uploaded', 'downloaded', 'deleted_local', 'deleted_remote'):
                if type(data.get(field)) is not int or data[field] < 0:
                    raise ValueError(f'{name}: invalid {field}')
        result[key] = data
    return result


def show_status(state, *, as_json=False):
    result = read_status(state)
    if as_json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    print(f"State directory: {result['state_dir']}")
    print('Saved snapshot; this command does not check server connectivity or process liveness.')
    print('Lock file: ' + ('present (may be stale)' if result['lock_present'] else 'absent'))
    loop = result['loop']
    if loop:
        for label, key in (('Saved loop status', 'status'), ('Updated UTC', 'updated_utc'),
                           ('Last successful pass UTC', 'last_success_utc'),
                           ('Consecutive errors', 'failures'), ('Retry delay seconds', 'retry_in_seconds'),
                           ('Error', 'error')):
            if loop.get(key) is not None:
                print(f'{label}: {loop[key]}')
    report = result['last_sync']
    if report is not None:
        print('Last completed pass:')
        for key in ('uploaded', 'downloaded', 'deleted_local', 'deleted_remote'):
            print(f'  {key}: {report[key]}')
        print(f"Conflicts: {len(report['conflicts'])}")
        for path in report['conflicts']:
            print(f'  {path}')
    elif loop is None:
        print('No synchronization reports yet.')
    return 0
