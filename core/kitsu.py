"""Kitsu (CGWire) integration for CGPipeline.

Wraps the official `gazu` client. Lets the user connect to a Kitsu server and
import its projects (assets, shots, and their tasks) into CGPipeline's local
project/registry structure.

`gazu` is imported lazily so the app still runs if it isn't installed; call
KitsuManager.available() to check, and tell the user to `pip install gazu`.
"""

import os

from .registry import TaskRegistry


# Kitsu asset type name (lowercased) -> CGPipeline asset sub-category.
ASSET_TYPE_MAP = {
    "character": "Char", "characters": "Char", "char": "Char",
    "prop": "Props", "props": "Props",
    "environment": "Sets", "environments": "Sets", "set": "Sets",
    "sets": "Sets", "location": "Sets", "locations": "Sets",
    "vehicle": "Vehicles", "vehicles": "Vehicles",
}

# Kitsu task type name (lowercased) -> CGPipeline task type.
TASK_TYPE_MAP = {
    "modeling": "Model", "model": "Model", "modelling": "Model",
    "texture": "Texture", "texturing": "Texture", "textures": "Texture",
    "shading": "Lookdev", "lookdev": "Lookdev", "look dev": "Lookdev",
    "look development": "Lookdev", "surfacing": "Lookdev",
    "rigging": "Rig", "rig": "Rig", "setup": "Rig",
    "animation": "Animation", "anim": "Animation",
    "layout": "Layout",
    "blocking": "Blocking",
    "lighting": "Lighting", "light": "Lighting", "lgt": "Lighting",
    "compositing": "Comp", "comp": "Comp", "composite": "Comp",
    "fx": "FX", "effects": "FX",
    "cfx": "CFX", "cloth": "CFX", "hair": "CFX", "groom": "CFX",
    "char fx": "CFX", "character fx": "CFX",
    "setdress": "Setdress", "set dressing": "Setdress",
    "set dress": "Setdress", "dressing": "Setdress",
    "assembly": "Assembly",
}

# Kitsu task status name/short-name (lowercased) -> CGPipeline status.
STATUS_MAP = {
    "todo": "Ready", "ready to start": "Ready", "ready": "Ready",
    "wip": "In Progress", "work in progress": "In Progress", "in progress": "In Progress",
    "wfa": "Pending Review", "waiting for approval": "Pending Review",
    "pending review": "Pending Review", "pending": "Pending Review",
    "done": "Approved", "approved": "Approved", "final": "Approved", "ok": "Approved",
    "retake": "In Progress", "rejected": "In Progress",
}


def _map_asset_type(name):
    return ASSET_TYPE_MAP.get((name or "").strip().lower(), "Props")


def _map_task_type(name):
    name = (name or "").strip()
    return TASK_TYPE_MAP.get(name.lower(), name.title() if name else "Model")


def _map_status(*names):
    for n in names:
        hit = STATUS_MAP.get((n or "").strip().lower())
        if hit:
            return hit
    return "Ready"


def _normalize_host(host):
    """Kitsu's API base usually ends in /api. Accept either the web URL or the
    API URL and normalise to the API URL gazu expects."""
    host = (host or "").strip().rstrip("/")
    if not host:
        return host
    if not host.endswith("/api"):
        host = host + "/api"
    return host


