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
import time
import atexit
import shutil
import subprocess

from maya import cmds, mel
from maya import OpenMayaUI

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import wrapInstance


SYSTEM_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "cgpipeline_system")
COMMAND_FILE = os.path.join(SYSTEM_ROOT, "maya_command.json")
SESSION_FILE = os.path.join(SYSTEM_ROOT, "maya_session.json")
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


def _set_project_workspace():
    """Point Maya's workspace at the CGPipeline project root so file textures,
    references, and caches resolve against it. The project root is the folder
    containing registry.json. Without this, Maya keeps its previous project and
    relative-path lookups go to the wrong place."""
    if not STATE.reg_path:
        return
    proj_root = os.path.dirname(STATE.reg_path)
    if not os.path.isdir(proj_root):
        return
    try:
        # openWorkspace falls back to defaults when workspace.mel is absent,
        # which is the common case for a freshly created CGPipeline project.
        cmds.workspace(proj_root, openWorkspace=True)
        print(f"CGPipeline: Maya project → {proj_root}")
    except Exception as e:
        # Fallback: just set the current workspace dir without opening.
        try:
            cmds.workspace(dir=proj_root)
            print(f"CGPipeline: Maya project dir set to {proj_root}")
        except Exception as e2:
            print(f"CGPipeline: Could not set Maya project: {e2}")


def _save_new_scene_if_requested():
    """Launcher passes CGP_NEW_FILE_TARGET when there's no existing version.
    Save the empty scene there so subsequent saves version up correctly."""
    target = os.environ.get("CGP_NEW_FILE_TARGET", "").strip()
    if not target:
        return
    # Safety: never clobber a scene that's already open. On "Continue Work" the file
    # is opened from the command line before this deferred bootstrap runs, so the
    # scene already has a name — bail out and leave it untouched.
    try:
        if cmds.file(q=True, sceneName=True):
            return
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        cmds.file(new=True, force=True)
        cmds.file(rename=target)
        cmds.file(save=True, type="mayaAscii")
        print(f"CGPipeline: Initialized new scene at {target}")
    except Exception as e:
        print(f"CGPipeline: Could not initialize new scene: {e}")


def _ensure_task_context_from_scene(force=False):
    """Populate task context from the currently-open scene when the env vars didn't
    provide it (e.g. the user opened a task file directly via File > Open). Only
    updates when the scene lives under a CGPipeline project (a folder containing
    registry.json); otherwise the existing context is left alone."""
    if not force and STATE.task_path and os.path.isdir(STATE.task_path):
        return
    try:
        scene = cmds.file(q=True, sceneName=True)
    except Exception:
        scene = ""
    if not scene:
        return
    task_dir = os.path.dirname(os.path.normpath(scene))
    cur, reg = task_dir, None
    for _ in range(12):
        cand = os.path.join(cur, "registry.json")
        if os.path.exists(cand):
            reg = cand
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if not reg:
        return  # not a CGPipeline scene — don't clobber existing context
    STATE.task_path = task_dir
    STATE.reg_path = reg
    parts = task_dir.replace("\\", "/").split("/")
    if "Assets" in parts:
        idx = parts.index("Assets")
        STATE.category = "Assets"
        if len(parts) > idx + 2:
            STATE.entity = parts[idx + 2]
    elif "Shots" in parts:
        idx = parts.index("Shots")
        STATE.category = "Shots"
        if len(parts) > idx + 1:
            STATE.entity = parts[idx + 1]


def _on_scene_opened():
    """Re-derive context from the freshly-opened scene and refresh the panel."""
    _ensure_task_context_from_scene(force=True)
    _refresh_panel_state()


_scene_job = None


def _register_scene_job():
    """Refresh the panel whenever a scene is opened, so switching tasks in an
    already-running Maya re-populates LookDev + Assembly automatically."""
    global _scene_job
    if _scene_job is not None:
        return
    try:
        _scene_job = cmds.scriptJob(event=["SceneOpened", _on_scene_opened], protected=True)
    except Exception:
        _scene_job = None


