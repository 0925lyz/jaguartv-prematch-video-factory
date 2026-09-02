from __future__ import annotations

import shutil
from pathlib import Path


def resolve_command(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if name == "node":
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node")
        if bundled.is_file():
            return str(bundled)
    return None