class KitsuManager:
    def __init__(self):
        self.connected = False
        self.host = ""
        self.email = ""
        self.user_name = ""

    @staticmethod
    def available():
        """True if the gazu client is importable."""
        try:
            import gazu  # noqa: F401
            return True
        except Exception:
            return False

    def connect(self, host, email, password):
        """Authenticate against a Kitsu server. Returns (ok, message)."""
        try:
            import gazu
        except Exception:
            return False, "The 'gazu' package is not installed. Run: pip install gazu"

        api_host = _normalize_host(host)
        if not api_host:
            return False, "Please enter a Kitsu host URL."
        try:
            gazu.set_host(api_host)
            result = gazu.log_in(email, password)
        except Exception as e:
            self.connected = False
            return False, f"Login failed: {e}"

        self.connected = True
        self.host = api_host
        self.email = email

        # Resolve a friendly display name for the connected user.
        self.user_name = email
        try:
            user = (result or {}).get("user") or {}
            if not user:
                user = gazu.client.get_current_user() or {}
            full = user.get("full_name") or (
                (user.get("first_name", "") + " " + user.get("last_name", "")).strip()
            )
            if full:
                self.user_name = full
        except Exception:
            pass

        return True, f"Connected to {api_host} as {self.user_name}"

    def disconnect(self):
        """Log out and clear connection state."""
        try:
            import gazu
            gazu.log_out()
        except Exception:
            pass
        self.connected = False
        self.user_name = ""

    def list_projects(self):
        """Return Kitsu project dicts. Open projects first, falling back to all."""
        import gazu
        try:
            projects = gazu.project.all_open_projects()
        except Exception:
            projects = []
        if not projects:
            try:
                projects = gazu.project.all_projects()
            except Exception:
                projects = []
        return projects or []

    # ---- import ----
    def import_project(self, kitsu_project, hub, root_dir, current_user="", progress=None):
        """Create (or reuse) a CGPipeline project from a Kitsu project and import
        its assets, shots, and tasks. Returns a summary dict."""
        import gazu

        def report(msg, cur=0, total=0):
            if progress:
                try:
                    progress(msg, cur, total)
                except Exception:
                    pass

        name = kitsu_project.get("name", "Kitsu Project")
        try:
            fps = int(float(kitsu_project.get("fps") or 24))
        except (TypeError, ValueError):
            fps = 24

        # Create or reuse the local project.
        hub.set_project_root(root_dir)
        project = next((p for p in hub.projects if p.get("name") == name), None)
        if not project:
            project = hub.create_project(name, root_dir, fps=fps)
        if not project:
            return {"ok": False, "error": "Could not create local project."}

        registry = TaskRegistry(project["path"])
        if current_user:
            registry.current_user = current_user

        summary = {
            "ok": True, "project": name, "path": project["path"],
            "assets": 0, "shots": 0, "tasks": 0, "skipped": 0,
        }

        # Lookup maps (resolve ids -> names when the task dict lacks them).
        try:
            tt_map = {t["id"]: t["name"] for t in gazu.task.all_task_types_for_project(kitsu_project)}
        except Exception:
            tt_map = {}
        try:
            ts_map = {s["id"]: s.get("name", "") for s in gazu.task.all_task_statuses()}
        except Exception:
            ts_map = {}

        def import_tasks(entity, entity_name, category, sub_category, frame_in=None, frame_out=None):
            try:
                if category == "Assets":
                    tasks = gazu.task.all_tasks_for_asset(entity)
                else:
                    tasks = gazu.task.all_tasks_for_shot(entity)
            except Exception:
                tasks = []
            for tk in tasks:
                ttype_raw = tk.get("task_type_name") or tt_map.get(tk.get("task_type_id"), "")
                if not ttype_raw:
                    summary["skipped"] += 1
                    continue
                ctype = _map_task_type(ttype_raw)
                status = _map_status(
                    tk.get("task_status_short_name"),
                    tk.get("task_status_name"),
                    ts_map.get(tk.get("task_status_id")),
                )
                created = registry.add_task(entity_name, category, sub_category, ctype)
                if created is None:
                    continue  # already exists
                updates = {"status": status}
                if frame_in is not None and frame_out is not None:
                    updates["frame_start"], updates["frame_end"] = frame_in, frame_out
                registry.update_task(created["id"], **updates)
                summary["tasks"] += 1

        # --- Assets ---
        try:
            atypes = {t["id"]: t["name"] for t in gazu.asset.all_asset_types_for_project(kitsu_project)}
        except Exception:
            atypes = {}
        try:
            assets = gazu.asset.all_assets_for_project(kitsu_project)
        except Exception:
            assets = []
        for i, a in enumerate(assets):
            aname = a.get("name", "Asset")
            report(f"Asset: {aname}", i + 1, len(assets))
            sub = _map_asset_type(a.get("asset_type_name") or atypes.get(a.get("entity_type_id")))
            import_tasks(a, aname, "Assets", sub)
            summary["assets"] += 1

        # --- Shots ---
        try:
            shots = gazu.shot.all_shots_for_project(kitsu_project)
        except Exception:
            shots = []
        for i, s in enumerate(shots):
            sname = s.get("name", "Shot")
            report(f"Shot: {sname}", i + 1, len(shots))
            data = s.get("data") or {}
            fin = fout = None
            try:
                if data.get("frame_in") is not None and data.get("frame_out") is not None:
                    fin, fout = int(data["frame_in"]), int(data["frame_out"])
                elif s.get("nb_frames"):
                    fin, fout = 1001, 1001 + int(s["nb_frames"]) - 1
            except (TypeError, ValueError):
                fin = fout = None
            import_tasks(s, sname, "Shots", "", frame_in=fin, frame_out=fout)
            summary["shots"] += 1

        registry.save()
        return summary

    def import_all_projects(self, hub, root_dir, current_user="", progress=None):
        """Sync every Kitsu project into the local hub (one-way: Kitsu -> pipeline).
        Returns an aggregate summary."""
        projects = self.list_projects()
        totals = {
            "ok": True, "projects": 0, "assets": 0, "shots": 0,
            "tasks": 0, "skipped": 0, "names": [],
        }
        n = len(projects)
        for i, p in enumerate(projects):
            if progress:
                try:
                    progress(p.get("name", ""), i + 1, n)
                except Exception:
                    pass
            s = self.import_project(p, hub, root_dir, current_user=current_user)
            if s.get("ok"):
                totals["projects"] += 1
                totals["assets"] += s.get("assets", 0)
                totals["shots"] += s.get("shots", 0)
                totals["tasks"] += s.get("tasks", 0)
                totals["skipped"] += s.get("skipped", 0)
                totals["names"].append(s.get("project", ""))
        return totals
