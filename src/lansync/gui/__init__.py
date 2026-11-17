"""Optional desktop client. Importing the CLI does not require Qt."""


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='LANSync desktop client')
    parser.add_argument('--config', help='Existing client project.toml')
    parser.add_argument('--start-sync', action='store_true', help='Start continuous sync for the explicit --config project')
    parser.add_argument('--minimized', action='store_true', help='Hide in tray when available')
    parser.add_argument('--smoke-test', metavar='NEW_DIRECTORY', help='Run isolated packaging check and exit (developer option)')
    parser.add_argument('--smoke-sync', action='store_true', help='Perform one real sync in smoke mode (modifies the explicit test project/server)')
    args = parser.parse_args()
    if args.start_sync and not args.config:
        parser.error('--start-sync requires --config')
    if args.smoke_sync and not (args.smoke_test and args.config):
        parser.error('--smoke-sync requires --smoke-test and --config')
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
        return run(app, args.smoke_test, args.config, sync=args.smoke_sync)
    from .instance import DesktopInstance
    from PySide6.QtWidgets import QMessageBox
    try:
        instance = DesktopInstance()
    except (OSError, ValueError) as exc:
        QMessageBox.warning(None, 'Не удалось запустить LANSync', str(exc))
        return 1
    if not instance.is_primary:
        if instance.notify():
            return 0
        QMessageBox.warning(None, 'LANSync уже запущен',
                            'Не удалось связаться с работающим окном. Дождитесь его запуска или завершения. '
                            'Второй клиент не запущен.')
        return 1
    window = MainWindow()
    from .shutdown import Shutdown
    shutdown = Shutdown(app, window)
    def activate_existing():
        window.tray.show()
        window.append('Повторный запуск: показано уже работающее окно. Проект и режим синхронизации не изменены.')
    instance.activated.connect(activate_existing)
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
    try:
        return app.exec()
    finally:
        shutdown.restore()
        instance.close()
