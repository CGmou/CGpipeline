"""CGPipeline integration for Maya 2025+.

Mirrors core/dcc/blender/cgpipeline_blender.py:
  - Reads CGP_* env vars on startup, applies color management, loads plugins.
  - Polls maya_command.json so the dashboard can open tasks in this Maya session.
  - Adds a CGPipeline shelf and a dockable panel: Status, Publish, Assembly, Quick Tools.
  - Operations: Save / Version Up / Update Status / Publish (ABC/USD/FBX/MA) /
    Fix Missing Textures / Switch 2K-4K / Assembly scan / Reference Lookdev /
    Apply Caches (Alembic by hierarchy, USD via mayaUsdImport, FBX import).
"""

import os
import re
import json
import shutil
import subprocess

from maya import cmds, mel
from maya import OpenMayaUI

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import wrapInstance


SYSTEM_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "cgpipeline_system")
COMMAND_FILE = os.path.join(SYSTEM_ROOT, "maya_command.json")
SHELF_NAME = "CGPipeline"

TASK_ABBR = {
    "Model": "mdl", "Texture": "txt", "Lookdev": "lkdev", "Rig": "rig",
    "Animation": "anim", "Layout": "lo", "Blocking": "blk", "Lighting": "lgt",
    "Comp": "comp", "FX": "fx", "CFX": "cfx", "Assembly": "asb", "Setdress": "sd",
}


class PipelineState:
    """Session-level pipeline context. Populated from env vars; mutated by the UI."""
    task_id = ""
    entity = ""
    task_path = ""
    task_type = ""
    category = ""
    reg_path = ""

    # Publish
    publish_list = []
    publish_format = ".abc"
    range_mode = "STILL"
    start_frame = 1001
    end_frame = 1100
    publish_separate = False
    include_materials = True

    # Assembly
    lookdev_items = []      # [{name, path, asset_name}]
    cache_items = []        # [{name, path}]
    collection_links = []   # [{name, assigned_cache, is_selected}]
    cache_anim_only = False


STATE = PipelineState()


# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------
def get_latest_version(folder_path):
    if not folder_path or not os.path.isdir(folder_path):
        return 0
    pat = re.compile(r"_v(\d+)\.")
    latest = 0
    for f in os.listdir(folder_path):
        if not f.lower().endswith((".ma", ".mb")):
            continue
        m = pat.search(f)
        if m:
            v = int(m.group(1))
            if v > latest:
                latest = v
    return latest


def build_work_filename(entity_name, task_type, version, ext=".ma"):
    abbr = TASK_ABBR.get(task_type, (task_type or "task")[:3].lower())
    clean = (entity_name or "entity").replace(" ", "_")
    return f"{clean}_{abbr}_wip_v{version:03d}{ext}"


def _read_env_into_state():
    STATE.task_id = os.environ.get("CGP_TASK_ID", "").strip()
    STATE.entity = os.environ.get("CGP_ENTITY_NAME", "").strip()
    STATE.task_path = os.environ.get("CGP_TASK_PATH", "").strip()
    STATE.task_type = os.environ.get("CGP_TASK_TYPE", "").strip()
    STATE.category = os.environ.get("CGP_CATEGORY", "").strip()
    STATE.reg_path = os.environ.get("CGP_REGISTRY_PATH", "").strip()


def _standalone_root():
    """Locate the dashboard's main.py — env var first, then settings.json, then relative."""
    env_path = os.environ.get("CGP_APP_MAIN", "")
    if env_path and os.path.exists(env_path):
        return os.path.dirname(env_path)
    settings = os.path.join(SYSTEM_ROOT, "settings.json")
    if os.path.exists(settings):
        try:
            with open(settings, "r") as f:
                ap = json.load(f).get("app_main_path", "")
            if ap and os.path.exists(ap):
                return os.path.dirname(ap)
        except Exception:
            pass
    # Fallback: this file lives at <root>/core/dcc/maya/cgpipeline_maya.py
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def _load_plugins():
    for plug in ("AbcImport", "AbcExport", "mayaUsdPlugin", "fbxmaya"):
        try:
            if not cmds.pluginInfo(plug, q=True, loaded=True):
                cmds.loadPlugin(plug, quiet=True)
        except Exception:
            pass


