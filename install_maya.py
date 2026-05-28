"""One-time installer: make CGPipeline load automatically every time Maya starts.

Run once with any Python:

    python install_maya.py

It writes a small, self-contained bootstrap block into Maya's persistent
userSetup.py (in the version-independent Maya scripts folder). After this, the
CGPipeline shelf + panel load on every Maya launch — no manual import needed,
and no need to launch Maya through the dashboard.

Safe to re-run: it replaces its own block between markers and leaves the rest of
your userSetup.py untouched. To uninstall, delete the block between the markers.
"""
import os
import sys
import platform

START = "# >>> CGPIPELINE BOOTSTRAP"
END = "# <<< CGPIPELINE BOOTSTRAP <<<"


def maya_app_dir():
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Windows":
        return os.path.join(home, "Documents", "maya")
    if system == "Darwin":
        return os.path.join(home, "Library", "Preferences", "Autodesk", "maya")
    return os.path.join(home, "maya")  # Linux


def dcc_maya_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "core", "dcc", "maya"))


def build_block(dcc_dir):
    return (
        START + " (auto-generated; do not edit between markers) >>>\n"
        "import os as _cgp_os, sys as _cgp_sys\n"
        "_cgp_dir = r\"" + dcc_dir + "\"\n"
        "if _cgp_os.path.isdir(_cgp_dir) and _cgp_dir not in _cgp_sys.path:\n"
        "    _cgp_sys.path.append(_cgp_dir)\n"
        "try:\n"
        "    import maya.utils as _cgp_mu\n"
        "    import maya.cmds as _cgp_cmds\n"
        "    if not _cgp_cmds.about(batch=True):\n"
        "        def _cgp_boot():\n"
        "            try:\n"
        "                import cgpipeline_maya\n"
        "                cgpipeline_maya.initialize()\n"
        "            except Exception as _cgp_e:\n"
        "                _cgp_cmds.warning('CGPipeline bootstrap failed: ' + str(_cgp_e))\n"
        "        _cgp_mu.executeDeferred(_cgp_boot)\n"
        "except Exception:\n"
        "    pass\n"
        + END + "\n"
    )


def strip_existing(text):
    """Remove any previously-installed CGPIPELINE block, leaving the rest intact."""
    start_i = text.find(START)
    if start_i == -1:
        return text
    end_i = text.find(END, start_i)
    if end_i == -1:
        # Malformed (no end marker) — drop from the start marker onward.
        return text[:start_i].rstrip() + "\n"
    end_i += len(END)
    return (text[:start_i] + text[end_i:]).strip() + "\n"


def main():
    dcc_dir = dcc_maya_dir()
    if not os.path.isdir(dcc_dir):
        print("ERROR: could not find core/dcc/maya next to this script:")
        print("  " + dcc_dir)
        return 1

    scripts_dir = os.path.join(maya_app_dir(), "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    user_setup = os.path.join(scripts_dir, "userSetup.py")

    existing = ""
    if os.path.exists(user_setup):
        with open(user_setup, "r", encoding="utf-8") as f:
            existing = f.read()

    cleaned = strip_existing(existing).strip() if existing else ""
    block = build_block(dcc_dir)
    new_content = (cleaned + "\n\n" + block) if cleaned else block

    with open(user_setup, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("CGPipeline installed for Maya.")
    print("  Plugin folder : " + dcc_dir)
    print("  userSetup.py  : " + user_setup)
    print("")
    print("Restart Maya. The CGPipeline shelf loads automatically; click its")
    print("'Panel' button (or open a task from the dashboard) to show the panel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
