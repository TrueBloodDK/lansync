from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox, QMessageBox
from .autostart import Registration, launch_command


class AutostartDialog(QDialog):
    def __init__(self, config, settings, parent=None):
        super().__init__(parent)
        self.config, self.settings = config, settings
        self.registration = Registration()
        self.expected = self.registration.read()
        stored = settings.value('autostart/entry', None)
        if self.expected is not None and self.expected != stored:
            raise ValueError('Запись LANSyncDesktop изменена вне этого приложения. Проверьте её в настройках автозапуска ОС; она не будет перезаписана.')
        self.setWindowTitle('Запуск при входе в систему')
        self.resize(560, 270)
        layout = QVBoxLayout(self)
        label = QLabel('Автозапуск относится к текущему пользователю и выбранному проекту:\n' + str(config))
        label.setWordWrap(True)
        layout.addWidget(label)
        self.enabled = QCheckBox('Открывать LANSync при входе в систему')
        self.enabled.setChecked(self.expected is not None)
        self.sync = QCheckBox('Сразу запускать постоянную синхронизацию')
        self.sync.setChecked(settings.value('autostart/sync', False, type=bool))
        self.minimized = QCheckBox('Скрывать окно в трей, если он доступен')
        self.minimized.setChecked(settings.value('autostart/minimized', False, type=bool))
        for widget in (self.enabled, self.sync, self.minimized):
            layout.addWidget(widget)
        note = QLabel('Если приложение или проект перемещены, настройте автозапуск заново. '
                      'Не запускайте этот же проект одновременно через CLI-службу. '
                      'Смена проекта в главном окне не меняет сохранённый автозапуск.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            value = self.registration.render(launch_command(self.config, self.sync.isChecked(),
                                                             self.minimized.isChecked())) if self.enabled.isChecked() else None
            self.registration.replace(self.expected, value)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Автозапуск не изменён', str(exc))
            return
        if value is None:
            self.settings.remove('autostart/entry')
        else:
            self.settings.setValue('autostart/entry', value)
        self.settings.setValue('autostart/sync', self.sync.isChecked())
        self.settings.setValue('autostart/minimized', self.minimized.isChecked())
        self.settings.sync()
        self.accept()