# --------------------------------------------------------------------------------------
# Same-session command listener + heartbeat
# --------------------------------------------------------------------------------------
def _touch_session():
    """Heartbeat so a standalone dashboard can detect this live Maya and route
    tasks here instead of launching a new instance. Rewritten every timer tick."""
    try:
        os.makedirs(SYSTEM_ROOT, exist_ok=True)
        with open(SESSION_FILE, "w") as f:
            json.dump(
                {"pid": os.getpid(), "ts": time.time(), "command_file": COMMAND_FILE}, f
            )
    except Exception:
        pass


def _remove_session():
    """Drop the heartbeat on clean shutdown so the launcher won't try to reuse us."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass


def _check_command_file():
    # Heartbeat first, every tick, regardless of whether a command is pending.
    _touch_session()
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
    os.environ["CGP_CATEGORY"] = cmd.get("category", "")
    os.environ["CGP_REGISTRY_PATH"] = cmd.get("registry_path", "")
    # Refresh state and Maya project BEFORE opening the file so relative-path
    # lookups (textures, refs, caches) resolve against the right project.
    _read_env_into_state()
    _set_project_workspace()
    try:
        if os.path.exists(fp):
            cmds.file(fp, open=True, force=True)
        else:
            # New task: launcher put the target name in env; save an empty scene there.
            os.environ["CGP_NEW_FILE_TARGET"] = fp
            cmds.file(new=True, force=True)
            _save_new_scene_if_requested()
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
    # Launch with a CLEANED environment. If we inherit Maya's env, PYTHONHOME /
    # PYTHONPATH point at Maya's own interpreter + its bundled PySide6/Qt, so the
    # system Python running main.py loads Maya's libraries and breaks — the dashboard
    # opens against the wrong interpreter and can't load projects/tasks. Strip the
    # Python/Qt/loader vars so the external Python uses its own stdlib + site-packages.
    env = os.environ.copy()
    for var in (
        "PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
        "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    ):
        env.pop(var, None)
    env["CGP_IN_DCC"] = "Maya"
    env["CGP_COMMAND_FILE"] = COMMAND_FILE
    if STATE.task_id:
        env["CGP_TASK_ID"] = STATE.task_id
    if STATE.reg_path:
        env["CGP_REGISTRY_PATH"] = STATE.reg_path
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen([py_exe, main_py], env=env, cwd=root, creationflags=creationflags)
        print("CGPipeline: Dashboard launched (linked to this Maya).")
    except Exception as e:
        cmds.warning(f"CGPipeline: Dashboard launch failed: {e}")


def _safe_name(n):
    """Filename-safe short name (drop namespace/DAG path, spaces)."""
    return n.split("|")[-1].split(":")[-1].replace(" ", "_")


def _master_dir():
    """Folder for the *_master file. Assets keep it in the department folder (one
    level above _wip); shots keep it inside the task (department) folder itself,
    e.g. Shots/<shot>/Anim/<entity>_anim_master.ma."""
    tp = os.path.normpath(STATE.task_path)
    if os.path.basename(tp).lower() == "_wip":
        return os.path.dirname(tp)
    return tp


def op_save():
    fp = cmds.file(q=True, sceneName=True)
    if not fp:
        cmds.warning("CGPipeline: Scene has no name yet — use Version Up.")
        return
    cmds.file(save=True)
    if STATE.entity and STATE.task_path:
        master = f"{STATE.entity}_{TASK_ABBR.get(STATE.task_type, 'task')}_master.ma"
        master_path = os.path.normpath(os.path.join(_master_dir(), master))
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
        master_path = os.path.normpath(os.path.join(_master_dir(), master))
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
        # -uvWrite writes the current UV set; -writeUVSets writes all UV sets.
        job = (f'-frameRange {s} {e} -uvWrite -writeUVSets -worldSpace '
               f'-dataFormat ogawa{root_args} -file "{fp}"')
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
    is_shot = STATE.category == "Shots"
    # Shot caches go into the task's own cache folder, e.g. Shots/<shot>/Anim/cache.
    # Asset publishes go to the asset's Publish folder.
    if is_shot:
        pub = os.path.join(STATE.task_path, "cache")
    else:
        pub = _resolve_publish_folder()
    if not pub:
        cmds.warning("CGPipeline: Could not resolve publish folder.")
        return
    os.makedirs(pub, exist_ok=True)
    abbr = TASK_ABBR.get(STATE.task_type, "task")
    fmt = STATE.publish_format
    s, e, is_anim = _frame_range()
    rng = f"_f{s:04d}_f{e:04d}"

    if is_shot:
        # Shot caches are ALWAYS one file per selected object/group, named with the
        # object, so the assembly can assign each one:
        #   <shot>_<object>_<task>_f0001_f0024.ext  (e.g. sh01_sq0010_woody_anim_f0001_f0024.abc)
        for obj in STATE.publish_list:
            if not cmds.objExists(obj):
                continue
            on = _safe_name(obj)
            if "cam" in obj.lower():
                fn = f"{STATE.entity}_{on}_cam{rng}{fmt}"
            else:
                fn = f"{STATE.entity}_{on}_{abbr}{rng}{fmt}"
            _export([obj], os.path.join(pub, fn), is_anim, s, e)
    elif STATE.publish_separate:
        for obj in STATE.publish_list:
            if not cmds.objExists(obj):
                continue
            on = _safe_name(obj)
            if STATE.task_type == "Lookdev" and fmt in (".usd", ".ma"):
                fn = f"{STATE.entity}_lkdev_{on}{fmt}"
            else:
                fn = f"{STATE.entity}_{on}_{abbr}{fmt}"
            _export([obj], os.path.join(pub, fn), is_anim, s, e)
    else:
        if STATE.task_type == "Lookdev" and fmt in (".usd", ".ma"):
            fn = f"{STATE.entity}_lkdev{fmt}"
        else:
            fn = f"{STATE.entity}_{abbr}{fmt}"
        _export(list(STATE.publish_list), os.path.join(pub, fn), is_anim, s, e)

    cmds.confirmDialog(title="Publish", message=f"Published → {pub}")


# --------------------------------------------------------------------------------------
# Operations: Import Model (asset-context setup for lookdev / rig / texture tasks)
# --------------------------------------------------------------------------------------
def _find_asset_publish_dir():
    """Return the current asset's Publish folder, or None if not in an asset context.
    Derived from the task path (Assets/<Category>/<AssetName>/...), so it works even
    when CGP_CATEGORY wasn't propagated (e.g. a same-session task open)."""
    if not STATE.task_path:
        return None
    norm = os.path.normpath(STATE.task_path).replace("\\", "/")
    parts = norm.split("/")
    if "Assets" not in parts:
        return None
    idx = parts.index("Assets")
    if len(parts) < idx + 3:
        return None
    asset_root = os.path.normpath("/".join(parts[:idx + 3]))
    pub = os.path.join(asset_root, "Publish")
    return pub if os.path.isdir(pub) else None


