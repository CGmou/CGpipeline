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

def get_system_root():
    home_dir = os.path.expanduser("~")
    system_root = os.path.join(home_dir, "Documents", "cgpipeline_system")
    if not os.path.exists(system_root):
        os.makedirs(system_root)
    return system_root

def bring_to_front(pid):
    """Attempts to bring the existing process window to the front."""
    system = platform.system()
    if system == "Darwin":
        try:
            script = f'tell application "System Events" to set frontmost of every process whose unix id is {pid} to true'
            subprocess.run(["osascript", "-e", script])
        except Exception as e:
            print(f"Failed to focus existing window on macOS: {e}")
    elif system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            # Define constants
            SW_RESTORE = 9
            
            def enum_window_callback(hwnd, lparam):
                window_pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                if window_pid.value == lparam:
                    # If we find ANY window belonging to this PID that is visible
                    if ctypes.windll.user32.IsWindowVisible(hwnd):
                        # Attempt to restore and bring to top
                        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        # We don't return False here because a process might have multiple windows (e.g. splash)
                        # but we want to find the "best" one. Usually the first visible one is good.
                        return False 
                return True

            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            callback = callback_type(enum_window_callback)
            ctypes.windll.user32.EnumWindows(callback, pid)
        except Exception as e:
            print(f"Failed to focus existing window on Windows: {e}")

def ensure_single_instance(system_root):
    """
    Ensures only one instance of the app is running.
    Uses a Named Mutex on Windows and a Lock File on macOS.
    """
    lock_file = os.path.join(system_root, "app.lock")
    
    if platform.system() == "Windows":
        try:
            import ctypes
            # Unique name for the mutex
            mutex_name = "Global\\CGPipeline_SingleInstance_Mutex"
            # CreateMutexW will return a handle even if it already exists
            kernel32 = ctypes.windll.kernel32
            # Handle to the mutex (we must keep this alive for the duration of the app)
            # We store it on the module level to prevent garbage collection
            global _app_mutex
            _app_mutex = kernel32.CreateMutexW(None, False, mutex_name)
            last_error = kernel32.GetLastError()
            
            # ERROR_ALREADY_EXISTS = 183
            if last_error == 183:
                # Already running! Try to find the PID from the lock file to bring it to front
                if os.path.exists(lock_file):
                    with open(lock_file, "r") as f:
                        try:
                            pid = int(f.read().strip())
                            bring_to_front(pid)
                        except: pass
                return False # Signal to exit
            
            # We are the first instance. Write our PID for others to find.
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
            return True
            
        except Exception as e:
            print(f"Windows single instance check error: {e}")
            return True # Fallback to allowing launch
            
    else:
        # macOS / Linux logic (Standard Lock File)
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r") as f:
                    pid = int(f.read().strip())
                if pid != os.getpid():
                    try:
                        os.kill(pid, 0)
                        bring_to_front(pid)
                        return False
                    except (OSError, ProcessLookupError):
                        pass
            except: pass
            
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
        return True

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
        # One shared Kitsu session for the whole app so connection state is
        # consistent between the Kitsu dialog and the project hub.
        from core.kitsu import KitsuManager
        self.kitsu = KitsuManager()

        self.setup_menubar()
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        settings = self.auth.settings
        # Honour the LAST login method so a Kitsu session resumes as the Kitsu user
        # (not the remembered local account).
        if settings.get("last_login_method") == "kitsu":
            if self._try_kitsu_autologin():
                self.enter_app()
            else:
                # Don't silently fall back to a different local account.
                self.show_login()
        elif settings.get("remember") and settings.get("last_user") and settings.get("last_pass"):
            user = self.auth.login(settings["last_user"], settings["last_pass"], remember=True)
            if user:
                self.enter_app()
            else:
                self.show_login()
        else:
            self.show_login()

    def _try_kitsu_autologin(self):
        """Resume a remembered Kitsu session. Returns True on success."""
        s = self.auth.settings
        host, email, pw = s.get("kitsu_host", ""), s.get("kitsu_email", ""), s.get("kitsu_pass", "")
        if not (s.get("kitsu_remember") and host and email and pw):
            return False
        try:
            ok, _ = self.kitsu.connect(host, email, pw)
        except Exception:
            ok = False
        if not ok:
            return False
        self.auth.login_with_kitsu(self.kitsu.email, self.kitsu.user_name, role=self.kitsu.pipeline_role())
        return True

    def enter_app(self):
        """After login, go straight to the active project's workspace when launched
        from a DCC (env carries CGP_REGISTRY_PATH); otherwise show the project hub."""
        self.show_hub()
        dcc_reg = os.environ.get("CGP_REGISTRY_PATH", "")
        in_dcc = os.environ.get("CGP_IN_DCC", "")
        if in_dcc and dcc_reg and os.path.exists(dcc_reg):
            project_path = os.path.normpath(os.path.dirname(dcc_reg))
            project = None
            try:
                for p in self.hub.projects:
                    if os.path.normpath(p.get("path", "")) == project_path:
                        project = p
                        break
            except Exception:
                project = None
            if not project:
                project = {"path": project_path, "name": os.path.basename(project_path)}
            self.on_project_selected(project)

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

        kitsu_menu = menubar.addMenu("Kitsu")
        kitsu_action = kitsu_menu.addAction("Production Tracker...")
        kitsu_action.triggered.connect(self.on_open_kitsu)

    def on_open_root_settings(self):
        dialog = ProjectRootDialog(self.auth, self)
        dialog.exec()

    def on_open_dcc_settings(self):
        dialog = DCCPathsDialog(self.auth, self)
        dialog.exec()

    def on_open_kitsu(self):
        # Imported lazily so the app starts even if gazu isn't installed.
        from ui.kitsu_dialog import KitsuDialog
        dialog = KitsuDialog(self.auth, self.hub, self.kitsu, self)
        dialog.imported.connect(self.on_kitsu_imported)
        dialog.exec()

    def on_kitsu_imported(self):
        # Refresh the hub so newly imported projects appear immediately.
        if hasattr(self, "hub_view"):
            try:
                self.hub_view.refresh()
            except Exception:
                pass

    def show_login(self):
        # End any Kitsu session on logout so the next login starts fresh and the
        # Kitsu dialog doesn't keep showing the previous Kitsu user.
        try:
            self.kitsu.disconnect()
        except Exception:
            pass
        self.login_view = LoginView(self.auth, self.kitsu)
        self.login_view.login_success.connect(self.on_login_success)
        self.stack.addWidget(self.login_view)
        self.stack.setCurrentWidget(self.login_view)

    def on_login_success(self, user_data):
        self.enter_app()

    def show_hub(self):
        self.hub_view = ProjectHubView(self.hub, self.auth, kitsu=self.kitsu)
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
    system_root = get_system_root()

    # CRITICAL: Check single instance BEFORE creating QApplication or any Window
    # This prevents "ghost" windows from appearing when launching a second instance.
    if not ensure_single_instance(system_root):
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = CGPipelineApp(system_root)
    
    # Save current app path for DCCs to find
    window.auth.settings["app_main_path"] = os.path.abspath(__file__)
    window.auth.save_settings(window.auth.settings.get("last_user", ""), window.auth.settings.get("last_pass", ""), window.auth.settings.get("remember", False))
    
    window.show()
    sys.exit(app.exec())

