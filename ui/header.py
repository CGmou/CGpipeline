from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QMenu
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QAction

class Header(QWidget):
    view_changed = Signal(str)
    back_to_hub = Signal()
    logout_requested = Signal()

    def __init__(self, registry):
        super().__init__()
        self.registry = registry
        self.setup_ui()

    def setup_ui(self):
        self.setFixedHeight(70)
        self.setObjectName("Header")
        self.setStyleSheet("""
            #Header { background-color: #1A1A1A; border-bottom: 1px solid #333333; }
            QLabel { color: #FFFFFF; font-family: "Segoe UI", sans-serif; }
            #ProjectLabel { font-size: 18px; font-weight: bold; color: #0078D4; }
            #UserBtn { background-color: transparent; color: #AAAAAA; border: none; font-size: 14px; padding: 5px 10px; text-decoration: underline; }
            #UserBtn:hover { color: white; }
            #NavBtn { background-color: transparent; color: #888888; border: none; font-size: 14px; font-weight: bold; padding: 10px 20px; }
            #NavBtn:hover { color: white; }
            #NavBtn[active="true"] { color: white; border-bottom: 2px solid #0078D4; }
            #HubBtn { color: #0078D4; font-size: 11px; border: 1px solid #0078D4; border-radius: 4px; padding: 5px 15px; font-weight: bold; background: transparent; }
            #HubBtn:hover { background-color: #0078D4; color: white; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 0, 30, 0)
        info_layout = QHBoxLayout()
        self.project_name = QLabel(self.registry.data.get("project_name", "Unknown Project"))
        self.project_name.setObjectName("ProjectLabel")
        info_layout.addWidget(self.project_name)
        
        # Color Management Label
        color_val = self.registry.data.get("color_management", "ACES 1.2")
        self.color_label = QLabel(f"[{color_val}]")
        self.color_label.setStyleSheet("color: #555; font-size: 11px; font-weight: bold; margin-left: 5px;")
        info_layout.addWidget(self.color_label)

        separator = QLabel("|")
        separator.setStyleSheet("color: #333333; margin: 0 10px;")
        info_layout.addWidget(separator)
        self.user_btn = QPushButton(self.registry.current_user)
        self.user_btn.setObjectName("UserBtn")
        self.user_btn.clicked.connect(self.show_user_menu)
        info_layout.addWidget(self.user_btn)
        layout.addLayout(info_layout)
        layout.addStretch()
        nav_layout = QHBoxLayout()
        self.desk_btn = QPushButton("MY TASKS")
        self.desk_btn.setObjectName("NavBtn")
        self.desk_btn.setProperty("active", True)
        self.desk_btn.clicked.connect(lambda: self.on_nav_clicked("desk"))
        self.sheet_btn = QPushButton("PROJECT SHEET")
        self.sheet_btn.setObjectName("NavBtn")
        self.sheet_btn.setProperty("active", False)
        self.sheet_btn.clicked.connect(lambda: self.on_nav_clicked("sheet"))
        nav_layout.addWidget(self.desk_btn)
        nav_layout.addWidget(self.sheet_btn)
        layout.addLayout(nav_layout)
        layout.addStretch()
        self.home_btn = QPushButton("PROJECT HUB")
        self.home_btn.setObjectName("HubBtn")
        self.home_btn.clicked.connect(self.back_to_hub.emit)
        layout.addWidget(self.home_btn)

    def show_user_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D; } QMenu::item:selected { background-color: #0078D4; }")
        logout_action = QAction("Logout", self)
        logout_action.triggered.connect(self.logout_requested.emit)
        menu.addAction(logout_action)
        menu.exec(self.user_btn.mapToGlobal(self.user_btn.rect().bottomLeft()))

    def on_nav_clicked(self, view):
        self.desk_btn.setProperty("active", view == "desk")
        self.sheet_btn.setProperty("active", view == "sheet")
        self.desk_btn.style().unpolish(self.desk_btn)
        self.desk_btn.style().polish(self.desk_btn)
        self.sheet_btn.style().unpolish(self.sheet_btn)
        self.sheet_btn.style().polish(self.sheet_btn)
        self.view_changed.emit(view)

