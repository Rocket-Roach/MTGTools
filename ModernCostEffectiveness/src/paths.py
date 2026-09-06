"""Central folder layout for the MTG Modern Deck Tracker.

Root
|-- tracker_gui.bat / tracker.bat / README.md / requirements.txt
|-- src/          all python modules (this file lives here)
|-- data/         JSON inputs, collection, prices, snapshots/
|-- assets/       card-art thumbnails (paths stored in JSON are relative to ROOT)
"""
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
ROOT = SRC_DIR.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
THUMBS_DIR = ASSETS_DIR / "thumbs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

PLAN_FILE = DATA_DIR / "esper_blink_gateway_plan.json"
METAGAME_FILE = DATA_DIR / "modern_metagame.json"
DECKLISTS_FILE = DATA_DIR / "decklists.json"
COLLECTION_FILE = DATA_DIR / "my_collection.json"
PRICES_FILE = DATA_DIR / "prices.json"
MANA_FILE = DATA_DIR / "mana_costs.json"
MATCHUPS_FILE = DATA_DIR / "matchups.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
BACKUPS_DIR = DATA_DIR / "backups"
FONTS_DIR = ASSETS_DIR / "fonts"
MANA_FONT_FILE = FONTS_DIR / "mana.ttf"

# Prices older than this are treated as stale (re-fetched, never trusted
# for buildable math without a refresh).
PRICE_TTL_DAYS = 14
SNAPSHOT_KEEP = 10
BACKUP_KEEP = 5


def asset_path(relpath: str) -> Path:
    """Resolve a JSON-stored relative path like 'assets/thumbs/x-art.jpg'."""
    return ROOT / relpath


def write_json_atomic(path, data) -> None:
    """Write JSON crash-safely: temp file + atomic rename."""
    import json
    import os
    path = Path(path)
    if path.parent and str(path.parent):
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def read_json(path, default=None):
    """Read JSON, returning default on any failure."""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default if default is not None else {}


def prune_snapshots(keep: int = SNAPSHOT_KEEP):
    """Keep only the newest snapshot files (names sort chronologically)."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SNAPSHOTS_DIR.glob("metagame_*.json"), key=lambda p: p.name)
    for old in files[:-keep] if len(files) > keep else []:
        try:
            old.unlink()
        except Exception:
            pass


def backup_file(path, keep: int = BACKUP_KEEP):
    """Timestamped copy of a data file into backups/, pruning old ones.
    Returns the backup Path, or None if there was nothing to back up."""
    import datetime
    path = Path(path)
    if not path.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUPS_DIR / f"{path.stem}_{stamp}{path.suffix}"
    i = 2
    while dest.exists():
        dest = BACKUPS_DIR / f"{path.stem}_{stamp}_{i}{path.suffix}"
        i += 1
    import shutil
    shutil.copyfile(path, dest)
    olds = sorted(BACKUPS_DIR.glob(f"{path.stem}_*{path.suffix}"),
                  key=lambda p: p.name)[:-keep]
    for old in olds:
        try:
            old.unlink()
        except Exception:
            pass
    return dest
