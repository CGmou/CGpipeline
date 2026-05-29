"""Shared CGPipeline constants.

Task status vocabulary is aligned with Kitsu's default statuses so the pipeline
and Kitsu read the same and stay in sync.
"""

# Canonical task statuses (Kitsu-aligned). "Retake" and "Omit" are pipeline extras.
TASK_STATUSES = [
    "Todo",
    "Work In Progress",
    "Waiting For Approval",
    "Retake",
    "Done",
    "Omit",
]

DEFAULT_STATUS = "Todo"

# Display colours per status (used by cards / sheets).
STATUS_COLORS = {
    "Done": "#28A745",
    "Waiting For Approval": "#0078D4",
    "Work In Progress": "#FFC107",
    "Retake": "#E0702A",
    "Todo": "#888888",
    "Omit": "#666666",
}

# Old CGPipeline status names -> new Kitsu-aligned names (migrated on registry load).
LEGACY_STATUS_MIGRATION = {
    "Ready": "Todo",
    "In Progress": "Work In Progress",
    "Pending Review": "Waiting For Approval",
    "Approved": "Done",
}
