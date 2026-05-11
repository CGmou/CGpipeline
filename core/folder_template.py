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
        "Layout",
        "Blocking",
        "Anim",
        "Cfx/_wip",
        "Cfx/cache",
        "Cfx/Publish",
        "Vfx",
        "Lgt",
        "Comp",
        "Render",
        "Assembly"
    ]
    os.makedirs(base_path, exist_ok=True)
    for sub in sub_folders:
        os.makedirs(os.path.join(base_path, sub), exist_ok=True)

def create_project_base(project_path):
    """Creates the top-level project folders."""
    folders = [
        "References",
        "Assets/Char",
        "Assets/Props",
        "Assets/Sets",
        "Assets/Vehicles",
        "Shots",
        "SFX",
        "FinalOutput/EXR",
        "FinalOutput/MOV",
        "ProjectConfig/ColorManagement"
    ]
    for folder in folders:
        os.makedirs(os.path.join(project_path, folder), exist_ok=True)

