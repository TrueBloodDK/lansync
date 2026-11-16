"""Optional desktop client. Importing the CLI does not require Qt."""


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='LANSync desktop client')
    parser.add_argument('--config', help='Existing client project.toml')
    parser.add_argument('--smoke-test', metavar='NEW_DIRECTORY', help='Run isolated packaging check and exit (developer option)')
    args = parser.parse_args()
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
    if args.config:
        window.open_project(args.config)
    window.show()
    return app.exec()
