from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QFrame, QHBoxLayout, QMenu
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QColor, QAction
import os
from core.utils import get_latest_version, build_work_filename

class TaskCard(QFrame):
    clicked = Signal(str)
    start_work_requested = Signal(dict)
    continue_work_requested = Signal(dict)
    modify_requested = Signal(str)

    def __init__(self, task_data, is_admin=False):
        super().__init__()
        self.task_data = task_data
        self.is_admin = is_admin
        self.setup_ui()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def setup_ui(self):
        self.setFixedWidth(250)
        self.setMinimumHeight(350)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("TaskCard")

        # Color Logic
        border_color = "#3D3D3D"
        bg_color = "#2D2D2D"
        is_critical = any(t.get("priority") in ["High", "Critical"] for t in self.task_data.get("all_task_objs", []))
        is_done = all(t.get("status") == "Approved" for t in self.task_data.get("all_task_objs", []))
        is_pending = any(t.get("status") == "Pending Review" for t in self.task_data.get("all_task_objs", []))

        if is_critical:
            border_color = "#A72828"; bg_color = "#3A1A1A"
        elif is_done:
            border_color = "#28A745"; bg_color = "#1A3A1A"
        elif is_pending:
            border_color = "#0078D4"; bg_color = "#1A2A3A"

        self.setStyleSheet(f"""
            #TaskCard {{ background-color: {bg_color}; border-radius: 12px; border: 2px solid {border_color}; }}
            #TaskCard:hover {{ border-color: #505050; background-color: #353535; }}
            QLabel {{ color: #E0E0E0; font-family: "Segoe UI", sans-serif; }}
            #Title {{ font-size: 16px; font-weight: bold; color: white; }}
            #TaskRow {{ background-color: rgba(0,0,0,0.25); border-radius: 4px; }}
            #TaskType {{ font-size: 10px; font-weight: bold; color: #AAAAAA; }}
            #Version {{ font-size: 10px; color: #888888; font-style: italic; }}
            #TaskStatus {{ font-size: 9px; color: #0078D4; text-transform: uppercase; font-weight: bold; }}
            #TaskPriority {{ font-size: 8px; color: #888; font-weight: bold; padding: 2px 4px; border: 1px solid #444; border-radius: 3px; }}
            #Thumbnail {{ background-color: #1A1A1A; border-radius: 8px; }}
            #LaunchBtn {{ background-color: #444444; color: white; border: none; border-radius: 6px; padding: 12px; font-weight: bold; }}
            #LaunchBtn:hover {{ background-color: #0078D4; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.title_label = QLabel(self.task_data["name"])
        self.title_label.setObjectName("Title")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(220, 110)
        self.thumb_label.setObjectName("Thumbnail")
        self.thumb_label.setAlignment(Qt.AlignCenter)

        thumb_path = self.task_data.get("thumbnail", "")
        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(220, 110, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.thumb_label.setPixmap(pix)
        else:
            self.thumb_label.setText("No Thumbnail")
        layout.addWidget(self.thumb_label)

        self.task_list_container = QWidget()
        task_list_layout = QVBoxLayout(self.task_list_container)
        task_list_layout.setContentsMargins(0, 0, 0, 0)
        task_list_layout.setSpacing(4)

        all_tasks = self.task_data.get("all_task_objs", [])
        current_user = self.task_data.get("current_user", "Unknown")
        display_tasks = all_tasks if self.is_admin else [t for t in all_tasks if t.get("assigned_to") == current_user]

        for task_obj in display_tasks:
            row = QFrame()
            row.setObjectName("TaskRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)

            t_type = QLabel(task_obj["type"])
            t_type.setObjectName("TaskType")
            row_layout.addWidget(t_type)

            v_num, _ = get_latest_version(task_obj["path"])
            v_str = "v" + str(v_num).zfill(3) if v_num > 0 else "NEW"
            v_label = QLabel(v_str)
            v_label.setObjectName("Version")
            row_layout.addWidget(v_label)
            
            # Priority
            p_label = QLabel(task_obj.get("priority", "Normal").upper())
            p_label.setObjectName("TaskPriority")
            if task_obj.get("priority") in ["High", "Critical"]: p_label.setStyleSheet("color: #FF4444; border-color: #662222;")
            row_layout.addWidget(p_label)

            row_layout.addStretch()

            t_status = QLabel(task_obj["status"])
            t_status.setObjectName("TaskStatus")
            if task_obj["status"] == "Approved": t_status.setStyleSheet("color: #28A745;")
            elif task_obj["status"] == "Pending Review": t_status.setStyleSheet("color: #0078D4;")
            elif task_obj["status"] == "In Progress": t_status.setStyleSheet("color: #FFC107;")
            row_layout.addWidget(t_status)

            task_list_layout.addWidget(row)

        layout.addWidget(self.task_list_container)
        
        self.launch_btn = QPushButton("CONTINUE WORK")
        self.launch_btn.setObjectName("LaunchBtn")
        self.launch_btn.clicked.connect(self.show_dept_menu)
        layout.addWidget(self.launch_btn)

    def show_dept_menu(self):
        all_tasks = self.task_data.get("all_task_objs", [])
        current_user = self.task_data.get("current_user", "Unknown")
        display_tasks = all_tasks if self.is_admin else [t for t in all_tasks if t.get("assigned_to") == current_user]

        if not display_tasks:
            return

        # Auto-launch if only one task
        if len(display_tasks) == 1:
            task_obj = display_tasks[0]
            v_num, latest_file = get_latest_version(task_obj["path"])
            if latest_file:
                self.continue_work_requested.emit(task_obj)
            else:
                self.start_work_requested.emit(task_obj)
            return

        # Otherwise show menu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D; }
            QMenu::item { padding: 10px 30px; }
            QMenu::item:selected { background-color: #0078D4; }
        """)

        for task_obj in display_tasks:
            v_num, latest_file = get_latest_version(task_obj["path"])

            if latest_file:
                version_str = "v" + str(v_num).zfill(3) if v_num > 0 else "Existing File"
                action = QAction(f"Continue {task_obj['type']} ({version_str})", self)
                def make_cont(t=task_obj):
                    return lambda: self.continue_work_requested.emit(t)
                action.triggered.connect(make_cont())
            else:
                action = QAction("Start " + task_obj["type"] + " (New File)", self)
                def make_start(t=task_obj):
                    return lambda: self.start_work_requested.emit(t)
                action.triggered.connect(make_start())

            menu.addAction(action)

        menu.exec(self.launch_btn.mapToGlobal(self.launch_btn.rect().bottomLeft()))

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D; } QMenu::item:selected { background-color: #0078D4; }")

        if self.is_admin:
            modify_action = QAction("Edit Tasks", self)
            modify_action.triggered.connect(lambda: self.modify_requested.emit(self.task_data["id"]))
            menu.addAction(modify_action)
            menu.addSeparator()

        reveal_action = QAction("Reveal in Explorer", self)
        folder = self.task_data.get("all_task_objs", [{}])[0].get("path")
        if folder:
            def make_reveal(f=folder):
                return lambda: os.startfile(f)
            reveal_action.triggered.connect(make_reveal())
            menu.addAction(reveal_action)

        if not menu.isEmpty():
            menu.exec(self.mapToGlobal(pos))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.task_data["id"])

