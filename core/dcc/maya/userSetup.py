# CGPipeline Maya bootstrap.
# This file is picked up by Maya when its containing folder is on MAYA_SCRIPT_PATH.
# The launcher injects that env var when launching Maya from the dashboard.
# Initialization is deferred so the Qt main loop is up before we build any UI.

import maya.utils
import maya.cmds as cmds


def _cgp_boot():
    try:
        import cgpipeline_maya
        cgpipeline_maya.initialize()
    except Exception as e:
        cmds.warning("CGPipeline: bootstrap failed: " + str(e))


# Only bootstrap when we're in interactive Maya (not mayapy / batch).
try:
    if not cmds.about(batch=True):
        maya.utils.executeDeferred(_cgp_boot)
except Exception:
    pass