def _apply_color_management():
    if not STATE.reg_path or not os.path.exists(STATE.reg_path):
        return
    try:
        with open(STATE.reg_path, "r") as f:
            cm = json.load(f).get("color_management", "")
        if cm in ("ACES 1.3", "ACES 2.0"):
            try:
                cmds.colorManagementPrefs(edit=True, cmEnabled=True)
                cmds.colorManagementPrefs(edit=True, renderingSpaceName="ACEScg")
                print(f"CGPipeline: Rendering space set to ACEScg ({cm}).")
            except Exception as e:
                print(f"CGPipeline Warning: Could not set ACEScg: {e}")
    except Exception as e:
        print(f"CGPipeline: Could not read color_management from registry: {e}")


def _save_new_scene_if_requested():
    """Launcher passes CGP_NEW_FILE_TARGET when there's no existing version.
    Save the empty scene there so subsequent saves version up correctly."""
    target = os.environ.get("CGP_NEW_FILE_TARGET", "").strip()
    if not target:
        return
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        cmds.file(new=True, force=True)
        cmds.file(rename=target)
        cmds.file(save=True, type="mayaAscii")
        print(f"CGPipeline: Initialized new scene at {target}")
    except Exception as e:
        print(f"CGPipeline: Could not initialize new scene: {e}")


# --------------------------------------------------------------------------------------
# Same-session command listener
# --------------------------------------------------------------------------------------
def _check_command_file():
    if not os.path.exists(COMMAND_FILE):
        return
    try:
        with open(COMMAND_FILE, "r") as f:
            cmd = json.load(f)
        os.remove(COMMAND_FILE)
    except Exception as e:
        print(f"CGPipeline Command Error: {e}")
        try:
            if os.path.exists(COMMAND_FILE):
                os.remove(COMMAND_FILE)
        except Exception:
            pass
        return

    if cmd.get("action") != "open_task":
        return
    fp = cmd.get("filepath", "")
    if not fp:
        return
    # Update env so the file-loaded state matches.
    os.environ["CGP_TASK_ID"] = cmd.get("task_id", "")
    os.environ["CGP_ENTITY_NAME"] = cmd.get("entity_name", "")
    os.environ["CGP_TASK_PATH"] = os.path.dirname(fp)
    os.environ["CGP_TASK_TYPE"] = cmd.get("task_type", "")
    os.environ["CGP_REGISTRY_PATH"] = cmd.get("registry_path", "")
    try:
        if os.path.exists(fp):
            cmds.file(fp, open=True, force=True)
        else:
            # New task: launcher put the target name in env; save an empty scene there.
            os.environ["CGP_NEW_FILE_TARGET"] = fp
            cmds.file(new=True, force=True)
            _save_new_scene_if_requested()
        _read_env_into_state()
        _apply_color_management()
        _refresh_panel_state()
        print(f"CGPipeline: Opened task {cmd.get('task_id')}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Failed to open task: {e}")


_command_timer = None


def _start_command_watcher():
    global _command_timer
    if _command_timer is not None:
        return
    _command_timer = QtCore.QTimer()
    _command_timer.setInterval(1000)
    _command_timer.timeout.connect(_check_command_file)
    _command_timer.start()


# --------------------------------------------------------------------------------------
# Operations: Core
# --------------------------------------------------------------------------------------
def op_open_dashboard():
    root = _standalone_root()
    main_py = os.path.normpath(os.path.join(root, "main.py"))
    if not os.path.exists(main_py):
        cmds.warning(f"CGPipeline: main.py not found at {main_py}")
        return
    if os.name == "nt":
        py_exe = shutil.which("pythonw") or shutil.which("python") or "python"
    else:
        py_exe = shutil.which("python3") or shutil.which("python") or "python3"
    env = os.environ.copy()
    env["CGP_IN_DCC"] = "Maya"
    env["CGP_COMMAND_FILE"] = COMMAND_FILE
    if STATE.task_id:
        env["CGP_TASK_ID"] = STATE.task_id
    if STATE.reg_path:
        env["CGP_REGISTRY_PATH"] = STATE.reg_path
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen([py_exe, main_py], env=env, creationflags=creationflags)
        print("CGPipeline: Dashboard launched (linked to this Maya).")
    except Exception as e:
        cmds.warning(f"CGPipeline: Dashboard launch failed: {e}")


def op_save():
    fp = cmds.file(q=True, sceneName=True)
    if not fp:
        cmds.warning("CGPipeline: Scene has no name yet — use Version Up.")
        return
    cmds.file(save=True)
    if STATE.entity and STATE.task_path:
        master = f"{STATE.entity}_{TASK_ABBR.get(STATE.task_type, 'task')}_master.ma"
        master_path = os.path.normpath(os.path.join(os.path.dirname(STATE.task_path), master))
        try:
            shutil.copy2(fp, master_path)
        except Exception as e:
            print(f"CGPipeline: Master copy failed: {e}")


