"""PyInstaller entry point; also usable as a normal Python launcher."""
from lansync.gui import main

if __name__ == '__main__':
    import sys
    try:
        result = main()
    except Exception as exc:
        # Packaging checks must fail rather than wait on a windowed traceback dialog.
        if '--smoke-test' not in sys.argv:
            raise
        import json
        import traceback
        from pathlib import Path
        output = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'startup-error.json').open('x', encoding='utf-8') as stream:
            json.dump({'result': 'failed', 'startup_error': str(exc), 'traceback': traceback.format_exc()}, stream)
        raise SystemExit(1)
    raise SystemExit(result)
