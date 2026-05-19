from __future__ import annotations

from pathlib import Path

CONAN_DEDICATED_SERVER_APP_ID = 443030
CONAN_WORKSHOP_APP_ID = 440900


def workshop_content_dir(serverfiles_dir: Path) -> Path:
    return serverfiles_dir / "steamapps" / "workshop" / "content" / str(CONAN_WORKSHOP_APP_ID)