def op_save_version():
    if not STATE.task_path:
        cmds.warning("CGPipeline: No task context — open via dashboard.")
        return
    v = get_latest_version(STATE.task_path) + 1
    fn = build_work_filename(STATE.entity, STATE.task_type, v, ".ma")
    fp = os.path.normpath(os.path.join(STATE.task_path, fn))
    try:
        os.makedirs(STATE.task_path, exist_ok=True)
        cmds.file(rename=fp)
        cmds.file(save=True, type="mayaAscii")
        master = f"{STATE.entity}_{TASK_ABBR.get(STATE.task_type, 'task')}_master.ma"
        master_path = os.path.normpath(os.path.join(os.path.dirname(STATE.task_path), master))
        try:
            shutil.copy2(fp, master_path)
        except Exception as e:
            print(f"CGPipeline: Master copy failed: {e}")
        print(f"CGPipeline: Saved v{v:03d}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Version save failed: {e}")


def op_update_status(new_status):
    if not STATE.reg_path or not os.path.exists(STATE.reg_path):
        cmds.warning("CGPipeline: No registry path.")
        return
    if not STATE.task_id:
        cmds.warning("CGPipeline: No task ID.")
        return
    if not new_status or new_status == "NO CHANGE":
        return
    try:
        with open(STATE.reg_path, "r") as f:
            data = json.load(f)
        for t in data.get("tasks", []):
            if t.get("id") == STATE.task_id:
                t["status"] = new_status
                break
        with open(STATE.reg_path, "w") as f:
            json.dump(data, f, indent=4)
        print(f"CGPipeline: Status → {new_status}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Status update failed: {e}")


# --------------------------------------------------------------------------------------
# Operations: Textures
# --------------------------------------------------------------------------------------
def _file_nodes_for_selection():
    sel = cmds.ls(selection=True) or []
    nodes = set()
    if not sel:
        return nodes
    for obj in sel:
        for n in (cmds.listHistory(obj) or []):
            try:
                if cmds.nodeType(n) == "file":
                    nodes.add(n)
            except Exception:
                continue
    return nodes


def op_fix_texture_paths():
    if not STATE.reg_path:
        cmds.warning("CGPipeline: No project context.")
        return
    nodes = _file_nodes_for_selection()
    if not nodes:
        cmds.warning("CGPipeline: Select objects with file textures first.")
        return
    project_root = os.path.dirname(STATE.reg_path)
    search_dirs = [
        os.path.join(project_root, "Assets"),
        os.path.join(project_root, "Textures"),
        project_root,
    ]
    fixed = 0
    missing = []
    for node in nodes:
        try:
            cur = cmds.getAttr(f"{node}.fileTextureName") or ""
            if cur and os.path.exists(cur):
                continue
            base = os.path.basename(cur) if cur else ""
            if not base:
                continue
            found = None
            for s in search_dirs:
                if not os.path.isdir(s):
                    continue
                for root_dir, _, files in os.walk(s):
                    if base in files:
                        found = os.path.join(root_dir, base)
                        break
                if found:
                    break
            if found:
                cmds.setAttr(f"{node}.fileTextureName", found, type="string")
                fixed += 1
            else:
                missing.append(base)
        except Exception:
            continue
    msg = f"Re-linked {fixed} textures."
    if missing:
        msg += f"  Could not find {len(missing)}."
    cmds.confirmDialog(title="Fix Textures", message=msg)


def op_switch_texture_res(target_res):
    other = "4k" if target_res == "2k" else "2k"
    nodes = _file_nodes_for_selection() or set(cmds.ls(type="file") or [])
    count = 0
    for n in nodes:
        try:
            cur = cmds.getAttr(f"{n}.fileTextureName") or ""
            if not cur:
                continue
            new = cur
            for sep in ("/", "\\"):
                marker = f"{sep}{other}{sep}"
                if marker.lower() in cur.lower():
                    idx = cur.lower().find(marker.lower())
                    new = cur[:idx] + sep + target_res + cur[idx + len(marker) - 1:]
                    break
            if new != cur:
                cmds.setAttr(f"{n}.fileTextureName", new, type="string")
                count += 1
        except Exception:
            continue
    print(f"CGPipeline: Switched {count} textures to {target_res.upper()}")


