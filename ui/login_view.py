from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QCheckBox, QMessageBox, QHBoxLayout)
from PySide6.QtCore import Signal, Qt

class LoginView(QWidget):
    login_success = Signal(dict)

    def __init__(self, auth_manager):
        super().__init__()
        self.auth = auth_manager
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self.container = QWidget()
        self.container.setFixedWidth(350)
        self.container.setStyleSheet("background-color: #1E1E1E; border-radius: 10px; padding: 30px;")
        c_layout = QVBoxLayout(self.container)
        title = QLabel("CGPipeline")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #0078D4; margin-bottom: 20px;")
        title.setAlignment(Qt.AlignCenter)
        c_layout.addWidget(title)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Username")
        self.user_edit.setText(self.auth.settings.get("last_user", ""))
        self.user_edit.setStyleSheet("background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 12px; border-radius: 5px;")
        c_layout.addWidget(self.user_edit)
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("Password")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.setText(self.auth.settings.get("last_pass", ""))
        self.pass_edit.setStyleSheet("background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 12px; border-radius: 5px; margin-top: 10px;")
        c_layout.addWidget(self.pass_edit)
        self.remember_check = QCheckBox("Remember Me")
        self.remember_check.setChecked(self.auth.settings.get("remember", False))
        self.remember_check.setStyleSheet("color: #AAA; margin-top: 10px;")
        c_layout.addWidget(self.remember_check)
        self.login_btn = QPushButton("LOGIN")
        self.login_btn.setStyleSheet("background-color: #0078D4; color: white; border: none; padding: 12px; border-radius: 5px; font-weight: bold; margin-top: 20px;")
        self.login_btn.clicked.connect(self.on_login)
        c_layout.addWidget(self.login_btn)
        layout.addWidget(self.container)

    def on_login(self):
        u = self.user_edit.text()
        p = self.pass_edit.text()
        rem = self.remember_check.isChecked()
        user = self.auth.login(u, p, remember=rem)
        if user: self.login_success.emit(user)
        else: QMessageBox.warning(self, "Error", "Invalid username or password.")

