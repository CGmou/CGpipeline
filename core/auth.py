import os
import json
import hashlib

class AuthManager:
    def __init__(self, system_root):
        self.system_root = system_root
        self.users_file = os.path.join(system_root, "users.json")
        self.settings_file = os.path.join(system_root, "settings.json")
        self.users = []
        self.current_user = None
        self.load_users()
        self.settings = self.load_settings()

    def load_users(self):
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, "r") as f:
                    self.users = json.load(f)
                    changed = False
                    for u in self.users:
                        if "password" not in u:
                            u["password"] = "123"; u["projects"] = []
                            changed = True
                    if changed: self.save_users()
            except Exception as e:
                print("Error loading users: " + str(e))
                self.users = []
        else:
            self.users = [
                {"username": "admin", "role": "admin", "password": "123"},
                {"username": "artist", "role": "artist", "password": "123", "projects": []}
            ]
            self.save_users()

    def save_users(self):
        try:
            with open(self.users_file, "w") as f:
                json.dump(self.users, f, indent=4)
        except Exception as e:
            print("Error saving users: " + str(e))

    def login(self, username, password, remember=False):
        for user in self.users:
            if user["username"] == username and user["password"] == password:
                self.current_user = user
                self.save_settings(username, password if remember else "", remember)
                return user
        return None

    def add_user(self, username, role, password):
        if any(u["username"] == username for u in self.users):
            return False
        self.users.append({"username": username, "role": role, "password": password, "projects": []})
        self.save_users()
        return True

    def update_user(self, username, **kwargs):
        for user in self.users:
            if user["username"] == username:
                user.update(kwargs)
                self.save_users()
                return True
        return False

    def delete_user(self, username):
        if username == "admin": return False
        self.users = [u for u in self.users if u["username"] != username]
        self.save_users()
        return True

    def logout(self):
        self.current_user = None

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    settings = json.load(f)
                    
                    # Cross-platform fix for DCC paths
                    if "dcc_paths" in settings:
                        import platform
                        is_windows = platform.system() == "Windows"
                        for dcc, path in settings["dcc_paths"].items():
                            if path:
                                # If it looks like a macOS path but we are on Windows
                                if is_windows and path.startswith("/") and not path.startswith("\\\\"):
                                    settings["dcc_paths"][dcc] = "" # Reset invalid path
                                # If it looks like a Windows path but we are on macOS
                                elif not is_windows and ":" in path:
                                    settings["dcc_paths"][dcc] = "" # Reset invalid path
                    return settings
            except: pass
        return {"last_user": "", "last_pass": "", "remember": False, "project_root": "", "dcc_paths": {"Maya": "", "Blender": "", "Houdini": ""}}

    def save_settings(self, username, password, remember):
        try:
            self.settings.update({
                "last_user": username,
                "last_pass": password,
                "remember": remember
            })
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except: pass

    def save_active_project(self, project_path):
        try:
            self.settings["active_project_path"] = project_path
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except: pass

    def is_admin(self):
        return self.current_user and self.current_user.get("role") == "admin"