def _list_model_publishes(pub_dir):
    """List importable model publishes in the asset's Publish folder (newest first).
    Accepts common DCC/cache formats; excludes lookdev (_lkdev) and camera (_cam)
    publishes so the list focuses on geometry we'd reference/import for lookdev/rig."""
    if not pub_dir or not os.path.isdir(pub_dir):
        return []
    exts = (".ma", ".mb", ".abc", ".fbx", ".obj", ".usd", ".usda", ".usdc", ".usdz")
    out = []
    for f in os.listdir(pub_dir):
        fl = f.lower()
        if not fl.endswith(exts):
            continue
        if "_lkdev" in fl or "_lookdev" in fl or "_cam" in fl:
            continue
        full = os.path.join(pub_dir, f)
        out.append((full, os.path.getmtime(full)))
    out.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in out]


def _pick_file_dialog(files, title):
    """Modal picker — returns the chosen absolute path, or None on cancel."""
    parent = _maya_main_window()
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(420, 320)
    layout = QtWidgets.QVBoxLayout(dlg)
    layout.addWidget(QtWidgets.QLabel(f"{len(files)} files. Latest first:"))
    lst = QtWidgets.QListWidget()
    for f in files:
        item = QtWidgets.QListWidgetItem(os.path.basename(f))
        item.setData(QtCore.Qt.UserRole, f)
        lst.addItem(item)
    lst.setCurrentRow(0)
    lst.itemDoubleClicked.connect(lambda _: dlg.accept())
    layout.addWidget(lst)
    btn_row = QtWidgets.QHBoxLayout()
    cancel_btn = QtWidgets.QPushButton("Cancel")
    ok_btn = QtWidgets.QPushButton("OK")
    ok_btn.setDefault(True)
    cancel_btn.clicked.connect(dlg.reject)
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addStretch()
    btn_row.addWidget(cancel_btn)
    btn_row.addWidget(ok_btn)
    layout.addLayout(btn_row)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    item = lst.currentItem()
    return item.data(QtCore.Qt.UserRole) if item else None


