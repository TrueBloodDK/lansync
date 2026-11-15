"""First desktop workflow: open, diagnose, preview, synchronize and stop."""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFileDialog, QMessageBox,
                              QPlainTextEdit, QProgressBar, QGroupBox, QFormLayout, QTabWidget, QDialog)

from .controller import Controller, client_project
from .setup_dialog import SetupDialog
from .projects import import_bundle
from .plan import PlanView

STATES = {'starting': 'Подключение…', 'synced': 'Синхронизировано',
          'retrying': 'Повтор подключения…', 'conflicts': 'Обнаружены конфликты',
          'stopped': 'Остановлено', 'failed': 'Ошибка'}


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('LANSync', 'Desktop')
        self.controller = Controller(self)
        self.config = None
        self.stopping = self.closing = self.failed = False
        self.action = None
        self.setWindowTitle('LANSync — синхронизация файлов')
        self.resize(920, 650)
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel('LANSync')
        title.setStyleSheet('font-size: 26px; font-weight: 600;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Синхронизация с Linux-сервером в локальной сети'))
        project_box = QGroupBox('Клиентский проект')
        form = QFormLayout(project_box)
        self.project_label = QLabel('Откройте существующий project.toml')
        self.server_label = QLabel('—')
        self.folder_label = QLabel('—')
        for label in (self.project_label, self.server_label, self.folder_label):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
        form.addRow('Конфигурация:', self.project_label)
        form.addRow('Сервер:', self.server_label)
        form.addRow('Локальная папка:', self.folder_label)
        self.open_button = QPushButton('Открыть проект…')
        self.open_button.clicked.connect(self.choose_project)
        self.new_button = QPushButton('Новый проект…')
        self.new_button.clicked.connect(self.new_project)
        self.import_button = QPushButton('Импорт реквизитов…')
        self.import_button.clicked.connect(self.import_credentials)
        project_buttons = QHBoxLayout()
        for button in (self.new_button, self.open_button, self.import_button):
            project_buttons.addWidget(button)
        form.addRow(project_buttons)
        layout.addWidget(project_box)
        buttons = QHBoxLayout()
        self.actions = {}
        for action, text in [('doctor', 'Проверить соединение'), ('plan', 'План изменений'),
                             ('once', 'Синхронизировать'), ('start', 'Запустить постоянно')]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, a=action: self.start_action(a))
            self.actions[action] = button
            buttons.addWidget(button)
        self.stop_button = QPushButton('Остановить')
        self.stop_button.clicked.connect(self.request_stop)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.state_label = QLabel('Не запущено')
        self.state_label.setStyleSheet('font-size: 18px; font-weight: 600;')
        self.last_label = QLabel('Успешных проходов в этой сессии ещё нет')
        self.transfer_label = QLabel('Ожидание операции')
        self.transfer_label.setTextFormat(Qt.PlainText)
        self.transfer_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.state_label)
        layout.addWidget(self.last_label)
        layout.addWidget(self.transfer_label)
        layout.addWidget(self.progress_bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setPlaceholderText('Здесь появятся результаты проверок и операций.')
        self.tabs = QTabWidget()
        self.plan_view = PlanView()
        self.tabs.addTab(self.log, 'Журнал')
        self.tabs.addTab(self.plan_view, 'План изменений')
        layout.addWidget(self.tabs, 1)
        self.controller.busy_changed.connect(self.update_controls)
        self.controller.event.connect(self.on_event)
        self.controller.progress.connect(self.on_progress)
        self.controller.result.connect(self.on_result)
        self.controller.error.connect(self.on_error)
        self.controller.finished.connect(self.on_finished)
        self.update_controls()
        last = self.settings.value('last_project', '')
        if last and Path(last).is_file():
            self.open_project(last)

    def append(self, text):
        self.log.appendPlainText(f'{datetime.now():%H:%M:%S}  {text}')

    def choose_project(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть клиентский проект', '', 'TOML (*.toml)')
        if path:
            self.open_project(path)

    def new_project(self):
        if self.controller.busy:
            return
        dialog = SetupDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.open_project(dialog.config)
        dialog.deleteLater()

    def import_credentials(self):
        if self.controller.busy or not self.config:
            return
        source = QFileDialog.getExistingDirectory(self, 'Комплект клиента: ca.pem и token.txt')
        if not source:
            return
        try:
            import_bundle(self.config, source)
        except (OSError, ValueError) as exc:
            self.on_error(str(exc))
            return
        self.append('Клиентские реквизиты импортированы. Проверьте соединение перед запуском.')
        self.plan_view.invalidate()

    def open_project(self, path):
        if self.controller.busy:
            return False
        try:
            config = Path(path).expanduser().resolve(strict=True)
            args = client_project(config)
        except (OSError, ValueError) as exc:
            self.on_error(str(exc))
            return False
        self.config = config
        self.project_label.setText(str(config))
        self.server_label.setText(args.url)
        self.folder_label.setText(str(args.root))
        self.settings.setValue('last_project', str(config))
        self.failed = False
        self.state_label.setText('Не запущено')
        self.last_label.setText('Успешных проходов в этой сессии ещё нет')
        self.transfer_label.setText('Ожидание операции')
        self.progress_bar.setValue(0)
        self.log.clear()
        self.plan_view.clear()
        self.append('Проект открыт. Синхронизация автоматически не запускается.')
        self.update_controls()
        return True

    def update_controls(self, *_):
        busy = self.controller.busy
        self.open_button.setEnabled(not busy)
        self.new_button.setEnabled(not busy)
        self.import_button.setEnabled(self.config is not None and not busy)
        for button in self.actions.values():
            button.setEnabled(self.config is not None and not busy)
        self.stop_button.setEnabled(busy and not self.stopping)

    def start_action(self, action):
        if not self.config or self.controller.busy:
            return
        self.action, self.failed, self.stopping = action, False, False
        if action in {'start', 'once', 'plan'}:
            self.plan_view.invalidate()
        self.state_label.setText('Выполняется…')
        self.transfer_label.setText('Подготовка операции')
        self.progress_bar.setRange(0, 0)
        self.controller.start(self.config, action)

    def request_stop(self):
        self.stopping = True
        self.controller.stop()
        self.state_label.setText('Остановка: ожидание текущей операции…')
        self.update_controls()

    def on_event(self, event):
        if not self.stopping:
            self.state_label.setText(STATES.get(event['status'], event['status']))
        if event.get('last_success_utc'):
            self.last_label.setText('Последний успешный проход (UTC): ' + event['last_success_utc'])
        if event.get('error'):
            self.append(event['error'])
        report = event.get('report')
        if report:
            self.show_report(report)

    def show_report(self, report):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        summary = (f"Отправлено: {report.get('uploaded', 0)}, получено: {report.get('downloaded', 0)}; "
                   f"удалено локально: {report.get('deleted_local', 0)}, на сервере: {report.get('deleted_remote', 0)}; "
                   f"конфликтов: {len(report.get('conflicts', []))}")
        self.transfer_label.setText(summary)
        if any(report.get(k) for k in ('uploaded', 'downloaded', 'deleted_local', 'deleted_remote', 'conflicts')):
            self.append(summary)
        if report.get('conflicts'):
            self.append('Конфликты: ' + ', '.join(report['conflicts']))

    def on_progress(self, event):
        if not self.stopping:
            self.state_label.setText('Передача файла…')
        self.progress_bar.setRange(0, 100)
        total = event.get('total', 0)
        self.progress_bar.setValue(min(100, int(event['bytes'] * 100 / total)) if total else 0)
        self.transfer_label.setText(f"{event['path']} — {event['bytes']} / {total} байт")

    def on_result(self, result):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        if self.action == 'doctor':
            self.failed = not result['ok']
            self.state_label.setText('Соединение проверено' if result['ok'] else 'Проверка не пройдена')
            self.append(json.dumps(result, ensure_ascii=False, indent=2))
            self.transfer_label.setText('Проверка подключения завершена; файлы не передавались.')
        elif self.action == 'plan':
            self.state_label.setText('План готов — файлы не изменены')
            self.transfer_label.setText('Предварительная сверка завершена; файлы не изменены.')
            self.plan_view.set_report(result)
            self.tabs.setCurrentWidget(self.plan_view)
            self.append(f"План: {len(result.get('planned', []))} действий, {len(result.get('conflicts', []))} конфликтов.")
        else:
            self.state_label.setText('Обнаружены конфликты' if result['conflicts'] else 'Проход завершён')
            self.last_label.setText('Последний завершённый проход: ' + datetime.now().isoformat(timespec='seconds'))
            self.show_report(result)

    def on_error(self, message):
        self.failed = True
        self.state_label.setText('Ошибка')
        self.append(message)
        self.tabs.setCurrentWidget(self.log)

    def on_finished(self):
        self.progress_bar.setRange(0, 100)
        if self.stopping and not self.failed:
            self.state_label.setText('Остановлено')
        self.stopping = False
        self.update_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.controller.busy:
            event.ignore()
            if not self.closing and QMessageBox.question(
                    self, 'Завершение работы',
                    'Остановить работу и закрыть окно после завершения текущей операции?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self.closing = True
                self.request_stop()
            return
        self.settings.sync()
        event.accept()
