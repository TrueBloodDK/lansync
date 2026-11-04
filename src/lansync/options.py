import math
from .policy import validate_patterns


def validate_options(configured):
    if configured.command == 'sync':
        if configured.dry_run and configured.watch:
            raise ValueError('--dry-run cannot be combined with --watch')
        if configured.json and not configured.dry_run:
            raise ValueError('--json requires --dry-run')
        if any(not math.isfinite(v) or v <= 0 for v in (configured.interval, configured.retry_max)) or configured.retry_max < configured.interval:
            raise ValueError('Invalid synchronization intervals')
    if configured.command == 'serve':
        if not 1 <= configured.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        if bool(configured.cert_file) != bool(configured.key_file):
            raise ValueError('Both cert_file and key_file are required')
    if configured.command in {'scan', 'watch', 'serve'}:
        validate_patterns(configured.exclude)
    if configured.command == 'watch':
        if any(not math.isfinite(v) or v <= 0 for v in (configured.interval, configured.debounce, configured.max_delay)):
            raise ValueError('Watch intervals must be positive finite numbers')
