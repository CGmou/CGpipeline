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

# Reverse maps for uploading CGPipeline -> Kitsu.
SUBCAT_TO_KITSU_ASSET_TYPE = {
    "Char": "Character", "Props": "Prop", "Sets": "Environment", "Vehicles": "Vehicle",
}
CGP_TO_KITSU_TASK_TYPE = {
    "Model": "Modeling", "Texture": "Texture", "Lookdev": "Lookdev", "Rig": "Rigging",
    "Animation": "Animation", "Layout": "Layout", "Blocking": "Blocking",
    "Lighting": "Lighting", "Comp": "Compositing", "FX": "FX", "CFX": "CFX",
    "Assembly": "Assembly", "Setdress": "Set Dressing",
}

# Kitsu task status name/short-name (lowercased) -> CGPipeline status.
# CGPipeline now uses the same Kitsu-aligned vocabulary, so this is mostly 1:1.
STATUS_MAP = {
    "todo": "Todo", "ready to start": "Todo", "ready": "Todo",
    "wip": "Work In Progress", "work in progress": "Work In Progress", "in progress": "Work In Progress",
    "wfa": "Waiting For Approval", "waiting for approval": "Waiting For Approval",
    "pending review": "Waiting For Approval", "pending": "Waiting For Approval",
    "done": "Done", "approved": "Done", "final": "Done", "ok": "Done",
    "retake": "Retake", "rejected": "Retake",
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
    return "Todo"


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

    def ensure_connected(self, auth):
        """Reconnect using saved credentials if not already connected.
        Returns (ok, message)."""
        if self.connected:
            return True, "Connected"
        s = auth.settings
        host = s.get("kitsu_host", "")
        email = s.get("kitsu_email", "")
        pw = s.get("kitsu_pass", "")
        if host and email and pw:
            return self.connect(host, email, pw)
        return False, "Not connected to Kitsu. Open Kitsu → Production Tracker and connect first."

    def _download_project_avatar(self, kproj, dest):
        import gazu
        if not kproj.get("has_avatar"):
            return None
        try:
            gazu.client.download_file(f"pictures/thumbnails/projects/{kproj['id']}.png", dest)
            return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None
        except Exception:
            return None

    def _download_entity_thumb(self, entity, dest):
        import gazu
        pfid = entity.get("preview_file_id")
        if not pfid:
            return None
        try:
            gazu.files.download_preview_file_thumbnail(pfid, dest)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                return dest
        except Exception:
            pass
        try:
            gazu.client.download_file(f"pictures/thumbnails/preview-files/{pfid}.png", dest)
            return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None
        except Exception:
            return None

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

        # Tag the local project as Kitsu-linked so the hub can mark it visually.
        try:
            hub.update_project(
                project["id"],
                kitsu_id=kitsu_project.get("id", ""),
                kitsu_host=self.host,
            )
            project["kitsu_id"] = kitsu_project.get("id", "")
        except Exception:
            pass

        registry = TaskRegistry(project["path"])
        if current_user:
            registry.current_user = current_user

        # Thumbnails: pull the project avatar + per-entity previews from Kitsu.
        thumbs_dir = os.path.join(project["path"], ".thumbnails")
        try:
            os.makedirs(thumbs_dir, exist_ok=True)
            pthumb = self._download_project_avatar(
                kitsu_project, os.path.join(thumbs_dir, "_project.png")
            )
            if pthumb:
                hub.update_project(project["id"], thumbnail=pthumb)
                project["thumbnail"] = pthumb
        except Exception:
            pass

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

        def import_tasks(entity, entity_name, category, sub_category, frame_in=None, frame_out=None, thumbnail=""):
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
                created = registry.add_task(entity_name, category, sub_category, ctype, thumbnail=thumbnail or "")
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
            thumb = self._download_entity_thumb(a, os.path.join(thumbs_dir, f"asset_{aname}.png")) or ""
            import_tasks(a, aname, "Assets", sub, thumbnail=thumb)
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
            thumb = self._download_entity_thumb(s, os.path.join(thumbs_dir, f"shot_{sname}.png")) or ""
            import_tasks(s, sname, "Shots", "", frame_in=fin, frame_out=fout, thumbnail=thumb)
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

    def upload_project_to_kitsu(self, local_project, hub, progress=None):
        """Push a local CGPipeline project up to Kitsu (one-way: pipeline -> Kitsu),
        creating the project plus its assets, shots, and tasks, then linking the
        local project to the new Kitsu project. Every Kitsu call is defensive so a
        permission/server hiccup on one entity doesn't abort the whole upload."""
        import gazu

        if not self.connected:
            return {"ok": False, "error": "Not connected to Kitsu."}

        name = local_project.get("name", "Project")
        kproj = None
        # Prefer a previously-known Kitsu id (active link, or one preserved by an
        # earlier "Unload from Kitsu") so re-upload re-links the same project.
        prior_id = local_project.get("kitsu_id") or \
            (local_project.get("kitsu_unlinked") or {}).get("kitsu_id")
        if prior_id:
            try:
                kproj = gazu.project.get_project(prior_id)
            except Exception:
                kproj = None
        if not kproj:
            try:
                kproj = gazu.project.get_project_by_name(name)
            except Exception:
                kproj = None
        if not kproj:
            try:
                kproj = gazu.project.new_project(name)
            except Exception as e:
                return {"ok": False, "error": f"Could not create project on Kitsu: {e}"}

        # Best-effort fps.
        fps = local_project.get("fps")
        if fps:
            try:
                kproj["fps"] = str(fps)
                gazu.project.update_project(kproj)
            except Exception:
                pass

        summary = {"ok": True, "project": name, "assets": 0, "shots": 0, "tasks": 0, "skipped": 0}

        registry = TaskRegistry(local_project["path"])
        # Group local tasks by entity (name + category + sub-category).
        entities = {}
        for t in registry.tasks:
            key = (t.get("name"), t.get("category"), t.get("sub_category", ""))
            entities.setdefault(key, []).append(t)

        at_cache, seq_cache, tt_cache = {}, {}, {}

        def get_task_type(cgp_type):
            kname = CGP_TO_KITSU_TASK_TYPE.get(cgp_type, cgp_type)
            if kname in tt_cache:
                return tt_cache[kname]
            tt = None
            try:
                tt = gazu.task.get_task_type_by_name(kname)
            except Exception:
                tt = None
            tt_cache[kname] = tt
            return tt

        items = list(entities.items())
        total = len(items)
        for i, ((ename, category, sub), tlist) in enumerate(items):
            if progress:
                try:
                    progress(ename, i + 1, total)
                except Exception:
                    pass

            kentity = None
            try:
                if category == "Assets":
                    at_name = SUBCAT_TO_KITSU_ASSET_TYPE.get(sub, "Prop")
                    at = at_cache.get(at_name)
                    if at is None:
                        try:
                            at = gazu.asset.get_asset_type_by_name(at_name)
                        except Exception:
                            at = None
                        if not at:
                            try:
                                at = gazu.asset.new_asset_type(at_name)
                            except Exception:
                                at = None
                        at_cache[at_name] = at
                    if at:
                        try:
                            kentity = gazu.asset.get_asset_by_name(kproj, ename)
                        except Exception:
                            kentity = None
                        if not kentity:
                            kentity = gazu.asset.new_asset(kproj, at, ename)
                        summary["assets"] += 1
                else:
                    seq_name = ename.split("_")[0] if "_" in ename else "seq01"
                    seq = seq_cache.get(seq_name)
                    if seq is None:
                        try:
                            seq = gazu.shot.get_sequence_by_name(kproj, seq_name)
                        except Exception:
                            seq = None
                        if not seq:
                            try:
                                seq = gazu.shot.new_sequence(kproj, seq_name)
                            except Exception:
                                seq = None
                        seq_cache[seq_name] = seq
                    if seq:
                        fin = fout = None
                        for t in tlist:
                            if t.get("frame_start") is not None and t.get("frame_end") is not None:
                                fin, fout = t["frame_start"], t["frame_end"]
                                break
                        try:
                            kentity = gazu.shot.get_shot_by_name(seq, ename)
                        except Exception:
                            kentity = None
                        if not kentity:
                            kwargs = {}
                            if fin is not None and fout is not None:
                                kwargs["frame_in"], kwargs["frame_out"] = fin, fout
                            kentity = gazu.shot.new_shot(kproj, seq, ename, **kwargs)
                        summary["shots"] += 1
            except Exception as e:
                print(f"CGPipeline Kitsu upload: entity '{ename}' failed: {e}")
                kentity = None

            if not kentity:
                continue

            for t in tlist:
                tt = get_task_type(t.get("type"))
                if not tt:
                    summary["skipped"] += 1
                    continue
                try:
                    gazu.task.new_task(kentity, tt)
                    summary["tasks"] += 1
                except Exception:
                    # Most likely the task already exists on this entity.
                    summary["skipped"] += 1

        # Link the local project to the new Kitsu project.
        try:
            hub.update_project(
                local_project["id"],
                kitsu_id=kproj.get("id", ""),
                kitsu_host=self.host,
            )
        except Exception:
            pass

        summary["kitsu_id"] = kproj.get("id", "")
        return summary