# --------------------------------------------------------------------------------------
# Operations: Publish
# --------------------------------------------------------------------------------------
def _resolve_publish_folder():
    if not STATE.task_path:
        return None
    check = os.path.dirname(STATE.task_path)
    for _ in range(4):
        pot = os.path.join(check, "Publish")
        if os.path.isdir(pot):
            return pot
        if os.path.basename(check).lower() == "wip":
            sib = os.path.join(os.path.dirname(check), "Publish")
            if os.path.isdir(sib):
                return sib
        check = os.path.dirname(check)
        if os.path.basename(check) in ("Assets", "Shots", ""):
            break
    return os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(STATE.task_path)), "Publish"))


def _frame_range():
    if STATE.range_mode == "STILL":
        s = e = cmds.currentTime(q=True)
    elif STATE.range_mode == "SLIDER":
        s = cmds.playbackOptions(q=True, min=True)
        e = cmds.playbackOptions(q=True, max=True)
    else:
        s, e = STATE.start_frame, STATE.end_frame
    return int(s), int(e), STATE.range_mode != "STILL"


def _export(items, filepath, is_anim, s, e):
    """Export the given nodes to filepath using STATE.publish_format. Returns True/False."""
    fmt = STATE.publish_format
    cmds.select(clear=True)
    roots = []
    for it in items:
        if cmds.objExists(it):
            cmds.select(it, add=True)
            roots.append(it)
    if not roots:
        return False
    fp = filepath.replace("\\", "/")

    if fmt == ".abc":
        root_args = "".join(f" -root {r}" for r in roots)
        job = f'-frameRange {s} {e} -uvWrite -worldSpace -dataFormat ogawa{root_args} -file "{fp}"'
        try:
            cmds.AbcExport(j=job)
        except Exception as ex:
            print(f"CGPipeline: ABC export failed: {ex}")
            return False
    elif fmt == ".usd":
        try:
            kwargs = dict(
                file=fp,
                selection=True,
                exportUVs=True,
                exportColorSets=True,
                defaultMeshScheme="catmullClark",
            )
            if STATE.include_materials:
                kwargs["exportMaterialCollections"] = True
                kwargs["materialsScopeName"] = "Looks"
            if is_anim:
                kwargs["frameRange"] = (s, e)
                kwargs["frameStride"] = 1.0
            cmds.mayaUSDExport(**kwargs)
        except Exception as ex:
            print(f"CGPipeline: USD export failed: {ex}")
            return False
    elif fmt == ".fbx":
        try:
            mel.eval("FBXResetExport;")
            mel.eval(f'FBXExportBakeComplexAnimation -v {"true" if is_anim else "false"};')
            if is_anim:
                mel.eval(f"FBXExportBakeComplexStart -v {s};")
                mel.eval(f"FBXExportBakeComplexEnd -v {e};")
            mel.eval(f'FBXExport -f "{fp}" -s;')
        except Exception as ex:
            print(f"CGPipeline: FBX export failed: {ex}")
            return False
    elif fmt == ".ma":
        try:
            cmds.file(fp, exportSelected=True, type="mayaAscii", force=True)
        except Exception as ex:
            print(f"CGPipeline: MA export failed: {ex}")
            return False
    else:
        print(f"CGPipeline: Unknown publish format {fmt}")
        return False
    return True


def op_publish():
    if not STATE.publish_list:
        cmds.warning("CGPipeline: Publish list is empty.")
        return
    pub = _resolve_publish_folder()
    if not pub:
        cmds.warning("CGPipeline: Could not resolve Publish folder.")
        return
    os.makedirs(pub, exist_ok=True)
    abbr = TASK_ABBR.get(STATE.task_type, "task")
    fmt = STATE.publish_format
    ext_upper = fmt[1:].upper()
    s, e, is_anim = _frame_range()

    if STATE.publish_separate:
        for obj in STATE.publish_list:
            if not cmds.objExists(obj):
                continue
            is_cam = "cam" in obj.lower()
            if STATE.task_type == "Lookdev" and fmt in (".usd", ".ma"):
                fn = f"{STATE.entity}_lkdev_{obj}{fmt}"
            elif is_cam:
                fn = f"{STATE.entity}_cam_f{s}_f{e}{fmt}"
            elif STATE.category == "Shots":
                fn = f"{STATE.entity}_{abbr}_{obj}_{ext_upper}{fmt}"
            else:
                fn = f"{STATE.entity}_{abbr}_{obj}{fmt}"
            _export([obj], os.path.join(pub, fn), is_anim, s, e)
    else:
        if STATE.task_type == "Lookdev" and fmt in (".usd", ".ma"):
            fn = f"{STATE.entity}_lkdev{fmt}"
        elif STATE.category == "Shots":
            fn = f"{STATE.entity}_{abbr}_{ext_upper}{fmt}"
        else:
            fn = f"{STATE.entity}_{abbr}{fmt}"
        _export(list(STATE.publish_list), os.path.join(pub, fn), is_anim, s, e)

    cmds.confirmDialog(title="Publish", message=f"Published → {pub}")


