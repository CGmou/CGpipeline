import os
import subprocess
import shutil
import sys
import platform
from .utils import build_work_filename, get_latest_version

def open_file_cross_platform(filepath):
    """
    Opens a file using the system's default application.
    """
    if platform.system() == "Windows":
        os.startfile(filepath)
    elif platform.system() == "Darwin":  # macOS
        subprocess.Popen(["open", filepath])
    else:  # Linux
        subprocess.Popen(["xdg-open", filepath])

def resolve_executable_path(dcc_name, path):
    """
    On macOS, if the path is a .app bundle, resolve to the internal executable.
    On Windows, if path is a directory, look for common executables.
    """
    if not path: return path
    
    system = platform.system()
    if system == "Darwin" and path.endswith(".app"):
        if dcc_name == "Blender":
            return os.path.join(path, "Contents", "MacOS", "Blender")
        elif dcc_name == "Maya":
            return os.path.join(path, "Contents", "MacOS", "Maya")
        elif dcc_name == "Houdini":
            return os.path.join(path, "Contents", "MacOS", "houdini")
    
    elif system == "Windows" and os.path.isdir(path):
        # If user selected a folder, try to find the executable inside
        if dcc_name == "Blender":
            exe = os.path.join(path, "blender.exe")
            if os.path.exists(exe): return exe
        elif dcc_name == "Maya":
            exe = os.path.join(path, "bin", "maya.exe")
            if os.path.exists(exe): return exe
        elif dcc_name == "Houdini":
            exe = os.path.join(path, "bin", "houdini.exe")
            if os.path.exists(exe): return exe
            
    return path

def launch_dcc(dcc_name, exe_path, task_obj, registry_path):
    """
    Launches a DCC with injected environment variables for the CGPipeline pipeline.
    """
    exe_path = resolve_executable_path(dcc_name, exe_path)
    
    if not exe_path or not os.path.exists(exe_path):
        return False, f"DCC executable not found at: {exe_path}"

    # Prepare Environment
    env = os.environ.copy()
    env["CGP_TASK_ID"] = str(task_obj.get("id", "")).strip()
    env["CGP_TASK_PATH"] = str(task_obj.get("path", "")).strip()
    env["CGP_ENTITY_NAME"] = str(task_obj.get("name", "")).strip()
    env["CGP_TASK_TYPE"] = str(task_obj.get("type", "")).strip()
    env["CGP_SUB_CAT"] = str(task_obj.get("sub_category", "")).strip()
    env["CGP_REGISTRY_PATH"] = str(registry_path).strip()
    env["CGP_CATEGORY"] = str(task_obj.get("category", "")).strip()
    env["CGP_IN_DCC_LAUNCH"] = "1" # Mark that we are launching from the hub

    # App Path
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_main = os.path.join(app_root, "main.py")
    env["CGP_APP_MAIN"] = str(app_main).strip()

    try:
        task_folder = str(task_obj.get("path", "")).strip()
        _, latest_file = get_latest_version(task_folder)

        # On Windows, shell=False is generally safer for .exe files with spaces in paths
        # when using a list of arguments.
        use_shell = False 

        # 1. Handle Blender Logic
        if dcc_name == "Blender":
            target_file = None
            if latest_file:
                target_file = os.path.join(task_folder, latest_file)
            else:
                template_path = os.path.join(app_root, "start_new_template", "blender_start_template.blend")
                v001_name = build_work_filename(
                    task_obj.get("name", "entity"),
                    task_obj.get("sub_category", ""),
                    task_obj.get("type", ""),
                    1,
                    ".blend"
                )
                v001_full_path = os.path.join(task_folder, v001_name)
                os.makedirs(task_folder, exist_ok=True)
                
                if os.path.exists(template_path):
                    shutil.copy2(template_path, v001_full_path)
                    target_file = v001_full_path
                else:
                    target_file = v001_full_path

            # SAME-SESSION LOGIC: Check if we were launched from a DCC session
            is_from_dcc = os.environ.get("CGP_IN_DCC") == "Blender"
            
            if is_from_dcc:
                import json
                command_file = os.environ.get("CGP_COMMAND_FILE")
                if not command_file:
                    command_file = os.path.join(os.path.expanduser("~"), "Documents", "cgpipeline_system", "blender_command.json")
                
                cmd_data = {
                    "action": "open_task",
                    "filepath": target_file,
                    "task_id": str(task_obj.get("id", "")),
                    "entity_name": str(task_obj.get("name", "")),
                    "task_type": str(task_obj.get("type", "")),
                    "registry_path": str(registry_path)
                }
                
                try:
                    with open(command_file, 'w') as f:
                        json.dump(cmd_data, f)
                    return True, None
                except Exception as e:
                    print(f"CGPipeline Error: Failed to write command file: {e}")

            # LAUNCH NEW BLENDER
            cmd = [exe_path]
            if target_file:
                cmd.append(target_file)
            subprocess.Popen(cmd, env=env, shell=use_shell)
            return True, None

        # 2. Handle Generic/Other DCCs
        else:
            if latest_file:
                full_latest_path = os.path.join(task_folder, latest_file)
                if exe_path:
                     subprocess.Popen([exe_path, full_latest_path], env=env, shell=use_shell)
                else:
                     open_file_cross_platform(full_latest_path)
            else:
                ext = ".ma" if dcc_name == "Maya" else ".hipnc" if dcc_name == "Houdini" else ".txt"
                v001_name = build_work_filename(
                    task_obj.get("name", "entity"),
                    task_obj.get("sub_category", ""),
                    task_obj.get("type", ""),
                    1,
                    ext
                )
                v001_full_path = os.path.normpath(os.path.join(task_folder, v001_name))
                os.makedirs(task_folder, exist_ok=True)
                
                with open(v001_full_path, "w") as f: pass
                
                if exe_path:
                    subprocess.Popen([exe_path, v001_full_path], env=env, shell=use_shell)
                else:
                    open_file_cross_platform(v001_full_path)
                    
            return True, None

    except Exception as e:
        return False, str(e)


