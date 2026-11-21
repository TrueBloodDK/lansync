import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from lansync.gui.worker import run_worker
from lansync.project_init import initialize


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Optional GUI dependencies')
class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        # Hold a non-listening port, so no external service can receive data.
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.addCleanup(sock.close)
        self.config = initialize(self.base/'project', 'client',
                                 f'https://127.0.0.1:{sock.getsockname()[1]}')
        text = self.config.read_text().replace('https://', 'http://')
        self.config.write_text(text.replace('ca_file = "./credentials/ca.pem"\n', '') +
                               'allow_http = true\nrequest_timeout = 1\n')
        (self.config.parent/'credentials/token.txt').write_text('x'*40)
        self.state = self.config.parent/'state'

    def wait_for(self, predicate):
        end = time.monotonic() + 15
        while not predicate() and time.monotonic() < end:
            time.sleep(.03)
        self.assertTrue(predicate())

    def status(self):
        path = self.state/'loop-status.json'
        return json.loads(path.read_text())['status'] if path.exists() else None

    def test_pipe_eof_and_stop_release_lock_and_allow_restart(self):
        for mode in ('eof', 'stop'):
            with self.subTest(mode=mode):
                context = multiprocessing.get_context('spawn')
                parent, child = context.Pipe()
                worker = context.Process(target=run_worker, args=(child, str(self.config), 'start', {}))
                worker.start()
                child.close()
                try:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        self.assertTrue(parent.poll(10), 'Worker did not report status')
                        kind, value = parent.recv()
                        self.assertNotEqual(kind, 'error', value)
                        if kind == 'event' and value['status'] == 'retrying':
                            break
                    self.assertTrue((self.state/'sync.lock').exists())
                    if mode == 'stop':
                        parent.send('stop')
                        worker.join(10)
                    parent.close()
                    worker.join(10)
                    self.assertEqual(worker.exitcode, 0)
                    self.assertEqual(self.status(), 'stopped')
                    self.assertFalse((self.state/'sync.lock').exists())
                finally:
                    parent.close()
                    if worker.is_alive():
                        worker.kill()
                    worker.join(10)
                    worker.close()

    def test_abrupt_gui_death_leaves_worker_able_to_release_state(self):
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                   XDG_DATA_HOME=str(self.base/'data'), XDG_CONFIG_HOME=str(self.base/'settings'))
        # A private minimal GUI avoids touching the user's desktop-instance lock.
        code = ('from PySide6.QtWidgets import QApplication; '
                'from lansync.gui.controller import Controller; import sys; '
                'a=QApplication([]); c=Controller(); c.start(sys.argv[1], "start"); a.exec()')
        gui = subprocess.Popen([sys.executable, '-c', code, str(self.config)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for(lambda: self.status() == 'retrying')
            self.assertTrue((self.state/'sync.lock').exists())
            gui.kill()
            gui.wait(timeout=10)
            self.wait_for(lambda: not (self.state/'sync.lock').exists())
            self.assertEqual(self.status(), 'stopped')
        finally:
            if gui.poll() is None:
                gui.kill()
            gui.wait(timeout=10)

    @unittest.skipUnless(os.name == 'nt', 'Windows session messages')
    def test_windows_worker_drains_on_native_query_end_session(self):
        import ctypes
        from ctypes import wintypes
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        worker = context.Process(target=run_worker, args=(child, str(self.config), 'start', {}))
        worker.start()
        child.close()
        try:
            while True:
                self.assertTrue(parent.poll(15))
                kind, value = parent.recv()
                self.assertNotEqual(kind, 'error', value)
                if kind == 'event' and value['status'] == 'retrying':
                    break
            user = ctypes.WinDLL('user32', use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            handles = []
            @callback_type
            def visit(hwnd, _):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                text = ctypes.create_unicode_buffer(128)
                user.GetWindowTextW(hwnd, text, 128)
                if pid.value == worker.pid and text.value == 'LANSync worker session guard':
                    handles.append(hwnd)
                return True
            user.EnumWindows(visit, 0)
            self.assertEqual(len(handles), 1)
            user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                                 wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                                 ctypes.POINTER(ctypes.c_size_t)]
            user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
            result = ctypes.c_size_t()
            self.assertTrue(user.SendMessageTimeoutW(handles[0], 0x11, 0, 0x80000000, 2, 10000, ctypes.byref(result)))
            self.assertEqual(result.value, 1)
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(self.status(), 'stopped')
            self.assertFalse((self.state/'sync.lock').exists())
        finally:
            parent.close()
            if worker.is_alive():
                worker.kill()
            worker.join(10)
            worker.close()