# --------------------------------------------------------------------------------------
# Operations: Assembly
# --------------------------------------------------------------------------------------
def _shot_root_from_task_path():
    if not STATE.task_path:
        return None
    norm = os.path.normpath(STATE.task_path).replace("\\", "/")
    parts = norm.split("/")
    if "Shots" not in parts:
        return None
    idx = parts.index("Shots")
    if len(parts) <= idx + 1:
        return None
    return os.path.normpath("/".join(parts[:idx + 2]))


def op_assembly_scan():
    if not STATE.reg_path:
        cmds.warning("CGPipeline: No project context.")
        return
    root = os.path.dirname(STATE.reg_path)
    STATE.lookdev_items = []
    STATE.cache_items = []

    # 1. Lookdev publishes under Assets/<cat>/<asset>/Publish
    assets_dir = os.path.join(root, "Assets")
    if os.path.isdir(assets_dir):
        for cat in os.listdir(assets_dir):
            cat_p = os.path.join(assets_dir, cat)
            if not os.path.isdir(cat_p):
                continue
            for asset in os.listdir(cat_p):
                asset_p = os.path.join(cat_p, asset)
                pub = os.path.join(asset_p, "Publish")
                if not os.path.isdir(pub):
                    continue
                for f in os.listdir(pub):
                    fl = f.lower()
                    if "_lkdev" in fl and fl.endswith((".ma", ".mb", ".usd", ".usda", ".usdc")):
                        STATE.lookdev_items.append({
                            "name": f, "path": os.path.join(pub, f), "asset_name": asset,
                        })

    # 2. Caches under <shot_root>/<dept>/Publish
    shot_root = _shot_root_from_task_path()
    if shot_root and os.path.isdir(shot_root):
        for dept in os.listdir(shot_root):
            pub_dir = os.path.join(shot_root, dept, "Publish")
            if not os.path.isdir(pub_dir):
                continue
            for f in os.listdir(pub_dir):
                fl = f.lower()
                if not fl.endswith((".abc", ".usd", ".usda", ".usdc", ".fbx")):
                    continue
                if STATE.cache_anim_only and "_anim_" not in fl:
                    continue
                if not any(c["name"] == f for c in STATE.cache_items):
                    STATE.cache_items.append({"name": f, "path": os.path.join(pub_dir, f)})

    # 3. Sync collection_links to top-level assemblies in the scene (skip default cameras)
    existing = {l["name"]: (l["assigned_cache"], l["is_selected"]) for l in STATE.collection_links}
    STATE.collection_links = []
    DEFAULT_CAMS = {"persp", "top", "front", "side", "back", "bottom", "left", "right"}
    for grp in (cmds.ls(assemblies=True) or []):
        if grp in DEFAULT_CAMS:
            continue
        cache, sel = existing.get(grp, ("", True))
        STATE.collection_links.append({"name": grp, "assigned_cache": cache, "is_selected": sel})

    print(f"CGPipeline: Scan complete — {len(STATE.lookdev_items)} lookdev, {len(STATE.cache_items)} caches.")


def op_import_lookdev(idx):
    if not (0 <= idx < len(STATE.lookdev_items)):
        return
    it = STATE.lookdev_items[idx]
    path = it["path"]
    ns = it["asset_name"] or "lkdev"
    try:
        # Maya references are the equivalent of Blender's library link.
        cmds.file(path, reference=True, namespace=ns, mergeNamespacesOnClash=False, ignoreVersion=True)
        print(f"CGPipeline: Referenced lookdev → {path}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Reference failed: {e}")


def op_make_editable():
    """Maya references are always editable in-place — this is a parity no-op that
    just confirms the selected node is referenced."""
    sel = cmds.ls(selection=True) or []
    if not sel:
        cmds.warning("CGPipeline: Select a referenced node first.")
        return
    referenced = []
    for n in sel:
        try:
            if cmds.referenceQuery(n, isNodeReferenced=True):
                referenced.append(n)
        except Exception:
            pass
    if not referenced:
        cmds.warning("CGPipeline: Selection contains no referenced nodes.")
        return
    cmds.confirmDialog(
        title="Editable",
        message=f"{len(referenced)} referenced node(s) are already editable.\n"
                "(Maya references don't need a separate override step.)",
    )


