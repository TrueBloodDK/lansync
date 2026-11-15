from pathlib import Path

from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QPushButton,
                              QDialogButtonBox, QLabel, QFileDialog, QHBoxLayout, QMessageBox)

from .projects import create_client


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Новый клиентский проект')
        self.resize(650, 330)
        self.config = None
        form = QFormLayout(self)
        description = QLabel('Проект хранит настройки, состояние и резервные копии.\n'
                             'Синхронизация начнётся только после нажатия кнопки запуска.')
        description.setWordWrap(True)
        form.addRow(description)
        self.directory = QLineEdit()
        self.url = QLineEdit('https://192.168.0.108:8765')
        self.bundle = QLineEdit()
        self.root = QLineEdit()
        self.directory.setPlaceholderText('Новая папка проекта')
        self.root.setPlaceholderText('Необязательно: по умолчанию создаётся shared внутри проекта')
        form.addRow('Папка проекта:', self.path_row(self.directory, 'project'))
        form.addRow('Адрес сервера:', self.url)
        form.addRow('Комплект реквизитов:', self.path_row(self.bundle, 'bundle'))
        form.addRow('Папка файлов:', self.path_row(self.root, 'root'))
        note = QLabel('Выберите полученную от сервера папку с ca.pem и token.txt.\n'
                      'Приватный ключ сервера не копируется. Существующие файлы не заменяются.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Создать проект')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.create)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def path_row(self, field, kind):
        row = QHBoxLayout()
        row.addWidget(field)
        button = QPushButton('Обзор…')
        def browse():
            if kind == 'project':
                parent = QFileDialog.getExistingDirectory(self, 'Родительская папка нового проекта')
                if parent:
                    field.setText(str(Path(parent) / 'LANSync-client'))
            else:
                path = QFileDialog.getExistingDirectory(self, 'Выберите папку')
                if path:
                    field.setText(path)
        button.clicked.connect(browse)
        row.addWidget(button)
        return row

    def create(self):
        if not self.directory.text().strip() or not self.bundle.text().strip():
            QMessageBox.warning(self, 'Не хватает данных', 'Укажите папку проекта и комплект реквизитов.')
            return
        try:
            self.config = create_client(self.directory.text().strip(), self.url.text().strip(),
                                        self.bundle.text().strip(), self.root.text().strip() or None)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Проект не создан', str(exc))
            return
        self.accept()
