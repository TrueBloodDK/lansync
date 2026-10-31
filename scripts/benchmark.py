"""Reproducible synthetic benchmark; never touches user data."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time
import tracemalloc

from lansync.manifest import export_manifest
from lansync.scanner import scan_paths, scan_tree
from lansync.storage import Store


def measure(operation):
    tracemalloc.start()
    start = time.perf_counter()
    try:
        manifest = operation()
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        if not manifest['complete']:
            raise RuntimeError('Benchmark produced an incomplete manifest')
        return {'seconds': elapsed, 'python_peak_bytes': peak}
    finally:
        tracemalloc.stop()


def run_case(parent, count, size):
    with tempfile.TemporaryDirectory(prefix='lansync-bench-', dir=parent) as temporary:
        base = Path(temporary)
        root = base / 'files'
        root.mkdir()
        payload = b'x' * size
        for number in range(count):
            directory = root / f'batch-{number // 100:04d}'
            directory.mkdir(exist_ok=True)
            (directory / f'file-{number:06d}.bin').write_bytes(payload)
        store = Store(base / 'index.sqlite3', root)
        try:
            def apply(result):
                manifest = store.update(result)
                export_manifest(manifest, base / 'manifest.json')
                return manifest
            full = measure(lambda: apply(scan_tree(root)))
            target = root / 'batch-0000' / 'file-000000.bin'
            target.write_bytes(b'y' * size)
            incremental = measure(lambda: apply(scan_paths(root, [target])))
            exported = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
            fresh = scan_tree(root)
            actual = {e['path']: e for e in exported['entries'] if not e['deleted']}
            # Directory timestamps can settle asynchronously on Windows.
            def content(entries):
                return {path: (e['kind'], e['size'], e['sha256']) for path, e in entries.items()}
            if fresh.errors or content(actual) != content(fresh.entries):
                raise RuntimeError('Incremental result differs from independent full scan')
            return {'files': count, 'file_bytes': size, 'full': full, 'one_file_update': incremental}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', type=int, nargs='+', default=[100, 1000, 10000])
    parser.add_argument('--file-bytes', type=int, default=4096)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min([args.file_bytes, args.repeats, *args.counts]) <= 0:
        parser.error('Counts, size and repeats must be positive')
    args.work_dir.mkdir(parents=True, exist_ok=True)
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'platform': platform.platform(), 'python': platform.python_version(),
              'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
              'watchdog': importlib.metadata.version('watchdog'),
              'method': 'Synthetic warm-cache files; scan + SQLite + JSON export; tracemalloc enabled. Memory is Python allocations, not process RSS. No event delivery/debounce latency measured.',
              'runs': [], 'summary': []}
    for count in args.counts:
        cases = []
        for repeat in range(args.repeats):
            case = run_case(args.work_dir, count, args.file_bytes)
            case['repeat'] = repeat + 1
            cases.append(case)
            report['runs'].append(case)
            print(f"{count} files, run {repeat + 1}: full={case['full']['seconds']:.3f}s update={case['one_file_update']['seconds']:.3f}s", flush=True)
        report['summary'].append({'files': count, **{
            phase: {metric: statistics.median(c[phase][metric] for c in cases)
                    for metric in ('seconds', 'python_peak_bytes')}
            for phase in ('full', 'one_file_update')}})
    export_manifest(report, args.output)


if __name__ == '__main__':
    main()
