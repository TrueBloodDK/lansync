"""Connection diagnostics using the same transport and validation as sync."""
from time import monotonic
import httpx
from .network import make_client, read_token
from .sync import fetch_manifest


def inspect_server(client):
    manifest = fetch_manifest(client)
    live = [e for e in manifest['entries'] if not e['deleted']]
    files = [e for e in live if e['kind'] == 'file']
    return {'folder_id': manifest['folder_id'], 'sync_protocol': manifest['sync_protocol'],
            'writable': manifest.get('writable') is True, 'files': len(files),
            'directories': len(live) - len(files), 'bytes': sum(e['size'] for e in files),
            'tombstones': len(manifest['entries']) - len(live),
            'exclude_patterns': manifest.get('exclude_patterns', [])}


def diagnose(url, token_file, ca_file=None, allow_http=False):
    started = monotonic()
    report = {'ok': False, 'checks': [], 'server': None}
    stage = 'credentials'
    try:
        token = read_token(token_file)
        report['checks'].append({'check': stage, 'ok': True})
        stage = 'transport_configuration'
        with make_client(url, token, ca_file, allow_http) as client:
            report['checks'].append({'check': stage, 'ok': True})
            stage = 'authenticated_manifest'
            report['server'] = inspect_server(client)
            report['checks'].append({'check': stage, 'ok': True})
            report['ok'] = True
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        reason = {401: 'authorization_failed', 403: 'access_denied', 409: 'server_state_changed_or_incomplete'}.get(status, 'http_error')
        report['checks'].append({'check': stage, 'ok': False, 'reason': reason, 'http_status': status})
    except httpx.TimeoutException:
        report['checks'].append({'check': stage, 'ok': False, 'reason': 'timeout'})
    except httpx.TransportError as exc:
        message = str(exc).lower()
        reason = 'certificate_verification_failed' if 'certificate_verify_failed' in message or 'certificate verify failed' in message else 'connection_failed'
        report['checks'].append({'check': stage, 'ok': False, 'reason': reason})
    except (OSError, ValueError, httpx.HTTPError):
        report['checks'].append({'check': stage, 'ok': False, 'reason': 'invalid_manifest' if stage == 'authenticated_manifest' else 'invalid_configuration_or_unreadable_file'})
    report['duration_seconds'] = round(monotonic() - started, 6)
    return report
