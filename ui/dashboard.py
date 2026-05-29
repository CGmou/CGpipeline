from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QGridLayout, QLabel, QPushButton, QFrame, QMenu
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from .task_card import TaskCard
from core.constants import DEFAULT_STATUS

class Dashboard(QWidget):
    modify_requested = Signal(str)
    start_work_requested = Signal(dict)
    continue_work_requested = Signal(dict)

    def __init__(self, registry):
        super().__init__()
        self.registry = registry
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 20, 30, 30)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        title = QLabel("MY TASKS")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # Refresh Button
        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setStyleSheet("""
            QPushButton { background-color: #333; color: #888; border: 1px solid #444; border-radius: 4px; padding: 10px 15px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background-color: #444; color: white; }
        """)
        self.refresh_btn.clicked.connect(lambda: self.refresh(is_admin=self.parent().parent().auth.is_admin()))
        header_layout.addWidget(self.refresh_btn)

        self.add_btn = QPushButton("+ NEW TASK")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #28A745; color: white; border: none; border-radius: 6px; padding: 10px 20px; font-weight: bold;
            }
            QPushButton:hover { background-color: #218838; }
        """)
        header_layout.addWidget(self.add_btn)

        main_layout.addLayout(header_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        self.sections_layout = QVBoxLayout(container)
        self.sections_layout.setSpacing(30)

        self.assets_section = QWidget()
        assets_layout = QVBoxLayout(self.assets_section)
        assets_title = QLabel("ASSETS")
        assets_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #888888; border-bottom: 1px solid #333;")
        assets_layout.addWidget(assets_title)
        self.assets_grid = QGridLayout()
        self.assets_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.assets_grid.setSpacing(20)
        assets_layout.addLayout(self.assets_grid)
        self.sections_layout.addWidget(self.assets_section)

        self.shots_section = QWidget()
        shots_layout = QVBoxLayout(self.shots_section)
        shots_title = QLabel("SHOTS")
        shots_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #888888; border-bottom: 1px solid #333;")
        shots_layout.addWidget(shots_title)
        self.shots_grid = QGridLayout()
        self.shots_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.shots_grid.setSpacing(20)
        shots_layout.addLayout(self.shots_grid)
        self.sections_layout.addWidget(self.shots_section)

        self.sections_layout.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def refresh(self, is_admin=False):
        # Reload tasks from disk
        self.registry.load()
        
        for grid in [self.assets_grid, self.shots_grid]:
            for i in reversed(range(grid.count())):
                widget = grid.itemAt(i).widget()
                if widget: widget.setParent(None)

        current_username = self.registry.current_user
        filtered_tasks = []
        for t in self.registry.tasks:
            if is_admin or t.get("assigned_to") == current_username:
                filtered_tasks.append(t)
        grouped = {}
        for task in filtered_tasks:
            name = task.get("name", "Unknown")
            if name not in grouped:
                grouped[name] = []
            grouped[name].append(task)

        asset_index = 0
        shot_index = 0
        columns = 4

        for name, tasks in grouped.items():
            main_task = tasks[0]
            display_data = main_task.copy()

            task_info = []
            for t in tasks:
                task_info.append({"type": t["type"], "status": t.get("status", DEFAULT_STATUS)})
            display_data["combined_tasks"] = task_info
            display_data["all_task_objs"] = tasks
            display_data["current_user"] = current_username

            card = TaskCard(display_data, is_admin=is_admin)
            card.clicked.connect(self.on_task_clicked)
            card.start_work_requested.connect(self.start_work_requested.emit)
            card.continue_work_requested.connect(self.continue_work_requested.emit)
            card.modify_requested.connect(self.modify_requested.emit)

            if main_task.get("category") == "Assets":
                self.assets_grid.addWidget(card, asset_index // columns, asset_index % columns)
                asset_index += 1
            else:
                self.shots_grid.addWidget(card, shot_index // columns, shot_index % columns)
                shot_index += 1

    def on_task_clicked(self, task_id):
        pass

