#!/usr/bin/env python3
"""User preferences (theme, font scale, window memory, auto-refresh).

Stored in data/settings.json. All fields optional — missing keys fall back
to DEFAULTS, so old/hand-edited files never break loading.
"""
from paths import SETTINGS_FILE, write_json_atomic

DEFAULTS = {
    "theme": "light",          # "light" | "dark"
    "font_scale": 1.0,         # 0.8 .. 1.3 multiplier on base sizes
    "remember_ui": True,       # restore window size + tab on launch
    "geometry": "",            # "WxH+X+Y" window frame, "" = default
    "last_tab": "",            # notebook tab text to reselect, "" = first
    "auto_refresh_days": 7,    # prompt a live refresh when snapshot older; 0 = off
    "last_visit": "",          # ISO date, updated on clean exit
}

VALID_THEMES = ("light", "dark")
MIN_SCALE, MAX_SCALE = 0.8, 1.3


def load(path=None):
    """Settings dict merged over defaults (never raises)."""
    data = dict(DEFAULTS)
    try:
        import json
        with open(path or SETTINGS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in DEFAULTS:
                    data[k] = v
    except Exception:
        pass
    if data.get("theme") not in VALID_THEMES:
        data["theme"] = DEFAULTS["theme"]
    try:
        data["font_scale"] = min(MAX_SCALE, max(MIN_SCALE, float(data.get("font_scale", 1.0))))
    except (TypeError, ValueError):
        data["font_scale"] = DEFAULTS["font_scale"]
    try:
        data["auto_refresh_days"] = int(data.get("auto_refresh_days", 7))
    except (TypeError, ValueError):
        data["auto_refresh_days"] = DEFAULTS["auto_refresh_days"]
    return data


def save(data, path=None):
    """Persist (only known keys)."""
    write_json_atomic(path or SETTINGS_FILE,
                      {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS})
