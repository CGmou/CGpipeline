bl_info = {
    "name": "CGPipeline",
    "author": "Daniel Wee",
    "version": (0, 0, 1),
    "blender": (5, 1, 1),
    "location": "View3D > Sidebar > CGPipeline",
    "description": "CGPipeline for management project",
    "category": "Pipeline",
}

import bpy
import os
import re
import json
import shutil
import subprocess
import sys
from bpy.app.handlers import persistent

# --- BOOTSTRAP STANDALONE UI ---
def get_standalone_root():
    # 1. Try Environment Variable (set when launched from Dashboard)
    env_path = os.environ.get('CGP_APP_MAIN', '')
    if env_path and os.path.exists(env_path):
        return os.path.dirname(env_path)
    
    # 2. Try Settings File (Standard location)
    settings_path = os.path.join(os.path.expanduser("~"), "Documents", "cgpipeline_system", "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
                app_path = settings.get("app_main_path", "")
                if app_path and os.path.exists(app_path):
                    return os.path.dirname(app_path)
        except: pass
    
    # 3. Fallback to relative path from this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, "..", "..", ".."))

STANDALONE_PATH = get_standalone_root()

if STANDALONE_PATH not in sys.path:
    sys.path.append(STANDALONE_PATH)

_internal_dashboard = None

# --- COMMAND LISTENER (FOR SAME-SESSION OPENING) ---
COMMAND_FILE = os.path.join(os.path.expanduser("~"), "Documents", "cgpipeline_system", "blender_command.json")

def check_external_commands():
    if os.path.exists(COMMAND_FILE):
        try:
            with open(COMMAND_FILE, 'r') as f:
                cmd = json.load(f)
            
            # Delete the file immediately to avoid loops
            os.remove(COMMAND_FILE)
            
            action = cmd.get("action")
            if action == "open_task":
                filepath = cmd.get("filepath")
                task_id = cmd.get("task_id")
                
                if filepath and os.path.exists(filepath):
                    print(f"CGPipeline: Opening task {task_id} from dashboard...")
                    # Set environment variables so the internal operators stay synced
                    os.environ["CGP_TASK_ID"] = task_id
                    os.environ["CGP_ENTITY_NAME"] = cmd.get("entity_name", "")
                    os.environ["CGP_TASK_PATH"] = os.path.dirname(filepath)
                    os.environ["CGP_TASK_TYPE"] = cmd.get("task_type", "")
                    os.environ["CGP_REGISTRY_PATH"] = cmd.get("registry_path", "")
                    
                    # Open the file
                    bpy.ops.wm.open_mainfile(filepath=filepath)
                    # Refresh our own UI props
                    cgp_load_post_handler(None)
                    
        except Exception as e:
            print(f"CGPipeline Command Error: {e}")
            if os.path.exists(COMMAND_FILE): os.remove(COMMAND_FILE)
            
    return 1.0 # Run every 1 second

# --- UTILITIES ---
TASK_ABBR = {
    'Model': 'mdl', 'Texture': 'txt', 'Lookdev': 'lkdev', 'Rig': 'rig',
    'Animation': 'anim', 'Layout': 'lo', 'Blocking': 'blk', 'Lighting': 'lgt',
    'Comp': 'comp', 'FX': 'fx', 'CFX': 'cfx', 'Assembly': 'asb', 'Setdress': 'sd'
}

CACHE_FOLDER_MAP = {
    'anim': 'Anim', 'blk': 'Blocking', 'fx': 'FX', 'cfx': 'CFX', 'lo': 'Layout'
}

def get_latest_version(folder_path):
    if not folder_path or not os.path.exists(folder_path): return 0
    try:
        files = [f for f in os.listdir(folder_path) if f.endswith('.blend')]
        if not files: return 0
        v_pat = re.compile(r'_v(\d+)\.')
        latest_v = 0
        for f in files:
            m = v_pat.search(f)
            if m:
                v = int(m.group(1)); latest_v = max(latest_v, v)
        return latest_v
    except: return 0

def build_work_filename(entity_name, sub_cat, task_type, version, ext):
    abbr = TASK_ABBR.get(task_type, task_type[:3].lower())
    clean_name = str(entity_name).replace(' ', '_')
    return f'{clean_name}_{abbr}_wip_v{version:03d}{ext}'

def find_matching_object_path(object_name_full, cache_db):
    if not cache_db or not hasattr(cache_db, "object_paths"): return None
    name = re.sub(r'\.\d+$', '', object_name_full)
    paths = [p.path for p in cache_db.object_paths]
    leaves = [p.split('/')[-1] for p in paths]
    guesses = [name, f"{name}_GEO", f"{name}Shape"]
    for g in guesses:
        if g in leaves: return paths[leaves.index(g)]
    for i, l in enumerate(leaves):
        if l.endswith(name): return paths[i]
    return None


def _resolve_task_context(props):
    """Return (registry_path, entity) for the current task. Falls back to deriving
    them from the open .blend file path when props/env aren't populated (e.g. the
    file was opened directly rather than via the Dashboard)."""
    reg = props.active_reg_path or os.environ.get('CGP_REGISTRY_PATH', '')
    entity = props.active_entity or os.environ.get('CGP_ENTITY_NAME', '')
    if reg and entity and os.path.exists(reg):
        return reg, entity

    blend = bpy.data.filepath
    if blend:
        if not reg or not os.path.exists(reg):
            cur = os.path.dirname(blend)
            for _ in range(12):
                cand = os.path.join(cur, 'registry.json')
                if os.path.exists(cand):
                    reg = cand
                    break
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
        if not entity:
            parts = os.path.normpath(blend).replace('\\', '/').split('/')
            if 'Assets' in parts:
                i = parts.index('Assets')
                if len(parts) > i + 2:
                    entity = parts[i + 2]
            elif 'Shots' in parts:
                i = parts.index('Shots')
                if len(parts) > i + 1:
                    entity = parts[i + 1]
    return reg, entity


def _find_view3d_context():
    """Return (area, region, space) for a 3D viewport, or (None, None, None)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    return area, region, area.spaces.active
    return None, None, None


def _opengl_capture(final_path, thumbs_dir, clean):
    """OpenGL-render the active viewport straight to PNG. The PNG format enum is
    locked to multilayer EXR when image_settings.views_format == 'MULTIVIEW'
    (Multi-View), so switch views_format to 'INDIVIDUAL' first, then restore."""
    scene = bpy.context.scene
    rs = scene.render
    imf = rs.image_settings
    saved_filepath = rs.filepath
    saved_res = (rs.resolution_x, rs.resolution_y, rs.resolution_percentage)
    saved_fmt = imf.file_format
    saved_views = getattr(imf, "views_format", None)
    saved_mode = getattr(imf, "color_mode", None)
    saved_depth = getattr(imf, "color_depth", None)
    saved_mv = rs.use_multiview
    out_path = None
    try:
        try:
            rs.use_multiview = False
        except Exception:
            pass
        # Unlock the PNG enum (views_format == MULTIVIEW forces multilayer EXR).
        if saved_views is not None:
            try:
                imf.views_format = 'INDIVIDUAL'
            except Exception:
                pass
        imf.file_format = 'PNG'
        # Force a plain 8-bit RGBA PNG so the dashboard's Qt loader can read it
        # (16-bit / unusual color modes can render as a "broken" image).
        try:
            imf.color_mode = 'RGBA'
        except Exception:
            pass
        try:
            imf.color_depth = '8'
        except Exception:
            pass
        rs.resolution_x, rs.resolution_y, rs.resolution_percentage = 640, 360, 100
        rs.filepath = os.path.join(thumbs_dir, clean + "_")
        out_path = rs.frame_path(frame=scene.frame_current)
        area, region, space = _find_view3d_context()
        if area and region:
            with bpy.context.temp_override(area=area, region=region, space_data=space):
                bpy.ops.render.opengl(write_still=True, view_context=True)
        else:
            bpy.ops.render.opengl(write_still=True)
    except Exception as e:
        return None, f"Viewport render failed: {e}"
    finally:
        rs.filepath = saved_filepath
        rs.resolution_x, rs.resolution_y, rs.resolution_percentage = saved_res
        try:
            imf.file_format = saved_fmt
        except Exception:
            pass
        if saved_views is not None:
            try:
                imf.views_format = saved_views
            except Exception:
                pass
        if saved_mode is not None:
            try:
                imf.color_mode = saved_mode
            except Exception:
                pass
        if saved_depth is not None:
            try:
                imf.color_depth = saved_depth
            except Exception:
                pass
        try:
            rs.use_multiview = saved_mv
        except Exception:
            pass

    if not out_path or not os.path.exists(out_path):
        return None, "Render produced no image (OpenGL viewport render wrote nothing)."
    try:
        if os.path.normpath(out_path) != final_path:
            if os.path.exists(final_path):
                os.remove(final_path)
            shutil.move(out_path, final_path)
        return final_path, "ok"
    except Exception:
        return out_path, "ok"


def capture_task_thumbnail(props):
    """Render the active 3D viewport to a PNG and set it as the thumbnail for every
    task of the current entity in the registry. Returns (path_or_None, message)."""
    reg, entity = _resolve_task_context(props)
    if not reg or not os.path.exists(reg) or not entity:
        return None, "No task context found. Open the task via the Dashboard, or save the file inside a CGPipeline project."

    project_root = os.path.dirname(reg)
    thumbs_dir = os.path.join(project_root, ".thumbnails")
    try:
        os.makedirs(thumbs_dir, exist_ok=True)
    except Exception as e:
        return None, f"Could not create thumbnails folder: {e}"
    clean = str(entity).replace(" ", "_")
    final_path = os.path.normpath(os.path.join(thumbs_dir, f"{clean}.png"))

    # OpenGL render captures the viewport reliably. (No GPU-offscreen fallback: it
    # produced noise on some setups, which is worse than a clear error.)
    thumb_path, err = _opengl_capture(final_path, thumbs_dir, clean)
    if not thumb_path:
        return None, err or "Thumbnail capture failed."

    # Update the registry so the dashboard card picks it up.
    try:
        with open(reg, 'r') as f:
            data = json.load(f)
        for tk in data.get('tasks', []):
            if tk.get('name') == entity:
                tk['thumbnail'] = thumb_path
        with open(reg, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        return thumb_path, f"Thumbnail saved but registry update failed: {e}"

    print(f"CGPipeline: Thumbnail updated -> {thumb_path}")
    # Best-effort: push the new thumbnail up to Kitsu (runs in system Python).
    fire_kitsu_sync(reg, entity, props.active_category, props.active_task_type, thumbnail=thumb_path)
    return thumb_path, "Thumbnail updated."


def _find_system_python():
    if os.name == 'nt':
        return shutil.which("pythonw") or shutil.which("python") or "python"
    return shutil.which("python3") or shutil.which("python") or "python3"


def fire_kitsu_sync(reg, entity, category, task_type, status=None, thumbnail=None):
    """Fire-and-forget: push a task's status/thumbnail to Kitsu via the system
    Python (which has gazu). Never blocks or raises into Blender."""
    if not reg or not entity or not task_type:
        return
    script = os.path.normpath(os.path.join(STANDALONE_PATH, "core", "kitsu_sync.py"))
    if not os.path.exists(script):
        return
    try:
        cmd = [_find_system_python(), script,
               "--registry", reg, "--entity", str(entity),
               "--category", category or "Assets", "--task-type", task_type]
        if status:
            cmd += ["--status", status]
        if thumbnail:
            cmd += ["--thumbnail", thumbnail]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.Popen(cmd, creationflags=creationflags)
        print("CGPipeline: Kitsu sync dispatched.")
    except Exception as e:
        print(f"CGPipeline: Could not dispatch Kitsu sync: {e}")

# --- PROPERTY GROUPS ---
class CGP_ObjectItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name='Object Name')

class CGP_CacheFileItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(); format: bpy.props.StringProperty()

class CGP_LookdevFileItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(); path: bpy.props.StringProperty(); asset_name: bpy.props.StringProperty()

class CGP_CollectionLink(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Collection Name")
    assigned_cache: bpy.props.StringProperty(name="Cache Name", default="")
    is_selected: bpy.props.BoolProperty(name="Apply", default=True)

class CGP_WindowManagerProps(bpy.types.PropertyGroup):
    active_entity: bpy.props.StringProperty(name="Active Entity", default="")
    active_task_id: bpy.props.StringProperty(name="Active Task ID", default="")
    active_reg_path: bpy.props.StringProperty(name="Active Registry", default="")
    active_task_path: bpy.props.StringProperty(name="Active Task Path", default="")
    active_task_type: bpy.props.StringProperty(name="Active Task Type", default="")
    active_category: bpy.props.StringProperty(name="Active Category", default="")
    
    # Publish settings
    publish_list: bpy.props.CollectionProperty(type=CGP_ObjectItem)
    publish_list_index: bpy.props.IntProperty(default=0)
    format_enum: bpy.props.EnumProperty(name='Format', items=[('.abc', 'Alembic', ''), ('.usd', 'USD', ''), ('.fbx', 'FBX', ''), ('.blend', 'Blender', '')])
    range_mode: bpy.props.EnumProperty(name='Range', items=[('STILL', 'Still', ''), ('SLIDER', 'Slider', ''), ('CUSTOM', 'Custom', '')])
    start_frame: bpy.props.IntProperty(name='Start', default=1001); end_frame: bpy.props.IntProperty(name='End', default=1100)
    status_enum: bpy.props.EnumProperty(name='Status', items=[('NO CHANGE', 'NO CHANGE', ''), ('Todo', 'Todo', ''), ('Work In Progress', 'Work In Progress', ''), ('Waiting For Approval', 'Waiting For Approval', ''), ('Retake', 'Retake', ''), ('Done', 'Done', '')])
    publish_separate: bpy.props.BoolProperty(name='Separate', default=False)
    include_materials: bpy.props.BoolProperty(name='Include Material', default=True)
    auto_thumbnail: bpy.props.BoolProperty(name='Auto Thumbnail on Publish', default=True, description='Snapshot the viewport as the task thumbnail when publishing')
    
    # Assembly settings
    lookdev_items: bpy.props.CollectionProperty(type=CGP_LookdevFileItem); lookdev_index: bpy.props.IntProperty(default=0)
    cache_items: bpy.props.CollectionProperty(type=CGP_CacheFileItem); cache_index: bpy.props.IntProperty(default=0)
    collection_links: bpy.props.CollectionProperty(type=CGP_CollectionLink); collection_index: bpy.props.IntProperty(default=0)
    import_mode: bpy.props.EnumProperty(name='Mode', items=[('LINK', 'Link', ''), ('APPEND', 'Append', '')])
    cache_tool_target_index: bpy.props.IntProperty(default=-1)
    cache_anim_only: bpy.props.BoolProperty(name="Anim Only", default=False)
    
    cache_prefix: bpy.props.EnumProperty(
        name="Task Prefix",
        description="Filter caches by task/department",
        items=[
            ('anim', 'Animation', ''),
            ('blk', 'Blocking', ''),
            ('fx', 'FX', ''),
            ('cfx', 'CFX', ''),
            ('lo', 'Layout', '')
        ],
        default='anim'
    )

# --- UI LISTS ---
class CGP_UL_PublishList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Determine icon (Collection vs Object)
        disp_icon = 'OUTLINER_COLLECTION' if item.name in bpy.data.collections else 'OBJECT_DATAMODE'
        layout.label(text=item.name, icon=disp_icon)

class CGP_UL_LookdevList(bpy.types.UIList):
    def draw_item(self, c, l, d, item, i, ad, ap, idx):
        l.label(text=item.asset_name if item.asset_name else item.name, icon='MATERIAL')

class CGP_UL_CollectionLinkList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "is_selected", text="")
            row.label(text=item.name, icon='OUTLINER_COLLECTION')
            button_label = item.assigned_cache if item.assigned_cache else "SELECT CACHE"
            op = row.operator("cgp.assembly_assign_popup", text=button_label, icon='FILE_TICK' if item.assigned_cache else 'FILEBROWSER')
            op.index = index
        else: layout.label(text=item.name, icon='OUTLINER_COLLECTION')

# --- HANDLERS ---
@persistent
def cgp_load_post_handler(dummy):
    wm = bpy.context.window_manager
    if not hasattr(wm, 'cgp_props'): return
    props = wm.cgp_props; env_id = os.environ.get('CGP_TASK_ID', '').strip()
    if env_id:
        props.active_task_id = env_id
        props.active_entity = os.environ.get('CGP_ENTITY_NAME', '').strip()
        props.active_reg_path = os.environ.get('CGP_REGISTRY_PATH', '').strip()
        props.active_task_path = os.environ.get('CGP_TASK_PATH', '').strip()
        props.active_task_type = os.environ.get('CGP_TASK_TYPE', '').strip()
        props.active_category = os.environ.get('CGP_CATEGORY', '').strip()
    
    # --- AUTO COLOR MANAGEMENT ---
    reg_path = props.active_reg_path
    if reg_path and os.path.exists(reg_path):
        try:
            with open(reg_path, 'r') as f:
                data = json.load(f)
                cm = data.get("color_management", "")
                if cm in ["ACES 1.3", "ACES 2.0"]:
                    # Set Sequencer to ACEScg
                    if hasattr(bpy.context.scene, "sequencer_colorspace_settings"):
                        try:
                            bpy.context.scene.sequencer_colorspace_settings.name = "ACEScg"
                            print(f"CGPipeline: Color Management set to {cm} (Sequencer: ACEScg)")
                        except:
                            print("CGPipeline Warning: Failed to set Sequencer color space to ACEScg. Is the OCIO config loaded?")
        except Exception as e:
            print(f"CGPipeline: Failed to read registry for color management: {e}")

    # Force relative paths for everything in this file
    try:
        bpy.ops.file.make_paths_relative()
    except: pass

# --- OPERATORS (CORE) ---
class CGP_OT_FixTexturePaths(bpy.types.Operator):
    bl_idname = "cgp.fix_texture_paths"; bl_label = "Fix Missing Textures"
    bl_description = "Searches for missing textures linked to selected objects and re-links them"

    def execute(self, context):
        props = context.window_manager.cgp_props
        reg = props.active_reg_path or os.environ.get('CGP_REGISTRY_PATH')
        if not reg:
            self.report({'ERROR'}, "Project root not detected. Open via Dashboard first.")
            return {'CANCELLED'}
        
        if not context.selected_objects:
            self.report({'WARNING'}, "Please select objects to fix textures for.")
            return {'CANCELLED'}

        project_root = os.path.dirname(reg)
        count = 0
        missing = []

        # Find all unique images linked to selected objects
        target_images = set()
        for obj in context.selected_objects:
            for slot in obj.material_slots:
                if slot.material and slot.material.use_nodes:
                    for node in slot.material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            target_images.add(node.image)

        if not target_images:
            self.report({'INFO'}, "No image textures found on selected objects.")
            return {'FINISHED'}

        # Process the collected images
        for img in target_images:
            if img.source != 'FILE' or not img.filepath: continue
            
            # Check if path is already valid
            abs_path = bpy.path.abspath(img.filepath)
            if os.path.exists(abs_path): continue

            # It's missing, try to find it in the project root
            filename = os.path.basename(img.filepath)
            found = False
            
            # Walk through Assets and Textures folders specifically for speed
            search_dirs = [
                os.path.join(project_root, "Assets"),
                os.path.join(project_root, "Textures"),
                project_root
            ]
            
            for s_dir in search_dirs:
                if not os.path.exists(s_dir): continue
                for root, dirs, files in os.walk(s_dir):
                    if filename in files:
                        new_path = os.path.join(root, filename)
                        img.filepath = bpy.path.relpath(new_path)
                        count += 1
                        found = True
                        break
                if found: break
            
            if not found: missing.append(filename)

        if count > 0:
            self.report({'INFO'}, f"Successfully re-linked {count} textures for selected objects.")
        if missing:
            self.report({'WARNING'}, f"Could not find {len(missing)} textures in project.")
        
        return {'FINISHED'}

class CGP_OT_SwitchTextureRes(bpy.types.Operator):
    bl_idname = "cgp.switch_texture_res"; bl_label = "Switch Texture Resolution"
    res: bpy.props.StringProperty()

    def execute(self, context):
        target_res = self.res.lower() # "2k" or "4k"
        other_res = "4k" if target_res == "2k" else "2k"
        
        # 1. Gather all objects (Selected + Objects in Selected Collections)
        target_objs = set(context.selected_objects)
        
        # Add objects from selected collections in Outliner
        if hasattr(context, 'selected_ids'):
            for item in context.selected_ids:
                if isinstance(item, bpy.types.Collection):
                    for obj in item.all_objects: target_objs.add(obj)

        # IMPORTANT: If an object is a collection instance, add all objects inside that collection
        for obj in list(target_objs):
            if obj.instance_type == 'COLLECTION' and obj.instance_collection:
                for sub_obj in obj.instance_collection.all_objects:
                    target_objs.add(sub_obj)

        if not target_objs:
            self.report({'WARNING'}, "Please select objects or a collection first.")
            return {'CANCELLED'}

        # 2. Find all unique images
        target_images = set()
        for obj in target_objs:
            for slot in obj.material_slots:
                if slot.material and slot.material.use_nodes:
                    for node in slot.material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            target_images.add(node.image)

        count = 0
        for img in target_images:
            if img.source != 'FILE' or not img.filepath: continue
            
            # Simple aggressive replacement
            old_path = img.filepath
            # Replace /2k/ with /4k/ or \2k\ with \4k\ etc (Case Insensitive)
            new_path = old_path
            
            # Try various common separators to find the resolution folder
            for sep in ['/', '\\']:
                marker = f"{sep}{other_res}{sep}"
                if marker.lower() in old_path.lower():
                    # Find the actual casing used in the path
                    idx = old_path.lower().find(marker.lower())
                    new_path = old_path[:idx] + sep + target_res + old_path[idx+len(marker)-1:]
                    break
            
            if new_path != old_path:
                img.filepath = new_path
                img.reload()
                count += 1
                print(f"CGPipeline: Switched {img.name} -> {target_res.upper()}")

        self.report({'INFO'}, f"Directly switched {count} textures to {target_res.upper()}.")
        return {'FINISHED'}

class CGP_OT_OpenDashboard(bpy.types.Operator):
    bl_idname = 'cgp.open_dashboard'; bl_label = 'Open'
    def execute(self, context):
        try:
            # Find the main.py path
            main_py = os.path.normpath(os.path.join(STANDALONE_PATH, "main.py"))
            if not os.path.exists(main_py):
                # Try finding it in the parent directory if STANDALONE_PATH is already at root
                main_py = os.path.normpath(os.path.join(STANDALONE_PATH, "..", "main.py"))
            
            if not os.path.exists(main_py):
                 self.report({'ERROR'}, f"Could not find main.py at: {main_py}")
                 return {'CANCELLED'}

            # Launch using system python to avoid DCC environment issues
            # We use 'python3' on macOS/Linux as 'python' is often not available or refers to Python 2.
            python_exe = "python"
            if os.name != 'nt':
                python_exe = shutil.which("python3") or shutil.which("python") or "python3"
            else:
                # On Windows, try pythonw to avoid console window, fallback to python
                python_exe = shutil.which("pythonw") or shutil.which("python") or "python"
            
            # Prepare environment variables to keep context (Project Root, etc.)
            env = os.environ.copy()
            props = context.window_manager.cgp_props
            if props.active_task_id: env['CGP_TASK_ID'] = props.active_task_id
            if props.active_reg_path: env['CGP_REGISTRY_PATH'] = props.active_reg_path
            
            # CRITICAL: Mark this dashboard as being 'inside' this Blender session
            env['CGP_IN_DCC'] = 'Blender'
            # Also pass the path to the command file for certainty
            env['CGP_COMMAND_FILE'] = COMMAND_FILE

            subprocess.Popen([python_exe, main_py], env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.report({'INFO'}, "Dashboard launched (Linked to this Blender).")
            
        except Exception as e:
            self.report({'ERROR'}, f"Launch Failed: {e}")
        return {'FINISHED'}

class CGP_OT_NormalSave(bpy.types.Operator):
    bl_idname = 'cgp.normal_save'; bl_label = 'Save'
    def execute(self, context):
        if not bpy.data.is_saved: return {'CANCELLED'}
        bpy.ops.wm.save_mainfile(); props = context.window_manager.cgp_props
        e, t, p = props.active_entity, props.active_task_type, props.active_task_path
        if e and p:
            r = os.path.dirname(p); m = f'{e}_{TASK_ABBR.get(t, "task")}_master.blend'
            try: shutil.copy2(bpy.data.filepath, os.path.normpath(os.path.join(r, m)))
            except: pass
        return {'FINISHED'}

class CGP_OT_SaveVersion(bpy.types.Operator):
    bl_idname = 'cgp.save_version'; bl_label = 'Version Up'
    def execute(self, context):
        props = context.window_manager.cgp_props; p, e, t = props.active_task_path, props.active_entity, props.active_task_type
        if not p: return {'CANCELLED'}
        v = get_latest_version(p) + 1; fn = build_work_filename(e, "", t, v, '.blend'); fp = os.path.normpath(os.path.join(p, fn))
        try:
            bpy.ops.wm.save_as_mainfile(filepath=fp)
            r = os.path.dirname(p); m = f'{e}_{TASK_ABBR.get(t, "task")}_master.blend'
            shutil.copy2(fp, os.path.normpath(os.path.join(r, m))); return {'FINISHED'}
        except: return {'CANCELLED'}

class CGP_OT_UpdateStatus(bpy.types.Operator):
    bl_idname = 'cgp.update_status'; bl_label = 'Update'
    def execute(self, context):
        props = context.window_manager.cgp_props
        reg, tid = props.active_reg_path or os.environ.get('CGP_REGISTRY_PATH'), props.active_task_id or os.environ.get('CGP_TASK_ID')
        if not reg or not tid or not os.path.exists(reg) or props.status_enum == 'NO CHANGE': return {'CANCELLED'}
        try:
            with open(reg, 'r') as f: data = json.load(f)
            for tk in data.get('tasks', []):
                if tk['id'] == tid: tk['status'] = props.status_enum; break
            with open(reg, 'w') as f: json.dump(data, f, indent=4)
            # Best-effort: push the status change up to Kitsu.
            fire_kitsu_sync(reg, props.active_entity, props.active_category,
                            props.active_task_type, status=props.status_enum)
            self.report({'INFO'}, f"Status set to: {props.status_enum}"); return {'FINISHED'}
        except: return {'CANCELLED'}

class CGP_OT_AddSelected(bpy.types.Operator):
    bl_idname = 'cgp.add_selected'; bl_label = 'Add Selected'
    def execute(self, context):
        props = context.window_manager.cgp_props
        
        # 1. Add Selected Objects (Viewport)
        for obj in context.selected_objects:
            if not any(i.name == obj.name for i in props.publish_list):
                item = props.publish_list.add(); item.name = obj.name
        
        # 2. Add Active Collection (The one highlighted in the Outliner)
        # This is the most reliable way to get a collection from the Sidebar
        active_lc = context.view_layer.active_layer_collection
        if active_lc and active_lc.collection:
            coll = active_lc.collection
            # Only add if it's not the Scene Collection (usually don't want to export everything)
            if coll != context.scene.collection:
                if not any(i.name == coll.name for i in props.publish_list):
                    list_item = props.publish_list.add()
                    list_item.name = coll.name
                    self.report({'INFO'}, f"Added collection: {coll.name}")

        # 3. Fallback: If no objects selected but user is highlighting a collection
        # Check 'selected_ids' via a simpler context check if visible
        try:
            outliner = next((a for a in context.screen.areas if a.type == 'OUTLINER'), None)
            if outliner:
                for s_id in context.selected_ids:
                    if isinstance(s_id, bpy.types.Collection):
                        if not any(i.name == s_id.name for i in props.publish_list):
                            item = props.publish_list.add(); item.name = s_id.name
        except: pass
        
        return {'FINISHED'}

class CGP_OT_RemoveObject(bpy.types.Operator):
    bl_idname = 'cgp.remove_object'; bl_label = 'Remove Object'
    def execute(self, context):
        props = context.window_manager.cgp_props
        props.publish_list.remove(props.publish_list_index)
        return {'FINISHED'}

class CGP_OT_PublishAction(bpy.types.Operator):
    bl_idname = 'cgp.publish_action'; bl_label = 'Publish'
    def execute(self, context):
        props = context.window_manager.cgp_props
        p, e, t, c = props.active_task_path, props.active_entity, props.active_task_type, props.active_category
        
        if not p or not props.publish_list:
            if not p: self.report({'WARNING'}, 'No active task path! Open a task from Dashboard first.')
            if not props.publish_list: self.report({'WARNING'}, 'Publish list is empty! Add objects or collections.')
            return {'CANCELLED'}
        
        if not e:
            self.report({'WARNING'}, 'Active entity name is missing. Please re-open the task.')
            return {'CANCELLED'}

        # Robust Publish Directory Discovery
        task_dir = os.path.dirname(p)
        pub = None

        check_dir = task_dir
        for _ in range(4): # Search up to 4 levels
            potential_pub = os.path.join(check_dir, 'Publish')
            if os.path.exists(potential_pub):
                pub = potential_pub
                break
            if os.path.basename(check_dir).lower() == 'wip':
                sibling_pub = os.path.join(os.path.dirname(check_dir), 'Publish')
                if os.path.exists(sibling_pub):
                    pub = sibling_pub
                    break
            check_dir = os.path.dirname(check_dir)
            if os.path.basename(check_dir) in {'Assets', 'Shots', ''}: break

        if not pub:
            pub = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(p)), 'Publish'))

        abbr, fmt = TASK_ABBR.get(t, 'task'), props.format_enum
        is_anim = props.range_mode != 'STILL'
        s = context.scene.frame_current if props.range_mode == 'STILL' else context.scene.frame_start if props.range_mode == 'SLIDER' else props.start_frame
        ef = context.scene.frame_current if props.range_mode == 'STILL' else context.scene.frame_end if props.range_mode == 'SLIDER' else props.end_frame
        
        def do_export(objs, filename, is_camera=False):
            bpy.ops.object.select_all(action='DESELECT')
            found_any = False
            for o in objs:
                if o in bpy.data.objects:
                    bpy.data.objects[o].select_set(True)
                    found_any = True
                elif o in bpy.data.collections:
                    for co in bpy.data.collections[o].all_objects:
                        co.select_set(True)
                        found_any = True
            
            if not found_any:
                self.report({'WARNING'}, f'No objects found to export for: {filename}')
                return

            # CRITICAL: Make paths absolute before export so they don't break when file moves
            bpy.ops.file.make_paths_absolute()

            bpy.context.view_layer.update(); f_path = os.path.normpath(os.path.join(pub, filename))
            
            if os.path.exists(f_path):
                try:
                    if fmt == '.blend': os.remove(f_path)
                except: pass

            try:
                if fmt == '.abc': bpy.ops.wm.alembic_export(filepath=f_path, selected=True, start=s, end=ef)
                elif fmt == '.usd':
                    op = bpy.ops.wm.usd_export; valid = op.get_rna_type().properties.keys()
                    kwargs = {'filepath': f_path}
                    if 'selected_objects_only' in valid: kwargs['selected_objects_only'] = True
                    if is_anim:
                        if 'export_animation' in valid: kwargs['export_animation'] = True
                        if 'frame_start' in valid: kwargs['frame_start'], kwargs['frame_end'] = s, ef
                    if 'export_materials' in valid: kwargs['export_materials'] = props.include_materials
                    if 'export_textures' in valid: kwargs['export_textures'] = props.include_materials
                    if 'relative_paths' in valid: kwargs['relative_paths'] = False
                    try:
                        bpy.ops.wm.usd_export(**kwargs)
                        if not props.include_materials:
                            tp = os.path.join(os.path.dirname(f_path), 'textures')
                            if os.path.exists(tp): shutil.rmtree(tp)
                    except: bpy.ops.wm.usd_export(filepath=f_path)
                elif fmt == '.fbx':
                    old_s = context.scene.frame_start; old_e = context.scene.frame_end; context.scene.frame_start, context.scene.frame_end = s, ef      
                    context.scene.frame_set(s)
                    try: 
                        # Use COPY and embed textures for FBX portability
                        bpy.ops.export_scene.fbx(filepath=f_path, use_selection=True, path_mode='COPY', embed_textures=True, bake_anim=is_anim, bake_anim_use_all_actions=False, bake_anim_simplify_factor=0.0, add_leaf_bones=False)
                    finally: context.scene.frame_start, context.scene.frame_end = old_s, old_e
                elif fmt == '.blend':
                    colls = [bpy.data.collections.get(o) for o in objs if o in bpy.data.collections]
                    if colls:
                        bpy.data.libraries.write(f_path, set(colls), fake_user=True)
                    else:
                        bpy.ops.wm.save_as_mainfile(filepath=f_path, copy=True)
            finally:
                # Revert to relative paths for the current WIP session
                bpy.ops.file.make_paths_relative()

        try:
            os.makedirs(pub, exist_ok=True)
            if props.publish_separate:
                for i in props.publish_list:
                    obj = bpy.data.objects.get(i.name)
                    coll = bpy.data.collections.get(i.name)
                    if not obj and not coll: continue
                    is_c = obj and (obj.type == 'CAMERA' or 'cam' in obj.name.lower())
                    if t == 'Lookdev' and fmt in {'.usd', '.blend'}:
                        fn = f'{e}_lkdev{fmt}' if not props.publish_separate else f'{e}_lkdev_{i.name}{fmt}'
                    else:
                        ext_upper = fmt[1:].upper()
                        if is_c: fn = f'{e}_cam_f{s}_f{ef}{fmt}'
                        else:
                            if c == 'Shots': fn = f'{e}_{abbr}_{i.name}_{ext_upper}{fmt}'
                            else: fn = f'{e}_{abbr}_{i.name}{fmt}'
                    do_export([i.name], fn, is_camera=is_c)
            else:
                if t == 'Lookdev' and fmt in {'.usd', '.blend'}:
                    fn = f'{e}_lkdev{fmt}'
                else:
                    ext_upper = fmt[1:].upper()
                    if c == 'Shots': fn = f'{e}_{abbr}_{ext_upper}{fmt}'
                    else: fn = f'{e}_{abbr}{fmt}'
                do_export([i.name for i in props.publish_list], fn)
            
            # Auto-snapshot a thumbnail for the task after a successful publish.
            if getattr(props, 'auto_thumbnail', False):
                try:
                    _, tmsg = capture_task_thumbnail(props)
                    print(f"CGPipeline: Auto thumbnail: {tmsg}")
                except Exception as te:
                    print(f"CGPipeline: Auto thumbnail failed: {te}")

            self.report({'INFO'}, f'Successfully published to: {pub}')
            return {'FINISHED'}
        except Exception as err:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f'Publish failed: {str(err)}')
            return {'CANCELLED'}


class CGP_OT_CaptureThumbnail(bpy.types.Operator):
    bl_idname = 'cgp.capture_thumbnail'; bl_label = 'Snapshot Thumbnail'
    bl_description = 'Capture the current viewport as the thumbnail for this task'
    def execute(self, context):
        path, msg = capture_task_thumbnail(context.window_manager.cgp_props)
        if path:
            self.report({'INFO'}, msg)
            return {'FINISHED'}
        self.report({'WARNING'}, msg)
        return {'CANCELLED'}

# --- OPERATORS (ASSEMBLY) ---
class CGP_OT_AssemblyScan(bpy.types.Operator):
    bl_idname = "cgp.assembly_scan"; bl_label = "Refresh"
    def execute(self, context):
        props = context.window_manager.cgp_props; reg = props.active_reg_path or os.environ.get('CGP_REGISTRY_PATH')
        if not reg: return {'CANCELLED'}
        root = os.path.dirname(reg); props.lookdev_items.clear(); props.cache_items.clear()
        
        # 1. SCAN LOOKDEV: Search Assets -> Category -> Asset -> Publish for *_lkdev.blend
        assets_dir = os.path.join(root, "Assets")
        if os.path.exists(assets_dir):
            for cat in os.listdir(assets_dir):
                cat_p = os.path.join(assets_dir, cat)
                if not os.path.isdir(cat_p): continue
                for asset in os.listdir(cat_p):
                    asset_p = os.path.join(cat_p, asset)
                    if not os.path.isdir(asset_p): continue
                    
                    # Look inside Publish folder for _lkdev files
                    pub_folder = os.path.join(asset_p, "Publish")
                    if os.path.isdir(pub_folder):
                        for f in os.listdir(pub_folder):
                            if '_lkdev' in f.lower() and f.endswith('.blend'):
                                item = props.lookdev_items.add()
                                item.name = f; item.path = os.path.join(pub_folder, f); item.asset_name = asset

        # 2. SCAN CACHES: Search all departments in current Shot for cache files
        p = props.active_task_path or os.environ.get('CGP_TASK_PATH', '').strip()
        if p:
            norm_p = os.path.normpath(p).replace('\\', '/')
            parts = norm_p.split('/')
            shot_root = ""
            if "Shots" in parts:
                idx = parts.index("Shots")
                if len(parts) > idx + 1: shot_root = os.path.normpath("/".join(parts[:idx+2]))
            
            if shot_root and os.path.exists(shot_root):
                # Scan EVERY folder in the shot root
                for dept in os.listdir(shot_root):
                    pub_dir = os.path.join(shot_root, dept, "Publish")
                    if os.path.isdir(pub_dir):
                        for f in os.listdir(pub_dir):
                            f_low = f.lower()
                            if f_low.endswith(('.abc', '.usd', '.usdc', '.usda', '.fbx')):
                                # Apply "Anim Only" filter if enabled: strictly look for '_anim_'
                                if props.cache_anim_only:
                                    if '_anim_' not in f_low:
                                        continue

                                if not any(i.name == f for i in props.cache_items):
                                    item = props.cache_items.add()
                                    item.name = f
            else:
                print(f"CGPipeline Debug: Could not resolve shot root from path: {p}")
        
        existing = {l.name: (l.assigned_cache, l.is_selected) for l in props.collection_links}
        props.collection_links.clear()
        for coll in context.scene.collection.children:
            l = props.collection_links.add()
            l.name = coll.name
            cache, selected = existing.get(coll.name, ("", True))
            l.assigned_cache = cache
            l.is_selected = selected
        return {'FINISHED'}

class CGP_OT_AssemblyImportLookdev(bpy.types.Operator):
    bl_idname = "cgp.assembly_import_lkdev"; bl_label = "Import Selected Lookdev"
    def execute(self, context):
        props = context.window_manager.cgp_props
        if not props.lookdev_items: return {'CANCELLED'}
        target = props.lookdev_items[props.lookdev_index]; is_link = (props.import_mode == 'LINK')
        with bpy.data.libraries.load(target.path, link=is_link) as (df, dt):
            tc = next((c for c in df.collections if c.lower() == target.asset_name.lower() or 'lkdev' in c.lower() or 'lookdev' in c.lower()), None)
            if not tc and df.collections: tc = df.collections[0]
            if tc: dt.collections.append(tc)
        for coll in dt.collections:
            if coll: context.scene.collection.children.link(coll)
        bpy.ops.cgp.assembly_scan(); return {'FINISHED'}

class CGP_OT_AssemblyMakeOverride(bpy.types.Operator):
    bl_idname = "cgp.assembly_make_override"; bl_label = "Make Library Override"
    def execute(self, context):
        try:
            # 1. Target the collection selected in the Outliner
            active_lc = context.view_layer.active_layer_collection
            target_coll = None
            
            if active_lc and active_lc.collection and active_lc.collection.library:
                target_coll = active_lc.collection
            elif context.collection and context.collection.library:
                target_coll = context.collection
            
            if not target_coll:
                self.report({'ERROR'}, "Please select a Linked Collection (Cyan icon) in the Outliner.")
                return {'CANCELLED'}

            # 2. Find the Outliner area to borrow context
            outliner_area = next((a for a in context.screen.areas if a.type == 'OUTLINER'), None)
            if not outliner_area:
                self.report({'ERROR'}, "Outliner area must be visible to perform this operation.")
                return {'CANCELLED'}

            # 3. Use the EXACT operator and parameters for "Selected and Content"
            # This is the 'in-place' override that doesn't create duplicates.
            try:
                if bpy.app.version >= (3, 2, 0):
                    # Modern syntax (Blender 3.2+)
                    with context.temp_override(area=outliner_area, selected_ids=[target_coll]):
                        bpy.ops.outliner.liboverride_operation(
                            type='OVERRIDE_LIBRARY_CREATE_HIERARCHY',
                            selection_set='SELECTED_AND_CONTENT'
                        )
                else:
                    # Legacy syntax (Blender 3.0/3.1)
                    ctx = context.copy()
                    ctx['area'] = outliner_area
                    ctx['selected_ids'] = [target_coll]
                    # In 3.0/3.1, 'selection_set' didn't exist yet, it defaulted to hierarchy
                    bpy.ops.outliner.liboverride_operation(
                        ctx, 
                        type='OVERRIDE_LIBRARY_CREATE_HIERARCHY'
                    )
                
                self.report({'INFO'}, f"In-place Library Override created for: {target_coll.name}")
                return {'FINISHED'}

            except Exception as op_err:
                # If liboverride_operation is not found, try the older command
                try:
                    ctx = context.copy()
                    ctx['area'] = outliner_area
                    ctx['selected_ids'] = [target_coll]
                    bpy.ops.outliner.lib_override_library_create(ctx, hierarchy=True)
                    self.report({'INFO'}, "Override created (Legacy Method).")
                    return {'FINISHED'}
                except:
                    raise op_err

        except Exception as e:
            self.report({'ERROR'}, f"Override Failed: {e}")
            return {'CANCELLED'}

class CGP_OT_AssemblyAssignPopup(bpy.types.Operator):
    bl_idname = "cgp.assembly_assign_popup"; bl_label = "Assign"; index: bpy.props.IntProperty()
    def execute(self, context):
        context.window_manager.cgp_props.cache_tool_target_index = self.index
        bpy.ops.wm.call_menu(name="CGP_MT_AssignMenu"); return {'FINISHED'}

class CGP_OT_AssemblySetCache(bpy.types.Operator):
    bl_idname = "cgp.assembly_set_cache"; bl_label = "Set"; index: bpy.props.IntProperty(); cache: bpy.props.StringProperty()
    def execute(self, context):
        context.window_manager.cgp_props.collection_links[self.index].assigned_cache = "" if self.cache == "NONE" else self.cache
        return {'FINISHED'}

class CGP_OT_AssemblyApply(bpy.types.Operator):
    bl_idname = "cgp.assembly_apply"; bl_label = "Apply Caches"; batch: bpy.props.BoolProperty(default=False)
    def execute(self, context):
        props = context.window_manager.cgp_props; p = props.active_task_path or os.environ.get('CGP_TASK_PATH', '').strip()
        if not p: return {'CANCELLED'}
        shot_root = ""
        norm_p = os.path.normpath(p).replace('\\', '/')
        parts = norm_p.split('/')
        if "Shots" in parts:
            idx = parts.index("Shots")
            if len(parts) > idx + 1: shot_root = os.path.normpath("/".join(parts[:idx+2]))
        
        if not shot_root: return {'CANCELLED'}
            
        links = props.collection_links if self.batch else [l for l in props.collection_links if l.is_selected]
        print(f"CGPipeline: Applying Caches (batch={self.batch}, count={len(links)})")
        
        for l in links:
            if not l.assigned_cache: continue
            print(f"CGPipeline:   - Processing Collection: {l.name} with cache {l.assigned_cache}")
            fp = None
            # Search in all department publish folders for the assigned filename
            for dept in os.listdir(shot_root):
                test_p = os.path.normpath(os.path.join(shot_root, dept, "Publish", l.assigned_cache))
                if os.path.exists(test_p): fp = test_p; break
            
            if not fp: continue
            db = next((c for c in bpy.data.cache_files if hasattr(c,'filepath') and bpy.path.abspath(c.filepath)==fp), None)
            if not db:
                try: bpy.ops.cachefile.open(filepath=fp); db = next((c for c in bpy.data.cache_files if hasattr(c,'filepath') and bpy.path.abspath(c.filepath)==fp), None)
                except: continue
            coll = context.scene.collection.children.get(l.name)
            if coll and db:
                for obj in [o for o in coll.all_objects if o.type=='MESH']:
                    mod = obj.modifiers.get("PipelineCache") or obj.modifiers.new("PipelineCache", 'MESH_SEQUENCE_CACHE')
                    mod.cache_file = db; path = find_matching_object_path(obj.name_full, db)
                    if path: mod.object_path = path
        return {'FINISHED'}

# --- PANELS ---
class CGP_PT_MainPanel(bpy.types.Panel):
    bl_label = 'CGPipeline'; bl_idname = 'CGP_PT_MainPanel'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'CGPipeline'
    def draw(self, context):
        l = self.layout; l.operator('cgp.open_dashboard', text='Open', icon='WINDOW')
        l.separator(); l.label(text="Quick Tools:"); row = l.row(align=True); row.operator('cgp.normal_save', icon='FILE_TICK'); row.operator('cgp.save_version', icon='FILE_NEW')
        l.operator('cgp.fix_texture_paths', text='Fix Missing Textures', icon='FILE_REFRESH')
        
        # Texture Resolution Switcher
        row = l.row(align=True)
        op2 = row.operator('cgp.switch_texture_res', text='2K', icon='IMAGE_DATA')
        op2.res = "2k"
        op4 = row.operator('cgp.switch_texture_res', text='4K', icon='IMAGE_DATA')
        op4.res = "4k"

class CGP_PT_StatusPanel(bpy.types.Panel):
    bl_label = 'Status'; bl_idname = 'CGP_PT_StatusPanel'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'CGPipeline'
    def draw(self, context):
        l, p = self.layout, context.window_manager.cgp_props; e = p.active_entity or "None"
        l.label(text=f"TASK: {e}", icon='INFO'); box = l.box(); row = box.row(align=True); row.prop(p, 'status_enum', text=""); row.operator('cgp.update_status', icon='FILE_REFRESH')

class CGP_PT_PublishPanel(bpy.types.Panel):
    bl_label = 'Publisher'; bl_idname = 'CGP_PT_PublishPanel'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'CGPipeline'
    def draw(self, context):
        l, p = self.layout, context.window_manager.cgp_props; e = p.active_entity or "None"
        l.label(text=f'PUBLISHING: {e}'); b = l.box(); col = b.column(align=True)
        r = col.row(align=True); r.prop(p, 'format_enum', text=""); r.prop(p, 'range_mode', text="")
        if p.range_mode == 'CUSTOM': r = col.row(align=True); r.prop(p, 'start_frame', text="S"); r.prop(p, 'end_frame', text="E")
        col.separator(); r = col.row(align=True); r.prop(p, 'publish_separate', text="Separate")
        
        # Only show Material checker for USD and Blender modes
        if p.format_enum in {'.usd', '.blend'}:
            r.prop(p, 'include_materials', text="Material")
            
        col.separator(); col.label(text="Selection List:"); col.template_list('CGP_UL_PublishList', '', p, 'publish_list', p, 'publish_list_index')
        r = col.row(align=True); r.operator('cgp.add_selected', text='Add', icon='ADD'); r.operator('cgp.remove_object', text='Remove', icon='REMOVE')
        col.separator(); col.prop(p, 'auto_thumbnail', text='Auto Thumbnail on Publish')
        col.operator('cgp.capture_thumbnail', text='Snapshot Thumbnail', icon='RESTRICT_RENDER_OFF')
        l.separator(); l.operator('cgp.publish_action', text='PUBLISH', icon='EXPORT')

class CGP_PT_AssemblyPanel(bpy.types.Panel):
    bl_label = 'Assembly'; bl_idname = 'CGP_PT_AssemblyPanel'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'CGPipeline'
    def draw(self, context):
        l, p = self.layout, context.window_manager.cgp_props; l.operator("cgp.assembly_scan", icon='FILE_REFRESH', text="1. REFRESH")
        b = l.box(); b.label(text="2. IMPORT LOOKDEV:", icon='MATERIAL'); b.template_list("CGP_UL_LookdevList", "lkdev", p, "lookdev_items", p, "lookdev_index")
        r = b.row(align=True); r.prop(p, 'import_mode', text=""); r.operator("cgp.assembly_import_lkdev", text="LINK", icon='IMPORT')
        l.separator(); l.operator("cgp.assembly_make_override", text="3. MAKE OVERRIDE", icon='LIBRARY_DATA_OVERRIDE')
        
        b = l.box(); b.label(text="4. ASSIGN CACHES:", icon='LINKED')
        b.template_list("CGP_UL_CollectionLinkList", "links", p, "collection_links", p, "collection_index")
        
        row = b.row(align=True)
        row.prop(p, "cache_anim_only", text="ANIM ONLY", icon='FILTER')
        
        row = b.row(align=True); row.scale_y = 1.2
        row.operator("cgp.assembly_apply", text="APPLY SELECTED", icon='CHECKMARK').batch=False
        row.operator("cgp.assembly_apply", text="APPLY ALL", icon='PLAY').batch=True

class CGP_MT_AssignMenu(bpy.types.Menu):
    bl_label = "Assign Cache"; bl_idname = "CGP_MT_AssignMenu"
    def draw(self, context):
        l, p = self.layout, context.window_manager.cgp_props; idx = p.cache_tool_target_index
        l.operator("cgp.assembly_set_cache", text="NONE").cache="NONE"; l.separator()
        for f in p.cache_items: op = l.operator("cgp.assembly_set_cache", text=f.name); op.index, op.cache = idx, f.name

# --- REGISTRATION ---
classes = [
    CGP_ObjectItem, CGP_CacheFileItem, CGP_LookdevFileItem, CGP_CollectionLink, CGP_WindowManagerProps,
    CGP_UL_PublishList, CGP_UL_LookdevList, CGP_UL_CollectionLinkList,
    CGP_OT_OpenDashboard, CGP_OT_NormalSave, CGP_OT_SaveVersion, CGP_OT_UpdateStatus,
    CGP_OT_AddSelected, CGP_OT_RemoveObject, CGP_OT_PublishAction, CGP_OT_CaptureThumbnail, CGP_OT_FixTexturePaths, CGP_OT_SwitchTextureRes,
    CGP_OT_AssemblyScan, CGP_OT_AssemblyImportLookdev, CGP_OT_AssemblyMakeOverride, 
    CGP_OT_AssemblyAssignPopup, CGP_OT_AssemblySetCache, CGP_OT_AssemblyApply,
    CGP_PT_MainPanel, CGP_PT_StatusPanel, CGP_PT_PublishPanel, CGP_PT_AssemblyPanel, CGP_MT_AssignMenu
]

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.WindowManager.cgp_props = bpy.props.PointerProperty(type=CGP_WindowManagerProps)
    
    if cgp_load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(cgp_load_post_handler)
    
    # Register the timer to check for external commands from the dashboard
    # We use a persistent registration if possible, or ensure it's re-added
    if not bpy.app.timers.is_registered(check_external_commands):
        bpy.app.timers.register(check_external_commands, persistent=True)
    
    cgp_load_post_handler(None)

def unregister():
    if bpy.app.timers.is_registered(check_external_commands):
        bpy.app.timers.unregister(check_external_commands)
        
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.cgp_props
    
    if cgp_load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(cgp_load_post_handler)

if __name__ == '__main__': register()
