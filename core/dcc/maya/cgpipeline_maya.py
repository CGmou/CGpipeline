"""CGPipeline integration for Maya 2025+.

Mirrors core/dcc/blender/cgpipeline_blender.py:
  - Reads CGP_* env vars on startup, applies color management, loads plugins.
  - Polls maya_command.json so the dashboard can open tasks in this Maya session.
  - Adds a CGPipeline shelf and a panel: header (Open Dashboard, Save, Version Up,
    Project/Task labels) + Publish, Assembly and Render tabs.
  - Operations: Save / Version Up / Publish (ABC/USD/FBX/MA, applies status +
    thumbnail) / Publish to Kitsu / Clean Up / Fix Missing Textures / Switch 2K-4K /
    Import Model / Assembly scan / Reference Lookdev / Apply Caches / Import Camera /
    Render (batch render to Shots/<shot>/Render/<task>_v###).
"""

import os
import re
import sys
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

# Task status vocabulary, Kitsu-aligned (see core/constants.py). "NO CHANGE" is a
# UI-only sentinel that leaves the status untouched.
STATUS_CHOICES = [
    "NO CHANGE", "Todo", "Work In Progress", "Waiting For Approval", "Retake", "Done",
]

# Default Maya nodes the Clean Up must never delete when merging duplicate shaders.
_PROTECTED_SHADING = {
    "initialShadingGroup", "initialParticleSE", "lambert1",
    "particleCloud1", "standardSurface1",
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
    publish_whole_scene = False    # export everything; ignore the selection list
    update_master = True           # also refresh the *_master file on save / publish
    publish_notes = ""       # comment sent to Kitsu on "Publish to Kitsu"
    publish_status = "NO CHANGE"   # status applied when publishing (no manual button)

    # Alembic "Additional…" export settings (mirror Maya's AbcExport flags).
    abc_uv_write = True
    abc_world_space = True
    abc_write_visibility = False
    abc_write_uv_sets = True
    abc_step = 1.0

    # Render
    render_range_mode = "Frame Range"   # Single Frame / Frame Range / Custom
    render_start = 1001
    render_end = 1100
    render_camera = ""
    render_res_w = 1920
    render_res_h = 1080
    render_layer = ""
    render_format = "png"

    # Assembly
    lookdev_items = []      # [{name, path, asset_name}]
    cache_items = []        # [{name, path}]
    cache_links = []        # [{cache, lookdev, is_selected}] — cache filename -> lookdev asset
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
def _find_system_python():
    if os.name == "nt":
        return shutil.which("pythonw") or shutil.which("python") or "python"
    return shutil.which("python3") or shutil.which("python") or "python3"


def _clean_system_env():
    """A copy of the environment with Maya's Python/Qt/loader vars stripped. If a
    subprocess inherits Maya's env, PYTHONHOME / PYTHONPATH point at Maya's own
    interpreter + its bundled PySide6/Qt, so the system Python loads Maya's libraries
    and breaks. Stripping them makes the external Python use its own stdlib +
    site-packages (where gazu and the dashboard's deps live)."""
    env = os.environ.copy()
    for var in (
        "PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
        "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    ):
        env.pop(var, None)
    return env


def fire_kitsu_sync(reg, entity, category, task_type, status=None, thumbnail=None, comment=None):
    """Fire-and-forget: push a task's status / thumbnail / comment up to Kitsu via the
    system Python (which has gazu). Runs core/kitsu_sync.py in a cleaned environment so
    Maya's bundled interpreter doesn't shadow the system one. Never blocks or raises."""
    if not reg or not entity or not task_type:
        return
    script = os.path.normpath(os.path.join(_standalone_root(), "core", "kitsu_sync.py"))
    if not os.path.exists(script):
        print(f"CGPipeline: kitsu_sync.py not found at {script}; skipping Kitsu sync.")
        return
    try:
        cmd = [_find_system_python(), script,
               "--registry", reg, "--entity", str(entity),
               "--category", category or "Assets", "--task-type", task_type]
        if status and status != "NO CHANGE":
            cmd += ["--status", status]
        if thumbnail:
            cmd += ["--thumbnail", thumbnail]
        if comment:
            cmd += ["--comment", comment]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(cmd, env=_clean_system_env(), creationflags=creationflags)
        print("CGPipeline: Kitsu sync dispatched.")
    except Exception as e:
        print(f"CGPipeline: Could not dispatch Kitsu sync: {e}")


def op_open_dashboard():
    root = _standalone_root()
    main_py = os.path.normpath(os.path.join(root, "main.py"))
    if not os.path.exists(main_py):
        cmds.warning(f"CGPipeline: main.py not found at {main_py}")
        return
    py_exe = _find_system_python()
    # Launch with a CLEANED environment (see _clean_system_env) so the dashboard opens
    # against the system Python rather than Maya's bundled interpreter.
    env = _clean_system_env()
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


def _copy_to_master(fp):
    """Copy the working file `fp` over the entity's *_master.ma. Gated by the
    "Update Master File" toggle so the user controls which version becomes the master."""
    if not (STATE.update_master and fp and STATE.entity and STATE.task_path):
        return
    master = f"{STATE.entity}_{TASK_ABBR.get(STATE.task_type, 'task')}_master.ma"
    master_path = os.path.normpath(os.path.join(_master_dir(), master))
    try:
        shutil.copy2(fp, master_path)
        print(f"CGPipeline: Master updated → {master_path}")
    except Exception as e:
        print(f"CGPipeline: Master copy failed: {e}")


def op_save():
    fp = cmds.file(q=True, sceneName=True)
    if not fp:
        cmds.warning("CGPipeline: Scene has no name yet — use Version Up.")
        return
    cmds.file(save=True)
    _copy_to_master(fp)


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
        _copy_to_master(fp)
        print(f"CGPipeline: Saved v{v:03d}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Version save failed: {e}")


def _set_task_status(new_status):
    """Write the chosen status onto the current task in the registry. Returns True if it
    was written. Kitsu is handled by the caller (publish) so a local publish doesn't push
    and a Publish-to-Kitsu pushes status + thumbnail in one go. 'NO CHANGE' is a no-op."""
    if not new_status or new_status == "NO CHANGE":
        return False
    if not STATE.reg_path or not os.path.exists(STATE.reg_path) or not STATE.task_id:
        return False
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
        return True
    except Exception as e:
        cmds.warning(f"CGPipeline: Status update failed: {e}")
        return False


def _project_name():
    """Project display name. Prefer the registry's project_name, but when it's still the
    default placeholder fall back to the authoritative project_index.json entry (matched by
    path), and finally to the project folder name."""
    if not STATE.reg_path:
        return "-"
    proj_root = os.path.dirname(STATE.reg_path)
    name = ""
    if os.path.exists(STATE.reg_path):
        try:
            with open(STATE.reg_path, "r") as f:
                name = (json.load(f).get("project_name") or "").strip()
        except Exception:
            name = ""
    if name and name not in ("New Project", "Unknown Project"):
        return name
    # project_index.json lives in the projects root (the parent of the project folder)
    # and carries the hub's display name — the same source the dashboard hub uses.
    index = os.path.join(os.path.dirname(proj_root), "project_index.json")
    try:
        with open(index, "r") as f:
            for p in json.load(f):
                if os.path.normpath(p.get("path", "")) == os.path.normpath(proj_root) and p.get("name"):
                    return p["name"]
    except Exception:
        pass
    return name or os.path.basename(proj_root) or "-"


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
        # Flags come from the "Additional…" dialog. -uvWrite writes the current UV set;
        # -writeUVSets writes all UV sets; -step sets the frame sampling stride.
        flags = []
        if STATE.abc_uv_write:
            flags.append("-uvWrite")
        if STATE.abc_write_uv_sets:
            flags.append("-writeUVSets")
        if STATE.abc_world_space:
            flags.append("-worldSpace")
        if STATE.abc_write_visibility:
            flags.append("-writeVisibility")
        step = STATE.abc_step if STATE.abc_step and STATE.abc_step > 0 else 1.0
        job = (f'-frameRange {s} {e} -step {step} {" ".join(flags)} '
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


_DEFAULT_CAMERAS = {"persp", "top", "front", "side", "back", "bottom", "left"}


def _scene_roots():
    """Top-level transforms for a "Whole Scene" publish — every assembly except the
    default cameras and the hidden lookdev material-source helper group."""
    out = []
    for a in (cmds.ls(assemblies=True, long=True) or []):
        short = a.split("|")[-1]
        if short in _DEFAULT_CAMERAS or short == LOOKDEV_SHD_GROUP:
            continue
        out.append(a)
    return out


def _run_publish():
    """Export the publish list (or the whole scene) to disk. Returns (ok, publish_folder)."""
    items = _scene_roots() if STATE.publish_whole_scene else list(STATE.publish_list)
    if not items:
        if STATE.publish_whole_scene:
            cmds.warning("CGPipeline: Whole Scene is on but the scene has nothing to export.")
        else:
            cmds.warning("CGPipeline: Publish list is empty — add objects or tick Whole Scene.")
        return False, None
    is_shot = STATE.category == "Shots"
    # Shot caches go into the task's own cache folder, e.g. Shots/<shot>/Anim/cache.
    # Asset publishes go to the asset's Publish folder.
    if is_shot:
        pub = os.path.join(STATE.task_path, "cache")
    else:
        pub = _resolve_publish_folder()
    if not pub:
        cmds.warning("CGPipeline: Could not resolve publish folder.")
        return False, None
    os.makedirs(pub, exist_ok=True)
    abbr = TASK_ABBR.get(STATE.task_type, "task")
    fmt = STATE.publish_format
    s, e, is_anim = _frame_range()
    rng = f"_f{s:04d}_f{e:04d}"

    # Whole Scene exports everything as one file (per-object only still makes sense for
    # shot caches, which are always split so the assembly can assign each one).
    separate = STATE.publish_separate and not STATE.publish_whole_scene

    if is_shot:
        # Shot caches are ALWAYS one file per selected object/group, named with the
        # object, so the assembly can assign each one:
        #   <shot>_<object>_<task>_f0001_f0024.ext  (e.g. sh01_sq0010_woody_anim_f0001_f0024.abc)
        for obj in items:
            if not cmds.objExists(obj):
                continue
            on = _safe_name(obj)
            if "cam" in obj.lower():
                fn = f"{STATE.entity}_{on}_cam{rng}{fmt}"
            else:
                fn = f"{STATE.entity}_{on}_{abbr}{rng}{fmt}"
            _export([obj], os.path.join(pub, fn), is_anim, s, e)
    elif separate:
        for obj in items:
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
        _export(items, os.path.join(pub, fn), is_anim, s, e)

    return True, pub


def op_publish(to_kitsu=False):
    """Publish the selection list (or the whole scene). Applies the chosen status, snaps a
    thumbnail, and saves the working file (refreshing the *_master copy when "Update Master
    File" is on). When `to_kitsu` is True, also pushes status / thumbnail / comment."""
    ok, pub = _run_publish()
    if not ok:
        return

    # Update the working file, and the master copy if "Update Master File" is ticked.
    fp = cmds.file(q=True, sceneName=True)
    if fp:
        try:
            cmds.file(save=True)
        except Exception as e:
            print(f"CGPipeline: Could not save working file on publish: {e}")
        _copy_to_master(fp)

    # Apply the status picked in the Status dropdown (registry). "NO CHANGE" is a no-op.
    new_status = STATE.publish_status
    status_changed = _set_task_status(new_status)

    # Always snapshot a thumbnail on publish and set it on the task.
    thumb, tmsg = capture_task_thumbnail()
    print(f"CGPipeline: Auto thumbnail: {tmsg}")

    status_line = f"\nStatus → {new_status}" if status_changed else ""

    if to_kitsu:
        # Push status + thumbnail + comment up to Kitsu in one go (system Python).
        note = STATE.publish_notes.strip() or None
        if status_changed or thumb or note:
            fire_kitsu_sync(
                STATE.reg_path, STATE.entity, STATE.category, STATE.task_type,
                status=(new_status if status_changed else None),
                thumbnail=thumb, comment=note,
            )
            kmsg = "Kitsu update dispatched (status / thumbnail / comment)."
        else:
            kmsg = "Nothing to send to Kitsu — pick a status or add a note."
        cmds.confirmDialog(title="Publish to Kitsu",
                           message=f"Published → {pub}{status_line}\n\n{kmsg}")
    else:
        cmds.confirmDialog(title="Publish", message=f"Published → {pub}{status_line}")


# --------------------------------------------------------------------------------------
# Operations: Thumbnail
# --------------------------------------------------------------------------------------
def _thumbnail_panel():
    """A modelPanel to playblast from — the focused one if it's a model panel, else the
    first available model panel (or None)."""
    try:
        p = cmds.getPanel(withFocus=True)
        if p and cmds.getPanel(typeOf=p) == "modelPanel":
            return p
    except Exception:
        pass
    panels = cmds.getPanel(type="modelPanel") or []
    return panels[0] if panels else None


def capture_task_thumbnail():
    """Playblast the active viewport to <project>/.thumbnails/<entity>.png and set it as
    the thumbnail for every task of the current entity in the registry (so the dashboard
    card picks it up). Returns (path_or_None, message)."""
    if not STATE.reg_path or not os.path.exists(STATE.reg_path) or not STATE.entity:
        return None, "No task context — open the task via the dashboard, or save inside a CGPipeline project."
    project_root = os.path.dirname(STATE.reg_path)
    thumbs_dir = os.path.join(project_root, ".thumbnails")
    try:
        os.makedirs(thumbs_dir, exist_ok=True)
    except Exception as e:
        return None, f"Could not create thumbnails folder: {e}"
    clean = str(STATE.entity).replace(" ", "_")
    final_path = os.path.normpath(os.path.join(thumbs_dir, f"{clean}.png"))

    panel = _thumbnail_panel()
    if not panel:
        return None, "No 3D viewport available to capture."
    cur = int(cmds.currentTime(q=True))

    # Clear the selection so selection highlights don't end up in the thumbnail, then
    # restore it afterwards.
    saved_sel = cmds.ls(selection=True) or []
    try:
        cmds.select(clear=True)
    except Exception:
        pass
    kwargs = dict(
        completeFilename=final_path, format="image", compression="png",
        frame=[cur], viewer=False, offScreen=True, showOrnaments=False,
        percent=100, quality=100, widthHeight=[640, 360], forceOverwrite=True,
    )
    try:
        try:
            cmds.playblast(editorPanelName=panel, **kwargs)
        except TypeError:
            cmds.playblast(**kwargs)   # older Maya without editorPanelName
    except Exception as e:
        return None, f"Playblast failed: {e}"
    finally:
        try:
            if saved_sel:
                cmds.select(saved_sel, replace=True)
        except Exception:
            pass

    if not os.path.exists(final_path):
        # Some Maya builds ignore completeFilename and write <name>.<frame>.png.
        alt = f"{os.path.splitext(final_path)[0]}.{cur:04d}.png"
        if os.path.exists(alt):
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
                shutil.move(alt, final_path)
            except Exception:
                final_path = alt
        else:
            return None, "Playblast produced no image."

    # Update the registry so the dashboard card picks it up (all tasks of this entity).
    try:
        with open(STATE.reg_path, "r") as f:
            data = json.load(f)
        for tk in data.get("tasks", []):
            if tk.get("name") == STATE.entity:
                tk["thumbnail"] = final_path
        with open(STATE.reg_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        return final_path, f"Thumbnail saved but registry update failed: {e}"

    print(f"CGPipeline: Thumbnail updated → {final_path}")
    return final_path, "Thumbnail updated."


# --------------------------------------------------------------------------------------
# Operations: Clean Up (Optimize Scene Size — explicit, predictable)
# --------------------------------------------------------------------------------------
def _remove_unknown_nodes():
    """Delete unknown nodes (left by missing plugins) and drop unknown-plugin
    requirements, so the scene stops carrying them. Returns the count removed."""
    count = 0
    nodes = []
    for t in ("unknown", "unknownDag", "unknownTransform"):
        nodes += cmds.ls(type=t) or []
    for n in dict.fromkeys(nodes):
        if not cmds.objExists(n):
            continue
        try:
            if cmds.lockNode(n, q=True, lock=True)[0]:
                cmds.lockNode(n, lock=False)
        except Exception:
            pass
        try:
            cmds.delete(n)
            count += 1
        except Exception:
            pass
    for p in (cmds.unknownPlugin(q=True, list=True) or []):
        try:
            cmds.unknownPlugin(p, remove=True)
        except Exception:
            pass
    return count


def _delete_unused_nodes():
    """Maya's Hypershade "Delete Unused Nodes" (removes orphaned shading nodes)."""
    try:
        mel.eval("MLdeleteUnused;")
        return True
    except Exception as e:
        print(f"CGPipeline: Delete unused nodes failed: {e}")
        return False


def _base_name(node):
    """Short node name with namespace/DAG path and any trailing digits stripped, so
    'ns:wood_mtl12' -> 'wood_mtl'. Used to group likely shader duplicates."""
    short = node.split("|")[-1].split(":")[-1]
    return re.sub(r"\d+$", "", short)


def _shader_signature(mat):
    """A hashable signature of a material's look: node type plus each keyable scalar /
    common colour input — either its value, or (for connected inputs) the source file
    texture path / source node type. Two materials with the same signature render
    identically, so one is a duplicate of the other."""
    try:
        sig = [cmds.nodeType(mat)]
    except Exception:
        return None
    attrs = set(cmds.listAttr(mat, keyable=True, write=True, scalar=True) or [])
    for a in ("color", "baseColor", "diffuse", "transparency", "incandescence",
              "specularColor", "reflectivity", "emissionColor"):
        if cmds.attributeQuery(a, node=mat, exists=True):
            attrs.add(a)
    for attr in sorted(attrs):
        plug = mat + "." + attr
        if not cmds.objExists(plug):
            continue
        src = cmds.listConnections(plug, s=True, d=False) or []
        if src:
            snode = src[0]
            try:
                if cmds.nodeType(snode) == "file":
                    sig.append((attr, "file:" + (cmds.getAttr(snode + ".fileTextureName") or "")))
                else:
                    sig.append((attr, "conn:" + cmds.nodeType(snode)))
            except Exception:
                sig.append((attr, "conn"))
            continue
        try:
            val = cmds.getAttr(plug)
        except Exception:
            continue
        if isinstance(val, list):
            val = tuple(tuple(v) if isinstance(v, (list, tuple)) else v for v in val)
        sig.append((attr, val))
    return tuple(sig)


def _remove_duplicate_shaders():
    """Merge duplicate shaders: materials of the same node type, same base name and
    identical signature are collapsed onto one, reassigning the duplicates' shading-group
    members. Conservative — default materials are never deleted, and anything that fails
    is left untouched. Returns the number of duplicates merged."""
    merged = 0
    mat_to_se = {}
    for se in (cmds.ls(type="shadingEngine") or []):
        for m in (cmds.listConnections(se + ".surfaceShader", s=True, d=False) or []):
            mat_to_se.setdefault(m, se)
    groups = {}
    for mat, se in mat_to_se.items():
        sig = _shader_signature(mat)
        if sig is None:
            continue
        groups.setdefault((_base_name(mat), sig), []).append((mat, se))
    for items in groups.values():
        if len(items) < 2:
            continue
        # Keeper: prefer a protected/default material, then the shortest name.
        items.sort(key=lambda x: (x[0] not in _PROTECTED_SHADING and x[1] not in _PROTECTED_SHADING,
                                  len(x[0]), x[0]))
        keep_mat, keep_se = items[0]
        for dup_mat, dup_se in items[1:]:
            if dup_se == keep_se or dup_mat in _PROTECTED_SHADING or dup_se in _PROTECTED_SHADING:
                continue
            try:
                members = cmds.sets(dup_se, q=True) or []
                if members:
                    cmds.sets(members, forceElement=keep_se)
                for n in (dup_se, dup_mat):
                    if cmds.objExists(n):
                        try:
                            cmds.delete(n)
                        except Exception:
                            pass
                merged += 1
            except Exception as e:
                print(f"CGPipeline: duplicate shader merge failed for {dup_mat}: {e}")
    return merged


def _cleanup_uv_sets():
    """Delete every UV set on each mesh except the default — "map1" if present, else the
    first set. Returns the number of meshes that had extra sets removed."""
    count = 0
    for mesh in (cmds.ls(type="mesh", long=True) or []):
        try:
            if cmds.getAttr(mesh + ".intermediateObject"):
                continue
        except Exception:
            pass
        try:
            sets = cmds.polyUVSet(mesh, q=True, allUVSets=True) or []
        except Exception:
            continue
        if len(sets) <= 1:
            continue
        keep = "map1" if "map1" in sets else sets[0]
        # The current UV set can't be deleted, so switch to the keeper first.
        try:
            cmds.polyUVSet(mesh, currentUVSet=True, uvSet=keep)
        except Exception:
            pass
        removed_any = False
        for uv in sets:
            if uv == keep:
                continue
            try:
                cmds.polyUVSet(mesh, delete=True, uvSet=uv)
                removed_any = True
            except Exception as ex:
                print(f"CGPipeline: Could not delete UV set '{uv}' on {mesh}: {ex}")
        if removed_any:
            count += 1
    return count


def _remove_turtle_nodes():
    """Remove Autodesk Turtle renderer leftovers — its persistent nodes plus the plugin
    itself. Turtle nodes are a frequent source of 'unknown'/dirty nodes in published
    scenes. Returns the count of nodes removed."""
    count = 0
    nodes = set()
    for n in ("TurtleDefaultBakeLayer", "TurtleRenderOptions",
              "TurtleUIOptions", "TurtleBakeLayerManager"):
        if cmds.objExists(n):
            nodes.add(n)
    for t in ("ilrOptionsNode", "ilrUIOptionsNode", "ilrBakeLayerManager", "ilrBakeLayer"):
        nodes.update(cmds.ls(type=t) or [])
    for n in nodes:
        if not cmds.objExists(n):
            continue
        try:
            cmds.lockNode(n, lock=False)
        except Exception:
            pass
        try:
            cmds.delete(n)
            count += 1
        except Exception:
            pass
    # Unload the plugin so the nodes don't get recreated and the scene stops requiring it.
    try:
        if cmds.pluginInfo("Turtle", q=True, loaded=True):
            cmds.unloadPlugin("Turtle")
    except Exception:
        pass
    return count


def op_cleanup_scene():
    """Optimize / clean the current scene: remove unknown nodes, remove Turtle renderer
    leftovers, delete unused shading nodes, merge duplicate shaders, and strip extra UV
    sets (keeping only the default 'map1'). Equivalent to Maya's Optimize Scene Size with
    those options, done explicitly so the result is predictable."""
    removed_unknown = _remove_unknown_nodes()
    removed_turtle = _remove_turtle_nodes()
    merged_shaders = _remove_duplicate_shaders()
    unused_ok = _delete_unused_nodes()
    cleaned_uv = _cleanup_uv_sets()
    msg = (
        "Clean Up complete:\n"
        f"  • Unknown nodes removed: {removed_unknown}\n"
        f"  • Turtle nodes removed: {removed_turtle}\n"
        f"  • Duplicate shaders merged: {merged_shaders}\n"
        f"  • Unused nodes: {'deleted' if unused_ok else 'skipped (error)'}\n"
        f"  • Meshes with extra UV sets cleaned: {cleaned_uv}"
    )
    print("CGPipeline: " + msg.replace("\n", "  "))
    if not cmds.about(batch=True):
        cmds.confirmDialog(title="Clean Up", message=msg)
    return msg


# --------------------------------------------------------------------------------------
# Operations: Camera cache import (assembly)
# --------------------------------------------------------------------------------------
_CAM_CACHE_RE = re.compile(r"_cam_f\d+_f\d+\.(abc|usd|usda|usdc|fbx)$", re.IGNORECASE)


def _find_camera_caches():
    """Published camera caches in the current shot — files named like
    sh01_sq0010_cam_f1000_f1010.abc under any dept's cache/Publish folder. Newest first,
    de-duplicated by file name."""
    shot_root = _shot_root_from_task_path()
    if not shot_root or not os.path.isdir(shot_root):
        return []
    found = []
    for dept in os.listdir(shot_root):
        dept_path = os.path.join(shot_root, dept)
        if not os.path.isdir(dept_path):
            continue
        for sub in ("cache", "Publish", ""):
            scan = os.path.join(dept_path, sub) if sub else dept_path
            if not os.path.isdir(scan):
                continue
            for f in os.listdir(scan):
                if _CAM_CACHE_RE.search(f):
                    full = os.path.join(scan, f)
                    try:
                        found.append((full, os.path.getmtime(full)))
                    except Exception:
                        found.append((full, 0))
    found.sort(key=lambda x: x[1], reverse=True)
    seen, out = set(), []
    for p, _ in found:
        b = os.path.basename(p)
        if b not in seen:
            seen.add(b)
            out.append(p)
    return out


def op_import_camera():
    """Auto-detect the shot's published camera cache and import it. Imports the newest
    when several exist."""
    cams = _find_camera_caches()
    if not cams:
        cmds.warning("CGPipeline: No camera cache found in this shot "
                     "(looking for *_cam_f####_f####).")
        return
    chosen = cams[0]
    ext = os.path.splitext(chosen)[1].lower()
    fwd = chosen.replace("\\", "/")
    try:
        if ext == ".abc":
            cmds.AbcImport(fwd, mode="import")
        elif ext in (".usd", ".usda", ".usdc"):
            cmds.mayaUSDImport(file=fwd)
        elif ext == ".fbx":
            mel.eval(f'FBXImport -f "{fwd}";')
        else:
            cmds.warning(f"CGPipeline: Unsupported camera cache format: {ext}")
            return
        print(f"CGPipeline: Imported camera → {chosen}")
        cmds.confirmDialog(title="Import Camera",
                           message=f"Imported camera:\n{os.path.basename(chosen)}")
    except Exception as e:
        cmds.warning(f"CGPipeline: Camera import failed: {e}")


# --------------------------------------------------------------------------------------
# Operations: Render
# --------------------------------------------------------------------------------------
def _entity_root():
    """Root folder of the current entity — the shot root for shots, the asset root
    (Assets/<cat>/<asset>) for assets. None when it can't be derived."""
    sr = _shot_root_from_task_path()
    if sr:
        return sr
    if STATE.task_path:
        parts = os.path.normpath(STATE.task_path).replace("\\", "/").split("/")
        if "Assets" in parts:
            i = parts.index("Assets")
            if len(parts) >= i + 3:
                return os.path.normpath("/".join(parts[:i + 3]))
    return None


def _shot_frame_range():
    """The shot's project-sheet frame range (frame_start/frame_end on the registry task
    for this entity), or None if unset."""
    if not STATE.reg_path or not os.path.exists(STATE.reg_path) or not STATE.entity:
        return None
    try:
        with open(STATE.reg_path, "r") as f:
            data = json.load(f)
    except Exception:
        return None
    for t in data.get("tasks", []):
        if t.get("name") == STATE.entity and \
                t.get("frame_start") is not None and t.get("frame_end") is not None:
            try:
                return int(t["frame_start"]), int(t["frame_end"])
            except (TypeError, ValueError):
                return None
    return None


def _current_work_version():
    """Version number parsed from the open scene's filename (…_v003.ma → 3), else 1."""
    try:
        scene = cmds.file(q=True, sceneName=True) or ""
    except Exception:
        scene = ""
    m = re.search(r"_v(\d+)\.", os.path.basename(scene))
    return int(m.group(1)) if m else 1


def _render_output_dir():
    """<entity_root>/Render/<entity>_<abbr>_v###  — renders are versioned per work file."""
    root = _entity_root()
    if not root:
        return None
    abbr = TASK_ABBR.get(STATE.task_type, "task")
    ver = _current_work_version()
    name = f"{STATE.entity or 'render'}_{abbr}_v{ver:03d}"
    return os.path.normpath(os.path.join(root, "Render", name))


def _resolve_render_range():
    mode = STATE.render_range_mode
    if mode == "Single Frame":
        try:
            f = int(cmds.currentTime(q=True))
        except Exception:
            f = STATE.render_start
        return f, f
    if mode == "Frame Range":
        rng = _shot_frame_range()
        if rng:
            return rng
    return int(STATE.render_start), int(STATE.render_end)


def _set_renderable_camera(cam):
    """Make `cam` (transform or shape) the only renderable camera."""
    target = None
    if cmds.objExists(cam):
        if cmds.nodeType(cam) == "camera":
            target = (cmds.ls(cam, long=True) or [cam])[0]
        else:
            shapes = cmds.listRelatives(cam, shapes=True, type="camera", fullPath=True) or []
            target = shapes[0] if shapes else None
    for c in (cmds.ls(type="camera", long=True) or []):
        try:
            cmds.setAttr(c + ".renderable", 1 if c == target else 0)
        except Exception:
            pass


# Maya defaultRenderGlobals.imageFormat enum values, and Arnold aiTranslator names.
_IMAGE_FORMAT_ENUM = {"exr": 51, "png": 32, "tif": 3, "tiff": 3,
                      "jpg": 8, "jpeg": 8, "tga": 19, "iff": 7}
_ARNOLD_FORMAT = {"exr": "exr", "png": "png", "tif": "tif", "tiff": "tif",
                  "jpg": "jpeg", "jpeg": "jpeg"}


def _set_image_format(ext):
    ext = ext.lower().lstrip(".")
    if cmds.objExists("defaultArnoldDriver") and ext in _ARNOLD_FORMAT:
        try:
            cmds.setAttr("defaultArnoldDriver.aiTranslator", _ARNOLD_FORMAT[ext], type="string")
        except Exception:
            pass
    if ext in _IMAGE_FORMAT_ENUM:
        try:
            cmds.setAttr("defaultRenderGlobals.imageFormat", _IMAGE_FORMAT_ENUM[ext])
        except Exception:
            pass


def _apply_render_settings():
    """Push the Render-tab settings onto the render globals and point the output at
    <entity_root>/Render/<task>_v###. Returns (render_dir, start, end)."""
    s, e = _resolve_render_range()
    # Resolution
    try:
        cmds.setAttr("defaultResolution.width", int(STATE.render_res_w))
        cmds.setAttr("defaultResolution.height", int(STATE.render_res_h))
        cmds.setAttr("defaultResolution.deviceAspectRatio",
                     float(STATE.render_res_w) / float(STATE.render_res_h))
        cmds.setAttr("defaultResolution.pixelAspect", 1.0)
    except Exception:
        pass
    # Frame range + naming → <RenderLayer>/<RenderLayer>.####.ext
    try:
        cmds.setAttr("defaultRenderGlobals.animation", 1 if s != e else 0)
        cmds.setAttr("defaultRenderGlobals.startFrame", s)
        cmds.setAttr("defaultRenderGlobals.endFrame", e)
        cmds.setAttr("defaultRenderGlobals.byFrameStep", 1)
        cmds.setAttr("defaultRenderGlobals.extensionPadding", 4)
        cmds.setAttr("defaultRenderGlobals.outFormatControl", 0)
        cmds.setAttr("defaultRenderGlobals.putFrameBeforeExt", 1)
        cmds.setAttr("defaultRenderGlobals.periodInExt", 1)   # name.#.ext
    except Exception:
        pass
    # The versioned Render/<task>_v### folder already identifies the shot, so the prefix
    # is just <RenderLayer>/<RenderLayer> → <RenderLayer>/<RenderLayer>.####.ext.
    prefix = "<RenderLayer>/<RenderLayer>"
    try:
        cmds.setAttr("defaultRenderGlobals.imageFilePrefix", prefix, type="string")
    except Exception:
        pass
    if STATE.render_camera:
        _set_renderable_camera(STATE.render_camera)
    _set_renderable_layer(STATE.render_layer)
    _set_image_format(STATE.render_format)
    # Output directory → the versioned Render folder. Set as the session 'images' rule
    # (not persisted) so a Maya-UI render lands there too; the batch render also gets it
    # explicitly via -rd.
    rdir = _render_output_dir()
    if rdir:
        try:
            os.makedirs(rdir, exist_ok=True)
            cmds.workspace(fileRule=["images", rdir])
        except Exception as ex:
            print(f"CGPipeline: Could not set render image dir: {ex}")
    return rdir, s, e


def _set_renderable_layer(layer):
    """Make `layer` the only renderable render layer (so the batch render writes just it).
    'masterLayer'/'' maps to defaultRenderLayer."""
    target = "defaultRenderLayer" if layer in ("", "masterLayer", "defaultRenderLayer") else layer
    for rl in (cmds.ls(type="renderLayer") or []):
        try:
            cmds.setAttr(rl + ".renderable", 1 if rl == target else 0)
        except Exception:
            pass


def _find_mayabatch_exe():
    """mayabatch(.exe) — Maya in batch mode — next to maya(.exe)."""
    name = "mayabatch.exe" if os.name == "nt" else "maya"
    cand = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(cand):
        return cand
    return shutil.which("mayabatch") or shutil.which("maya")


def op_render():
    """Apply the Render-tab settings, save the scene, and launch a mayabatch batch render
    into <entity_root>/Render/<task>_v###. mayabatch opens the saved scene and runs
    `renderSequence` over the frame range using the scene's renderer + settings."""
    rdir, s, e = _apply_render_settings()
    fp = cmds.file(q=True, sceneName=True)
    if not fp:
        cmds.warning("CGPipeline: Save the scene first (Version Up) before rendering.")
        return
    try:
        cmds.file(save=True)
    except Exception as ex:
        print(f"CGPipeline: Could not save before render: {ex}")

    mayabatch = _find_mayabatch_exe()
    if not mayabatch:
        cmds.confirmDialog(
            title="Render",
            message=("Render settings applied and output set to:\n"
                     f"{rdir}\n\nmayabatch wasn't found, so start a Batch Render from Maya "
                     "(Render menu) to write the frames."))
        return

    # MEL run by mayabatch after it loads the scene: lock the range, point the images
    # rule at our versioned Render folder, then render the sequence to disk.
    rdir_fwd = (rdir or "").replace("\\", "/")
    mel_cmd = (
        f"playbackOptions -min {s} -max {e} -ast {s} -aet {e}; "
        f"currentTime {s}; "
        f"setAttr defaultRenderGlobals.startFrame {s}; "
        f"setAttr defaultRenderGlobals.endFrame {e}; "
        f"setAttr defaultRenderGlobals.animation 1; "
    )
    if rdir_fwd:
        mel_cmd += f'workspace -fileRule "images" "{rdir_fwd}"; '
    mel_cmd += "renderSequence;"

    cmd = [mayabatch]
    if STATE.reg_path:
        cmd += ["-proj", os.path.dirname(STATE.reg_path)]
    cmd += ["-file", fp, "-command", mel_cmd]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=creationflags)
        print("CGPipeline: mayabatch render launched: " + subprocess.list2cmdline(cmd))
        cmds.confirmDialog(
            title="Render",
            message=(f"Batch render started → {rdir}\n\n"
                     f"Frames {s}-{e}, layer '{STATE.render_layer or 'masterLayer'}', "
                     f"{STATE.render_res_w}x{STATE.render_res_h}, .{STATE.render_format}."))
    except Exception as e2:
        cmds.warning(f"CGPipeline: Could not launch mayabatch render: {e2}")


def _scene_cameras():
    """Renderable scene cameras (transforms), default cameras last."""
    out, defaults = [], []
    for shp in (cmds.ls(type="camera", long=True) or []):
        par = (cmds.listRelatives(shp, parent=True, fullPath=True) or [None])[0]
        if not par:
            continue
        short = par.split("|")[-1]
        if short in _DEFAULT_CAMERAS:
            defaults.append(short)
        else:
            out.append(short)
    return out + defaults


def _scene_render_layers():
    """Render layer names for the picker — 'masterLayer' plus any user render layers
    (including Render Setup's rs_<name> nodes, whose names are what -rl expects)."""
    layers = ["masterLayer"]
    for rl in (cmds.ls(type="renderLayer") or []):
        if rl == "defaultRenderLayer":
            continue
        if rl not in layers:
            layers.append(rl)
    return layers


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
def _rel_key(full_path, root_long):
    """Namespace-stripped node names below `root_long`, as a tuple. Used to match a
    cache mesh to the corresponding mesh under a target group regardless of
    namespace, e.g. |woody1:CH_Woody|woody1:body|woody1:bodyShape -> ('body','bodyShape')."""
    if root_long and full_path.startswith(root_long):
        sub = full_path[len(root_long):].strip("|")
    else:
        sub = full_path.strip("|")
    return tuple(p.split(":")[-1] for p in sub.split("|") if p)


def _mesh_shapes(root):
    """Non-intermediate mesh shapes under `root` (full DAG paths)."""
    out = []
    for m in (cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []):
        try:
            if cmds.getAttr(m + ".intermediateObject"):
                continue
        except Exception:
            pass
        out.append(m)
    return out


def _transfer_shading(src_shape, dst_shape):
    """Copy the shading-group assignment from src_shape onto dst_shape — both whole-
    object and per-face. Assumes identical topology (same face indices), so the face
    component lists transfer directly. dst is fresh cache geometry, so we just assign
    it to the source's shading groups. Returns True if anything was assigned."""
    src_long = (cmds.ls(src_shape, long=True) or [src_shape])[0]
    src_parent = (cmds.listRelatives(src_shape, parent=True, fullPath=True) or [None])[0]
    own = {src_long, src_parent} - {None}
    assigned = False
    for sg in dict.fromkeys(cmds.listConnections(src_shape, type="shadingEngine") or []):
        comps, whole = [], False
        for m in (cmds.sets(sg, q=True) or []):
            node = m.split(".")[0]
            node_long = cmds.ls(node, long=True) or []
            if not (node_long and node_long[0] in own):
                continue
            if "." in m:
                comps.append(m.split(".", 1)[1])   # e.g. 'f[0:50]'
            else:
                whole = True
        try:
            if comps:
                cmds.sets([dst_shape + "." + c for c in comps], forceElement=sg)
                assigned = True
            elif whole:
                cmds.sets(dst_shape, forceElement=sg)
                assigned = True
        except Exception as e:
            print(f"CGPipeline: shading transfer failed for {sg}: {e}")
    return assigned


def _remove_imported_cache_by_tag(cache_tag):
    """Delete a previously imported cache tagged with `cache_tag` (its file name), so
    re-applying the same cache swaps cleanly instead of stacking copies."""
    for nr in (cmds.ls("*.cgpCacheImport", objectsOnly=True, long=True) or []):
        try:
            if cmds.getAttr(nr + ".cgpCacheImport") == cache_tag:
                cmds.delete(nr)
        except Exception:
            pass


# Outliner organisation: category groups for renderable assembly geometry, and one
# hidden group for the lookdev material-source references used for shader transfer.
LOOKDEV_SHD_GROUP = "_LOOKDEV_SHD_"
CATEGORY_GROUP_MAP = {
    "char": "_CHAR_", "character": "_CHAR_", "characters": "_CHAR_",
    "set": "_SETS_", "sets": "_SETS_", "setdress": "_SETS_",
    "prop": "_PROPS_", "props": "_PROPS_",
    "env": "_ENV_", "environment": "_ENV_", "environments": "_ENV_",
    "veh": "_VEH_", "vehicle": "_VEH_", "vehicles": "_VEH_",
}


def _category_group_name(category):
    key = (category or "").strip().lower()
    if key in CATEGORY_GROUP_MAP:
        return CATEGORY_GROUP_MAP[key]
    return "_" + (category or "MISC").strip().upper().replace(" ", "_") + "_"


def _get_or_make_group(name, hidden=False):
    """Get (or create) a top-level empty group called `name`."""
    existing = cmds.ls("|" + name, long=True) or []
    if existing:
        return existing[0]
    g = (cmds.ls(cmds.group(empty=True, world=True, name=name), long=True) or [name])[0]
    if hidden:
        try:
            cmds.setAttr(g + ".visibility", 0)
        except Exception:
            pass
    return g


def _parent_under(node, group):
    """Reparent `node` under `group`, relative (no world compensation, so animated /
    connected transforms aren't disturbed — the groups are at the origin). Returns the
    node's resolved path after the move."""
    try:
        moved = cmds.parent(node, group, relative=True) or [node]
        return (cmds.ls(moved[0], long=True) or [moved[0]])[0]
    except Exception:
        return node


def _ensure_lookdev_referenced(lookdev_item):
    """Bring the lookdev in ONCE as a shared material source, reusing it if already
    present (tracked by a tag). IMPORTED (not referenced) so the nodes are clean and
    un-namespaced; parented immediately under the hidden _LOOKDEV_SHD_ group (which also
    clears it from the root so a later cache import keeps the clean CH_Woody name). It
    only provides materials, the caches render. Returns the material-source top groups."""
    asset = lookdev_item.get("asset_name") or "lkdev"
    shd = _get_or_make_group(LOOKDEV_SHD_GROUP, hidden=True)

    # Reuse an existing (tagged) material source for this asset.
    existing = []
    for c in (cmds.listRelatives(shd, children=True, fullPath=True) or []):
        try:
            if cmds.attributeQuery("cgpLookdevSrc", node=c, exists=True) and \
                    cmds.getAttr(c + ".cgpLookdevSrc") == asset:
                existing.append(c)
        except Exception:
            pass
    if existing:
        return existing

    before = set(cmds.ls(assemblies=True, long=True) or [])
    try:
        cmds.file(lookdev_item["path"], i=True, ignoreVersion=True,
                  mergeNamespacesOnClash=True, namespace=":")
        print(f"CGPipeline: Imported lookdev material source '{asset}'.")
    except Exception as e:
        cmds.warning(f"CGPipeline: Lookdev import failed: {e}")
        return []
    out = []
    for r in (set(cmds.ls(assemblies=True, long=True) or []) - before):
        r2 = _parent_under(r, shd)
        try:
            if not cmds.attributeQuery("cgpLookdevSrc", node=r2, exists=True):
                cmds.addAttr(r2, longName="cgpLookdevSrc", dataType="string")
            cmds.setAttr(r2 + ".cgpLookdevSrc", asset, type="string")
            cmds.setAttr(r2 + ".visibility", 0)   # hide material source; caches render
        except Exception:
            pass
        out.append(r2)
    return out


def op_reference_lookdev(lookdev_item):
    """Bring a lookdev into the scene as VISIBLE geometry — for assets that are NOT
    driven by an animation cache (sets, props, or any asset to keep as-is). IMPORTED
    (not referenced) so the nodes are clean and un-namespaced; Maya auto-numbers
    duplicates (CH_Woody, CH_Woody1, …) with no wrapper. Each instance sits as a direct
    child of the asset's category group (_CHAR_/_SETS_/_PROPS_…), at the same level as
    the imported caches."""
    asset = lookdev_item.get("asset_name") or "lkdev"
    before = set(cmds.ls(assemblies=True, long=True) or [])
    try:
        cmds.file(lookdev_item["path"], i=True, ignoreVersion=True,
                  mergeNamespacesOnClash=True, namespace=":")
    except Exception as e:
        cmds.warning(f"CGPipeline: Import failed: {e}")
        return
    cat_grp = _get_or_make_group(_category_group_name(lookdev_item.get("category")))
    for r in (set(cmds.ls(assemblies=True, long=True) or []) - before):
        _parent_under(r, cat_grp)
    print(f"CGPipeline: Imported lookdev '{asset}' into scene "
          f"({_category_group_name(lookdev_item.get('category'))}).")


_CACHE_NAME_RE = re.compile(
    r"^sh\d+_sq\d+_(.+)_f\d+_f\d+\.(abc|usd|usda|usdc|fbx)$", re.IGNORECASE)


def _is_object_cache(name):
    """True only for caches named SH##_SQ####_<object>_<task>_f####_f####.<ext> that
    INCLUDE an object name. 'sh01_sq0010_anim_f0001_f0024.abc' (object omitted) has a
    single token between the sequence and the frame range, so it's excluded; an object
    name adds at least one more '_'-separated token."""
    m = _CACHE_NAME_RE.match(name)
    return bool(m) and "_" in m.group(1)


def _import_cache_and_shade(cache_path, lookdev_roots, category=""):
    """Import the alembic cache as its own geometry and copy the lookdev's shaders onto
    it (whole-object AND per-face). `lookdev_roots` are the referenced lookdev groups
    used purely as the material source. Topology / UVs / names match, so per-face
    assignments transfer exactly. The imported cache is parented under its category
    group (_CHAR_/_SETS_/…) and tagged with its file name so a re-apply replaces it.
    Returns True on success."""
    cache_fwd = cache_path.replace("\\", "/")
    cache_tag = os.path.basename(cache_path)

    look_map = {}
    for lr in lookdev_roots:
        for sh in _mesh_shapes(lr):
            look_map.setdefault(_rel_key(sh, lr), sh)
    if not look_map:
        cmds.warning("CGPipeline: Lookdev has no meshes to source shaders from.")
        return False

    # Clean re-apply: remove a previous import of THIS cache.
    _remove_imported_cache_by_tag(cache_tag)

    before = set(cmds.ls(assemblies=True, long=True) or [])
    try:
        cmds.AbcImport(cache_fwd, mode="import")
    except Exception as e:
        cmds.warning(f"CGPipeline: AbcImport failed for {cache_tag}: {e}")
        return False
    new_roots = list(set(cmds.ls(assemblies=True, long=True) or []) - before)
    if not new_roots:
        cmds.warning(f"CGPipeline: Cache produced no geometry: {cache_tag}.")
        return False

    cat_grp = _get_or_make_group(_category_group_name(category)) if category else None
    transferred = 0
    for nr in new_roots:
        for sh in _mesh_shapes(nr):
            src = look_map.get(_rel_key(sh, nr))
            if src and _transfer_shading(src, sh):
                transferred += 1
        node = _parent_under(nr, cat_grp) if cat_grp else nr
        try:
            if not cmds.attributeQuery("cgpCacheImport", node=node, exists=True):
                cmds.addAttr(node, longName="cgpCacheImport", dataType="string")
            cmds.setAttr(node + ".cgpCacheImport", cache_tag, type="string")
        except Exception:
            pass

    if transferred:
        print(f"CGPipeline: Imported {cache_tag}; copied lookdev shaders to {transferred} shape(s).")
    else:
        cmds.warning(f"CGPipeline: Imported {cache_tag} but matched no shapes to copy shaders onto.")
    return True


def _resolve_cache_path(cache_name):
    """Full path of a cache file by name: prefer the scan result, else search the
    shot's dept cache/Publish folders."""
    p = next((c["path"] for c in STATE.cache_items if c["name"] == cache_name), None)
    if p and os.path.exists(p):
        return p
    shot_root = _shot_root_from_task_path()
    if shot_root and os.path.isdir(shot_root):
        for dept in os.listdir(shot_root):
            for sub in ("cache", "Publish", ""):
                base = os.path.join(shot_root, dept, sub) if sub else os.path.join(shot_root, dept)
                test = os.path.normpath(os.path.join(base, cache_name))
                if os.path.exists(test):
                    return test
    return None


def _auto_match_lookdev(cache_name):
    """Best-guess lookdev for a cache: the asset name that appears in the cache file
    name (e.g. '..._CH_Woody_anim_...' -> a lookdev whose asset is 'CH_Woody')."""
    cl = cache_name.lower()
    best = ""
    for it in STATE.lookdev_items:
        a = (it.get("asset_name") or "").lower()
        if a and a in cl and len(a) > len(best):
            best = it["asset_name"]
    return best


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
                            "name": f, "path": os.path.join(pub, f),
                            "asset_name": asset, "category": cat,
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
                    # Only object caches: SH##_SQ####_<object>_<task>_f####_f####.
                    # Skip object-less names like sh01_sq0010_anim_f0001_f0024.abc.
                    if not _is_object_cache(f):
                        continue
                    if STATE.cache_anim_only and "_anim_" not in fl:
                        continue
                    if not any(c["name"] == f for c in STATE.cache_items):
                        STATE.cache_items.append({"name": f, "path": os.path.join(scan_dir, f)})

    # 3. Build the Cache -> Lookdev assignment list (preserve existing choices).
    existing = {l["cache"]: (l.get("lookdev", ""), l.get("is_selected", True)) for l in STATE.cache_links}
    STATE.cache_links = []
    for c in STATE.cache_items:
        if c["name"] in existing:
            lookdev, sel = existing[c["name"]]
        else:
            lookdev, sel = _auto_match_lookdev(c["name"]), True
        STATE.cache_links.append({"cache": c["name"], "lookdev": lookdev, "is_selected": sel})

    print(f"CGPipeline: Scan complete — {len(STATE.lookdev_items)} lookdev, {len(STATE.cache_items)} caches.")


def op_assembly_apply(batch=False):
    """Import each selected cache and copy its assigned lookdev's shaders onto it. The
    lookdev is referenced once as a shared material source — multiple caches that use
    the same lookdev reuse that single reference."""
    links = STATE.cache_links if batch else [l for l in STATE.cache_links if l.get("is_selected")]
    if not links:
        cmds.warning("CGPipeline: No caches to apply — assign a lookdev and tick Apply.")
        return
    for l in links:
        cache_name, lookdev_name = l.get("cache"), l.get("lookdev")
        if not cache_name:
            continue
        if not lookdev_name:
            print(f"CGPipeline: No lookdev assigned for {cache_name}; skipping.")
            continue
        cache_path = _resolve_cache_path(cache_name)
        if not cache_path:
            print(f"CGPipeline: Cache not found: {cache_name}")
            continue
        ext = os.path.splitext(cache_path)[1].lower()
        if ext != ".abc":
            cmds.warning(f"CGPipeline: This flow imports .abc caches (got {ext}); skipping {cache_name}.")
            continue
        lookdev_item = next((it for it in STATE.lookdev_items if it.get("asset_name") == lookdev_name), None)
        if not lookdev_item:
            print(f"CGPipeline: Lookdev not found: {lookdev_name}")
            continue
        lookdev_roots = _ensure_lookdev_referenced(lookdev_item)
        if not lookdev_roots:
            print(f"CGPipeline: Could not reference lookdev '{lookdev_name}'.")
            continue
        _import_cache_and_shade(cache_path, lookdev_roots, lookdev_item.get("category", ""))


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

        # Always-visible header: dashboard entry point, file actions, current task.
        dash_btn = self._btn("Open Dashboard", op_open_dashboard)
        dash_btn.setMinimumHeight(48)  # ~2 rows tall
        dash_btn.setStyleSheet(
            "background-color: #0078D4; color: #ffffff; font-weight: bold;")
        outer.addWidget(dash_btn)

        # File actions live in the header (moved out of the old "Task" tab) so Save /
        # Version Up are always one click away regardless of the active tab.
        frow = QtWidgets.QHBoxLayout()
        save_btn = self._btn("Save", op_save)
        save_btn.setStyleSheet(
            "background-color: #E58A2E; color: #000000; font-weight: bold; padding: 4px;")
        vup_btn = self._btn("Version Up", op_save_version)
        vup_btn.setStyleSheet(
            "background-color: #3FA34D; color: #ffffff; font-weight: bold; padding: 4px;")
        frow.addWidget(save_btn)
        frow.addWidget(vup_btn)
        outer.addLayout(frow)

        # Project + task on one row, same font size / weight.
        info_row = QtWidgets.QHBoxLayout()
        info_row.setSpacing(10)
        self.project_label = QtWidgets.QLabel("PROJECT: -")
        self.project_label.setStyleSheet(
            "color: #9cc3ff; font-weight: bold; font-size: 16px; padding: 4px 0;")
        self.task_label = QtWidgets.QLabel("TASK: -")
        self.task_label.setStyleSheet(
            "color: #ffffff; font-weight: bold; font-size: 16px; padding: 4px 0;")
        info_row.addWidget(self.project_label)
        info_row.addWidget(self.task_label)
        info_row.addStretch()
        outer.addLayout(info_row)

        tabs = QtWidgets.QTabWidget()
        outer.addWidget(tabs, 1)
        tabs.addTab(self._make_publish_tab(), "Publish")
        tabs.addTab(self._make_assembly_tab(), "Assembly")
        tabs.addTab(self._make_render_tab(), "Render")

    # ---- tabs ----
    def _add_lookdev_section(self, v):
        """Import Model + Textures (the old LookDev tab) — now lives at the top of the
        Assembly tab. The bottom REFRESH button rescans this list with the rest.
        Import Model is asset-only (lists the current asset's model publishes) so it's
        hidden on Shot tasks; Textures stay available everywhere."""
        self.import_model_section = QtWidgets.QWidget()
        iv = QtWidgets.QVBoxLayout(self.import_model_section)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(6)
        iv.addWidget(self._section("Import Model"))
        self.model_list_w = QtWidgets.QListWidget()
        self.model_list_w.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.model_list_w.setMaximumHeight(120)
        iv.addWidget(self.model_list_w)
        mrow = QtWidgets.QHBoxLayout()
        mrow.addWidget(self._btn("REFERENCE", lambda: self._on_import_model("reference")))
        mrow.addWidget(self._btn("IMPORT", lambda: self._on_import_model("import")))
        iv.addLayout(mrow)
        iv.addWidget(self._sep())
        v.addWidget(self.import_model_section)

        # Textures
        v.addWidget(self._section("Textures"))
        v.addWidget(self._btn("Fix Missing Textures", op_fix_texture_paths))
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(self._btn("2K", lambda: op_switch_texture_res("2k")))
        trow.addWidget(self._btn("4K", lambda: op_switch_texture_res("4k")))
        v.addLayout(trow)
        v.addWidget(self._sep())
        self._refresh_model_list()

    def _make_publish_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        v.addWidget(self._section("Publish"))
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
        self.whole_scene_chk = QtWidgets.QCheckBox("Whole Scene")
        self.whole_scene_chk.setChecked(STATE.publish_whole_scene)
        self.whole_scene_chk.setToolTip(
            "Export the entire scene — no need to add anything to the list below.")
        self.whole_scene_chk.toggled.connect(self._on_whole_scene_changed)
        opts.addWidget(self.whole_scene_chk)
        self.master_chk = QtWidgets.QCheckBox("Update Master File")
        self.master_chk.setChecked(STATE.update_master)
        self.master_chk.setToolTip(
            "When on, Save / Version Up / Publish also refresh the entity's *_master file. "
            "Turn off to keep an existing master while you work on a different version.")
        self.master_chk.toggled.connect(lambda c: setattr(STATE, "update_master", c))
        opts.addWidget(self.master_chk)
        opts.addStretch()
        v.addLayout(opts)

        # Additional Alembic export settings (full-width button, like Clean Up).
        add_btn = self._btn("Additional…", self._open_additional_dialog)
        add_btn.setToolTip("Alembic export options: UV Write, World Space, Write "
                           "Visibility, Write UV Sets, and Step.")
        v.addWidget(add_btn)

        v.addWidget(QtWidgets.QLabel("Selection List:"))
        self.publish_list_w = QtWidgets.QListWidget()
        self.publish_list_w.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        v.addWidget(self.publish_list_w, 1)
        lrow = QtWidgets.QHBoxLayout()
        self._list_btns = [
            self._btn("Add", self._on_publish_add),
            self._btn("Remove", self._on_publish_remove),
            self._btn("Clear", self._on_publish_clear),
        ]
        for b in self._list_btns:
            lrow.addWidget(b)
        v.addLayout(lrow)
        # Reflect any pre-set Whole Scene state on the list controls.
        self._on_whole_scene_changed(STATE.publish_whole_scene)

        # Status — applied automatically on Publish (no separate Update button). Grouped
        # with the publish controls (no divider). "NO CHANGE" keeps the current status.
        v.addWidget(self._section("Status"))
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(STATUS_CHOICES)
        self.status_combo.setCurrentText(STATE.publish_status)
        self.status_combo.currentTextChanged.connect(
            lambda t: setattr(STATE, "publish_status", t))
        v.addWidget(self.status_combo)

        # Notes / Comment — posted to Kitsu when using "Publish to Kitsu".
        v.addWidget(self._section("Notes / Comment"))
        self.notes_edit = QtWidgets.QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "Comment for this publish (sent to Kitsu on \"Publish to Kitsu\")…")
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setPlainText(STATE.publish_notes)
        self.notes_edit.textChanged.connect(
            lambda: setattr(STATE, "publish_notes", self.notes_edit.toPlainText()))
        v.addWidget(self.notes_edit)

        # Clean Up — optimize/cleanup the scene; sits above Publish as a pre-publish step.
        v.addWidget(self._sep())
        cleanup_btn = self._btn("CLEAN UP", self._on_cleanup)
        cleanup_btn.setStyleSheet(
            "font-weight: bold; padding: 6px; background-color: #f0c000; color: #000000;")
        cleanup_btn.setToolTip(
            "Optimize the scene: remove unknown + Turtle nodes, delete unused nodes, "
            "merge duplicate shaders, and strip extra UV sets (keep only map1).")
        v.addWidget(cleanup_btn)

        # Publish locally, or publish + push status/thumbnail/comment to Kitsu.
        publish_btn = self._btn("PUBLISH", lambda: op_publish(False))
        publish_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        v.addWidget(publish_btn)
        kitsu_btn = self._btn("PUBLISH TO KITSU", lambda: op_publish(True))
        kitsu_btn.setStyleSheet("font-weight: bold; padding: 6px; background-color: #2f6f4f;")
        v.addWidget(kitsu_btn)
        return self._wrap_scroll(w)

    def _on_cleanup(self):
        op_cleanup_scene()

    def _make_assembly_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # LookDev tools (Import Model + Textures) merged in from the old LookDev tab.
        self._add_lookdev_section(v)

        # Cache assignment is a Shot-only workflow (animation caches assigned a lookdev).
        # On an asset task there are no caches, so this whole block is hidden and a short
        # note points the user at the "Reference Lookdev into Scene" section below.
        self.cache_section = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(self.cache_section)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        cv.addWidget(self._section("Assign Lookdev to Cache"))
        self.cache_tree = QtWidgets.QTreeWidget()
        self.cache_tree.setColumnCount(3)
        self.cache_tree.setHeaderLabels(["Apply", "Cache", "Lookdev"])
        self.cache_tree.itemClicked.connect(self._on_cache_link_clicked)
        cv.addWidget(self.cache_tree, 2)

        self.cache_anim_chk = QtWidgets.QCheckBox("ANIM ONLY")
        self.cache_anim_chk.toggled.connect(self._on_anim_only_changed)
        cv.addWidget(self.cache_anim_chk)

        arow = QtWidgets.QHBoxLayout()
        arow.addWidget(self._btn("APPLY SELECTED", lambda: self._apply_caches(False)))
        arow.addWidget(self._btn("APPLY ALL", lambda: self._apply_caches(True)))
        arow.addWidget(self._btn("CLEAR ALL", self._clear_cache_checks))
        cv.addLayout(arow)

        # Auto-detect and import the shot's published camera cache.
        cam_btn = self._btn("IMPORT CAM", op_import_camera)
        cam_btn.setToolTip("Find this shot's published camera cache "
                           "(…_cam_f####_f####) and import the newest one.")
        cv.addWidget(cam_btn)
        cv.addWidget(self._sep())
        v.addWidget(self.cache_section)

        # Shown instead of the cache block on asset tasks.
        self.cache_assets_note = QtWidgets.QLabel(
            "Cache assembly is a Shot workflow. On an asset, use "
            "“Reference Lookdev into Scene” below.")
        self.cache_assets_note.setWordWrap(True)
        self.cache_assets_note.setStyleSheet("color: #888888; padding: 4px;")
        self.cache_assets_note.setVisible(False)   # shown only on asset tasks
        v.addWidget(self.cache_assets_note)

        # Reference lookdev assets directly into the scene (sets, props, or any asset
        # that isn't driven by an animation cache). Works for both shots and assets.
        v.addWidget(self._section("Reference Lookdev into Scene"))
        self.lookdev_ref_tree = QtWidgets.QTreeWidget()
        self.lookdev_ref_tree.setHeaderHidden(True)
        self.lookdev_ref_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        v.addWidget(self.lookdev_ref_tree, 1)
        rrow = QtWidgets.QHBoxLayout()
        rrow.addWidget(self._btn("REFERENCE SELECTED", self._on_reference_lookdev))
        rrow.addWidget(self._btn("CLEAR ALL", self._clear_ref_checks))
        v.addLayout(rrow)

        # One REFRESH rescans the Import Model list AND rebuilds the assembly lists.
        v.addWidget(self._sep())
        refresh_btn = self._btn("REFRESH", self._on_assembly_refresh_all)
        refresh_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        v.addWidget(refresh_btn)
        return self._wrap_scroll(w)

    def _on_assembly_refresh_all(self):
        """Rescan everything in the Assembly tab: the Import Model list and both the
        cache-assign and lookdev-reference lists."""
        self._refresh_model_list()
        self._on_assembly_scan()

    # ---- Render tab ----
    def _make_render_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        r = 0

        # Frame range mode.
        grid.addWidget(QtWidgets.QLabel("Frame Range:"), r, 0)
        self.render_range_combo = QtWidgets.QComboBox()
        self.render_range_combo.addItems(["Single Frame", "Frame Range", "Custom"])
        self.render_range_combo.setCurrentText(STATE.render_range_mode)
        self.render_range_combo.currentTextChanged.connect(self._on_render_range_changed)
        grid.addWidget(self.render_range_combo, r, 1)
        r += 1

        # Custom start/end (enabled only for Custom).
        grid.addWidget(QtWidgets.QLabel("Start / End:"), r, 0)
        frow = QtWidgets.QHBoxLayout()
        self.render_start_spin = QtWidgets.QSpinBox()
        self.render_start_spin.setRange(-100000, 100000)
        self.render_start_spin.setValue(STATE.render_start)
        self.render_start_spin.valueChanged.connect(lambda x: setattr(STATE, "render_start", x))
        self.render_end_spin = QtWidgets.QSpinBox()
        self.render_end_spin.setRange(-100000, 100000)
        self.render_end_spin.setValue(STATE.render_end)
        self.render_end_spin.valueChanged.connect(lambda x: setattr(STATE, "render_end", x))
        frow.addWidget(self.render_start_spin)
        frow.addWidget(self.render_end_spin)
        fwrap = QtWidgets.QWidget()
        fwrap.setLayout(frow)
        frow.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(fwrap, r, 1)
        r += 1

        # Camera.
        grid.addWidget(QtWidgets.QLabel("Camera:"), r, 0)
        self.render_cam_combo = QtWidgets.QComboBox()
        self.render_cam_combo.currentTextChanged.connect(
            lambda t: setattr(STATE, "render_camera", t))
        grid.addWidget(self.render_cam_combo, r, 1)
        r += 1

        # Resolution.
        grid.addWidget(QtWidgets.QLabel("Resolution:"), r, 0)
        resrow = QtWidgets.QHBoxLayout()
        resrow.setContentsMargins(0, 0, 0, 0)
        self.render_w_spin = QtWidgets.QSpinBox()
        self.render_w_spin.setRange(1, 100000)
        self.render_w_spin.setValue(STATE.render_res_w)
        self.render_w_spin.valueChanged.connect(lambda x: setattr(STATE, "render_res_w", x))
        self.render_h_spin = QtWidgets.QSpinBox()
        self.render_h_spin.setRange(1, 100000)
        self.render_h_spin.setValue(STATE.render_res_h)
        self.render_h_spin.valueChanged.connect(lambda x: setattr(STATE, "render_res_h", x))
        resrow.addWidget(self.render_w_spin)
        resrow.addWidget(QtWidgets.QLabel("x"))
        resrow.addWidget(self.render_h_spin)
        reswrap = QtWidgets.QWidget()
        reswrap.setLayout(resrow)
        grid.addWidget(reswrap, r, 1)
        r += 1

        # Render layer.
        grid.addWidget(QtWidgets.QLabel("Render Layer:"), r, 0)
        self.render_layer_combo = QtWidgets.QComboBox()
        self.render_layer_combo.currentTextChanged.connect(
            lambda t: setattr(STATE, "render_layer", t))
        grid.addWidget(self.render_layer_combo, r, 1)
        r += 1

        # Format.
        grid.addWidget(QtWidgets.QLabel("Format:"), r, 0)
        self.render_format_combo = QtWidgets.QComboBox()
        self.render_format_combo.addItems(["png", "exr", "tif", "jpg", "tga"])
        self.render_format_combo.setCurrentText(STATE.render_format)
        self.render_format_combo.currentTextChanged.connect(
            lambda t: setattr(STATE, "render_format", t))
        grid.addWidget(self.render_format_combo, r, 1)
        r += 1

        v.addLayout(grid)

        hdr = QtWidgets.QHBoxLayout()
        hdr.addStretch()
        hdr.addWidget(self._btn("Refresh", self._refresh_render_lists))
        v.addLayout(hdr)

        self.render_out_label = QtWidgets.QLabel("")
        self.render_out_label.setWordWrap(True)
        self.render_out_label.setStyleSheet("color: #888888; font-size: 11px;")
        v.addWidget(self.render_out_label)

        v.addStretch()
        render_btn = self._btn("RENDER", op_render)
        render_btn.setStyleSheet(
            "font-weight: bold; padding: 8px; background-color: #A72828; color: #ffffff;")
        v.addWidget(render_btn)

        self._refresh_render_lists()
        self._on_render_range_changed(STATE.render_range_mode)
        return self._wrap_scroll(w)

    def _on_render_range_changed(self, mode):
        STATE.render_range_mode = mode
        custom = mode == "Custom"
        self.render_start_spin.setEnabled(custom)
        self.render_end_spin.setEnabled(custom)
        # Reflect the shot's project-sheet range in the spinboxes for "Frame Range".
        if mode == "Frame Range":
            rng = _shot_frame_range()
            if rng:
                self.render_start_spin.blockSignals(True)
                self.render_end_spin.blockSignals(True)
                self.render_start_spin.setValue(rng[0])
                self.render_end_spin.setValue(rng[1])
                self.render_start_spin.blockSignals(False)
                self.render_end_spin.blockSignals(False)
                STATE.render_start, STATE.render_end = rng

    def _refresh_render_lists(self):
        """Repopulate the camera + render-layer dropdowns and seed the frame range from
        the shot's project-sheet range."""
        cams = _scene_cameras()
        self.render_cam_combo.blockSignals(True)
        self.render_cam_combo.clear()
        self.render_cam_combo.addItems(cams)
        if STATE.render_camera in cams:
            self.render_cam_combo.setCurrentText(STATE.render_camera)
        elif cams:
            STATE.render_camera = self.render_cam_combo.currentText()
        self.render_cam_combo.blockSignals(False)

        layers = _scene_render_layers()
        self.render_layer_combo.blockSignals(True)
        self.render_layer_combo.clear()
        self.render_layer_combo.addItems(layers)
        if STATE.render_layer in layers:
            self.render_layer_combo.setCurrentText(STATE.render_layer)
        else:
            STATE.render_layer = self.render_layer_combo.currentText()
        self.render_layer_combo.blockSignals(False)

        rng = _shot_frame_range()
        if rng and STATE.render_range_mode == "Frame Range":
            self.render_start_spin.setValue(rng[0])
            self.render_end_spin.setValue(rng[1])
            STATE.render_start, STATE.render_end = rng

        out = _render_output_dir()
        self.render_out_label.setText(f"Output: {out}" if out else "Output: (no task context)")

    def _clear_cache_checks(self):
        """Untick every cache in the Assign-Lookdev list."""
        for l in STATE.cache_links:
            l["is_selected"] = False
        root = self.cache_tree.invisibleRootItem()
        for i in range(root.childCount()):
            root.child(i).setText(0, "")

    def _clear_ref_checks(self):
        """Uncheck every asset in the Reference-Lookdev list."""
        root = self.lookdev_ref_tree.invisibleRootItem()
        for i in range(root.childCount()):
            cat = root.child(i)
            for j in range(cat.childCount()):
                cat.child(j).setCheckState(0, QtCore.Qt.Unchecked)

    def _apply_caches(self, batch):
        op_assembly_apply(batch=batch)

    def _update_assembly_context(self):
        """Adapt the Assembly tab to the task type: Shot tasks get the cache-assign block
        (and hide the asset-only Import Model list); asset tasks get Import Model + the
        lookdev-referencing workflow and hide the cache block."""
        is_shot = (_shot_root_from_task_path() is not None) or (STATE.category == "Shots")
        try:
            self.cache_section.setVisible(is_shot)
            self.cache_assets_note.setVisible(not is_shot)
            self.import_model_section.setVisible(not is_shot)
        except Exception:
            pass

    # ---- state sync ----
    def _entity_task_name(self):
        """e.g. 'buzz_lkdev' / 'buzz_mdl' — entity plus the task abbreviation."""
        if not STATE.entity:
            return "None"
        abbr = TASK_ABBR.get(STATE.task_type, "")
        return f"{STATE.entity}_{abbr}" if abbr else STATE.entity

    def _refresh_state_labels(self):
        self.project_label.setText(f"PROJECT: {_project_name()}")
        self.task_label.setText(f"TASK: {self._entity_task_name()}")

    def _auto_populate(self):
        """Refresh the LookDev model list and Assembly scan for the current task.
        Runs on panel open and whenever the active task changes."""
        _ensure_task_context_from_scene()
        try:
            self._refresh_model_list()
        except Exception:
            pass
        try:
            self._refresh_render_lists()
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

    def _on_publish_clear(self):
        self.publish_list_w.clear()
        self._sync_publish_list()

    def _sync_publish_list(self):
        STATE.publish_list = [self.publish_list_w.item(i).text() for i in range(self.publish_list_w.count())]

    def _on_whole_scene_changed(self, checked):
        STATE.publish_whole_scene = checked
        # When exporting the whole scene the per-object list is irrelevant — grey it out.
        try:
            self.publish_list_w.setEnabled(not checked)
            for b in getattr(self, "_list_btns", []):
                b.setEnabled(not checked)
        except Exception:
            pass

    def _open_additional_dialog(self):
        """Popup for the extra Alembic export flags (UV Write / World Space / Write
        Visibility / Write UV Sets) plus the frame Step. Writes back to STATE on OK."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Additional Export Settings")
        form = QtWidgets.QVBoxLayout(dlg)
        uvw = QtWidgets.QCheckBox("UV Write");           uvw.setChecked(STATE.abc_uv_write)
        ws = QtWidgets.QCheckBox("World Space");          ws.setChecked(STATE.abc_world_space)
        wv = QtWidgets.QCheckBox("Write Visibility");     wv.setChecked(STATE.abc_write_visibility)
        wuv = QtWidgets.QCheckBox("Write UV Sets");       wuv.setChecked(STATE.abc_write_uv_sets)
        for c in (uvw, ws, wv, wuv):
            form.addWidget(c)
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(QtWidgets.QLabel("Step:"))
        step = QtWidgets.QDoubleSpinBox()
        step.setRange(0.01, 100.0)
        step.setSingleStep(0.1)
        step.setDecimals(2)
        step.setValue(STATE.abc_step)
        srow.addWidget(step)
        srow.addStretch()
        form.addLayout(srow)
        note = QtWidgets.QLabel("These apply to Alembic (.abc) export.")
        note.setStyleSheet("color: #888888;")
        form.addWidget(note)
        brow = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        ok = QtWidgets.QPushButton("OK")
        ok.setDefault(True)
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        brow.addStretch()
        brow.addWidget(cancel)
        brow.addWidget(ok)
        form.addLayout(brow)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            STATE.abc_uv_write = uvw.isChecked()
            STATE.abc_world_space = ws.isChecked()
            STATE.abc_write_visibility = wv.isChecked()
            STATE.abc_write_uv_sets = wuv.isChecked()
            STATE.abc_step = step.value()

    def _on_anim_only_changed(self, checked):
        STATE.cache_anim_only = checked
        # Re-scan caches so the filter takes effect immediately.
        self._on_assembly_scan()

    def _on_assembly_scan(self):
        op_assembly_scan()
        self.cache_tree.clear()
        for l in STATE.cache_links:
            item = QtWidgets.QTreeWidgetItem([
                "✓" if l["is_selected"] else "",
                l["cache"],
                l["lookdev"] or "(pick lookdev)",
            ])
            self.cache_tree.addTopLevelItem(item)
        for c in range(3):
            self.cache_tree.resizeColumnToContents(c)
        # Lookdev assets available to reference directly into the scene, grouped by
        # category (Character / Sets / Props / …) for easier reading.
        self.lookdev_ref_tree.clear()
        by_cat, seen = {}, set()
        for it in STATE.lookdev_items:
            a = it.get("asset_name") or ""
            if not a or a in seen:
                continue
            seen.add(a)
            by_cat.setdefault(it.get("category") or "Misc", []).append(a)
        for cat in sorted(by_cat):
            top = QtWidgets.QTreeWidgetItem([cat])
            top.setFlags(QtCore.Qt.ItemIsEnabled)   # category header: not checkable
            self.lookdev_ref_tree.addTopLevelItem(top)
            for a in sorted(by_cat[cat]):
                leaf = QtWidgets.QTreeWidgetItem([a])
                leaf.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
                              | QtCore.Qt.ItemIsSelectable)
                leaf.setCheckState(0, QtCore.Qt.Unchecked)   # checkbox beside the name
                top.addChild(leaf)
            top.setExpanded(True)
        # Hide the cache-assign block on asset tasks (Shots-only workflow).
        self._update_assembly_context()

    def _on_reference_lookdev(self):
        # Reference the CHECKED assets (the checkbox shows what's selected to bring in).
        assets = []
        root = self.lookdev_ref_tree.invisibleRootItem()
        for i in range(root.childCount()):
            cat_item = root.child(i)
            for j in range(cat_item.childCount()):
                leaf = cat_item.child(j)
                if leaf.checkState(0) == QtCore.Qt.Checked:
                    assets.append(leaf.text(0))
        if not assets:
            cmds.warning("CGPipeline: Tick a lookdev asset to reference.")
            return
        for a in assets:
            it = next((x for x in STATE.lookdev_items if x.get("asset_name") == a), None)
            if it:
                op_reference_lookdev(it)

    def _on_cache_link_clicked(self, item, col):
        idx = self.cache_tree.indexOfTopLevelItem(item)
        if idx < 0:
            return
        if col == 0:
            STATE.cache_links[idx]["is_selected"] = not STATE.cache_links[idx]["is_selected"]
            item.setText(0, "✓" if STATE.cache_links[idx]["is_selected"] else "")
            return
        if col != 2:
            return
        # Pick which lookdev's material to copy onto this cache. The guessed match (by
        # asset name in the cache file) is offered first.
        guess = _auto_match_lookdev(STATE.cache_links[idx]["cache"])
        names = []
        for it in STATE.lookdev_items:
            a = it.get("asset_name") or ""
            if a and a not in names:
                names.append(a)
        names.sort(key=lambda a: (a != guess, a.lower()))   # guessed match on top
        menu = QtWidgets.QMenu(self)
        none_act = menu.addAction("(none)")
        menu.addSeparator()
        acts = {}
        for a in names:
            label = f"{a}  ⟵ match" if a == guess else a
            acts[menu.addAction(label)] = a
        if not names:
            na = menu.addAction("(no lookdev publishes found)")
            na.setEnabled(False)
        chosen = menu.exec_(QtGui.QCursor.pos())
        if chosen is None:
            return
        if chosen is none_act:
            STATE.cache_links[idx]["lookdev"] = ""
            item.setText(2, "(pick lookdev)")
        elif chosen in acts:
            STATE.cache_links[idx]["lookdev"] = acts[chosen]
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
