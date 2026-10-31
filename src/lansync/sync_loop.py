"""Sequential continuous synchronization with bounded retries."""
from datetime import datetime, timezone
import math
from threading import Event

import httpx

from .manifest import export_manifest
from .sync import _synchronize, locked_sync_state


def retryable(error):
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 409, 429} or error.response.status_code >= 500
    if isinstance(error, httpx.TransportError):
        # Invalid certificates require configuration changes, not repeated requests.
        return 'certificate_verify_failed' not in str(error).lower() and 'certificate verify failed' not in str(error).lower()
    return False


def continuous_sync(client, root, state, *, interval=30.0, retry_max=300.0,
                    stop=None, on_event=None):
    for value in (interval, retry_max):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Sync intervals must be positive finite numbers')
    if retry_max < interval:
        raise ValueError('Maximum retry delay must not be smaller than the interval')
    stop = stop if stop is not None else Event()
    with locked_sync_state(root, state) as (root, state):
        status = {'status': 'starting', 'failures': 0, 'last_success_utc': None}
        previous_event = None
        delay = interval

        def publish(event):
            nonlocal previous_event
            status.update(event)
            status['updated_utc'] = datetime.now(timezone.utc).isoformat()
            export_manifest(status, state / 'loop-status.json')
            report = event.get('report') or {}
            key = (event['status'], event.get('error'), tuple(report.get('conflicts', [])))
            changed_files = any(report.get(field, 0) for field in
                                ('uploaded', 'downloaded', 'deleted_local', 'deleted_remote'))
            if on_event and (key != previous_event or changed_files):
                on_event(dict(status))
            previous_event = key

        publish({'status': 'starting'})
        try:
            while not stop.is_set():
                try:
                    report = _synchronize(client, root, state)
                except httpx.HTTPError as exc:
                    if not retryable(exc):
                        raise
                    status['failures'] += 1
                    # Saturating multiplication avoids overflow after a long outage.
                    delay = interval if status['failures'] == 1 else min(retry_max, delay * 2)
                    publish({'status': 'retrying', 'error': str(exc), 'retry_in_seconds': delay, 'report': None})
                else:
                    delay = interval
                    status['failures'] = 0
                    status['last_success_utc'] = datetime.now(timezone.utc).isoformat()
                    publish({'status': 'conflicts' if report['conflicts'] else 'synced',
                             'report': report, 'error': None, 'retry_in_seconds': None})
                stop.wait(delay)
        except KeyboardInterrupt:
            publish({'status': 'stopped', 'retry_in_seconds': None})
        except Exception as exc:
            publish({'status': 'failed', 'error': str(exc), 'retry_in_seconds': None})
            raise
        else:
            publish({'status': 'stopped', 'retry_in_seconds': None})
