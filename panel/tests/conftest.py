from __future__ import annotations

import os
import sys
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PANEL_DIR.parent

if str(PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(PANEL_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("CONAN_MANAGER_PATH", str(REPO_ROOT / "conanserver.sh"))
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
