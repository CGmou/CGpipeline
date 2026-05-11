import sys
import os
import json
import platform
import subprocess
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QMessageBox
from core.auth import AuthManager
from core.hub import HubManager
from ui.login_view import LoginView
from ui.project_hub import ProjectHubView
from ui.workspace_view import WorkspaceView
from ui.settings_dialog import ProjectRootDialog, DCCPathsDialog

class CGPipelineApp(QMainWindow):
    def __init__(self, system_root):
        super().__init__()
        self.system_root = system_root
        self.lock_file = os.path.join(system_root, "app.lock")
        
        self.setWindowTitle("CGPipeline")
        self.resize(1200, 850)
        self.setStyleSheet("QMainWindow { background-color: #121212; }")

        self.auth = AuthManager(system_root)
        self.hub = HubManager(system_root)

        self.setup_menubar()
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        settings = self.auth.settings
        if settings.get("remember") and settings.get("last_user") and settings.get("last_pass"):
            user = self.auth.login(settings["last_user"], settings["last_pass"], remember=True)
            if user:
                self.show_hub()
            else:
                self.show_login()
        else:
            self.show_login()

    def check_single_instance(self):
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r") as f:
                    pid = int(f.read().strip())
                if os.name == 'nt':
                    # Windows PID check
                    import ctypes
                    process_exists = ctypes.windll.kernel32.OpenProcess(1, False, pid) > 0
                else:
                    # Unix PID check
                    try:
                        os.kill(pid, 0)
                        process_exists = True
                    except OSError:
                        process_exists = False
                
                if process_exists:
                    self.bring_to_front(pid)
                    return False
            except: pass
            
        with open(self.lock_file, "w") as f:
            f.write(str(os.getpid()))
        return True

    def bring_to_front(self, pid):
        """Attempts to bring the existing process window to the front."""
        system = platform.system()
        if system == "Darwin":
            try:
                # Use AppleScript to focus the process by PID
                script = f'tell application "System Events" to set frontmost of every process whose unix id is {pid} to true'
                subprocess.run(["osascript", "-e", script])
            except Exception as e:
                print(f"Failed to focus existing window on macOS: {e}")
        elif system == "Windows":
            try:
                import ctypes
                def enum_window_callback(hwnd, lparam):
                    window_pid = ctypes.c_int()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                    if window_pid.value == lparam:
                        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        return False
                    return True

                callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                callback = callback_type(enum_window_callback)
                ctypes.windll.user32.EnumWindows(callback, pid)
            except Exception as e:
                print(f"Failed to focus existing window on Windows: {e}")

    def closeEvent(self, event):
        if hasattr(self, 'lock_file') and os.path.exists(self.lock_file):
            try: os.remove(self.lock_file)
            except: pass
        super().closeEvent(event)

    def setup_menubar(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar { background-color: #1A1A1A; color: #AAA; border-bottom: 1px solid #333; }
            QMenuBar::item:selected { background-color: #333; color: white; }
            QMenu { background-color: #1A1A1A; color: #AAA; border: 1px solid #333; }
            QMenu::item:selected { background-color: #0078D4; color: white; }
        """)
        settings_menu = menubar.addMenu("Settings")
        
        root_action = settings_menu.addAction("Project Root")
        root_action.triggered.connect(self.on_open_root_settings)
        
        paths_action = settings_menu.addAction("DCC Paths")
        paths_action.triggered.connect(self.on_open_dcc_settings)

    def on_open_root_settings(self):
        dialog = ProjectRootDialog(self.auth, self)
        dialog.exec()

    def on_open_dcc_settings(self):
        dialog = DCCPathsDialog(self.auth, self)
        dialog.exec()

    def show_login(self):
        self.login_view = LoginView(self.auth)
        self.login_view.login_success.connect(self.on_login_success)
        self.stack.addWidget(self.login_view)
        self.stack.setCurrentWidget(self.login_view)

    def on_login_success(self, user_data):
        self.show_hub()

    def show_hub(self):
        self.hub_view = ProjectHubView(self.hub, self.auth)
        self.hub_view.project_selected.connect(self.on_project_selected)
        self.stack.addWidget(self.hub_view)
        self.stack.setCurrentWidget(self.hub_view)

    def on_project_selected(self, project_data):
        self.auth.save_active_project(project_data["path"])
        self.workspace_view = WorkspaceView(project_data["path"], self.auth)
        
        # Sync color management from hub to registry if needed
        if "color_management" in project_data:
            self.workspace_view.registry.data["color_management"] = project_data["color_management"]
            self.workspace_view.registry.save()
            self.workspace_view.header.color_label.setText(f"[{project_data['color_management']}]")
            
        self.workspace_view.exit_requested.connect(self.show_hub)
        self.workspace_view.logout_requested.connect(self.show_login)
        self.stack.addWidget(self.workspace_view)
        self.stack.setCurrentWidget(self.workspace_view)

if __name__ == "__main__":
    # Cross-platform: Use home directory for system data
    home_dir = os.path.expanduser("~")
    system_root = os.path.join(home_dir, "Documents", "cgpipeline_system")
    
    if not os.path.exists(system_root):
        os.makedirs(system_root)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = CGPipelineApp(system_root)
    if not window.check_single_instance():
        sys.exit(0)
    
    # Save current app path for DCCs to find
    window.auth.settings["app_main_path"] = os.path.abspath(__file__)
    window.auth.save_settings(window.auth.settings.get("last_user", ""), window.auth.settings.get("last_pass", ""), window.auth.settings.get("remember", False))
    
    window.show()
    sys.exit(app.exec())

