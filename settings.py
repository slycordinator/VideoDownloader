import json
import logging
import os
from pathlib import Path, PurePath
from typing import overload

logger = logging.getLogger(__name__)


def get_appdata_path(app_name="VideoDownloader") -> Path:
    """Returns the AppData/Local/VideoDownloader path, creating it if needed."""
    local_appdata = Path(os.getenv("LOCALAPPDATA") or Path.home())
    app_folder = local_appdata / app_name
    app_folder.mkdir(parents=True, exist_ok=True)
    return app_folder


CONFIG_PATH = get_appdata_path() / "config.json"

@overload
def load_setting(key: str, default: str) -> str: ...
@overload
def load_setting(key: str, default: Path) -> Path: ...
@overload
def load_setting(key: str, default: bool) -> bool: ...
@overload
def load_setting(key: str, default: float) -> float: ...

def load_setting(key: str, default: str | Path | bool = False):
    """Load a single setting from config.json. Returns default on any failure."""
    try:
        if CONFIG_PATH.exists() and CONFIG_PATH.stat().st_size > 0:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(default, PurePath):
                    return Path(data.get(key, str(default)))
                return data.get(key, default)
    except json.JSONDecodeError as e:
        logger.warning("config.json is malformed, using defaults. Error: %s", e)
    except Exception as e:
        logger.error("Failed to load setting '%s': %s", key, e)
    return default


def save_setting(key: str, value) -> None:
    """Save a single setting to config.json."""
    try:
        data = {}
        if CONFIG_PATH.exists() and CONFIG_PATH.stat().st_size > 0:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)

        if isinstance(value, PurePath):
            value = str(value)
        data[key] = value

        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error("Failed to save setting '%s': %s", key, e)
