import os
import re

ASSET_GROUP_ABBR = {
    "Char": "CH", "Props": "PR", "Sets": "ST", "Vehicles": "VH"
}

TASK_ABBR = {
    "Model": "mdl", "Texture": "txt", "Shader": "shd", "Rig": "rig",
    "Animation": "anim", "Layout": "lo", "Blocking": "blk", "Lighting": "lgt",
    "Comp": "comp", "FX": "fx", "CFX": "cfx", "Assembly": "asb", "Setdress": "sd"
}

def get_latest_version(folder_path):
    if not os.path.exists(folder_path): return 0, None
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    if not files: return 0, None

    version_pattern = re.compile(r"_v(\d+)\.")
    latest_v = 0
    latest_file = None
    
    # 1. Try to find by version pattern
    dcc_extensions = ['.blend', '.ma', '.mb', '.hip', '.hipnc', '.hiplc']
    
    for f in files:
        # Avoid Blender backup files like .blend1, .blend2
        ext = os.path.splitext(f)[1].lower()
        if ext not in dcc_extensions:
            continue
            
        match = version_pattern.search(f)
        if match:
            v = int(match.group(1))
            if v > latest_v:
                latest_v = v; latest_file = f
    
    if latest_file:
        return latest_v, latest_file

    # 2. Fallback: Find any recognized DCC file
    dcc_extensions = ['.blend', '.ma', '.mb', '.hip', '.hipnc', '.hiplc']
    dcc_files = [f for f in files if os.path.splitext(f)[1].lower() in dcc_extensions]
    
    if dcc_files:
        # Pick the most recently modified one
        dcc_files.sort(key=lambda x: os.path.getmtime(os.path.join(folder_path, x)), reverse=True)
        return 0, dcc_files[0]

    return 0, None

def build_work_filename(entity_name, sub_cat, task_type, version, ext):
    """Builds a filename like Woody_mdl_wip_v001.blend"""
    abbr = TASK_ABBR.get(task_type, task_type[:3].lower())
    clean_name = entity_name.replace(" ", "_")
    return f"{clean_name}_{abbr}_wip_v{version:03d}{ext}"

