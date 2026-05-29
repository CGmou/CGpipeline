from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QLabel, QCheckBox, QListWidget, QListWidgetItem, QGroupBox, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, Signal
import os

from core.kitsu import KitsuManager


class KitsuDialog(QDialog):
    """Connect to a Kitsu server and import its projects into CGPipeline."""

    imported = Signal()  # emitted after a successful import so the hub can refresh

    def __init__(self, auth_manager, hub_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.hub = hub_manager
        self.kitsu = KitsuManager()

        self.setWindowTitle("Kitsu Production Tracker")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QGroupBox { color: #DDD; border: 1px solid #333; border-radius: 6px; margin-top: 10px; padding-top: 12px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QLineEdit { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 8px; border-radius: 4px; }
            QListWidget { background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; border-radius: 4px; }
            QCheckBox { color: #BBB; }
            QPushButton { background-color: #444; color: white; border: none; padding: 8px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #555; }
            QPushButton:disabled { background-color: #2A2A2A; color: #666; }
            #PrimaryBtn { background-color: #0078D4; font-weight: bold; }
            #PrimaryBtn:hover { background-color: #1086E0; }
        """)
        self.setup_ui()
        self._prefill()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel("KITSU PRODUCTION TRACKER"))

        if not KitsuManager.available():
            warn = QLabel(
                "The 'gazu' Python package is required for Kitsu integration.\n\n"
                "Install it, then reopen this dialog:\n"
                "    pip install gazu"
            )
            warn.setStyleSheet("color: #E0A030; padding: 10px; border: 1px solid #5a4a20; border-radius: 4px;")
            warn.setWordWrap(True)
            layout.addWidget(warn)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)
            layout.addWidget(close_btn)
            return

        # --- Connection ---
        conn_box = QGroupBox("Connection")
        conn_form = QFormLayout(conn_box)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("https://your-studio.cg-wire.com")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@studio.com")
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.remember_chk = QCheckBox("Remember credentials on this machine")
        conn_form.addRow("Host:", self.host_edit)
        conn_form.addRow("Email:", self.email_edit)
        conn_form.addRow("Password:", self.pass_edit)
        conn_form.addRow("", self.remember_chk)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("PrimaryBtn")
        self.connect_btn.clicked.connect(self.on_connect)
        conn_form.addRow(self.connect_btn)

        self.status_label = QLabel("Not connected.")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        self.status_label.setWordWrap(True)
        conn_form.addRow(self.status_label)
        layout.addWidget(conn_box)

        # --- Projects / Import ---
        self.import_box = QGroupBox("Import Projects")
        imp_layout = QVBoxLayout(self.import_box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Kitsu projects:"))
        row.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.load_projects)
        row.addWidget(self.refresh_btn)
        imp_layout.addLayout(row)

        self.project_list = QListWidget()
        self.project_list.setMinimumHeight(160)
        imp_layout.addWidget(self.project_list)

        self.dest_label = QLabel()
        self.dest_label.setStyleSheet("color: #888; font-size: 11px;")
        self.dest_label.setWordWrap(True)
        imp_layout.addWidget(self.dest_label)

        self.import_btn = QPushButton("Import Selected Project")
        self.import_btn.setObjectName("PrimaryBtn")
        self.import_btn.clicked.connect(self.on_import)
        imp_layout.addWidget(self.import_btn)

        self.import_box.setEnabled(False)
        layout.addWidget(self.import_box)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #BBB; font-size: 11px;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

    def _prefill(self):
        if not KitsuManager.available():
            return
        s = self.auth.settings
        self.host_edit.setText(s.get("kitsu_host", ""))
        self.email_edit.setText(s.get("kitsu_email", ""))
        self.remember_chk.setChecked(bool(s.get("kitsu_remember", False)))
        if s.get("kitsu_remember") and s.get("kitsu_pass"):
            self.pass_edit.setText(s.get("kitsu_pass", ""))
        self._update_dest_label()

    def _update_dest_label(self):
        root = self.auth.settings.get("project_root", "")
        if root:
            self.dest_label.setText(f"Imported into project root:\n{root}")
        else:
            self.dest_label.setText(
                "⚠ No project root set. Set one in Settings → Project Root before importing."
            )

    # ---- handlers ----
    def on_connect(self):
        host = self.host_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.pass_edit.text()

        self.status_label.setText("Connecting…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, msg = self.kitsu.connect(host, email, password)
        finally:
            QApplication.restoreOverrideCursor()

        self.status_label.setText(msg)
        if not ok:
            self.status_label.setStyleSheet("color: #E06060; font-size: 11px;")
            return

        self.status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")

        # Persist connection settings.
        self.auth.settings["kitsu_host"] = host
        self.auth.settings["kitsu_email"] = email
        self.auth.settings["kitsu_remember"] = self.remember_chk.isChecked()
        self.auth.settings["kitsu_pass"] = password if self.remember_chk.isChecked() else ""
        self.auth.save()

        self.import_box.setEnabled(True)
        self._update_dest_label()
        self.load_projects()

    def load_projects(self):
        self.project_list.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            projects = self.kitsu.list_projects()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Kitsu", f"Could not load projects:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        for p in sorted(projects, key=lambda x: x.get("name", "").lower()):
            item = QListWidgetItem(p.get("name", "Unnamed"))
            item.setData(Qt.UserRole, p)
            self.project_list.addItem(item)

        if not projects:
            self.summary_label.setText("No projects found on this Kitsu server.")

    def on_import(self):
        root = self.auth.settings.get("project_root", "")
        if not root or not os.path.exists(root):
            QMessageBox.warning(
                self, "Project Root Required",
                "Set a project root in Settings → Project Root before importing."
            )
            return

        item = self.project_list.currentItem()
        if not item:
            QMessageBox.information(self, "Kitsu", "Select a project to import.")
            return
        kitsu_project = item.data(Qt.UserRole)

        current_user = ""
        if self.auth.current_user:
            current_user = self.auth.current_user.get("username", "")

        def progress(msg, cur, total):
            self.summary_label.setText(f"Importing… {msg} ({cur}/{total})")
            QApplication.processEvents()

        self.import_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            summary = self.kitsu.import_project(
                kitsu_project, self.hub, root,
                current_user=current_user, progress=progress,
            )
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.import_btn.setEnabled(True)
            QMessageBox.critical(self, "Kitsu Import Failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.import_btn.setEnabled(True)

        if not summary.get("ok"):
            QMessageBox.warning(self, "Kitsu", summary.get("error", "Import failed."))
            return

        self.summary_label.setText(
            f"Imported '{summary['project']}': "
            f"{summary['assets']} assets, {summary['shots']} shots, "
            f"{summary['tasks']} tasks created"
            + (f" ({summary['skipped']} skipped)" if summary.get("skipped") else "")
            + "."
        )
        self.imported.emit()
