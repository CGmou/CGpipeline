from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QLabel, QCheckBox, QWidget, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, Signal
import os

from core.kitsu import KitsuManager


class KitsuDialog(QDialog):
    """Connect to a Kitsu server and sync its projects into the CGPipeline hub."""

    imported = Signal()  # emitted after a successful sync so the hub can refresh

    def __init__(self, auth_manager, hub_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.hub = hub_manager
        self.kitsu = KitsuManager()

        self.setWindowTitle("Kitsu Production Tracker")
        self.setMinimumWidth(480)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QLineEdit { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 8px; border-radius: 4px; }
            QCheckBox { color: #BBB; }
            QPushButton { background-color: #444; color: white; border: none; padding: 8px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #555; }
            QPushButton:disabled { background-color: #2A2A2A; color: #666; }
            #PrimaryBtn { background-color: #0078D4; font-weight: bold; }
            #PrimaryBtn:hover { background-color: #1086E0; }
            #ConnectedCard { background-color: #15301A; border: 1px solid #2c5a34; border-radius: 6px; }
        """)
        self.setup_ui()
        self._prefill()

    def setup_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)
        self.layout.addWidget(QLabel("KITSU PRODUCTION TRACKER"))

        if not KitsuManager.available():
            warn = QLabel(
                "The 'gazu' Python package is required for Kitsu integration.\n\n"
                "Install it, then reopen this dialog:\n"
                "    pip install gazu"
            )
            warn.setStyleSheet("color: #E0A030; padding: 10px; border: 1px solid #5a4a20; border-radius: 4px;")
            warn.setWordWrap(True)
            self.layout.addWidget(warn)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)
            self.layout.addWidget(close_btn)
            return

        self._build_login_form()
        self._build_connected_panel()
        self._show_state()

    # ---- login form (shown when disconnected) ----
    def _build_login_form(self):
        self.conn_widget = QWidget()
        form = QFormLayout(self.conn_widget)
        form.setContentsMargins(0, 0, 0, 0)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("https://your-studio.cg-wire.com")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@studio.com")
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.pass_edit.returnPressed.connect(self.on_connect)
        self.remember_chk = QCheckBox("Remember credentials on this machine")

        form.addRow("Host:", self.host_edit)
        form.addRow("Email:", self.email_edit)
        form.addRow("Password:", self.pass_edit)
        form.addRow("", self.remember_chk)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("PrimaryBtn")
        self.connect_btn.clicked.connect(self.on_connect)
        form.addRow(self.connect_btn)

        self.status_label = QLabel("Not connected.")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)

        self.layout.addWidget(self.conn_widget)

    # ---- connected panel (shown when connected) ----
    def _build_connected_panel(self):
        self.connected_widget = QWidget()
        v = QVBoxLayout(self.connected_widget)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        card = QWidget()
        card.setObjectName("ConnectedCard")
        cv = QVBoxLayout(card)
        self.user_label = QLabel("Connected")
        self.user_label.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 14px;")
        self.host_label = QLabel("")
        self.host_label.setStyleSheet("color: #9cc79f; font-size: 11px;")
        self.host_label.setWordWrap(True)
        cv.addWidget(self.user_label)
        cv.addWidget(self.host_label)
        v.addWidget(card)

        self.dest_label = QLabel()
        self.dest_label.setStyleSheet("color: #888; font-size: 11px;")
        self.dest_label.setWordWrap(True)
        v.addWidget(self.dest_label)

        self.sync_btn = QPushButton("Sync All Projects from Kitsu")
        self.sync_btn.setObjectName("PrimaryBtn")
        self.sync_btn.clicked.connect(self.on_sync_all)
        v.addWidget(self.sync_btn)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #BBB; font-size: 11px;")
        self.summary_label.setWordWrap(True)
        v.addWidget(self.summary_label)

        row = QHBoxLayout()
        row.addStretch()
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.on_disconnect)
        row.addWidget(self.disconnect_btn)
        v.addLayout(row)

        self.layout.addWidget(self.connected_widget)

    # ---- state ----
    def _show_state(self):
        connected = self.kitsu.connected
        self.conn_widget.setVisible(not connected)
        self.connected_widget.setVisible(connected)
        if connected:
            self.user_label.setText(f"Connected as {self.kitsu.user_name}")
            self.host_label.setText(self.kitsu.host)
            self._update_dest_label()
        self.adjustSize()

    def _update_dest_label(self):
        root = self.auth.settings.get("project_root", "")
        if root:
            self.dest_label.setText(f"Projects will sync into:\n{root}")
        else:
            self.dest_label.setText(
                "⚠ No project root set. Set one in Settings → Project Root before syncing."
            )

    def _prefill(self):
        if not KitsuManager.available():
            return
        s = self.auth.settings
        self.host_edit.setText(s.get("kitsu_host", ""))
        self.email_edit.setText(s.get("kitsu_email", ""))
        self.remember_chk.setChecked(bool(s.get("kitsu_remember", False)))
        if s.get("kitsu_remember") and s.get("kitsu_pass"):
            self.pass_edit.setText(s.get("kitsu_pass", ""))
            # Auto-connect so the user lands on the clean connected view.
            self._connect(silent=True)

    # ---- handlers ----
    def _connect(self, silent=False):
        host = self.host_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.pass_edit.text()
        if not (host and email and password):
            if not silent:
                self.status_label.setStyleSheet("color: #E06060; font-size: 11px;")
                self.status_label.setText("Enter host, email, and password.")
            return False

        self.status_label.setText("Connecting…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, msg = self.kitsu.connect(host, email, password)
        finally:
            QApplication.restoreOverrideCursor()

        if not ok:
            self.status_label.setStyleSheet("color: #E06060; font-size: 11px;")
            self.status_label.setText(msg)
            return False

        # Persist connection settings.
        self.auth.settings["kitsu_host"] = host
        self.auth.settings["kitsu_email"] = email
        self.auth.settings["kitsu_remember"] = self.remember_chk.isChecked()
        self.auth.settings["kitsu_pass"] = password if self.remember_chk.isChecked() else ""
        self.auth.save()

        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        self.status_label.setText("Not connected.")
        self._show_state()
        return True

    def on_connect(self):
        self._connect(silent=False)

    def on_disconnect(self):
        self.kitsu.disconnect()
        self.summary_label.setText("")
        self._show_state()

    def on_sync_all(self):
        root = self.auth.settings.get("project_root", "")
        if not root or not os.path.exists(root):
            QMessageBox.warning(
                self, "Project Root Required",
                "Set a project root in Settings → Project Root before syncing."
            )
            return

        current_user = ""
        if self.auth.current_user:
            current_user = self.auth.current_user.get("username", "")

        def progress(name, cur, total):
            self.summary_label.setText(f"Syncing… {name} ({cur}/{total})")
            QApplication.processEvents()

        self.sync_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            totals = self.kitsu.import_all_projects(
                self.hub, root, current_user=current_user, progress=progress,
            )
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.sync_btn.setEnabled(True)
            QMessageBox.critical(self, "Kitsu Sync Failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.sync_btn.setEnabled(True)

        self.summary_label.setText(
            f"Synced {totals['projects']} project(s): "
            f"{totals['assets']} assets, {totals['shots']} shots, "
            f"{totals['tasks']} tasks"
            + (f" ({totals['skipped']} skipped)" if totals.get("skipped") else "")
            + "."
        )
        self.imported.emit()