def op_assembly_apply(batch=False):
    shot_root = _shot_root_from_task_path()
    if not shot_root:
        cmds.warning("CGPipeline: Apply only works in shot context.")
        return
    links = STATE.collection_links if batch else [l for l in STATE.collection_links if l["is_selected"]]
    for l in links:
        if not l["assigned_cache"]:
            continue
        cache_path = None
        for dept in os.listdir(shot_root):
            test = os.path.normpath(os.path.join(shot_root, dept, "Publish", l["assigned_cache"]))
            if os.path.exists(test):
                cache_path = test
                break
        if not cache_path:
            print(f"CGPipeline: Cache not found: {l['assigned_cache']}")
            continue
        grp = l["name"]
        if not cmds.objExists(grp):
            print(f"CGPipeline: Group not found in scene: {grp}")
            continue
        ext = os.path.splitext(cache_path)[1].lower()
        try:
            if ext == ".abc":
                # Connect by hierarchy onto the target group.
                cmds.AbcImport(cache_path, mode="import", connect="/" + grp)
            elif ext in (".usd", ".usda", ".usdc"):
                cmds.mayaUSDImport(file=cache_path)
            elif ext == ".fbx":
                mel.eval(f'FBXImport -f "{cache_path.replace(chr(92), "/")}";')
            print(f"CGPipeline: Applied {l['assigned_cache']} → {grp}")
        except Exception as e:
            print(f"CGPipeline: Cache apply failed for {grp}: {e}")


