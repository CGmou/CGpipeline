"""CLI helper to push a single task's status and/or thumbnail up to Kitsu.

Invoked as a fire-and-forget subprocess by the DCC addons (which may not have
gazu in their own Python). Runs in the system Python that has gazu installed and
reads the saved Kitsu credentials from the CGPipeline settings.

Usage:
    python core/kitsu_sync.py --registry <registry.json> --entity <name>
        --category Assets|Shots --task-type <CGPipeline task type>
        [--status <status>] [--thumbnail <image.png>]

Silent no-op (exit 0) when not connectable, not linked, or anything is missing —
it must never disrupt the DCC.
"""

import os
import sys
import json
import argparse

# Make `core` importable when run as a standalone script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.kitsu import KitsuManager, CGP_TO_KITSU_TASK_TYPE  # noqa: E402


def _log(msg):
    print(f"CGPipeline kitsu_sync: {msg}")


def _load_settings():
    settings_path = os.path.join(
        os.path.expanduser("~"), "Documents", "cgpipeline_system", "settings.json"
    )
    try:
        with open(settings_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_kitsu_id(registry_path):
    """Look up the Kitsu project id for the project that owns this registry."""
    project_root = os.path.normpath(os.path.dirname(registry_path))
    projects_root = os.path.dirname(project_root)
    index = os.path.join(projects_root, "project_index.json")
    try:
        with open(index, "r") as f:
            projects = json.load(f)
    except Exception:
        return None
    for p in projects:
        if os.path.normpath(p.get("path", "")) == project_root:
            return p.get("kitsu_id") or (p.get("kitsu_unlinked") or {}).get("kitsu_id")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--category", default="Assets")
    ap.add_argument("--task-type", dest="task_type", required=True)
    ap.add_argument("--status", default="")
    ap.add_argument("--thumbnail", default="")
    args = ap.parse_args()

    if not KitsuManager.available():
        _log("gazu not available; skipping.")
        return 0

    kid = _find_kitsu_id(args.registry)
    if not kid:
        _log("project not linked to Kitsu; skipping.")
        return 0

    s = _load_settings()
    host, email, pw = s.get("kitsu_host", ""), s.get("kitsu_email", ""), s.get("kitsu_pass", "")
    if not (host and email and pw):
        _log("no saved Kitsu credentials (enable Remember in the Kitsu dialog); skipping.")
        return 0

    km = KitsuManager()
    ok, msg = km.connect(host, email, pw)
    if not ok:
        _log(f"connect failed: {msg}")
        return 0

    import gazu

    try:
        kproj = gazu.project.get_project(kid)
    except Exception as e:
        _log(f"could not load Kitsu project: {e}")
        return 0

    # Resolve the entity (asset or shot) by name.
    entity = None
    try:
        if args.category == "Assets":
            entity = gazu.asset.get_asset_by_name(kproj, args.entity)
        else:
            shots = gazu.shot.all_shots_for_project(kproj)
            entity = next((sh for sh in shots if sh.get("name") == args.entity), None)
    except Exception as e:
        _log(f"entity lookup failed: {e}")
    if not entity:
        _log(f"entity '{args.entity}' not found on Kitsu; skipping.")
        return 0

    # Resolve the task on that entity matching the task type.
    kitsu_tt_name = CGP_TO_KITSU_TASK_TYPE.get(args.task_type, args.task_type)
    try:
        if args.category == "Assets":
            tasks = gazu.task.all_tasks_for_asset(entity)
        else:
            tasks = gazu.task.all_tasks_for_shot(entity)
    except Exception as e:
        _log(f"task lookup failed: {e}")
        return 0
    task = next((t for t in tasks if (t.get("task_type_name") or "").lower() == kitsu_tt_name.lower()), None)
    if not task:
        _log(f"task '{kitsu_tt_name}' not found on '{args.entity}'; skipping.")
        return 0

    # --- Status ---
    status_obj = None
    if args.status:
        try:
            status_obj = gazu.task.get_task_status_by_name(args.status)
            if status_obj:
                gazu.task.add_comment(task, status_obj, "Status updated from CGPipeline")
                _log(f"status -> {args.status}")
            else:
                _log(f"Kitsu has no status named '{args.status}'.")
        except Exception as e:
            _log(f"status update failed: {e}")

    # --- Thumbnail (preview) ---
    if args.thumbnail and os.path.exists(args.thumbnail):
        try:
            # add_preview needs a comment to attach to; reuse the status change or
            # make a neutral comment with the task's current status.
            comment = None
            if status_obj:
                comment = gazu.task.add_comment(task, status_obj, "CGPipeline thumbnail")
            else:
                try:
                    full = gazu.task.get_task(task["id"])
                    cur = gazu.task.get_task_status(full["task_status_id"])
                    comment = gazu.task.add_comment(task, cur, "CGPipeline thumbnail")
                except Exception:
                    comment = None
            if comment:
                preview = gazu.task.add_preview(task, comment, args.thumbnail)
                try:
                    gazu.task.set_main_preview(preview)
                except Exception:
                    pass
                _log("thumbnail uploaded to Kitsu.")
            else:
                _log("could not create a comment to attach the preview.")
        except Exception as e:
            _log(f"thumbnail upload failed: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"unexpected error: {e}")
        sys.exit(0)
