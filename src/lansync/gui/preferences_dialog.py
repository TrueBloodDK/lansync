from pathlib import Path
from PySide6.QtWidgets import QDialog, QFormLayout, QDoubleSpinBox, QCheckBox, QLabel, QDialogButtonBox, QMessageBox
from .controller import client_project
from .preferences import save_preferences


class PreferencesDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = Path(config)
        self.original = self.config.read_bytes()
        args = client_project(config)
        self.backup = None
        self.setWindowTitle('Параметры синхронизации')
        self.resize(540, 340)
        form = QFormLayout(self)
        self.fields = {}
        for key, title in [('interval', 'Период сверки, с'), ('retry_max', 'Максимальное ожидание повтора, с'),
                           ('request_timeout', 'Тайм-аут сетевого ожидания, с'), ('event_debounce', 'Объединение событий файлов, с')]:
            field = QDoubleSpinBox()
            field.setDecimals(3)
            field.setRange(.001, max(86400, getattr(args, key)))
            field.setValue(getattr(args, key))
            self.fields[key] = field
            form.addRow(title, field)
        self.events = QCheckBox('Следить за локальными изменениями')
        self.events.setChecked(args.local_events)
        self.events.setEnabled(args.watch)
        form.addRow(self.events)
        note = QLabel('Сервер, папки и реквизиты здесь не меняются: для другого подключения создайте новый проект.\n'
                      'Файл TOML будет переформатирован; исходный текст с комментариями сохранится рядом в резервной копии.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        changes = {key: field.value() for key, field in self.fields.items()}
        changes['local_events'] = self.events.isChecked()
        try:
            self.backup = save_preferences(self.config, self.original, changes)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Настройки не сохранены', str(exc))
            return
        self.accept()