# --------------------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------------------
class CGPipelinePanel(QtWidgets.QWidget):
    _instance = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CGPipelinePanel")
        self.setWindowTitle("CGPipeline")
        self.resize(360, 720)
        self._build()
        self._refresh_state_labels()

    # ---- builders ----
    def _btn(self, text, fn):
        b = QtWidgets.QPushButton(text)
        b.clicked.connect(fn)
        return b

    def _section(self, title):
        l = QtWidgets.QLabel(title)
        l.setStyleSheet("font-weight: bold; color: #cccccc; margin-top: 4px;")
        return l

    def _sep(self):
        s = QtWidgets.QFrame()
        s.setFrameShape(QtWidgets.QFrame.HLine)
        s.setFrameShadow(QtWidgets.QFrame.Sunken)
        return s

    def _build(self):
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)
        v = QtWidgets.QVBoxLayout(inner)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Dashboard
        v.addWidget(self._btn("Open Dashboard", op_open_dashboard))
        v.addWidget(self._sep())

        # Quick Tools
        v.addWidget(self._section("Quick Tools"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._btn("Save", op_save))
        row.addWidget(self._btn("Version Up", op_save_version))
        v.addLayout(row)
        v.addWidget(self._btn("Fix Missing Textures", op_fix_texture_paths))
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self._btn("2K", lambda: op_switch_texture_res("2k")))
        row2.addWidget(self._btn("4K", lambda: op_switch_texture_res("4k")))
        v.addLayout(row2)
        v.addWidget(self._sep())

        # Status
        v.addWidget(self._section("Status"))
        self.task_label = QtWidgets.QLabel("TASK: -")
        v.addWidget(self.task_label)
        srow = QtWidgets.QHBoxLayout()
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(["NO CHANGE", "Pending Review", "Approved", "In Progress"])
        srow.addWidget(self.status_combo, 1)
        srow.addWidget(self._btn("Update", lambda: op_update_status(self.status_combo.currentText())))
        v.addLayout(srow)
        v.addWidget(self._sep())

        # Publisher
        v.addWidget(self._section("Publisher"))
        self.publish_label = QtWidgets.QLabel("PUBLISHING: -")
        v.addWidget(self.publish_label)
        prow = QtWidgets.QHBoxLayout()
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems([".abc", ".usd", ".fbx", ".ma"])
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        prow.addWidget(self.format_combo)
        self.range_combo = QtWidgets.QComboBox()
        self.range_combo.addItems(["STILL", "SLIDER", "CUSTOM"])
        self.range_combo.currentTextChanged.connect(self._on_range_changed)
        prow.addWidget(self.range_combo)
        v.addLayout(prow)

        crow = QtWidgets.QHBoxLayout()
        self.start_spin = QtWidgets.QSpinBox()
        self.start_spin.setRange(-100000, 100000)
        self.start_spin.setValue(STATE.start_frame)
        self.start_spin.valueChanged.connect(lambda x: setattr(STATE, "start_frame", x))
        self.end_spin = QtWidgets.QSpinBox()
        self.end_spin.setRange(-100000, 100000)
        self.end_spin.setValue(STATE.end_frame)
        self.end_spin.valueChanged.connect(lambda x: setattr(STATE, "end_frame", x))
        self.start_spin.setEnabled(False)
        self.end_spin.setEnabled(False)
        crow.addWidget(QtWidgets.QLabel("S:"))
        crow.addWidget(self.start_spin)
        crow.addWidget(QtWidgets.QLabel("E:"))
        crow.addWidget(self.end_spin)
        v.addLayout(crow)

        opts = QtWidgets.QHBoxLayout()
        self.separate_chk = QtWidgets.QCheckBox("Separate")
        self.separate_chk.toggled.connect(lambda c: setattr(STATE, "publish_separate", c))
        opts.addWidget(self.separate_chk)
        self.material_chk = QtWidgets.QCheckBox("Material")
        self.material_chk.setChecked(STATE.include_materials)
        self.material_chk.toggled.connect(lambda c: setattr(STATE, "include_materials", c))
        opts.addWidget(self.material_chk)
        v.addLayout(opts)

        v.addWidget(QtWidgets.QLabel("Selection List:"))
        self.publish_list_w = QtWidgets.QListWidget()
        self.publish_list_w.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        v.addWidget(self.publish_list_w)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(self._btn("Add", self._on_publish_add))
        lrow.addWidget(self._btn("Remove", self._on_publish_remove))
        v.addLayout(lrow)
        publish_btn = self._btn("PUBLISH", op_publish)
        publish_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        v.addWidget(publish_btn)
        v.addWidget(self._sep())

        # Assembly
        v.addWidget(self._section("Assembly"))
        v.addWidget(self._btn("1. REFRESH", self._on_assembly_scan))
        v.addWidget(QtWidgets.QLabel("2. IMPORT LOOKDEV:"))
        self.lookdev_list_w = QtWidgets.QListWidget()
        v.addWidget(self.lookdev_list_w)
        v.addWidget(self._btn("REFERENCE LOOKDEV", self._on_import_lookdev))
        v.addWidget(self._btn("3. MAKE EDITABLE (Maya ref is editable)", op_make_editable))

        v.addWidget(QtWidgets.QLabel("4. ASSIGN CACHES:"))
        self.collection_tree = QtWidgets.QTreeWidget()
        self.collection_tree.setColumnCount(3)
        self.collection_tree.setHeaderLabels(["Apply", "Group", "Cache"])
        self.collection_tree.itemClicked.connect(self._on_link_clicked)
        v.addWidget(self.collection_tree)

        self.cache_anim_chk = QtWidgets.QCheckBox("ANIM ONLY")
        self.cache_anim_chk.toggled.connect(self._on_anim_only_changed)
        v.addWidget(self.cache_anim_chk)

        arow = QtWidgets.QHBoxLayout()
        arow.addWidget(self._btn("APPLY SELECTED", lambda: op_assembly_apply(batch=False)))
        arow.addWidget(self._btn("APPLY ALL", lambda: op_assembly_apply(batch=True)))
        v.addLayout(arow)

    # ---- state sync ----
    def _refresh_state_labels(self):
        self.task_label.setText(f"TASK: {STATE.entity or 'None'}")
        self.publish_label.setText(f"PUBLISHING: {STATE.entity or 'None'}")

    # ---- handlers ----
    def _on_format_changed(self, t):
        STATE.publish_format = t
        # Material checkbox is meaningful only for USD/MA — match the Blender UI.
        self.material_chk.setEnabled(t in (".usd", ".ma"))

    def _on_range_changed(self, mode):
        STATE.range_mode = mode
        enabled = mode == "CUSTOM"
        self.start_spin.setEnabled(enabled)
        self.end_spin.setEnabled(enabled)

    def _on_publish_add(self):
        for n in (cmds.ls(selection=True) or []):
            already = any(self.publish_list_w.item(i).text() == n for i in range(self.publish_list_w.count()))
            if not already:
                self.publish_list_w.addItem(n)
        self._sync_publish_list()

    def _on_publish_remove(self):
        row = self.publish_list_w.currentRow()
        if row >= 0:
            self.publish_list_w.takeItem(row)
        self._sync_publish_list()

    def _sync_publish_list(self):
        STATE.publish_list = [self.publish_list_w.item(i).text() for i in range(self.publish_list_w.count())]

    def _on_anim_only_changed(self, checked):
        STATE.cache_anim_only = checked
        # Re-scan caches so the filter takes effect immediately.
        self._on_assembly_scan()

    def _on_assembly_scan(self):
        op_assembly_scan()
        self.lookdev_list_w.clear()
        for it in STATE.lookdev_items:
            self.lookdev_list_w.addItem(f"{it['asset_name']} — {it['name']}")
        self.collection_tree.clear()
        for l in STATE.collection_links:
            item = QtWidgets.QTreeWidgetItem([
                "✓" if l["is_selected"] else "",
                l["name"],
                l["assigned_cache"] or "(none)",
            ])
            self.collection_tree.addTopLevelItem(item)
        for c in range(3):
            self.collection_tree.resizeColumnToContents(c)

    def _on_import_lookdev(self):
        idx = self.lookdev_list_w.currentRow()
        if idx < 0:
            cmds.warning("CGPipeline: Select a lookdev item first.")
            return
        op_import_lookdev(idx)
        self._on_assembly_scan()

    def _on_link_clicked(self, item, col):
        idx = self.collection_tree.indexOfTopLevelItem(item)
        if idx < 0:
            return
        if col == 0:
            STATE.collection_links[idx]["is_selected"] = not STATE.collection_links[idx]["is_selected"]
            item.setText(0, "✓" if STATE.collection_links[idx]["is_selected"] else "")
            return
        if col != 2:
            return
        # Show a menu of available caches
        menu = QtWidgets.QMenu(self)
        none_act = menu.addAction("(none)")
        menu.addSeparator()
        acts = {}
        for c in STATE.cache_items:
            acts[menu.addAction(c["name"])] = c["name"]
        chosen = menu.exec_(QtGui.QCursor.pos())
        if chosen is None:
            return
        if chosen is none_act:
            STATE.collection_links[idx]["assigned_cache"] = ""
            item.setText(2, "(none)")
        elif chosen in acts:
            STATE.collection_links[idx]["assigned_cache"] = acts[chosen]
            item.setText(2, acts[chosen])


