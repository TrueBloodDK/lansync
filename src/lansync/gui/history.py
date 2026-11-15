"""Read-only record tables; actions are routed through the single worker."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView)


def version_text(entry):
    if not entry or entry.get('deleted'):
        return 'Удалён / отсутствует'
    kind = {'file': 'Файл', 'directory': 'Каталог'}.get(entry.get('kind'), entry.get('kind', '—'))
    return f"{kind}, {entry.get('size', 0)} байт, SHA-256 {entry.get('sha256', '—')}"


class RecordView(QWidget):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.entries = []
        self.available = False
        layout = QVBoxLayout(self)
        self.note = QLabel('Нажмите «Обновить», чтобы прочитать сохранённые данные.')
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        layout.addWidget(self.note)
        self.buttons = QHBoxLayout()
        layout.addLayout(self.buttons)
        self.refresh = QPushButton('Обновить')
        self.buttons.addWidget(self.refresh)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.update_controls)
        layout.addWidget(self.table)

    def selected(self):
        row = self.table.currentRow()
        return self.entries[row] if self.table.selectedItems() and 0 <= row < len(self.entries) else None

    def fill(self, entries, rows):
        self.table.clearSelection()
        self.entries = entries
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        self.update_controls()

    def clear(self):
        self.fill([], [])
        self.note.setText('Нажмите «Обновить», чтобы прочитать сохранённые данные.')

    def set_available(self, available):
        self.available = available
        self.update_controls()

    def update_controls(self):
        self.refresh.setEnabled(self.available)


class ConflictView(RecordView):
    resolve_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(['Путь', 'Локальная версия', 'Серверная версия'], parent)
        self.choices = {}
        for choice, title in [('local', 'Выбрать локальную'), ('remote', 'Выбрать серверную'),
                              ('both', 'Сохранить обе')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, c=choice: self.resolve_requested.emit(c))
            self.buttons.addWidget(button)
            self.choices[choice] = button
        self.update_controls()

    def set_report(self, report):
        versions = report.get('conflict_versions', {})
        entries = [{'path': path, 'snapshot': versions.get(path)} for path in report.get('conflicts', [])]
        self.fill(entries, [(e['path'], version_text((e['snapshot'] or {}).get('local')) if e['snapshot'] else 'Нет снимка',
                             version_text((e['snapshot'] or {}).get('remote')) if e['snapshot'] else 'Нет снимка') for e in entries])
        self.note.setText(f'Сохранённых конфликтов: {len(entries)}. Это снимок последнего прохода; '
                          'перед выбором ядро проверяет, не изменились ли версии. Конфликты имён и каталогов могут требовать ручного решения.')

    def update_controls(self):
        super().update_controls()
        entry = self.selected()
        snapshot = (entry or {}).get('snapshot')
        for choice, button in getattr(self, 'choices', {}).items():
            enabled = self.available and bool(snapshot)
            if choice == 'both' and snapshot:
                enabled = enabled and all(snapshot.get(side) and snapshot[side].get('kind') == 'file'
                                          and not snapshot[side].get('deleted') for side in ('local', 'remote'))
            button.setEnabled(enabled)


class RecoveryView(RecordView):
    def __init__(self, parent=None):
        super().__init__(['Путь', 'Размер, байт', 'Состояние', 'Идентификатор', 'Подробности'], parent)
        self.verify = QPushButton('Проверить целостность')
        self.export = QPushButton('Экспортировать копию…')
        self.buttons.addWidget(self.verify)
        self.buttons.addWidget(self.export)
        self.update_controls()

    def set_report(self, report):
        labels = {'verified': 'Проверена', 'missing': 'Нет файла', 'corrupt': 'Повреждена',
                  'orphan': 'Нет метаданных', 'error': 'Ошибка'}
        entries = report['entries']
        self.fill(entries, [(e.get('path', '—'), e.get('size', '—'),
                             labels.get(e.get('status'), 'Ошибка' if e.get('error') else
                                        'Не проверена' if e.get('available') else 'Нет файла'),
                             e['id'], e.get('error', '')) for e in entries])
        self.note.setText((f"Всего: {report['total']}; проверено: {report['verified']}; проблем: {report['problems']}. "
                           if 'total' in report else f'Копий в списке: {len(entries)}. Хеши ещё не проверены. ') +
                          'Экспорт создаёт новый файл вне папки синхронизации; оригиналы и recovery сохраняются.')

    def update_controls(self):
        super().update_controls()
        if hasattr(self, 'verify'):
            self.verify.setEnabled(self.available)
            entry = self.selected() or {}
            self.export.setEnabled(self.available and not entry.get('error') and
                                   (entry.get('available') is True or entry.get('status') == 'verified'))
