from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QHBoxLayout, QLabel, QFileDialog
import os

class ProjectRootDialog(QDialog):
    def __init__(self, auth_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.setWindowTitle("Project Storage Settings")
        self.setMinimumWidth(400)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QLineEdit { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 8px; border-radius: 4px; }
            QPushButton { background-color: #444; color: white; border: none; padding: 8px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #555; }
            #SaveBtn { background-color: #0078D4; font-weight: bold; }
        """)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        layout.addWidget(QLabel("PROJECT STORAGE"))
        root_form = QFormLayout()
        root_row = QHBoxLayout()
        self.root_edit = QLineEdit(self.auth.settings.get("project_root", ""))
        root_btn = QPushButton("Browse")
        root_btn.clicked.connect(self.browse_root)
        root_row.addWidget(self.root_edit)
        root_row.addWidget(root_btn)
        root_form.addRow("Default Root:", root_row)
        layout.addLayout(root_form)

        layout.addStretch()

        save_btn = QPushButton("SAVE STORAGE SETTINGS")
        save_btn.setObjectName("SaveBtn")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self.save)
        layout.addWidget(save_btn)

    def browse_root(self):
        p = QFileDialog.getExistingDirectory(self, "Select Default Project Root")
        if p: self.root_edit.setText(p)

    def save(self):
        settings = self.auth.settings.copy()
        settings["project_root"] = self.root_edit.text()
        self.auth.settings = settings
        self.auth.save_settings(settings.get("last_user", ""), settings.get("last_pass", ""), settings.get("remember", False))
        self.accept()

class DCCPathsDialog(QDialog):
    def __init__(self, auth_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.setWindowTitle("DCC Path Settings")
        self.setMinimumWidth(500)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QLineEdit { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 8px; border-radius: 4px; }
            QPushButton { background-color: #444; color: white; border: none; padding: 8px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #555; }
            #SaveBtn { background-color: #0078D4; font-weight: bold; }
        """)

        self.paths = self.auth.dcc_paths
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        layout.addWidget(QLabel("DCC EXECUTABLES"))
        dcc_form = QFormLayout()
        self.edits = {}
        for dcc in ["Maya", "Blender", "Houdini"]:
            row = QHBoxLayout()
            edit = QLineEdit(self.paths.get(dcc, ""))
            self.edits[dcc] = edit
            btn = QPushButton("Browse")
            btn.clicked.connect(lambda _, d=dcc, e=edit: self.browse(d, e))
            row.addWidget(edit)
            row.addWidget(btn)
            dcc_form.addRow(f"{dcc} Path:", row)
        layout.addLayout(dcc_form)

        layout.addStretch()

        save_btn = QPushButton("SAVE DCC PATHS")
        save_btn.setObjectName("SaveBtn")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self.save)
        layout.addWidget(save_btn)

    def browse(self, dcc, edit):
        import platform
        if platform.system() == "Windows":
            filter_str = "Executable (*.exe);;All Files (*)"
        elif platform.system() == "Darwin":
            filter_str = "Application (*.app);;All Files (*)"
        else:
            filter_str = "All Files (*)"

        path, _ = QFileDialog.getOpenFileName(self, f"Select {dcc} Executable", "", filter_str)       
        if path:
            edit.setText(path)

    def save(self):
        new_paths = {d: e.text() for d, e in self.edits.items()}
        self.auth.dcc_paths = new_paths
        self.auth.save_settings(self.auth.settings.get("last_user", ""), self.auth.settings.get("last_pass", ""), self.auth.settings.get("remember", False))
        self.accept()