def _maya_main_window():
    try:
        ptr = OpenMayaUI.MQtUtil.mainWindow()
        if ptr:
            return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        pass
    return None


def _refresh_panel_state():
    panel = CGPipelinePanel._instance
    if panel is not None:
        try:
            panel._refresh_state_labels()
        except Exception:
            pass


def show_panel():
    if cmds.about(batch=True):
        return
    try:
        if CGPipelinePanel._instance is not None:
            CGPipelinePanel._instance.close()
    except Exception:
        pass
    parent = _maya_main_window()
    w = CGPipelinePanel(parent)
    w.setWindowFlags(QtCore.Qt.Window)
    w.show()
    CGPipelinePanel._instance = w
    return w


# --------------------------------------------------------------------------------------
# Shelf
# --------------------------------------------------------------------------------------
def _ensure_shelf():
    if cmds.about(batch=True):
        return
    try:
        shelf_top = mel.eval("$tmp=$gShelfTopLevel")
    except Exception:
        return
    if not shelf_top:
        return
    if cmds.shelfLayout(SHELF_NAME, exists=True):
        for c in (cmds.shelfLayout(SHELF_NAME, q=True, childArray=True) or []):
            try:
                cmds.deleteUI(c)
            except Exception:
                pass
    else:
        cmds.shelfLayout(SHELF_NAME, parent=shelf_top)

    def _add(label, ann, py):
        cmds.shelfButton(
            parent=SHELF_NAME, label=label, annotation=ann,
            image="pythonFamily.png", sourceType="python", command=py,
            style="iconAndTextHorizontal",
        )

    _add("Panel", "Open CGPipeline panel", "import cgpipeline_maya; cgpipeline_maya.show_panel()")
    _add("Dash", "Open dashboard", "import cgpipeline_maya; cgpipeline_maya.op_open_dashboard()")
    _add("Save", "Save current file", "import cgpipeline_maya; cgpipeline_maya.op_save()")
    _add("V+", "Version up", "import cgpipeline_maya; cgpipeline_maya.op_save_version()")


# --------------------------------------------------------------------------------------
# Entry point — called from userSetup.py
# --------------------------------------------------------------------------------------
def initialize():
    _read_env_into_state()
    _load_plugins()
    _save_new_scene_if_requested()
    _apply_color_management()
    _start_command_watcher()
    _ensure_shelf()
    if STATE.task_id and not cmds.about(batch=True):
        show_panel()
    print("CGPipeline: Maya integration ready.")
