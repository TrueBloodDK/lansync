from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QTableView, QHeaderView


OPERATIONS = {'upload': 'Отправить на сервер', 'download': 'Скачать с сервера',
              'delete_local': 'Удалить локально', 'delete_remote': 'Удалить на сервере',
              'conflict': 'Конфликт — нужен выбор версии'}


class PlanModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_report(self, report):
        self.beginResetModel()
        self.rows = [(item['path'], OPERATIONS.get(item['operation'], item['operation']), item.get('bytes', 0))
                     for item in report.get('planned', [])]
        self.rows.extend((path, OPERATIONS['conflict'], None) for path in report.get('conflicts', []))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            value = self.rows[index.row()][index.column()]
            return '—' if value is None else str(value)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return ('Путь', 'Действие', 'Объём, байт')[section]


class PlanView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.has_report = False
        self.note = QLabel('План ещё не рассчитан.')
        self.note.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск по пути или действию')
        self.model = PlanModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.note)
        layout.addWidget(self.search)
        layout.addWidget(self.table)

    def clear(self):
        self.has_report = False
        self.model.set_report({})
        self.search.clear()
        self.note.setText('План ещё не рассчитан.')

    def invalidate(self):
        if self.has_report:
            self.note.setText('Предыдущий план мог устареть. Рассчитайте его заново; синхронизация всегда выполняет новую сверку.')

    def set_report(self, report):
        self.has_report = True
        self.search.clear()
        self.model.set_report(report)
        self.note.setText(f"Действий: {len(report.get('planned', []))}; конфликтов: {len(report.get('conflicts', []))}. "
                          'Это снимок на момент проверки. Файлы не изменены; при синхронизации план рассчитывается заново.')