def _do_import_model(chosen, mode):
    """Reference or import a specific model publish file.
    mode='reference' — Maya reference (good for lookdev — updates propagate).
    mode='import' — merge into scene as native nodes (good for rig — editable topology).

    Reference uses "Merge into selected namespace and rename incoming objects that
    match" — i.e. the root namespace with clash-renaming, so the model comes in
    without a per-asset namespace prefix.
    """
    if not chosen:
        return
    ext = os.path.splitext(chosen)[1].lower()
    label = mode.title()

    try:
        if mode == "reference":
            if ext in (".ma", ".mb"):
                cmds.file(
                    chosen, reference=True,
                    namespace=":", mergeNamespacesOnClash=True,
                    ignoreVersion=True,
                )
            elif ext == ".abc":
                # Alembic-as-reference isn't a real Maya reference; AbcImport
                # creates a cacheFile-driven mesh, which behaves reference-like
                # (updates if the cache changes) and is fine for lookdev.
                cmds.AbcImport(chosen, mode="import")
                cmds.warning(
                    "CGPipeline: ABC referenced via cacheFile (no Maya reference node)."
                )
            elif ext in (".usd", ".usda", ".usdc"):
                try:
                    cmds.file(
                        chosen, reference=True,
                        namespace=":", mergeNamespacesOnClash=True,
                        type="USD Import", ignoreVersion=True,
                    )
                except Exception:
                    cmds.mayaUSDImport(file=chosen)
                    cmds.warning(
                        "CGPipeline: USD reference unsupported here; imported instead."
                    )
        else:  # import
            if ext in (".ma", ".mb"):
                # No namespace on import — rig/lookdev artists usually want
                # native names in the outliner.
                cmds.file(chosen, i=True, ignoreVersion=True)
            elif ext == ".abc":
                cmds.AbcImport(chosen, mode="import")
            elif ext in (".usd", ".usda", ".usdc"):
                cmds.mayaUSDImport(file=chosen)
        print(f"CGPipeline: {label} model → {chosen}")
    except Exception as e:
        cmds.warning(f"CGPipeline: {label} failed: {e}")


def op_import_model(mode="reference"):
    """Discover the asset's model publishes and reference/import one (picker if many).
    The LookDev tab lists models directly; this is kept for shelf/standalone use."""
    pub = _find_asset_publish_dir()
    if not pub:
        cmds.warning(
            "CGPipeline: Not in an asset context, or asset has no Publish folder yet."
        )
        return
    files = _list_model_publishes(pub)
    if not files:
        cmds.warning(f"CGPipeline: No model publishes found in {pub}")
        return
    chosen = files[0] if len(files) == 1 else _pick_file_dialog(
        files, f"Pick model to {mode.title()}"
    )
    _do_import_model(chosen, mode)


