import os
import json
import uuid
from datetime import datetime

class HubManager:
    def __init__(self, system_root):
        self.system_root = system_root
        self.project_root = None
        self.projects = []

    def set_project_root(self, root_path):
        """Sets the active storage root and loads the project index from it."""
        if not root_path or not os.path.exists(root_path):
            self.project_root = None
            self.projects = []
            return False
            
        self.project_root = os.path.normpath(root_path)
        self.load_projects()
        return True

    @property
    def index_file(self):
        if not self.project_root:
            return None
        return os.path.join(self.project_root, "project_index.json")

    def load_projects(self):
        if self.index_file and os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r") as f:
                    self.projects = json.load(f)
                
                # Cross-platform fix: if project path doesn't exist, try to find it under current root
                changed = False
                for p in self.projects:
                    old_path = p.get("path", "")
                    if old_path and not os.path.exists(old_path):
                        # Extract folder name and join with current project_root
                        folder_name = os.path.basename(old_path.replace("\\", "/"))
                        new_path = os.path.normpath(os.path.join(self.project_root, folder_name))
                        if os.path.exists(new_path):
                            p["path"] = new_path
                            changed = True
                if changed:
                    self.save_projects()
            except Exception as e:
                print(f"CGPipeline Error: Failed to load index from {self.index_file}: {e}")
                self.projects = []
        else:
            self.projects = []

    def save_projects(self):
        if not self.index_file:
            return
        try:
            with open(self.index_file, "w") as f:
                json.dump(self.projects, f, indent=4)
        except Exception as e:
            print(f"CGPipeline Error: Failed to save index to {self.index_file}: {e}")

    def create_project(self, name, root_dir, thumbnail="", fps=24, color_management="ACES 1.3"):
        # Use the provided root_dir if different from self.project_root, 
        # but usually they should match in this new architecture.
        active_root = root_dir if root_dir else self.project_root
        if not active_root:
            return None

        project_id = str(uuid.uuid4())
        project_folder = name.replace(" ", "_")
        project_path = os.path.normpath(os.path.join(active_root, project_folder))
        
        if not os.path.exists(project_path):
            os.makedirs(project_path)

        project = {
            "id": project_id,
            "name": name,
            "path": project_path,
            "thumbnail": thumbnail,
            "fps": fps,
            "color_management": color_management,
            "created_at": datetime.now().isoformat()
        }
        
        # Reload current projects in case index file was modified externally
        self.load_projects()
        self.projects.append(project)
        self.save_projects()
        return project

    def update_project(self, project_id, **kwargs):
        self.load_projects()
        for p in self.projects:
            if p["id"] == project_id:
                p.update(kwargs)
                self.save_projects()
                return True
        return False

    def delete_project(self, project_id):
        self.load_projects()
        target_project = next((p for p in self.projects if p["id"] == project_id), None)
        if target_project:
            project_path = target_project.get("path")
            if project_path and os.path.exists(project_path):
                try:
                    import shutil
                    shutil.rmtree(project_path)
                except Exception as e:
                    print(f"CGPipeline Error: Failed to remove project folder: {e}")
            
            self.projects = [p for p in self.projects if p["id"] != project_id]
            self.save_projects()
            return True
        return False

