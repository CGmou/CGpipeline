import sys
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget
from core.auth import AuthManager
from core.hub import HubManager
from ui.login_view import LoginView
from ui.project_hub import ProjectHubView
from ui.workspace_view import WorkspaceView
from ui.settings_dialog import ProjectRootDialog, DCCPathsDialog

class CGPipelineApp(QMainWindow):
    def __init__(self, system_root):
        super().__init__()
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
    
    # Save current app path for DCCs to find
    window.auth.settings["app_main_path"] = os.path.abspath(__file__)
    window.auth.save_settings(window.auth.settings.get("last_user", ""), "", window.auth.settings.get("remember", False))
    
    window.show()
    sys.exit(app.exec())

