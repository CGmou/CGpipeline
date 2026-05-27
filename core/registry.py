import os
import json
import uuid
import shutil
from datetime import datetime
from .folder_template import create_asset_structure, create_shot_structure, create_project_base
from .utils import is_safe_subpath

class TaskRegistry:
    def __init__(self, root_path):
        self.root_path = os.path.normpath(root_path)
        self.registry_file = os.path.join(self.root_path, "registry.json")
        self.data = {
            "project_name": "New Project",
            "color_management": "ACES 1.3",
            "tasks": []
        }
        # Per-session, not persisted to JSON. Set by the workspace view after login.
        self.current_user = "Unknown Artist"
        self.load()

    def load(self):
        if os.path.exists(self.registry_file):
            try:
                with open(self.registry_file, "r") as f:
                    self.data.update(json.load(f))
                needs_save = False

                # Legacy cleanup: older registries persisted current_user, which leaked
                # across logins. Strip it from data so it never gets re-saved.
                if "current_user" in self.data:
                    self.data.pop("current_user", None)
                    needs_save = True

                # Ensure color_management exists
                if "color_management" not in self.data:
                    self.data["color_management"] = "ACES 1.3"; needs_save = True

                for task in self.data["tasks"]:
                    if "category" not in task:
                        task["category"] = "Assets"; needs_save = True
                    if "sub_category" not in task:
                        task["sub_category"] = "Legacy"; needs_save = True
                    if "priority" not in task:
                        task["priority"] = "Normal"; needs_save = True
                    
                    # Cross-platform fix: resolve absolute paths from other systems
                    old_path = task.get("path", "")
                    if old_path and not os.path.exists(old_path):
                        parts = old_path.replace("\\", "/").split("/")
                        for marker in ["Assets", "Shots"]:
                            if marker in parts:
                                idx = parts.index(marker)
                                rel_path = os.path.join(*parts[idx:])
                                new_path = os.path.normpath(os.path.join(self.root_path, rel_path))
                                if os.path.exists(new_path):
                                    task["path"] = new_path
                                    needs_save = True
                                break
                if needs_save: self.save()
            except Exception as e:
                print("Error loading registry: " + str(e))
        else:
            create_project_base(self.root_path)
            self.save()

    @property
    def tasks(self): return self.data.get("tasks", [])
    @property
    def project_name(self): return self.data.get("project_name", "Unknown Project")

    def save(self):
        if not os.path.exists(self.root_path): os.makedirs(self.root_path)
        try:
            with open(self.registry_file, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print("Error saving registry: " + str(e))

    def task_exists(self, name, task_type):
        for t in self.tasks:
            if t["name"] == name and t["type"] == task_type: return True
        return False

    def add_task(self, name, category, sub_category, task_type, thumbnail=""):
        if self.task_exists(name, task_type): return None
        task_id = str(uuid.uuid4())
        clean_name = name.replace(" ", "_")
        entity_rel_path = ""
        if category == "Assets":
            entity_rel_path = os.path.join("Assets", sub_category, clean_name)
        else:
            entity_rel_path = os.path.join("Shots", clean_name)
        entity_full_path = os.path.normpath(os.path.join(self.root_path, entity_rel_path))

        if category == "Assets": create_asset_structure(entity_full_path)
        else: create_shot_structure(entity_full_path)

        dept_map = {
            "Model": "Model/_wip", "Texture": "Textures/_wip", "Lookdev": "Lookdev/_wip",
            "Rig": "Rig/_wip", "Animation": "Anim", "Blocking": "Blocking",
            "Lighting": "Lgt", "FX": "Vfx", "Comp": "Comp",
            "Layout": "Layout", "CFX": "Cfx/_wip", "Assembly": "Assembly", "Setdress": "Layout"
        }
        dept_sub = dept_map.get(task_type, "")
        task_path = os.path.normpath(os.path.join(entity_full_path, dept_sub))
        os.makedirs(task_path, exist_ok=True)

        task = {
            "id": task_id, "name": name, "category": category, "sub_category": sub_category,
            "type": task_type, "path": task_path, "thumbnail": thumbnail,
            "status": "Ready", "priority": "Normal", "assigned_to": self.current_user,
            "created_at": datetime.now().isoformat()
        }
        self.data["tasks"].append(task)
        self.save()
        return task

    def update_task(self, task_id, **kwargs):
        for task in self.data["tasks"]:
            if task["id"] == task_id:
                task.update(kwargs)
                task["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                self.save()
                return True
        return False

    def delete_task(self, task_id):
        target_task = next((t for t in self.data["tasks"] if t["id"] == task_id), None)
        if not target_task: return False
        
        task_path = target_task.get("path")
        entity_name = target_task.get("name")
        category = target_task.get("category")
        
        # Remove from registry first
        self.data["tasks"] = [t for t in self.data["tasks"] if t["id"] != task_id]
        
        # Check if any other tasks for this entity still exist
        remaining = [t for t in self.data["tasks"] if t.get("name") == entity_name]
        
        try:
            if not remaining:
                # If no tasks left, delete the entire entity folder
                if category == "Assets":
                    # task_path is .../Asset/Model/_wip -> entity_root is .../Asset
                    entity_root = os.path.dirname(os.path.dirname(task_path))
                else:
                    # task_path is .../Shot/Lighting -> entity_root is .../Shot
                    entity_root = os.path.dirname(task_path)

                if not is_safe_subpath(entity_root, self.root_path):
                    print(f"CGPipeline Error: Refusing to delete entity folder outside project root: {entity_root}")
                elif os.path.exists(entity_root):
                    shutil.rmtree(entity_root)
                    print(f"CGPipeline: Deleted entity folder: {entity_root}")
            else:
                # Just delete the specific task wip folder
                if not is_safe_subpath(task_path, self.root_path):
                    print(f"CGPipeline Error: Refusing to delete task folder outside project root: {task_path}")
                elif os.path.exists(task_path):
                    shutil.rmtree(task_path)
                    print(f"CGPipeline: Deleted task folder: {task_path}")
        except Exception as e:
            print(f"CGPipeline Error: Failed to remove task folders: {e}")
            
        self.save()
        return True

