"""One desktop client per user. This lock is unrelated to sync.lock."""
import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QLockFile, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class DesktopInstance(QObject):
    activated = Signal()

    def __init__(self, directory=None):
        super().__init__()
        directory = Path(directory) if directory else Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        directory.mkdir(parents=True, exist_ok=True)
        directory = directory.resolve()
        self.name = 'lansync-desktop-' + hashlib.sha256(str(directory).encode()).hexdigest()[:32]
        self.lock = QLockFile(str(directory / 'desktop-instance.lock'))
        self.lock.setStaleLockTime(0)  # A healthy GUI can run indefinitely.
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.is_primary = self.lock.tryLock(0)
        if self.is_primary:
            # Only the lock owner may clean up a socket left by a crashed GUI.
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                self.lock.unlock()
                self.is_primary = False
                raise OSError('Не удалось открыть локальный канал GUI: ' + self.server.errorString())
            self.server.newConnection.connect(self._activate)
        elif self.lock.error() != QLockFile.LockFailedError:
            raise OSError('Не удалось создать блокировку GUI. Проверьте права каталога приложения.')

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            self.activated.emit()

    def notify(self):
        # No project paths, tokens or commands are sent: activation only.
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        connected = socket.waitForConnected(1500)
        socket.abort()
        return connected

    def close(self):
        if self.is_primary:
            self.server.close()
            self.lock.unlock()
            self.is_primary = False
