from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, 
                             QPushButton, QLineEdit, QComboBox, QLabel, QFormLayout)
from PySide6.QtCore import Qt

class UserManagementDialog(QDialog):
    def __init__(self, auth_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.setWindowTitle("Studio User Management")
        self.setMinimumSize(500, 400)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBBBBB; }
            QListWidget, QLineEdit, QComboBox {
                background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 5px;
            }
            QPushButton { border-radius: 4px;
                background-color: #444; color: white; border: none; padding: 8px; border-radius: 4px; font-weight: bold;     
            }
            #DeleteBtn { background-color: #A72828; }
            #AddBtn { background-color: #28A745; }
            #UpdateBtn { background-color: #0078D4; }
            #ClearBtn { background-color: #555; border: 1px solid #777; }
        """)
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        # Left: User List
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Registered Users:"))
        self.user_list = QListWidget()
        self.user_list.itemClicked.connect(self.on_user_selected)
        self.user_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.user_list.viewport().installEventFilter(self)
        left_layout.addWidget(self.user_list)
        main_layout.addLayout(left_layout, 1)

        # Right: User Details
        right_layout = QVBoxLayout()
        form = QFormLayout()

        self.name_edit = QLineEdit()
        form.addRow("Username:", self.name_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("New Password")
        form.addRow("Password:", self.pass_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItems(["admin", "artist"])
        form.addRow("Role:", self.role_combo)
        
        right_layout.addWidget(QLabel("Assign Projects:"))
        self.proj_list = QListWidget()
        self.proj_list.setSelectionMode(QListWidget.NoSelection)
        right_layout.addWidget(self.proj_list)
        self.assign_btn = QPushButton("ASSIGN PROJECTS")
        self.assign_btn.setStyleSheet("background-color: #0078D4; margin-top: 5px;")
        self.assign_btn.clicked.connect(self.on_assign_projects)
        right_layout.addWidget(self.assign_btn)

        right_layout.addLayout(form)

        

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("ADD USER")
        self.add_btn.setObjectName("AddBtn")
        self.add_btn.clicked.connect(self.on_add_user)
        btn_row.addWidget(self.add_btn)

        self.update_btn = QPushButton("UPDATE USER")
        self.update_btn.setObjectName("UpdateBtn")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self.on_update_user)
        btn_row.addWidget(self.update_btn)
        right_layout.addLayout(btn_row)

        self.delete_btn = QPushButton("DELETE USER")
        self.delete_btn.setObjectName("DeleteBtn")
        self.delete_btn.clicked.connect(self.on_delete_user)
        right_layout.addWidget(self.delete_btn)

        right_layout.addStretch()
        main_layout.addLayout(right_layout, 1)

        self.refresh_list()

    def load_user_projects(self, user):
        self.proj_list.clear()
        hub = self.parent().hub
        user_projs = user.get("projects", [])
        for p in hub.projects:
            item = QListWidgetItem(p["name"])
            item.setData(Qt.UserRole, p["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p["id"] in user_projs else Qt.Unchecked)
            self.proj_list.addItem(item)

    def on_assign_projects(self):
        name = self.name_edit.text().strip()
        if not name: return
        selected_projs = []
        for i in range(self.proj_list.count()):
            item = self.proj_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_projs.append(item.data(Qt.UserRole))
        if self.auth.update_user(name, projects=selected_projs):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Success", f"Projects assigned to {name}")

    def refresh_list(self):
        self.user_list.clear()
        for u in self.auth.users:
            self.user_list.addItem(u["username"])

    def on_user_selected(self, item):
        username = item.text()
        user = next((u for u in self.auth.users if u["username"] == username), None)
        if user:
            self.name_edit.setText(user["username"])
            self.pass_edit.setText(user.get("password", ""))
            self.role_combo.setCurrentText(user.get("role", "artist"))
            self.update_btn.setEnabled(True)
            self.add_btn.setEnabled(False)
            self.load_user_projects(user)

    def on_add_user(self):
        name = self.name_edit.text().strip()
        pwd = self.pass_edit.text()
        role = self.role_combo.currentText()
        if not name or not pwd: return
        if self.auth.add_user(name, role, pwd):
            self.refresh_list()
            self.clear_inputs()

    def on_update_user(self):
        name = self.name_edit.text().strip()
        pwd = self.pass_edit.text()
        role = self.role_combo.currentText()
        selected_projs = []
        for i in range(self.proj_list.count()):
            item = self.proj_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_projs.append(item.data(Qt.UserRole))
        
        if self.auth.update_user(name, password=pwd, role=role):
            self.refresh_list()
            self.clear_inputs()

    def on_delete_user(self):
        name = self.name_edit.text()
        if self.auth.delete_user(name):
            self.refresh_list()
            self.clear_inputs()

    def mousePressEvent(self, event):
        # Clear selection if clicking empty space in the dialog
        self.clear_inputs()
        super().mousePressEvent(event)

    def clear_inputs(self):
        self.name_edit.clear()
        self.pass_edit.clear()
        self.user_list.clearSelection()
        self.update_btn.setEnabled(False)
        self.add_btn.setEnabled(True)
