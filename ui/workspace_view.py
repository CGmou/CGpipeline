from PySide6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget, QDialog, QHBoxLayout, QPushButton, QMessageBox, QLabel
from PySide6.QtCore import Signal, Qt
from core.registry import TaskRegistry
from ui.dashboard import Dashboard
from ui.project_sheet import ProjectSheet
from ui.header import Header
from ui.new_task_dialog import NewTaskDialog
from ui.modify_task_dialog import ModifyTaskDialog
from core.utils import build_work_filename, get_latest_version
from core.launcher import launch_dcc
from core.constants import DEFAULT_STATUS
import os

class WorkspaceView(QWidget):
    exit_requested = Signal()
    logout_requested = Signal()

    def __init__(self, project_path, auth_manager):
        super().__init__()
        self.project_path = project_path
        self.auth = auth_manager
        self.registry = TaskRegistry(project_path)
        # Per-session only — never persisted, so concurrent users on the same
        # project don't overwrite each other's "MY TASKS" filter.
        self.registry.current_user = self.auth.current_user["username"]
        self.show_thumbs = True
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = Header(self.registry)
        layout.addWidget(self.header)
        self.toolbar = QWidget()
        self.toolbar.setStyleSheet("background-color: #1A1A1A; border-bottom: 1px solid #222;")
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(30, 5, 30, 5)
        self.thumb_toggle = QPushButton("HIDE THUMBNAILS")
        self.thumb_toggle.setStyleSheet("color: #888; border: 1px solid #444; border-radius: 4px; padding: 5px 10px; font-size: 10px;")
        self.thumb_toggle.clicked.connect(self.toggle_thumbnails)
        toolbar_layout.addWidget(self.thumb_toggle)
        toolbar_layout.addStretch()
        layout.addWidget(self.toolbar)
        self.views = QStackedWidget()
        layout.addWidget(self.views)
        self.dashboard = Dashboard(self.registry)
        self.project_sheet = ProjectSheet(self.registry)
        self.views.addWidget(self.dashboard)
        self.views.addWidget(self.project_sheet)
        if not self.auth.is_admin(): self.dashboard.add_btn.hide()
        self.header.view_changed.connect(self.switch_view)
        self.header.back_to_hub.connect(self.exit_requested.emit)
        self.header.logout_requested.connect(self.logout_requested.emit)
        self.dashboard.add_btn.clicked.connect(self.on_new_task)
        self.dashboard.modify_requested.connect(self.on_modify_task)
        self.dashboard.start_work_requested.connect(self.on_start_new_work)
        self.dashboard.continue_work_requested.connect(self.on_continue_work)
        self.refresh_all()

    def toggle_thumbnails(self):
        self.show_thumbs = not self.show_thumbs
        self.thumb_toggle.setText("SHOW THUMBNAILS" if not self.show_thumbs else "HIDE THUMBNAILS")
        self.refresh_all()

    def refresh_all(self):
        is_admin = self.auth.is_admin()
        self.dashboard.refresh(is_admin=is_admin, show_thumbs=self.show_thumbs)
        self.project_sheet.refresh(is_admin=is_admin)

    def switch_view(self, view):
        if view == "desk": self.views.setCurrentWidget(self.dashboard)
        else: self.views.setCurrentWidget(self.project_sheet)
        self.refresh_all()

    def on_new_task(self):
        dialog = NewTaskDialog(self.auth, self)
        dialog.add_requested.connect(self.create_tasks_from_data)
        if dialog.exec(): self.create_tasks_from_data(dialog.get_data())

    def create_tasks_from_data(self, data):
        if data["name"] and data["types"]:
            for t_type in data["types"]:
                if self.registry.task_exists(data["name"], t_type): continue
                task = self.registry.add_task(data["name"], data["category"], data["sub_category"], t_type, thumbnail=data.get("thumbnail", ""))
                if task:
                    upd = {"assigned_to": data["assigned_to"]}
                    if data.get("frame_range"): upd["frame_start"], upd["frame_end"] = data["frame_range"]
                    self.registry.update_task(task["id"], **upd)
            self.refresh_all()

    def on_modify_task(self, task_id):
        main_task = next((t for t in self.registry.tasks if t["id"] == task_id), None)
        if not main_task: return
        entity_name = main_task["name"]
        def get_current_tasks(): return [t for t in self.registry.tasks if t["name"] == entity_name]
        dialog = ModifyTaskDialog(entity_name, get_current_tasks(), self.auth, self)
        def handle_delete_selected(selected_ids):
            if QMessageBox.question(dialog, "Confirm", "Delete selected tasks?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
                for tid in selected_ids: self.registry.delete_task(tid)
                self.refresh_all()
                new_tasks = get_current_tasks()
                if not new_tasks: dialog.accept()
                else: dialog.refresh_tasks(new_tasks)
        def handle_add_type():
            from PySide6.QtWidgets import QInputDialog
            all_types = ["Model", "Texture", "Lookdev", "Rig"] if main_task["category"] == "Assets" else ["Blocking", "Animation", "Layout", "Lighting", "FX", "CFX", "Comp", "Assembly", "Setdress"]
            available = [t for t in all_types if t not in [tk["type"] for tk in get_current_tasks()]]
            if not available: return
            val, ok = QInputDialog.getItem(dialog, "Add Task", "Select Task Type:", available, 0, False)
            if ok:
                nt = self.registry.add_task(entity_name, main_task["category"], main_task["sub_category"], val, thumbnail=main_task.get("thumbnail", ""))
                if main_task.get("frame_start"): self.registry.update_task(nt["id"], frame_start=main_task["frame_start"], frame_end=main_task["frame_end"])
                self.refresh_all(); dialog.refresh_tasks(get_current_tasks())

        def handle_delete_all():
            if QMessageBox.question(dialog, "Confirm", f"Delete ALL tasks for '{entity_name}'?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
                for t in get_current_tasks():
                    self.registry.delete_task(t["id"])
                self.refresh_all()
                dialog.accept()

        dialog.delete_selected_requested.connect(handle_delete_selected)
        dialog.add_type_requested.connect(handle_add_type)
        dialog.delete_all_requested.connect(handle_delete_all)
        if dialog.exec():
            data = dialog.get_data()
            for t_data in data["tasks"]:
                self.registry.update_task(t_data["id"], name=data["name"], thumbnail=data["thumbnail"], status=t_data["status"], priority=t_data["priority"], assigned_to=t_data["assigned_to"])
            self.refresh_all()

    def on_continue_work(self, task_obj):
        # Auto-detect DCC from latest file extension
        _, latest_file = get_latest_version(task_obj["path"])
        if not latest_file:
             # If no file found, this is actually a "Start New" situation
             self.show_dcc_selector(task_obj)
             return
             
        # Robust extension check
        ext = os.path.splitext(latest_file)[1].lower()
        
        if ext == ".blend":
            dcc_name = "Blender"
        elif ext in [".ma", ".mb"]:
            dcc_name = "Maya"
        elif ext in [".hip", ".hipnc", ".hiplc"]:
            dcc_name = "Houdini"
        else:
            # If extension is unknown, show selector
            self.show_dcc_selector(task_obj)
            return

        dcc_exe = self.auth.dcc_paths.get(dcc_name)
        reg_path = os.path.join(self.project_path, "registry.json")
        
        if not dcc_exe or not os.path.exists(dcc_exe):
            QMessageBox.warning(self, "DCC Path Missing", 
                                f"This task was created in {dcc_name}, but the path to {dcc_name} is not configured.\n\n"
                                "Please set it in Settings -> DCC Paths.")
            return

        success, err = launch_dcc(dcc_name, dcc_exe, task_obj, reg_path)
        if success:
            if task_obj["status"] == DEFAULT_STATUS:
                self.registry.update_task(task_obj["id"], status="Work In Progress")
            self.refresh_all()
        else: 
            QMessageBox.critical(self, "Launch Error", f"Failed to launch {dcc_name}:\n{err}")

    def on_start_new_work(self, task_obj):
        # Check if work already exists in this folder
        _, latest_file = get_latest_version(task_obj["path"])
        
        if latest_file:
            # If work exists, treat it as "Continue" to avoid asking for DCC again
            self.on_continue_work(task_obj)
        else:
            # ONLY ask for DCC if this is truly the first time
            self.show_dcc_selector(task_obj)

    def show_dcc_selector(self, task_obj):
        """Ask the user which DCC to use for a brand-new task (no existing version)."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("Select DCC")
        dialog.setMinimumWidth(320)
        dialog.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBB; }
            QPushButton {
                background-color: #2D2D2D; color: white; border: 1px solid #3D3D3D;
                padding: 14px 20px; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background-color: #0078D4; border-color: #0078D4; }
        """)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Choose a DCC for new task:\n"
            f"  {task_obj.get('name', '')}  —  {task_obj.get('type', '')}"
        ))

        row = QHBoxLayout()
        configured = self.auth.dcc_paths or {}
        for dcc in ("Blender", "Maya"):
            btn = QPushButton(dcc)
            if not configured.get(dcc):
                btn.setEnabled(False)
                btn.setToolTip(f"{dcc} path not set — Settings → DCC Paths")
            btn.clicked.connect(lambda checked=False, d=dcc: self.launch_task(d, task_obj, dialog))
            row.addWidget(btn)
        layout.addLayout(row)
        dialog.exec()

    def launch_task(self, dcc_name, task_obj, dialog=None):
        dcc_exe = self.auth.dcc_paths.get(dcc_name)
        reg_path = os.path.join(self.project_path, "registry.json")
        if not dcc_exe:
            target = dialog if dialog else self
            QMessageBox.warning(target, "Not Configured", "Path for " + dcc_name + " is not set in Settings.")
            return
        success, err = launch_dcc(dcc_name, dcc_exe, task_obj, reg_path)
        if success:
            if task_obj["status"] == DEFAULT_STATUS: self.registry.update_task(task_obj["id"], status="Work In Progress")
            self.refresh_all()
            if dialog: dialog.accept()
        else: 
            target = dialog if dialog else self
            QMessageBox.critical(target, "Error", err)

