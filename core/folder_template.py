import os

def create_asset_structure(base_path):
    """Creates the detailed asset folder structure."""
    sub_folders = [
        "Publish/usd",
        "Textures/2k",
        "Textures/4k",
        "Textures/_wip",
        "Model/_wip",
        "Rig/_wip",
        "Shader/_wip"
    ]
    os.makedirs(base_path, exist_ok=True)
    for sub in sub_folders:
        os.makedirs(os.path.join(base_path, sub), exist_ok=True)

def create_shot_structure(base_path):
    """Creates the detailed shot folder structure."""
    sub_folders = [
        "01_Layout",
        "02_Blocking",
        "03_Anim",
        "04_Cfx/_wip",
        "04_Cfx/cache",
        "04_Cfx/Publish",
        "05_Vfx",
        "06_Lgt",
        "07_Comp",
        "08_Render",
        "09_Assembly"
    ]
    os.makedirs(base_path, exist_ok=True)
    for sub in sub_folders:
        os.makedirs(os.path.join(base_path, sub), exist_ok=True)

def create_project_base(project_path):
    """Creates the top-level project folders."""
    folders = [
        "00_References",
        "01_Assets/01_Char",
        "01_Assets/02_Props",
        "01_Assets/03_Sets",
        "01_Assets/04_Vehicles",
        "02_Shots",
        "03_SFX",
        "04_FinalOutput/EXR",
        "04_FinalOutput/MOV",
        "05_ProjectConfig/ColorManagement"
    ]
    for folder in folders:
        os.makedirs(os.path.join(project_path, folder), exist_ok=True)

