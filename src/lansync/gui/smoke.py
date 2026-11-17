"""Bounded, isolated smoke mode for testing the distributed application."""
import json
from pathlib import Path
import platform
import sys

from PySide6.QtCore import QSettings, QTimer
from .window import MainWindow
from .. import __version__
from ..project_init import initialize
from ..network import make_client


def run(app, output, config=None, sync=False):
    output = Path(output).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    # Exercise bundled TLS defaults even for offline smoke runs.
    with make_client('https://localhost:8765', 'x' * 40):
        pass
    supplied = config is not None
    config = config or initialize(output / 'project', 'client', 'https://localhost:8765')
    window = MainWindow(QSettings(str(output / 'ui.ini'), QSettings.IniFormat))
    errors = []
    window.controller.error.connect(errors.append)
    report = {'result': 'failed', 'version': __version__, 'frozen': bool(getattr(sys, 'frozen', False)),
              'platform': platform.platform(), 'mode': 'https-sync' if sync else 'https-doctor' if supplied else 'offline-recovery'}
    def record_result(result):
        if sync:
            report['transfer'] = {key: result.get(key) for key in
                                  ('uploaded', 'downloaded', 'deleted_local', 'deleted_remote', 'conflicts')}
    window.controller.result.connect(record_result)
    def finish():
        report['errors'] = errors
        report['result'] = 'passed' if not errors and not window.failed else 'failed'
        (output / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        window.grab().save(str(output / 'window.png'))
        window.close()
        app.exit(0 if report['result'] == 'passed' else 1)
    if not window.open_project(config):
        errors.append('Cannot open client project')
        QTimer.singleShot(0, finish)
    else:
        window.controller.finished.connect(finish)
        QTimer.singleShot(0, lambda: window.start_action('once' if sync else 'doctor' if supplied else 'recovery-verify'))
    window.show()
    return app.exec()
