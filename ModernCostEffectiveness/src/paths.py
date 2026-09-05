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
FONTS_DIR = ASSETS_DIR / "fonts"
MANA_FONT_FILE = FONTS_DIR / "mana.ttf"


def asset_path(relpath: str) -> Path:
    """Resolve a JSON-stored relative path like 'assets/thumbs/x-art.jpg'."""
    return ROOT / relpath
