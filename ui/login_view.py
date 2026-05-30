from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QCheckBox, QMessageBox, QHBoxLayout,
                             QDialog, QFormLayout, QApplication)
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

        self.kitsu_btn = QPushButton("LOGIN WITH KITSU")
        self.kitsu_btn.setStyleSheet("background-color: #2D2D2D; color: #DDD; border: 1px solid #00B8A9; padding: 12px; border-radius: 5px; font-weight: bold; margin-top: 10px;")
        self.kitsu_btn.clicked.connect(self.on_kitsu_login)
        c_layout.addWidget(self.kitsu_btn)

        layout.addWidget(self.container)

    def on_kitsu_login(self):
        from core.kitsu import KitsuManager
        if not KitsuManager.available():
            QMessageBox.warning(self, "Kitsu", "The 'gazu' package is not installed.\nRun: pip install gazu")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Login with Kitsu")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QLineEdit { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 8px; border-radius: 4px; }
            QCheckBox { color: #BBB; }
            QPushButton { background-color: #00B8A9; color: white; border: none; padding: 8px 15px; border-radius: 4px; font-weight: bold; }
        """)
        form = QFormLayout(dlg)
        s = self.auth.settings
        host_e = QLineEdit(s.get("kitsu_host", "")); host_e.setPlaceholderText("https://your-studio.cg-wire.com")
        email_e = QLineEdit(s.get("kitsu_email", ""))
        pass_e = QLineEdit(s.get("kitsu_pass", "") if s.get("kitsu_remember") else "")
        pass_e.setEchoMode(QLineEdit.Password)
        remember_c = QCheckBox("Remember credentials")
        remember_c.setChecked(bool(s.get("kitsu_remember", False)))
        form.addRow("Host:", host_e)
        form.addRow("Email:", email_e)
        form.addRow("Password:", pass_e)
        form.addRow("", remember_c)
        status = QLabel("")
        status.setStyleSheet("color: #888; font-size: 11px;")
        status.setWordWrap(True)
        form.addRow(status)
        connect_btn = QPushButton("Connect & Login")
        form.addRow(connect_btn)

        def do_connect():
            host, email, pw = host_e.text().strip(), email_e.text().strip(), pass_e.text()
            if not (host and email and pw):
                status.setText("Enter host, email, and password.")
                return
            status.setText("Connecting…")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                km = KitsuManager()
                ok, msg = km.connect(host, email, pw)
            finally:
                QApplication.restoreOverrideCursor()
            if not ok:
                status.setText(msg)
                return
            # Persist connection so the Kitsu menu is also connected.
            self.auth.settings["kitsu_host"] = host
            self.auth.settings["kitsu_email"] = email
            self.auth.settings["kitsu_remember"] = remember_c.isChecked()
            self.auth.settings["kitsu_pass"] = pw if remember_c.isChecked() else ""
            self.auth.save()
            user = self.auth.login_with_kitsu(km.email, km.user_name, role=km.pipeline_role())
            dlg.accept()
            self.login_success.emit(user)

        connect_btn.clicked.connect(do_connect)
        pass_e.returnPressed.connect(do_connect)
        dlg.exec()

    def on_login(self):
        u = self.user_edit.text()
        p = self.pass_edit.text()
        rem = self.remember_check.isChecked()
        user = self.auth.login(u, p, remember=rem)
        if user: self.login_success.emit(user)
        else: QMessageBox.warning(self, "Error", "Invalid username or password.")

