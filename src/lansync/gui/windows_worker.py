"""Receive Windows session notifications in the operation-owning process."""


def run_windows_task(task):
    import ctypes
    from ctypes import wintypes
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSessionManager
    from PySide6.QtWidgets import QApplication, QWidget

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetProcessShutdownParameters.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.SetProcessShutdownParameters.restype = wintypes.BOOL
    # The GUI (default 0x280) gets a chance to drain this worker first.
    if not kernel.SetProcessShutdownParameters(0x100, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    # Explicit native platform: even a packaging test's offscreen environment
    # must not leave a Windows worker without real session notifications.
    app = QApplication(['LANSync worker', '-platform', 'windows'])
    app.setQuitOnLastWindowClosed(False)
    window = QWidget()
    window.setWindowTitle('LANSync worker session guard')
    window.winId()  # Hidden top-level HWND; never shown and no desktop controls.

    def drain():
        task.stop.set()
        task.wait()

    def commit(manager):
        manager.setRestartHint(QSessionManager.RestartNever)
        drain()

    app.commitDataRequest.connect(commit)
    app.aboutToQuit.connect(drain)
    task.finished.connect(app.quit)
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: None)
    timer.start()
    task.start()
    try:
        app.exec()
    finally:
        drain()
        timer.stop()
        window.close()
