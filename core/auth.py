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

    def login_with_kitsu(self, email, full_name, role="artist"):
        """Log in via Kitsu. Finds the local user linked to this Kitsu account
        (by kitsu_email, else by matching username), or creates one, links it,
        sets it as the current user, and returns it."""
        email_l = (email or "").lower()
        user = next((u for u in self.users if u.get("kitsu_email", "").lower() == email_l and email_l), None)
        if not user and full_name:
            user = next((u for u in self.users if u["username"].lower() == full_name.lower()), None)

        if not user:
            username = full_name or (email.split("@")[0] if email else "kitsu_user")
            base, i = username, 1
            while any(u["username"] == username for u in self.users):
                i += 1
                username = f"{base}{i}"
            user = {
                "username": username, "role": role, "password": "",
                "projects": [], "kitsu_email": email, "kitsu_name": full_name,
            }
            self.users.append(user)
        else:
            user["kitsu_email"] = email
            user["kitsu_name"] = full_name
            if role:
                user["role"] = role
        self.save_users()
        self.current_user = user
        return user

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
        default_settings = {
            "last_user": "",
            "last_pass": "",
            "remember": False,
            "project_root": "",
            "dcc_paths_win": {"Maya": "", "Blender": "", "Houdini": ""},
            "dcc_paths_mac": {"Maya": "", "Blender": "", "Houdini": ""},
            "kitsu_host": "",
            "kitsu_email": "",
            "kitsu_pass": "",
            "kitsu_remember": False,
        }
        
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    settings = json.load(f)
                    
                    # Migration: if old dcc_paths exists, move it to the correct platform
                    if "dcc_paths" in settings and "dcc_paths_win" not in settings:
                        import platform
                        if platform.system() == "Windows":
                            settings["dcc_paths_win"] = settings.pop("dcc_paths")
                            settings["dcc_paths_mac"] = default_settings["dcc_paths_mac"]
                        else:
                            settings["dcc_paths_mac"] = settings.pop("dcc_paths")
                            settings["dcc_paths_win"] = default_settings["dcc_paths_win"]
                    
                    # Ensure all keys exist
                    for k, v in default_settings.items():
                        if k not in settings:
                            settings[k] = v
                            
                    return settings
            except: pass
        return default_settings

    @property
    def dcc_paths(self):
        import platform
        if platform.system() == "Windows":
            return self.settings.get("dcc_paths_win", {})
        else:
            return self.settings.get("dcc_paths_mac", {})

    @dcc_paths.setter
    def dcc_paths(self, value):
        import platform
        if platform.system() == "Windows":
            self.settings["dcc_paths_win"] = value
        else:
            self.settings["dcc_paths_mac"] = value

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

    def save(self):
        """Persist the current settings dict as-is (used by feature dialogs that
        write their own keys, e.g. Kitsu)."""
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print("Error saving settings: " + str(e))

    def is_admin(self):
        return self.current_user and self.current_user.get("role") == "admin"

