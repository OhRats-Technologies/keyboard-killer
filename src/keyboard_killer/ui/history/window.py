"""History surface: recent actions with copy and clear."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.ui.history.db import HistoryStore


class HistoryPanel(QWidget):
    def __init__(self, store: HistoryStore | None = None) -> None:
        super().__init__()
        self.store = store or HistoryStore()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        title = QLabel("History")
        title.setObjectName("Heading")
        header.addWidget(title)
        header.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        copy = QPushButton("Copy selected")
        copy.clicked.connect(self.copy_selected)
        header.addWidget(copy)
        clear = QPushButton("Clear")
        clear.setObjectName("Destructive")
        clear.clicked.connect(self.clear)
        header.addWidget(clear)
        layout.addLayout(header)

        hint = QLabel("Recent actions stay on this device.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Heard", "Action", "Target", "Result", "Latency"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.empty = QLabel("No actions yet.")
        self.empty.setObjectName("Muted")
        layout.addWidget(self.empty)
        self.refresh()

    def refresh(self) -> None:
        rows = self.store.recent(200)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                str(row["created_at"])[:19].replace("T", " "),
                str(row["transcript"]),
                str(row["route"]),
                str(row["target"]),
                str(row["result"]),
                f"{float(row['latency_ms']):.0f} ms" if row["latency_ms"] else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not row["success"]:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row_index, column, item)
        self.empty.setVisible(not rows)

    def copy_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        values = [
            self.table.item(row, column).text() if self.table.item(row, column) else ""
            for column in range(self.table.columnCount())
        ]
        QGuiApplication.clipboard().setText(" | ".join(values))

    def clear(self) -> None:
        self.store.clear()
        self.refresh()
