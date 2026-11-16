from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QStyle


class Tray:
    """Explicit minimize-to-tray; ordinary window close continues to mean exit."""
    def __init__(self, window):
        self.window = window
        self.icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.icon = QSystemTrayIcon(window.style().standardIcon(QStyle.SP_DriveNetIcon), window)
        self.icon.setToolTip('LANSync')
        self.menu = QMenu(window)
        self.menu.addAction('Открыть LANSync', self.show)
        self.stop_action = self.menu.addAction('Остановить синхронизацию', window.request_stop)
        self.menu.addSeparator()
        self.menu.addAction('Завершить LANSync', self.exit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.activated)
        self.icon.show()

    @property
    def available(self):
        return self.icon is not None and QSystemTrayIcon.isSystemTrayAvailable()

    def show(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show()

    def hide_window(self):
        if self.available:
            self.window.hide()
            return True
        return False

    def exit(self):
        self.show()  # A confirmation, if needed, must remain visible.
        self.window.close()

    def update(self, busy, stopping, status):
        if self.icon:
            self.stop_action.setEnabled(busy and not stopping)
            self.icon.setToolTip('LANSync — ' + status)
        if self.icon and not self.available and not self.window.isVisible():
            self.show()

    def close(self):
        if self.icon:
            self.icon.hide()
