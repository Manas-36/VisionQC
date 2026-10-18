"""
Central configuration for the VisionQC hub.

Unlike the previous version, machines and cameras are NOT predefined here —
the admin adds them from the dashboard's Admin tab at runtime. This file
only holds structural paths and tunables that apply system-wide.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Override with VISIONQC_DATA_DIR (used by Docker and the test-suite).
DATA_DIR = os.environ.get("VISIONQC_DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "inspection.db")
IMAGE_DIR = os.path.join(DATA_DIR, "images")
REFERENCE_DIR = os.path.join(DATA_DIR, "references")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(REFERENCE_DIR, exist_ok=True)

# A camera is considered OFFLINE if the hub hasn't heard from it for this long.
CAMERA_OFFLINE_TIMEOUT_SEC = 45

# Rolling window size used by the trend/diagnostics engine (per camera).
TREND_WINDOW_SIZE = 25

# Number of consecutive same-direction points that counts as a "drift" trend.
TREND_RUN_LENGTH = 6

# Default max allowed error (%) for a newly created machine, if the admin
# doesn't set one explicitly.
DEFAULT_MAX_ERROR_PERCENT = 3.0
