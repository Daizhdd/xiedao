"""Runtime paths; resources are read-only, manuscripts live outside macOS bundles."""
import os
from pathlib import Path
import sys


def data_directory():
    """Keep Windows portable data compatible and use a stable macOS user path."""
    override = os.environ.get("XIEDAO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "写道"
    base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return base / "data"


def resource_path(relative):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / relative)