# --------------------------------------------------------------------------------------
# Operations: Assembly
# --------------------------------------------------------------------------------------
def _group_match_token(grp_name):
    """The name used to match caches to a group, namespace/DAG-path stripped.
    'woody:CH_Woody' -> 'CH_Woody'."""
    return grp_name.split("|")[-1].split(":")[-1]


def _remove_existing_alembic(grp):
    """Delete any AlembicNode(s) already driving the meshes under `grp`, so applying
    a cache replaces the previous one instead of stacking (which bogs Maya down)."""
    try:
        meshes = cmds.listRelatives(grp, allDescendents=True, type="mesh", fullPath=True) or []
    except Exception:
        meshes = []
    nodes = set()
    for m in meshes:
        for n in (cmds.listHistory(m) or []):
            try:
                if cmds.nodeType(n) == "AlembicNode":
                    nodes.add(n)
            except Exception:
                continue
    if nodes:
        try:
            cmds.delete(list(nodes))
            print(f"CGPipeline: Removed {len(nodes)} old Alembic node(s) on {grp}")
        except Exception as e:
            print(f"CGPipeline: Could not remove old Alembic nodes on {grp}: {e}")


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

    # 2. Caches under <shot_root>/<dept>/{cache,Publish} (and the dept folder itself).
    shot_root = _shot_root_from_task_path()
    if shot_root and os.path.isdir(shot_root):
        for dept in os.listdir(shot_root):
            dept_path = os.path.join(shot_root, dept)
            if not os.path.isdir(dept_path):
                continue
            for sub in ("cache", "Publish", ""):
                scan_dir = os.path.join(dept_path, sub) if sub else dept_path
                if not os.path.isdir(scan_dir):
                    continue
                for f in os.listdir(scan_dir):
                    fl = f.lower()
                    if not fl.endswith((".abc", ".usd", ".usda", ".usdc", ".fbx")):
                        continue
                    if STATE.cache_anim_only and "_anim_" not in fl:
                        continue
                    if not any(c["name"] == f for c in STATE.cache_items):
                        STATE.cache_items.append({"name": f, "path": os.path.join(scan_dir, f)})

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


