from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QScrollArea, QFrame, QFileDialog, QDialog, QLineEdit, QMenu, QFormLayout, QMessageBox, QComboBox, QSpinBox, QApplication)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QAction
import os
import webbrowser
from .user_management_dialog import UserManagementDialog

class ProjectCard(QFrame):
    clicked = Signal(dict)
    modify_requested = Signal(dict)
    delete_requested = Signal(dict)
    upload_requested = Signal(dict)

    def __init__(self, project_data, is_admin=False):
        super().__init__()
        self.project_data = project_data
        self.is_admin = is_admin
        self.is_kitsu = bool(project_data.get("kitsu_id"))
        self.setFixedSize(250, 200)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Kitsu-linked projects get a teal accent; local projects keep the default.
        accent = "#00B8A9" if self.is_kitsu else "#3D3D3D"
        hover = "#1FD6C6" if self.is_kitsu else "#0078D4"
        self.setStyleSheet(f"""
            QFrame {{ background-color: #2D2D2D; border-radius: 12px; border: 1px solid {accent}; }}
            QFrame:hover {{ background-color: #353535; border: 1px solid {hover}; }}
            QLabel {{ color: white; border: none; font-family: "Segoe UI"; }}
            #Thumb {{ background-color: #1A1A1A; border-radius: 8px; }}
            #KitsuBadge {{ background-color: #00B8A9; color: white; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px; }}
            #LocalBadge {{ background-color: #3A3A3A; color: #999; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(5)

        # Source badge row.
        badge_row = QHBoxLayout()
        badge = QLabel("KITSU" if self.is_kitsu else "LOCAL")
        badge.setObjectName("KitsuBadge" if self.is_kitsu else "LocalBadge")
        badge_row.addWidget(badge)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        self.thumb = QLabel()
        self.thumb.setObjectName("Thumb")
        self.thumb.setFixedSize(220, 84)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.update_thumbnail()
        layout.addWidget(self.thumb)

        name = QLabel(self.project_data["name"])
        name.setStyleSheet("font-size: 16px; font-weight: bold; color: #EEEEEE;")
        name.setWordWrap(True)
        layout.addWidget(name)

        info = QLabel(str(self.project_data.get("fps", 24)) + " fps | " + str(self.project_data.get("color_management", "ACES 1.3")))
        info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(info)

        layout.addStretch()

    def update_thumbnail(self):
        thumb_path = self.project_data.get("thumbnail", "")
        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path).scaled(220, 100, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.thumb.setPixmap(pix)
        else:
            self.thumb.setText("NO THUMBNAIL")
            self.thumb.setStyleSheet("color: #444444; font-weight: bold;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.project_data)

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D; } QMenu::item:selected { background-color: #0078D4; }")

        if self.is_admin:
            modify_action = QAction("Modify Project Settings", self)
            modify_action.triggered.connect(lambda: self.modify_requested.emit(self.project_data))
            menu.addAction(modify_action)

            if self.is_kitsu:
                open_action = QAction("Open in Kitsu (browser)", self)
                open_action.triggered.connect(self.open_in_kitsu)
                menu.addAction(open_action)
            else:
                upload_action = QAction("Upload to Kitsu", self)
                upload_action.triggered.connect(lambda: self.upload_requested.emit(self.project_data))
                menu.addAction(upload_action)

            menu.addSeparator()
            delete_action = QAction("Delete Project", self)
            delete_action.triggered.connect(lambda: self.delete_requested.emit(self.project_data))
            menu.addAction(delete_action)

        if not menu.isEmpty():
            menu.exec(self.mapToGlobal(pos))

    def open_in_kitsu(self):
        kid = self.project_data.get("kitsu_id", "")
        host = self.project_data.get("kitsu_host", "")
        if not kid or not host:
            return
        web = host[:-4] if host.endswith("/api") else host  # strip /api for the web URL
        webbrowser.open(f"{web.rstrip('/')}/productions/{kid}/assets")

class ProjectHubView(QWidget):
    project_selected = Signal(dict)

    def __init__(self, hub_manager, auth_manager, kitsu=None):
        super().__init__()
        self.hub = hub_manager
        self.auth = auth_manager
        if kitsu is None:
            from core.kitsu import KitsuManager
            kitsu = KitsuManager()
        self.kitsu = kitsu
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(30)

        header = QHBoxLayout()
        title = QLabel("PROJECT HUB")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: white;")
        header.addWidget(title)
        header.addStretch()
        
        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setStyleSheet("""
            QPushButton { background-color: #333; color: #888; border: 1px solid #444; border-radius: 4px; padding: 10px 15px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background-color: #444; color: white; }
        """)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)

        if self.auth.is_admin():
            self.user_mgr_btn = QPushButton("USERS")
            self.user_mgr_btn.setStyleSheet("""
                QPushButton { background-color: #444; color: white; border: none; border-radius: 4px; padding: 10px 20px; font-weight: bold; }
                QPushButton:hover { background-color: #555; }
            """)
            self.user_mgr_btn.clicked.connect(self.on_open_user_mgmt)
            header.addWidget(self.user_mgr_btn)

            self.new_proj_btn = QPushButton("+ NEW PROJECT")
            self.new_proj_btn.setStyleSheet("""
                QPushButton { background-color: #0078D4; color: white; border: none; border-radius: 4px; padding: 10px 20px; font-weight: bold; }
                QPushButton:hover { background-color: #1086E0; }
            """)
            self.new_proj_btn.clicked.connect(self.on_new_project)
            header.addWidget(self.new_proj_btn)

        self.user_btn = QPushButton(self.auth.current_user["username"])
        self.user_btn.setStyleSheet("""
            QPushButton { 
                background-color: transparent; color: #AAAAAA; border: none; font-size: 14px; padding: 5px 10px; text-decoration: underline;
            }
            QPushButton:hover { color: white; }
        """)
        self.user_btn.clicked.connect(self.show_user_menu)
        header.addWidget(self.user_btn)
            
        layout.addLayout(header)

        # Root Label
        self.root_label = QLabel("No root selected")
        self.root_label.setStyleSheet("color: #666; font-size: 11px; margin-top: -20px;")
        layout.addWidget(self.root_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        
        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setSpacing(25)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)
        self.refresh()

    def on_set_root(self):
        root = QFileDialog.getExistingDirectory(self, "Select Project Root folder (contains project_index.json)")
        if root:
            root = os.path.normpath(root)
            self.auth.settings["project_root"] = root
            self.auth.save_settings(self.auth.current_user["username"], "", self.auth.settings.get("remember", False))
            self.refresh()

    def show_user_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D; } QMenu::item:selected { background-color: #0078D4; }")
        logout_action = QAction("Logout", self)
        logout_action.triggered.connect(lambda: self.window().show_login())
        menu.addAction(logout_action)
        menu.exec(self.user_btn.mapToGlobal(self.user_btn.rect().bottomLeft()))

    def refresh(self):
        root = self.auth.settings.get("project_root", "")
        if root and os.path.exists(root):
            self.hub.set_project_root(root)
            self.root_label.setText(f"Active Root: {root}")
        else:
            self.root_label.setText("Please select a project root to see projects.")

        for i in reversed(range(self.grid.count())): 
            widget = self.grid.itemAt(i).widget()
            if widget: widget.setParent(None)
            
        if self.hub.projects:
            for index, project in enumerate(self.hub.projects):
                card = ProjectCard(project, is_admin=self.auth.is_admin())
                card.clicked.connect(self.project_selected.emit)
                if self.auth.is_admin():
                    card.modify_requested.connect(self.on_modify_project)
                    card.delete_requested.connect(self.on_delete_project)
                    card.upload_requested.connect(self.on_upload_to_kitsu)
                self.grid.addWidget(card, index // 4, index % 4)
        else:
            # Show placeholder if root is set but no projects found
            if root:
                placeholder = QLabel("No projects found in this root folder.")
                placeholder.setStyleSheet("color: #555; font-size: 14px; margin-top: 20px;")
                self.grid.addWidget(placeholder, 0, 0)

    def on_open_user_mgmt(self):
        dialog = UserManagementDialog(self.auth, self)
        dialog.exec()

    def on_new_project(self):
        root = self.auth.settings.get("project_root", "")
        if not root or not os.path.exists(root):
            QMessageBox.warning(self, "Root Required", "Please set a Project Root folder before creating a project.")
            self.on_set_root()
            return
        self.show_project_dialog()

    def on_modify_project(self, project_data):
        self.show_project_dialog(project_data)

    def on_upload_to_kitsu(self, project_data):
        from core.kitsu import KitsuManager
        if not KitsuManager.available():
            QMessageBox.warning(self, "Kitsu", "The 'gazu' package is not installed.\nRun: pip install gazu")
            return

        ok, msg = self.kitsu.ensure_connected(self.auth)
        if not ok:
            QMessageBox.information(self, "Connect to Kitsu", msg)
            return

        reply = QMessageBox.question(
            self, "Upload to Kitsu",
            f"Create '{project_data['name']}' on Kitsu and upload its assets, shots, "
            f"and tasks?\n\nThis pushes the project up to {self.kitsu.host}.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            summary = self.kitsu.upload_project_to_kitsu(project_data, self.hub)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Kitsu Upload Failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()

        if not summary.get("ok"):
            QMessageBox.warning(self, "Kitsu", summary.get("error", "Upload failed."))
            return

        QMessageBox.information(
            self, "Uploaded to Kitsu",
            f"'{summary['project']}' is now on Kitsu:\n"
            f"{summary['assets']} assets, {summary['shots']} shots, "
            f"{summary['tasks']} tasks"
            + (f" ({summary['skipped']} skipped)" if summary.get("skipped") else "")
            + ".",
        )
        self.refresh()

    def on_delete_project(self, project_data):
        # LEVEL 1: Standard Question
        msg = f"Are you sure you want to delete project '{project_data['name']}'?\n\nThis will PERMANENTLY remove all project files from your drive."
        reply = QMessageBox.question(self, "Delete Project - Step 1/2", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            # LEVEL 2: Name Verification
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            text, ok = QInputDialog.getText(self, "Delete Project - Step 2/2", 
                                          f"To confirm permanent deletion, type the project name exactly:\n\n'{project_data['name']}'", 
                                          QLineEdit.Normal, "")
            
            if ok and text == project_data["name"]:
                self.hub.delete_project(project_data["id"])
                self.refresh()
            elif ok:
                QMessageBox.warning(self, "Deletetion Cancelled", "Project name did not match. Deletion aborted.")

    def show_project_dialog(self, project_data=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Project Settings")
        dialog.setMinimumWidth(350)
        layout = QFormLayout(dialog)
        
        name_edit = QLineEdit(project_data["name"] if project_data else "")
        layout.addRow("Project Name:", name_edit)
        
        fps_spin = QSpinBox()
        fps_spin.setRange(1, 120)
        fps_spin.setValue(project_data.get("fps", 24) if project_data else 24)
        layout.addRow("FPS:", fps_spin)
        
        color_combo = QComboBox()
        color_combo.addItems(["ACES 1.3", "ACES 2.0", "Legacy sRGB", "Custom"])
        if project_data:
            color_combo.setCurrentText(project_data.get("color_management", "ACES 1.3"))
        layout.addRow("Color Management:", color_combo)

        thumb_path = [project_data["thumbnail"] if project_data else ""]
        thumb_btn = QPushButton("Select Thumbnail")
        if thumb_path[0]: thumb_btn.setText(os.path.basename(thumb_path[0]))
        
        def pick_thumb():
            p, _ = QFileDialog.getOpenFileName(self, "Select Thumbnail", "", "Images (*.png *.jpg *.jpeg)")
            if p: 
                thumb_path[0] = p
                thumb_btn.setText(os.path.basename(p))
        thumb_btn.clicked.connect(pick_thumb)
        layout.addRow("Thumbnail:", thumb_btn)
        
        save_btn = QPushButton("SAVE" if project_data else "CREATE PROJECT")
        layout.addRow(save_btn)
        
        def do_save():
            if not name_edit.text().strip():
                QMessageBox.warning(dialog, "Required Field", "Please enter a Project Name.")
                return

            fps_val = fps_spin.value()
            color_val = color_combo.currentText()
            
            if project_data:
                self.hub.update_project(project_data["id"], 
                    name=name_edit.text(), 
                    thumbnail=thumb_path[0],
                    fps=fps_val,
                    color_management=color_val
                )
            else:
                root = self.auth.settings.get("project_root")
                if root:
                    self.hub.create_project(name_edit.text(), root, thumbnail=thumb_path[0], fps=fps_val, color_management=color_val)
                else:
                    return # Should not happen with new validation
            dialog.accept()
            self.refresh()
        save_btn.clicked.connect(do_save)
        dialog.exec()

