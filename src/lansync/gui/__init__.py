"""Optional desktop client. Importing the CLI does not require Qt."""


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='LANSync desktop client')
    parser.add_argument('--config', help='Existing client project.toml')
    parser.add_argument('--start-sync', action='store_true', help='Start continuous sync for the explicit --config project')
    parser.add_argument('--minimized', action='store_true', help='Hide in tray when available')
    parser.add_argument('--smoke-test', metavar='NEW_DIRECTORY', help='Run isolated packaging check and exit (developer option)')
    args = parser.parse_args()
    if args.start_sync and not args.config:
        parser.error('--start-sync requires --config')
    try:
        from PySide6.QtWidgets import QApplication
        from .window import MainWindow
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith('PySide6'):
            parser.error('Install the GUI dependencies: python -m pip install "lansync[gui]"')
        raise
    app = QApplication([sys.argv[0]])
    app.setStyle('Fusion')
    app.setApplicationName('LANSync')
    app.setOrganizationName('LANSync')
    if args.smoke_test:
        from .smoke import run
        return run(app, args.smoke_test, args.config)
    window = MainWindow()
    opened = window.open_project(args.config) if args.config else False
    window.show()
    from PySide6.QtCore import QTimer
    def startup():
        if args.config and not opened:
            return
        if args.minimized:
            window.tray.hide_window()
        if args.start_sync and opened:
            window.start_action('start')
    window.controller.error.connect(lambda _: window.tray.show() if not window.isVisible() else None)
    QTimer.singleShot(0, startup)
    return app.exec()