def op_assembly_apply(batch=False):
    shot_root = _shot_root_from_task_path()
    if not shot_root:
        cmds.warning("CGPipeline: Apply only works in shot context.")
        return
    links = STATE.collection_links if batch else [l for l in STATE.collection_links if l["is_selected"]]
    for l in links:
        if not l["assigned_cache"]:
            continue
        # Prefer the path resolved during scan; fall back to searching the dept's
        # cache/Publish folders.
        cache_path = next((c["path"] for c in STATE.cache_items
                           if c["name"] == l["assigned_cache"]), None)
        if not cache_path or not os.path.exists(cache_path):
            cache_path = None
            for dept in os.listdir(shot_root):
                for sub in ("cache", "Publish", ""):
                    base = os.path.join(shot_root, dept, sub) if sub else os.path.join(shot_root, dept)
                    test = os.path.normpath(os.path.join(base, l["assigned_cache"]))
                    if os.path.exists(test):
                        cache_path = test
                        break
                if cache_path:
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
                # Replace any previous cache first so AlembicNodes don't pile up.
                _remove_existing_alembic(grp)
                # Cache > Alembic Cache > Import Cache (merge under current selection):
                # select the target group and pass its DAG root path(s) to -connect so
                # AbcImport attaches the cache to the matching geometry UNDER the
                # selection (by name) instead of creating a new disconnected hierarchy.
                try:
                    cmds.select(grp, replace=True)
                except Exception:
                    pass
                roots = cmds.ls(selection=True, long=True) or [grp]
                cmds.AbcImport(cache_path, mode="import", connect=" ".join(roots))
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
        self._auto_populate()

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

    def _wrap_scroll(self, widget):
        """Put a tab's content widget inside a scroll area so tall tabs stay usable."""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Always-visible header: dashboard entry point + current task.
        dash_btn = self._btn("Open Dashboard", op_open_dashboard)
        dash_btn.setMinimumHeight(48)  # ~2 rows tall
        outer.addWidget(dash_btn)
        self.task_label = QtWidgets.QLabel("TASK: -")
        self.task_label.setStyleSheet(
            "color: #ffffff; font-weight: bold; font-size: 20px; padding: 4px 0;"
        )
        outer.addWidget(self.task_label)

        tabs = QtWidgets.QTabWidget()
        outer.addWidget(tabs, 1)
        tabs.addTab(self._make_task_tab(), "Task")
        tabs.addTab(self._make_model_tab(), "LookDev")
        tabs.addTab(self._make_publish_tab(), "Publish")
        tabs.addTab(self._make_assembly_tab(), "Assembly")

    # ---- tabs ----
    def _make_task_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Status
        v.addWidget(self._section("Status"))
        srow = QtWidgets.QHBoxLayout()
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(["NO CHANGE", "Pending Review", "Approved", "In Progress"])
        srow.addWidget(self.status_combo, 1)
        srow.addWidget(self._btn("Update", lambda: op_update_status(self.status_combo.currentText())))
        v.addLayout(srow)
        v.addWidget(self._sep())

        # File
        v.addWidget(self._section("File"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._btn("Save", op_save))
        row.addWidget(self._btn("Version Up", op_save_version))
        v.addLayout(row)

        v.addStretch()
        return self._wrap_scroll(w)

    def _make_model_tab(self):
        # The LookDev stage tab: bring in the asset's model, plus texture tools.
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Import Model — list the models available to reference/import.
        hdr = QtWidgets.QHBoxLayout()
        hdr.addWidget(self._section("Import Model"))
        hdr.addStretch()
        hdr.addWidget(self._btn("Refresh", self._refresh_model_list))
        v.addLayout(hdr)
        self.model_list_w = QtWidgets.QListWidget()
        self.model_list_w.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        v.addWidget(self.model_list_w, 1)
        mrow = QtWidgets.QHBoxLayout()
        mrow.addWidget(self._btn("REFERENCE", lambda: self._on_import_model("reference")))
        mrow.addWidget(self._btn("IMPORT", lambda: self._on_import_model("import")))
        v.addLayout(mrow)
        v.addWidget(self._sep())

        # Textures
        v.addWidget(self._section("Textures"))
        v.addWidget(self._btn("Fix Missing Textures", op_fix_texture_paths))
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(self._btn("2K", lambda: op_switch_texture_res("2k")))
        trow.addWidget(self._btn("4K", lambda: op_switch_texture_res("4k")))
        v.addLayout(trow)

        self._refresh_model_list()
        return self._wrap_scroll(w)

    def _make_publish_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

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
        crow.addWidget(QtWidgets.QLabel("Start Frame:"))
        crow.addWidget(self.start_spin)
        crow.addWidget(QtWidgets.QLabel("End Frame:"))
        crow.addWidget(self.end_spin)
        v.addLayout(crow)

        opts = QtWidgets.QHBoxLayout()
        self.separate_chk = QtWidgets.QCheckBox("Separate Models")
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
        v.addWidget(self.publish_list_w, 1)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(self._btn("Add", self._on_publish_add))
        lrow.addWidget(self._btn("Remove", self._on_publish_remove))
        v.addLayout(lrow)
        publish_btn = self._btn("PUBLISH", op_publish)
        publish_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        v.addWidget(publish_btn)
        return self._wrap_scroll(w)

    def _make_assembly_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        v.addWidget(self._btn("1. REFRESH", self._on_assembly_scan))
        v.addWidget(QtWidgets.QLabel("2. IMPORT LOOKDEV:"))
        self.lookdev_list_w = QtWidgets.QListWidget()
        v.addWidget(self.lookdev_list_w, 1)
        v.addWidget(self._btn("REFERENCE LOOKDEV", self._on_import_lookdev))

        v.addWidget(QtWidgets.QLabel("3. ASSIGN CACHES:"))
        self.collection_tree = QtWidgets.QTreeWidget()
        self.collection_tree.setColumnCount(3)
        self.collection_tree.setHeaderLabels(["Apply", "Group", "Cache"])
        self.collection_tree.itemClicked.connect(self._on_link_clicked)
        v.addWidget(self.collection_tree, 1)

        self.cache_anim_chk = QtWidgets.QCheckBox("ANIM ONLY")
        self.cache_anim_chk.toggled.connect(self._on_anim_only_changed)
        v.addWidget(self.cache_anim_chk)

        arow = QtWidgets.QHBoxLayout()
        arow.addWidget(self._btn("APPLY SELECTED", lambda: op_assembly_apply(batch=False)))
        arow.addWidget(self._btn("APPLY ALL", lambda: op_assembly_apply(batch=True)))
        v.addLayout(arow)
        return self._wrap_scroll(w)

    # ---- state sync ----
    def _entity_task_name(self):
        """e.g. 'buzz_lkdev' / 'buzz_mdl' — entity plus the task abbreviation."""
        if not STATE.entity:
            return "None"
        abbr = TASK_ABBR.get(STATE.task_type, "")
        return f"{STATE.entity}_{abbr}" if abbr else STATE.entity

    def _refresh_state_labels(self):
        self.task_label.setText(f"TASK: {self._entity_task_name()}")

    def _auto_populate(self):
        """Refresh the LookDev model list and Assembly scan for the current task.
        Runs on panel open and whenever the active task changes."""
        _ensure_task_context_from_scene()
        try:
            self._refresh_model_list()
        except Exception:
            pass
        if STATE.task_path or STATE.reg_path:
            try:
                self._on_assembly_scan()
            except Exception:
                pass

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

    def _refresh_model_list(self):
        """Populate the LookDev tab's model list from the current asset's Publish folder."""
        self.model_list_w.clear()
        pub = _find_asset_publish_dir()
        self._model_files = _list_model_publishes(pub) if pub else []
        for f in self._model_files:
            self.model_list_w.addItem(os.path.basename(f))
        if not self._model_files:
            placeholder = QtWidgets.QListWidgetItem("(no model publishes found)")
            placeholder.setFlags(QtCore.Qt.NoItemFlags)
            self.model_list_w.addItem(placeholder)

    def _on_import_model(self, mode):
        row = self.model_list_w.currentRow()
        files = getattr(self, "_model_files", [])
        if row < 0 or row >= len(files):
            cmds.warning(f"CGPipeline: Select a model to {mode} first.")
            return
        _do_import_model(files[row], mode)

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
            self.lookdev_list_w.addItem(it["asset_name"])
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
        # Show only caches whose name matches this group (namespace ignored), e.g.
        # group 'woody:CH_Woody' -> token 'CH_Woody' -> only *_CH_Woody_* caches.
        token = _group_match_token(STATE.collection_links[idx]["name"]).lower()
        matches = [c for c in STATE.cache_items if token and token in c["name"].lower()]
        menu = QtWidgets.QMenu(self)
        none_act = menu.addAction("(none)")
        menu.addSeparator()
        acts = {}
        for c in matches:
            acts[menu.addAction(c["name"])] = c["name"]
        if not matches:
            na = menu.addAction(f"(no caches for {token})")
            na.setEnabled(False)
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
            panel._auto_populate()
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
    _ensure_task_context_from_scene()     # fall back to the open scene if env had no task
    _set_project_workspace()
    _load_plugins()
    _save_new_scene_if_requested()
    _apply_color_management()
    _start_command_watcher()
    _touch_session()                      # advertise immediately, before the first timer tick
    atexit.register(_remove_session)      # best-effort cleanup on Maya quit
    _register_scene_job()                 # auto-refresh the panel when a task scene is opened
    _ensure_shelf()
    if (STATE.task_id or STATE.task_path) and not cmds.about(batch=True):
        show_panel()
    print("CGPipeline: Maya integration ready.")
