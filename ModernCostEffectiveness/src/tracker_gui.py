#!/usr/bin/env python3
"""
MTG Modern Deck Tracker - GUI
Visual dashboard for collection tracking across the Modern metagame.
Uses: modern_metagame.json + decklists.json (inputs)
      + my_collection.json (cached collection)

Run:  python tracker_gui.py
       or double-click tracker_gui.bat
"""

import datetime
import json
import os
import queue
import re
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from collections import defaultdict

from paths import (ROOT as BASE_DIR, PLAN_FILE, COLLECTION_FILE, METAGAME_FILE,
                     DATA_DIR, SNAPSHOTS_DIR, PRICES_FILE, DECKLISTS_FILE,
                     MANA_FILE, MANA_FONT_FILE, MATCHUPS_FILE, asset_path,
                     write_json_atomic, backup_file, prune_snapshots,
                     PRICE_TTL_DAYS)
METAGAME_URL = "https://www.mtggoldfish.com/metagame/modern#paper"

# Reuse parsing / model logic from CLI tracker
try:
    from tracker import Card, Collection, DeckPlan, FileParser
    HAS_TRACKER = True
except ImportError as e:
    HAS_TRACKER = False
    IMPORT_ERROR = str(e)

# Pillow for deck thumbnail art (optional - table works without it)
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import theme
from theme import C, F
import settings as app_settings


# True mana-symbol glyphs (Mana font codepoints) composited onto art.
MANA_GLYPHS = {'W': 0xE600, 'U': 0xE601, 'B': 0xE602,
               'R': 0xE603, 'G': 0xE604, 'C': 0xE904}
_mana_fonts = {}


def _mana_font(size: int):
    """Mana symbol font at a size (cached). None if unavailable."""
    if size in _mana_fonts:
        return _mana_fonts[size]
    font = None
    if HAS_PIL and MANA_FONT_FILE.exists():
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(str(MANA_FONT_FILE), size)
        except Exception:
            font = None
    _mana_fonts[size] = font
    return font


def draw_mana_pip(draw, x, y, d, letter):
    """Filled pip + true mana-symbol glyph. Falls back to a plain letter."""
    from PIL import ImageFont
    draw.ellipse([x, y, x + d, y + d], fill=PIP_FILL[letter],
                 outline=PIP_EDGE[letter], width=max(1, d // 14))
    glyph = chr(MANA_GLYPHS.get(letter, 0xE904))
    font = _mana_font(int(d * 0.72))
    if font is None:
        try:
            font = ImageFont.truetype("arialbd.ttf", int(d * 0.5))
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", int(d * 0.5))
            except Exception:
                font = ImageFont.load_default()
        glyph = letter
    bbox = draw.textbbox((0, 0), glyph, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x + (d - tw) / 2 - bbox[0], y + (d - th) / 2 - bbox[1]),
              glyph, font=font, fill=PIP_TEXT[letter])


def top_symbols(colors, n=3):
    """Top-n (letter, share-of-total) colored symbols, most represented first."""
    counts = {c: v for c, v in (colors or {}).items() if v > 0}
    total = sum(counts.values())
    if total <= 0:
        return []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(c, v / total) for c, v in ranked[:n]]


def pip_diameters(shares, dmax, dmin=14):
    """Diameters linear in share so ratios read directly: the top share gets
    dmax, e.g. 50/25/25 -> 2:1:1 with equal small symbols floored at dmin."""
    if not shares:
        return []
    top = max(shares)
    if top <= 0:
        return [dmin] * len(shares)
    return [max(dmin, int(round(dmax * s / top))) for s in shares]


_pip_images = {}


def make_pip_image(letter, d):
    """Transparent mana-symbol image of diameter d (cached, Tk required)."""
    key = (letter, int(d))
    if key in _pip_images:
        return _pip_images[key]
    photo = None
    try:
        from PIL import Image as _Image, ImageDraw as _Draw
        pad = max(2, int(d) // 14 + 1)
        size = int(d) + 2 * pad
        im = _Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw_mana_pip(_Draw.Draw(im), pad, pad, int(d), letter)
        photo = ImageTk.PhotoImage(im)
    except Exception:
        photo = None
    _pip_images[key] = photo
    return photo


def _wheel_steps(event):
    """Single-step scroll delta for tear-free image scrolling."""
    if getattr(event, "num", None) == 4:
        return -1
    if getattr(event, "num", None) == 5:
        return 1
    delta = getattr(event, "delta", 0) or 0
    return -1 * int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)

# Mana pip + archetype chip palette.
PIP_FILL = {'W': '#FFFBD5', 'U': '#0E68AB', 'B': '#150B00',
            'R': '#D32029', 'G': '#00733E', 'C': '#CCC2C0'}
PIP_EDGE = {'W': '#A99B5F', 'U': '#0A4A7A', 'B': '#000000',
            'R': '#8E141B', 'G': '#004D29', 'C': '#8E8684'}
PIP_TEXT = {'W': '#000000', 'U': '#FFFFFF', 'B': '#FFFFFF',
            'R': '#FFFFFF', 'G': '#FFFFFF', 'C': '#000000'}
ARCH_CHIP = {'Aggro': '#C0392B', 'Tempo': '#17A589', 'Midrange': '#2874A6',
             'Control': '#7D3C98', 'Combo': '#7D6608', 'Ramp': '#1E8449',
             'Unclassified': '#707070'}
ARCH_CHIP_FG = {'Tempo': '#1a1a1a'}  # default chip text is white; teal needs black


def fmt_price(p) -> str:
    """$12, $0.35, or '-' for unknown (cheap cards show cents)."""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "-"
    if not p:
        return "-"
    return f"${p:.2f}" if p < 1 else f"${p:,.0f}"


BUILDABLE_USD = 20.0


def wr_colors(wr):
    """(fill, text) for a winrate cell, red -> yellow -> green."""
    if wr is None:
        return ("#3a3f4a", "#bbbbbb")
    if wr < 40:
        return ("#b03a2e", "#ffffff")
    if wr < 47:
        return ("#e67e22", "#1a1a1a")
    if wr < 53:
        return ("#f1c40f", "#1a1a1a")
    if wr < 60:
        return ("#7dc48a", "#1a1a1a")
    return ("#239b56", "#1a1a1a")


def wr_text_color(wr):
    """Readable winrate text color on a white background."""
    if wr is None:
        return "#999999"
    if wr < 47:
        return "#c0392b"
    if wr > 53:
        return "#1e8449"
    return "#7d6608"


MU_MIN_MATCHES = 10


def mu_fade(hexcolor: str, toward: str = "#8a8f98", t: float = 0.6) -> str:
    """Desaturate a cell fill for low-sample matchups (still readable)."""
    try:
        def ch(s, i):
            return int(s[i:i + 2], 16)
        r = round(ch(hexcolor, 1) * (1 - t) + ch(toward, 1) * t)
        g = round(ch(hexcolor, 3) * (1 - t) + ch(toward, 3) * t)
        b = round(ch(hexcolor, 5) * (1 - t) + ch(toward, 5) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hexcolor


def short_deck_name(name: str, limit: int = 11) -> str:
    """Compact (possibly two-line) label for narrow matrix headers."""
    if len(name) <= limit:
        return name
    parts = name.split(" ")
    if len(parts) < 2:
        return name[:limit]
    best, best_diff = None, None
    for i in range(1, len(parts)):
        top = " ".join(parts[:i])
        rest = " ".join(parts[i:])
        diff = abs(len(top) - len(rest))
        if best_diff is None or diff < best_diff:
            best, best_diff = (top, rest), diff
    top, rest = best
    if len(top) > 16 or len(rest) > 16:
        return name[:limit]
    return top + "\n" + rest


def is_buildable(prog) -> bool:
    """A deck counts as buildable when complete or when the missing cards
    total less than $20. Cards with unknown prices never count as free:
    any unpriced shortfall blocks buildable status."""
    if not prog or prog.get("total", 0) <= 0:
        return False
    if prog.get("pct", 0) >= 100:
        return True
    if prog.get("missing_value", 0) >= BUILDABLE_USD:
        return False
    return all(not (r.get("short", 0) > 0 and not r.get("price"))
               for r in prog.get("rows", []))


def read_text_file_smart(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class GuiStore:
    """Thin wrapper: metagame data + collection with fast indexed lookup."""

    def __init__(self, plan_file: Path, collection_file: Path):
        self.plan_file = Path(plan_file)
        self.collection_file = Path(collection_file)
        with open(self.plan_file, "r", encoding="utf-8") as f:
            self.plan_data = json.load(f)
        self.phases = self.plan_data["phases"]
        self.summary = self.plan_data.get("summary", {})
        self.metadata = self.plan_data.get("metadata", {})
        self.collection = Collection()
        self.load_collection()
        self.metagame = self.load_metagame()
        self.decklists = self.load_decklists()
        self.price_cache = self.load_price_cache()
        self.price_map = self.build_price_map()
        self.mana_cache = self.load_mana_cache()
        self.matchups = self.load_matchups()
        self.deck_overrides = self.load_overrides()

    def load_matchups(self):
        try:
            if MATCHUPS_FILE.exists():
                with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def matchup_row(self, ours: str):
        """Resolved matchup row for one of our decks (None if unmapped)."""
        return (self.matchups or {}).get("decks", {}).get(ours)

    def matchup_cell(self, a_ours: str, b_ours: str):
        """(winrate, matches) for A vs B from A's perspective.
        'mirror' for the diagonal, None when unknown."""
        if a_ours == b_ours:
            return "mirror"
        ra = self.matchup_row(a_ours)
        if not ra:
            return None
        opp = (self.matchups or {}).get("mapping", {}).get(b_ours)
        if not opp:
            return None
        return ra.get("vs", {}).get(opp)

    def load_metagame(self):
        try:
            if METAGAME_FILE.exists():
                with open(METAGAME_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"source": METAGAME_URL, "snapshot_date": "unknown", "decks": []}

    def list_snapshots(self):
        d = SNAPSHOTS_DIR
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("metagame_*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                out.append({"path": f, "date": str(data.get("snapshot_date", f.stem)),
                            "timeframe": str(data.get("timeframe", "?")), "data": data})
            except Exception:
                continue
        out.sort(key=lambda s: (s["date"], s["path"].name))
        return out

    @staticmethod
    def compare_snapshots(old, new):
        old_map = {dd["name"]: dd for dd in old.get("decks", [])}
        new_map = {dd["name"]: dd for dd in new.get("decks", [])}
        rows = []
        for name in set(old_map) | set(new_map):
            o = old_map.get(name)
            n = new_map.get(name)
            op = o.get("meta_pct") if o else None
            np = n.get("meta_pct") if n else None
            if op is None:
                status, delta = "NEW", None
            elif np is None:
                status, delta = "OUT", None
            else:
                delta = round(np - op, 2)
                status = "UP" if delta > 0.05 else ("DOWN" if delta < -0.05 else "FLAT")
            rows.append({"name": name, "old": op, "new": np, "delta": delta,
                         "status": status})
        rows.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or 0)))
        return rows

    def load_decklists(self):
        try:
            if DECKLISTS_FILE.exists():
                with open(DECKLISTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def load_price_cache(self):
        try:
            if PRICES_FILE.exists():
                with open(PRICES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def build_price_map(self):
        """Cheapest known paper price per card: bundled reference data, then
        scraped cheapest-printing prices from prices.json (scraped wins)."""
        prices = {}
        for phase in self.phases:
            for ci in phase.get("cards", []):
                key = ci["name"].strip().lower()
                p = float(ci.get("price", 0) or 0)
                if p and (key not in prices or p < prices[key]):
                    prices[key] = p
        for key, val in (self.price_cache or {}).items():
            p = val.get("price") if isinstance(val, dict) else val
            try:
                p = float(p) if p else 0
            except (TypeError, ValueError):
                p = 0
            if p:
                prices[key] = p
        return prices

    def price_age_days(self, key: str):
        """Age in days of a cached price, or None if unknown/undated."""
        v = (self.price_cache or {}).get(key)
        stamp = v.get("updated") if isinstance(v, dict) else None
        if not stamp:
            return None
        try:
            return (datetime.date.today() - datetime.date.fromisoformat(str(stamp)[:10])).days
        except Exception:
            return None

    def is_price_stale(self, key: str) -> bool:
        """A cached price counts as stale past PRICE_TTL_DAYS."""
        v = (self.price_cache or {}).get(key)
        price = v.get("price") if isinstance(v, dict) else None
        if not price:
            return False
        return (self.price_age_days(key) or 0) > PRICE_TTL_DAYS

    def cards_missing_prices(self):
        """{lower_name: display_name} for top-20 cards with no *fresh* price
        that the collection doesn't already cover (owned < most needed
        copies). Fully-owned cards need no price since there's nothing
        left to buy. Stale cache entries count as missing so refreshes
        re-check them (display math keeps using the old price meanwhile)."""
        disp = {}
        need = {}
        for entry in (self.decklists or {}).values():
            for section in ("mainboard", "sideboard"):
                for ci in entry.get(section, []):
                    key = ci["name"].strip().lower()
                    disp.setdefault(key, ci["name"])
                    q = int(ci.get("qty", 1))
                    if key not in need or q > need[key]:
                        need[key] = q
        out = {}
        for key, display in disp.items():
            if self.price_map.get(key) and not self.is_price_stale(key):
                continue
            if self.owned_qty(key) < need.get(key, 1):
                out[key] = display
        return out

    def load_overrides(self):
        """Per-deck card substitutions: {deck: {original: replacement}}."""
        try:
            path = DATA_DIR / "deck_overrides.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def save_overrides(self):
        try:
            write_json_atomic(DATA_DIR / "deck_overrides.json", self.deck_overrides)
        except Exception:
            pass

    def set_override(self, deck_name: str, original: str, replacement: str):
        """Swap one card out of a deck's list (quantity stays the same)."""
        if not original or not replacement or original == replacement:
            return False
        self.deck_overrides.setdefault(deck_name, {})[original] = replacement
        self.save_overrides()
        return True

    def clear_overrides(self, deck_name: str):
        if deck_name in self.deck_overrides:
            del self.deck_overrides[deck_name]
            self.save_overrides()
            return True
        return False

    def decklist_progress(self, deck_name: str):
        """Ownership vs a deck's own 60+15 list (with user substitutions
        applied at the same quantity). None if no list stored."""
        entry = (self.decklists or {}).get(deck_name)
        if not entry:
            return None
        subs = (self.deck_overrides or {}).get(deck_name, {})
        owned = 0
        total = 0
        rows = []
        for section, items in (("Mainboard", entry.get("mainboard", [])),
                               ("Sideboard", entry.get("sideboard", []))):
            for ci in items:
                orig = ci["name"]
                name = subs.get(orig, orig)
                need = int(ci.get("qty", 1))
                have = self.owned_qty(name)
                price = self.price_map.get(name.strip().lower(), 0)
                short = max(0, need - have)
                owned += min(have, need)
                total += need
                rows.append({"name": name, "orig": orig, "need": need,
                             "have": have, "short": short, "price": price,
                             "cost": short * price, "complete": short == 0,
                             "section": section,
                             "substituted_from": orig if name != orig else None})
        pct = (owned / total * 100) if total else 100.0
        return {"owned": owned, "total": total, "pct": pct,
                "missing_value": sum(r["cost"] for r in rows), "rows": rows}

    def load_collection(self):
        if self.collection_file.exists():
            try:
                with open(self.collection_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.collection = Collection.from_dict(data)
            except Exception:
                self.collection = Collection()
        else:
            self.collection = Collection()

    def save_collection(self):
        write_json_atomic(self.collection_file, self.collection.to_dict())

    def owned_qty(self, name: str) -> int:
        """Printing-agnostic, face-aware lookup (any shared '//' face counts)."""
        return self.collection.get_quantity(name)

    def load_mana_cache(self):
        try:
            if MANA_FILE.exists():
                with open(MANA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def deck_colors(self, deck_name: str):
        """Colored-pip counts from card mana costs (qty-weighted over the
        full 75), always in sync with the list used for progress."""
        entry = (self.decklists or {}).get(deck_name)
        if not entry:
            return {}
        counts = {}
        for section in ("mainboard", "sideboard"):
            for ci in entry.get(section, []):
                v = (self.mana_cache or {}).get(ci["name"].strip().lower())
                pips = v.get("pips") if isinstance(v, dict) else None
                if not pips:
                    continue
                q = int(ci.get("qty", 1))
                for c, n in pips.items():
                    counts[c] = counts.get(c, 0) + n * q
        return counts

    def cards_missing_mana(self):
        """{lower_name: display_name} for top-20 cards with no pip data."""
        disp = {}
        for entry in (self.decklists or {}).values():
            for section in ("mainboard", "sideboard"):
                for ci in entry.get(section, []):
                    disp.setdefault(ci["name"].strip().lower(), ci["name"])
        cache = self.mana_cache or {}
        return {k: v for k, v in disp.items()
                if not (isinstance(cache.get(k), dict)
                        and isinstance(cache[k].get("pips"), dict))}

    def deck_progress_list(self):
        """All metagame decks with own-list progress, buildable first."""
        out = []
        for d in (getattr(self, "metagame", {}) or {}).get("decks", []):
            prog = self.decklist_progress(d["name"])
            if prog is None:
                prog = {"owned": 0, "total": 0, "pct": 0.0,
                        "missing_value": 0, "rows": []}
            out.append({"deck": d["name"], "meta_pct": d.get("meta_pct"),
                        "archetype": d.get("archetype", "—"),
                        "colors": self.deck_colors(d["name"]),
                        "buildable": is_buildable(prog), **prog})
        out.sort(key=lambda x: (not x["buildable"], -x["pct"]))
        return out

    def shopping_priorities(self, deck_list=None):
        """Ranked buy list for the bang-for-buck question: cards covering
        the most decks first, then cheapest total. buy = max shortfall
        across decks (one purchase serves every deck). Unpriced cards sort
        last since they can't unlock anything reliably."""
        if deck_list is None:
            deck_list = self.deck_progress_list()
        per = {}
        for prog in deck_list:
            for r in prog["rows"]:
                if r["short"] <= 0:
                    continue
                key = r["name"].strip().lower()
                e = per.setdefault(key, {"display": r["name"], "decks": set(),
                                         "buy": 0, "price": r["price"] or 0})
                e["decks"].add(prog["deck"])
                e["buy"] = max(e["buy"], r["short"])
                if r["price"]:
                    e["price"] = e["price"] or r["price"]
        rows = []
        for key, e in per.items():
            total = e["buy"] * e["price"] if e["price"] else None
            rows.append({"name": e["display"], "decks": len(e["decks"]),
                         "deck_names": sorted(e["decks"]), "buy": e["buy"],
                         "price": e["price"], "total": total,
                         "unpriced": not e["price"]})
        rows.sort(key=lambda r: (r["unpriced"], -r["decks"],
                                 r["total"] if r["total"] is not None else float("inf")))
        return rows

    def deck_unlock_order(self, deck_list=None):
        """Non-buildable decks cheapest-first; decks with unpriced gaps
        sort last (their totals are understated)."""
        if deck_list is None:
            deck_list = self.deck_progress_list()
        out = []
        for prog in deck_list:
            if prog["buildable"]:
                continue
            unpriced = any(r["short"] > 0 and not r["price"] for r in prog["rows"])
            missing_n = sum(1 for r in prog["rows"] if r["short"] > 0)
            out.append({"deck": prog["deck"], "meta_pct": prog.get("meta_pct"),
                        "missing_value": prog["missing_value"],
                        "missing_n": missing_n, "unpriced": unpriced})
        out.sort(key=lambda d: (d["unpriced"], d["missing_value"]))
        return out

    def deck_art(self, deck_name: str):
        """Local art file for a dashboard deck (metagame thumb or slug fallback)."""
        for d in (getattr(self, "metagame", {}) or {}).get("decks", []):
            if d["name"] == deck_name and d.get("thumb"):
                return d["thumb"]
        s = re.sub(r'[^a-z0-9]+', '-', deck_name.lower()).strip('-')
        rel = f"assets/thumbs/{s}-art.jpg"
        return rel if asset_path(rel).exists() else None

    def overall(self, deck_list=None):
        # Unique cards across all stored decklists (max copies needed per
        # name), counted face-aware so DFCs aren't undercounted.
        if deck_list is None:
            deck_list = self.deck_progress_list()
        need = {}  # key -> [maxqty, display name]
        for entry in (self.decklists or {}).values():
            for section in ("mainboard", "sideboard"):
                for ci in entry.get(section, []):
                    key = ci["name"].strip().lower()
                    q = int(ci.get("qty", 1))
                    if key not in need or q > need[key][0]:
                        need[key] = [q, ci["name"]]
        uniq_owned = 0
        copies_need = 0
        copies_owned = 0
        missing_val = 0.0
        for key, (qty, display) in need.items():
            have = self.collection.get_quantity(display)
            copies_need += qty
            copies_owned += min(have, qty)
            if have >= qty:
                uniq_owned += 1
            else:
                missing_val += (qty - have) * self.price_map.get(key, 0)
        uniq_total = len(need)
        buildable = sum(1 for d in deck_list if d["buildable"])
        return {
            "uniq_owned": uniq_owned, "uniq_total": uniq_total,
            "uniq_pct": (uniq_owned / uniq_total * 100) if uniq_total else 100,
            "copies_owned": copies_owned, "copies_need": copies_need,
            "copies_pct": (copies_owned / copies_need * 100) if copies_need else 100,
            "missing_val": missing_val,
            "buildable": buildable, "decks_total": len(deck_list),
        }

    def add_cards(self, cards):
        for c in cards:
            self.collection.add(c)
        self.save_collection()
        return len(cards)

    def import_file(self, filepath: str) -> int:
        ext = Path(filepath).suffix.lower()
        cards = []
        if ext in (".txt", ".dek"):
            content = read_text_file_smart(filepath)
            cards = FileParser.parse_dek(content) if ext == ".dek" else FileParser.parse_text(content)
        elif ext == ".csv":
            cards = FileParser.parse_csv(filepath)
        elif ext == ".xlsx":
            cards = FileParser.parse_xlsx(filepath)
        elif ext in (".docx", ".doc"):
            try:
                cards = FileParser.parse_docx(filepath)
            except Exception as e:
                raise RuntimeError(
                    f"Could not read {ext} file ({e}). "
                    "For .doc, re-save as .docx or .txt and retry. "
                    "Tip: pip install python-docx for .docx support."
                )
        else:
            raise ValueError(f"Unsupported file type: {ext}. Use .txt .dek .csv .xlsx .docx .doc")
        return self.add_cards(cards)


class DeckDetailWindow(tk.Toplevel):
    def __init__(self, parent, deck_info: dict, prog: dict, store=None,
                 deck_name=None, on_change=None, on_copy=None):
        super().__init__(parent)
        self.title(deck_info["name"])
        self.geometry("860x540")
        self.deck_info = deck_info
        self.prog = prog
        self.store = store
        self.deck_name = deck_name or deck_info.get("name")
        self.on_change = on_change
        self.on_copy = on_copy
        self._row_items = {}  # tree item id -> index into prog["rows"]
        self._build()

    def _build(self):
        # MTGGoldfish-style sample list: flat Mainboard + Sideboard sections.
        self.lbl_header = ttk.Label(self, text="", font=F("l_b"),
                                    wraplength=820)
        self.lbl_header.pack(padx=10, pady=(10, 2))
        desc = self.deck_info.get("description", "")
        if desc:
            ttk.Label(self, text=desc, wraplength=820, foreground=C("muted")).pack(padx=10, pady=(0, 2))
        self.lbl_source = tk.Label(self, text="", font=F("xs"),
                                   fg=C("link"), cursor="hand2")
        self.lbl_source.pack(padx=10, pady=(0, 8), anchor="w")
        self._refresh_source()

        # Button bar is packed BEFORE the expanding tree so the buttons can
        # never be starved out of the fixed-size window.
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar, text="Copy shopping list to clipboard", command=self._copy).pack(side="left")
        if self.store is not None:
            ttk.Button(bar, text="Replace selected card...",
                       command=self._open_replace).pack(side="left", padx=(8, 0))
            ttk.Button(bar, text="Revert swaps",
                       command=self._revert_swaps).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")

        cols = ("need", "have", "missing", "price")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=14)
        self.tree.heading("#0", text="Card")
        self.tree.column("#0", width=340, anchor="w")
        for c, w, h in [("need", 60, "Need"), ("have", 60, "Have"),
                        ("missing", 70, "Missing"), ("price", 70, "$ ea")]:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        theme.apply_tree_tags(self.tree, theme.DECK_TAGS)
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)
        self._refresh()

    def _refresh_source(self):
        """Sample provenance line (+ trimmed cards, if the source list was
        capped to 60+15). The deck id is clickable."""
        if self.store is None or not self.deck_name:
            self.lbl_source.pack_forget()
            return
        entry = (self.store.decklists or {}).get(self.deck_name, {})
        did, url = entry.get("source_deck_id"), entry.get("source_url")
        if not did and not url:
            self.lbl_source.pack_forget()
            return
        text = f"Sample: MTGGoldfish deck #{did}" if did else "Sample: MTGGoldfish deck list"
        trimmed = entry.get("truncated") or []
        if trimmed:
            text += f"  (trimmed to 60+15: {', '.join(trimmed)})"
        self.lbl_source.config(text=text)
        self.lbl_source.bind("<Button-1>", lambda e: webbrowser.open(url) if url else None)

    def _refresh(self):
        if self.store is not None and self.deck_name:
            fresh = self.store.decklist_progress(self.deck_name)
            if fresh is not None:
                self.prog = fresh
        self._refresh_source()
        self.lbl_header.config(
            text=(f"{self.deck_info['name']}  -  {self.prog['owned']}/{self.prog['total']} "
                  f"({self.prog['pct']:.1f}%)  -  missing ${self.prog['missing_value']:,.0f}"))
        tree = self.tree
        for item in tree.get_children(""):
            tree.delete(item)
        self._row_items = {}
        by_id = {id(r): i for i, r in enumerate(self.prog["rows"])}
        for section in ("Mainboard", "Sideboard"):
            rows = [r for r in self.prog["rows"] if r.get("section", "Mainboard") == section]
            if not rows:
                continue
            sec_id = tree.insert("", "end", text=f"{section} ({sum(r['need'] for r in rows)} cards)",
                                 values=("", "", "", ""), tags=("section",), open=True)
            for r in rows:
                label = f"{r['need']}x {r['name']}"
                if r.get("substituted_from"):
                    label += f"  (replaces {r['substituted_from']})"
                    tag = "swapped"
                else:
                    tag = "done" if r["complete"] else "need"
                item = tree.insert(sec_id, "end", text=label,
                                   values=(r["need"], r["have"], r["short"] if r["short"] else "",
                                           fmt_price(r["price"])), tags=(tag,))
                self._row_items[item] = by_id[id(r)]
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def _copy(self):
        lines = [f"# {self.deck_info['name']} - shopping list"]
        for r in self.prog["rows"]:
            if r["short"] > 0:
                lines.append(f"{r['short']} {r['name']}")
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        if self.on_copy:
            self.on_copy(text)

    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self._row_items.get(sel[0])
        if idx is None:
            return None
        return idx, self.prog["rows"][idx]

    def _open_replace(self):
        picked = self._selected_row()
        if picked is None:
            messagebox.showinfo("Replace card", "Select a card row first.")
            return
        idx, row = picked
        dlg = tk.Toplevel(self)
        dlg.title(f"Replace {row['need']}x {row['name']}")
        dlg.geometry("520x420")
        dlg.transient(self)
        ttk.Label(dlg, text=f"Replace {row['need']}x {row['name']} with (same quantity):",
                  wraplength=480).pack(padx=10, pady=(10, 4))
        entry = ttk.Entry(dlg)
        entry.pack(fill="x", padx=10)
        entry.focus_set()
        # Buttons first so they survive small/fixed window sizes.
        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=10, pady=(0, 10), side="bottom")
        cols = ("owned", "card")
        tree = ttk.Treeview(dlg, columns=cols, show="headings", height=10)
        tree.heading("owned", text="Owned")
        tree.heading("card", text="Card (from your collection)")
        tree.column("owned", width=70, anchor="center")
        tree.column("card", width=410, anchor="w")
        tree.pack(fill="both", expand=True, padx=10, pady=6)
        counts = {}
        for card in self.store.collection.cards.values():
            counts[card.name] = counts.get(card.name, 0) + card.quantity
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        names = [name for name, _ in ordered]

        def refill(event=None):
            q = entry.get().strip().lower()
            for item in tree.get_children(""):
                tree.delete(item)
            shown = 0
            for name in names:
                if q and q not in name.lower():
                    continue
                tree.insert("", "end", values=(counts[name], name))
                shown += 1
                if shown >= 200:
                    break

        entry.bind("<KeyRelease>", refill)
        refill()

        def confirm(_event=None):
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Replace card", "Pick a replacement card first.")
                return
            new_name = tree.item(sel[0], "values")[1]
            self._do_replace(idx, new_name)
            dlg.destroy()

        ttk.Button(btns, text="Replace", command=confirm).pack(side="left")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        tree.bind("<Double-1>", confirm)

    def _do_replace(self, row_idx, new_name):
        row = self.prog["rows"][row_idx]
        orig = row.get("orig", row["name"])
        if self.store.set_override(self.deck_name, orig, new_name):
            self._refresh()

    def _revert_swaps(self):
        if self.store.clear_overrides(self.deck_name):
            self._refresh()
        else:
            messagebox.showinfo("Revert swaps", "No substitutions to revert for this deck.")


class TrackerGUI(tk.Tk):
    FILETYPES = [
        ("All supported", "*.txt *.dek *.csv *.xlsx *.docx *.doc"),
        ("Text / deck lists", "*.txt *.dek"),
        ("CSV", "*.csv"),
        ("Excel", "*.xlsx"),
        ("Word", "*.docx *.doc"),
        ("All files", "*.*"),
    ]

    def __init__(self):
        super().__init__()
        self.title("MTG Modern Deck Tracker")
        self.settings = app_settings.load()
        theme.init_fonts(self.settings.get("font_scale", 1.0))
        theme.apply(self, self.settings.get("theme", "light"))
        if self.settings.get("remember_ui", True) and self.settings.get("geometry"):
            try:
                self.geometry(self.settings["geometry"])
            except Exception:
                self.geometry("1080x740")
        else:
            self.geometry("1080x740")
        self.store = GuiStore(PLAN_FILE, COLLECTION_FILE)
        self._build_ui()
        self.refresh_all()
        # Restore last tab (quietly ignore unknown names).
        if self.settings.get("remember_ui", True):
            try:
                for i in range(self.notebook.index("end")):
                    if self.notebook.tab(i, "text").strip() == (self.settings.get("last_tab") or ""):
                        self.notebook.select(i)
                        break
            except Exception:
                pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Bring the window to the front on launch (double-click starts
        # hidden behind other windows otherwise).
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(600, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass
        self.after(800, self._maybe_auto_refresh)

    def _on_close(self):
        """Persist window/tab/visit, then exit."""
        try:
            if self.settings.get("remember_ui", True):
                self.settings["geometry"] = self.geometry()
                try:
                    self.settings["last_tab"] = self.notebook.tab(
                        self.notebook.select(), "text").strip()
                except Exception:
                    pass
            import datetime
            self.settings["last_visit"] = datetime.date.today().isoformat()
            app_settings.save(self.settings)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _maybe_auto_refresh(self):
        days = self.settings.get("auto_refresh_days", 0) or 0
        if days <= 0:
            return
        try:
            age = self.snapshot_age_days()
        except Exception:
            return
        if age is None or age <= days:
            return
        if messagebox.askyesno(
                "Snapshot is stale",
                f"Your metagame snapshot is {age} days old "
                f"(auto-refresh is set to {days} days).\n\n"
                f"Run Refresh all data now?"):
            self.refresh_all_data()

    def open_settings(self):
        """Preferences dialog. Theme and font size apply live; everything
        is saved on close (and again on app exit)."""
        dlg = tk.Toplevel(self)
        dlg.title("Settings")
        dlg.geometry("380x330")
        dlg.transient(self)
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Theme", font=F("m_b")).pack(anchor="w")
        theme_var = tk.StringVar(value=self.settings.get("theme", "light"))
        trow = ttk.Frame(body)
        trow.pack(anchor="w", pady=(2, 10))
        ttk.Radiobutton(trow, text="Light", variable=theme_var, value="light",
                        command=lambda: self.apply_theme(theme_var.get())).pack(side="left")
        ttk.Radiobutton(trow, text="Dark", variable=theme_var, value="dark",
                        command=lambda: self.apply_theme(theme_var.get())).pack(side="left", padx=(12, 0))

        ttk.Label(body, text="Font size", font=F("m_b")).pack(anchor="w")
        scale_var = tk.DoubleVar(value=float(self.settings.get("font_scale", 1.0)) * 100)
        ttk.Label(body, text="Drag to resize all text (applies instantly):").pack(anchor="w")
        slider = ttk.Scale(body, from_=80, to=130, variable=scale_var, orient="horizontal")
        slider.pack(fill="x", pady=(2, 10))

        def _on_scale_release(_event=None):
            try:
                theme.set_scale(scale_var.get() / 100.0)
                self.settings["font_scale"] = theme.get_scale()
            except Exception:
                pass

        slider.bind("<ButtonRelease-1>", _on_scale_release)
        mem_var = tk.BooleanVar(value=bool(self.settings.get("remember_ui", True)))
        ttk.Checkbutton(body, text="Remember window size and tab",
                        variable=mem_var).pack(anchor="w", pady=(0, 10))
        ttk.Label(body, text="Auto-refresh snapshot older than:", font=F("m_b")).pack(anchor="w")
        age_var = tk.StringVar()
        age_map = {"Off": 0, "3 days": 3, "7 days": 7, "14 days": 14, "30 days": 30}
        current_days = self.settings.get("auto_refresh_days", 7)
        age_var.set(next((k for k, v in age_map.items() if v == current_days), "7 days"))
        ttk.OptionMenu(body, age_var, age_var.get(), *age_map.keys()).pack(anchor="w", pady=(2, 10))

        def _close():
            try:
                self.settings["theme"] = theme.current()
                self.settings["font_scale"] = theme.get_scale()
                self.settings["remember_ui"] = bool(mem_var.get())
                self.settings["auto_refresh_days"] = age_map.get(age_var.get(), 7)
                app_settings.save(self.settings)
            except Exception:
                pass
            try:
                dlg.destroy()
            except Exception:
                pass

        ttk.Button(body, text="Done", command=_close).pack(anchor="e")

    def apply_theme(self, name: str):
        """Switch light/dark live: restyle chrome, recolor static surfaces,
        then rebuild data views (tiles/rows/charts pick up the palette)."""
        theme.apply(self, name)
        self.settings["theme"] = theme.current()
        for canvas in ("dash_canvas", "meta_canvas", "pie_canvas",
                       "bar_canvas", "arch_canvas", "mu_canvas"):
            try:
                getattr(self, canvas).configure(bg=theme.C("canvas"))
            except Exception:
                pass
        for frame in ("dash_inner", "meta_inner"):
            try:
                getattr(self, frame).configure(bg=theme.C("bg"))
            except Exception:
                pass
        try:
            self.paste_box.configure(bg=theme.C("entry_bg"),
                                     fg=theme.C("entry_fg"),
                                     insertbackground=theme.C("entry_fg"))
        except Exception:
            pass
        try:
            theme.apply_tree_tags(self.trend_tree, theme.TREND_TAGS)
            self.shop_tree.tag_configure("unpriced",
                                         foreground=theme.C("faint"))
        except Exception:
            pass
        try:
            self.refresh_all()
        except Exception:
            pass

    # ---------- UI construction ----------
    def _build_ui(self):
        # Top summary bar
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.lbl_overall = ttk.Label(top, text="", font=F("l_b"))
        self.lbl_overall.pack(side="left")
        self.lbl_buildable = ttk.Label(top, text="", font=F("m"))
        self.lbl_buildable.pack(side="left", padx=(16, 0))
        ttk.Button(top, text="Reload data", command=self.reload).pack(side="right")
        self.btn_refresh_all = ttk.Button(top, text="Refresh all data",
                                          command=self.refresh_all_data)
        self.btn_refresh_all.pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Settings", command=self.open_settings).pack(side="right",
                                                                           padx=(0, 8))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tab_dash = ttk.Frame(self.notebook, padding=8)
        self.tab_shop = ttk.Frame(self.notebook, padding=8)
        self.tab_coll = ttk.Frame(self.notebook, padding=8)
        self.tab_import = ttk.Frame(self.notebook, padding=8)
        self.tab_meta = ttk.Frame(self.notebook, padding=8)
        self.tab_stats = ttk.Frame(self.notebook, padding=8)
        self.tab_mu = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.tab_dash, text="  Dashboard (decks)  ")
        self.notebook.add(self.tab_shop, text="  Buy Next  ")
        self.notebook.add(self.tab_coll, text="  Collection  ")
        self.notebook.add(self.tab_import, text="  Import  ")
        self.notebook.add(self.tab_meta, text="  Metagame  ")
        self.notebook.add(self.tab_stats, text="  Statistics  ")
        self.notebook.add(self.tab_mu, text="  Matchups  ")

        self._build_dashboard_tab()
        self._build_shop_tab()
        self._build_collection_tab()
        self._build_import_tab()
        self._build_metagame_tab()
        self._build_stats_tab()
        self._build_matchups_tab()
        self.status = ttk.Label(self, text="", relief="sunken", anchor="w", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    def _build_dashboard_tab(self):
        # Scrollable tile grid; tiles are built in refresh_dashboard.
        # Solid white background throughout so image tiles don't flicker
        # against the default theme grey while scrolling.
        canvas = tk.Canvas(self.tab_dash, highlightthickness=0, bg=C("bg"))
        scrollbar = ttk.Scrollbar(self.tab_dash, orient="vertical", command=canvas.yview)
        self.dash_canvas = canvas
        self.dash_scrollbar = scrollbar
        self.dash_inner = tk.Frame(canvas, bg=C("bg"), padx=4, pady=4)
        self.dash_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.dash_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind("<MouseWheel>", self._dash_wheel)
        canvas.bind("<Button-4>", self._dash_wheel)
        canvas.bind("<Button-5>", self._dash_wheel)
        # Scrollbar drags bit-blit image tiles and can leave smear trails;
        # force a settle repaint the moment the drag ends.
        scrollbar.bind("<ButtonRelease-1>", lambda e: self._dash_settle())
        self._dash_settle_id = None
        self.phase_widgets = []  # filled in refresh
        self._tile_imgs = []  # keeps tile PhotoImages alive

    def _smooth_scroll(self, canvas, event):
        """Small scroll steps for tear-free image scrolling. Swallows the
        event so the default (larger, jumpy) scroll doesn't also fire."""
        canvas.yview_scroll(_wheel_steps(event), "units")
        self._settle_canvas_soon(canvas)
        return "break"

    def _dash_wheel(self, event):
        return self._smooth_scroll(self.dash_canvas, event)

    def _settle_canvas_soon(self, canvas, delay_ms=120):
        ids = self.__dict__.setdefault("_settle_ids", {})
        try:
            if ids.get(id(canvas)) is not None:
                self.after_cancel(ids[id(canvas)])
            ids[id(canvas)] = self.after(delay_ms, lambda: self._settle_canvas(canvas))
        except Exception:
            pass

    def _settle_canvas(self, canvas):
        """Repaint any bit-blit smear trails once scrolling stops. Uses a
        full event flush (not just idle tasks) so stale image pixels are
        actually redrawn; guarded against reentrancy."""
        try:
            self.__dict__.setdefault("_settle_ids", {})[id(canvas)] = None
        except Exception:
            pass
        if self.__dict__.get("_settling"):
            return
        self.__dict__["_settling"] = True
        try:
            canvas.update_idletasks()
            canvas.update()
        except Exception:
            pass
        finally:
            self.__dict__["_settling"] = False

    def _dash_settle_soon(self, delay_ms=120):
        self._settle_canvas_soon(self.dash_canvas, delay_ms)

    def _dash_settle(self):
        self._settle_canvas(self.dash_canvas)

    def _bind_dashboard_wheel(self, widget):
        widget.bind("<MouseWheel>", self._dash_wheel)
        widget.bind("<Button-4>", self._dash_wheel)
        widget.bind("<Button-5>", self._dash_wheel)
        for child in widget.winfo_children():
            self._bind_dashboard_wheel(child)

    def _build_shop_tab(self):
        self.lbl_shop_head = ttk.Label(self.tab_shop, text="", wraplength=980,
                                       font=F("m_b"))
        self.lbl_shop_head.pack(anchor="w", pady=(0, 4))
        ttk.Label(self.tab_shop,
                  text="Shared staples first — one purchase serves every deck listed:",
                  font=F("s_b")).pack(anchor="w")
        cols = ("card", "decks", "buy", "each", "total")
        self.shop_tree = ttk.Treeview(self.tab_shop, columns=cols, show="headings", height=12)
        for c, w, h in [("card", 300, "Card"), ("decks", 70, "Decks"),
                        ("buy", 60, "Buy"), ("each", 80, "$ each"),
                        ("total", 90, "$ total")]:
            self.shop_tree.heading(c, text=h)
            self.shop_tree.column(c, width=w, anchor="center" if c != "card" else "w",
                                  stretch=(c == "card"))
        self.shop_tree.pack(fill="both", expand=True, pady=(0, 6))
        row = ttk.Frame(self.tab_shop)
        row.pack(fill="x")
        ttk.Button(row, text="Copy staples shopping list",
                   command=self._copy_shop_list).pack(side="left")
        ttk.Label(self.tab_shop, text="Cheapest decks to unlock next:",
                  font=F("s_b")).pack(anchor="w", pady=(6, 0))
        ucols = ("deck", "cost", "cards")
        self.unlock_tree = ttk.Treeview(self.tab_shop, columns=ucols, show="headings", height=7)
        for c, w, h in [("deck", 300, "Deck"), ("cost", 120, "$ to finish"),
                        ("cards", 140, "Cards missing")]:
            self.unlock_tree.heading(c, text=h)
            self.unlock_tree.column(c, width=w, anchor="center" if c != "deck" else "w",
                                    stretch=(c == "deck"))
        self.unlock_tree.pack(fill="both", expand=True)

    def refresh_shop(self, deck_list=None):
        if not hasattr(self, "shop_tree"):
            return
        if deck_list is None:
            deck_list = self.store.deck_progress_list()
        for tree in (self.shop_tree, self.unlock_tree):
            for r in tree.get_children():
                tree.delete(r)
        for p in self.store.shopping_priorities(deck_list):
            total = f"${p['total']:,.0f}" if p["total"] is not None else "—"
            self.shop_tree.insert("", "end", values=(
                p["name"], p["decks"], f"{p['buy']}x", fmt_price(p["price"]), total),
                tags=("unpriced" if p["unpriced"] else ""))
        self.shop_tree.tag_configure("unpriced", foreground=C("faint"))
        unlocks = self.store.deck_unlock_order(deck_list)
        for u in unlocks:
            cost = f"${u['missing_value']:,.0f}" + (" +?" if u["unpriced"] else "")
            self.unlock_tree.insert("", "end", values=(u["deck"], cost, u["missing_n"]))
        if unlocks:
            top = unlocks[0]
            self.lbl_shop_head.config(
                text=f"Closest unlock: {top['deck']} — "
                     f"${top['missing_value']:,.0f}{' + unknown prices' if top['unpriced'] else ''} "
                     f"to finish ({top['missing_n']} cards).")
        else:
            self.lbl_shop_head.config(text="Everything is buildable — nothing left to buy.")

    def _copy_shop_list(self):
        lines = []
        for p in self.store.shopping_priorities():
            if p["unpriced"]:
                continue
            lines.append(f"{p['buy']}x {p['name']}")
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.set_status(f"Copied {len(lines)} staples to clipboard.")

    def _get_deck_art(self, deck_name, size=(264, 150)):
        """Uniform center-cropped art tile (symbols live in their own strip
        above the art, never composited over it)."""
        if not HAS_PIL:
            return None
        rel = self.store.deck_art(deck_name)
        if not rel:
            return None
        key = ("tileart", rel, size)
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        try:
            from PIL import ImageOps
            im = Image.open(asset_path(rel)).convert("RGB")
            im = ImageOps.fit(im, size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
        except Exception:
            return None
        self._thumb_cache[key] = photo
        return photo

    def _pip_strip(self, parent, colors, dmax=46, cursor="hand2"):
        """Row of up-to-3 mana symbols scaled by share (50/25/25 -> 2:1:1).
        Returns the strip frame, or None when the deck has no pips."""
        syms = top_symbols(colors)
        if not syms:
            return None
        ds = pip_diameters([s for _, s in syms], dmax)
        strip = tk.Frame(parent, bg=C("bg"), cursor=cursor)
        imgs = []
        for (letter, _share), d in zip(syms, ds):
            img = make_pip_image(letter, d)
            if img is None:
                continue
            imgs.append(img)  # local ref; caller keeps strip alive via tile
            tk.Label(strip, image=img, bg=C("bg"), cursor=cursor).pack(side="left", padx=3)
        strip.pack(pady=(6, 2))
        strip.imgs = imgs
        return strip

    def _deck_subtitle(self, prog):
        arch = prog.get("archetype")
        prefix = f"{arch} · " if arch and arch != "—" else ""
        if prog.get("meta_pct") is not None:
            meta = next((d for d in (self.store.metagame.get("decks", []) or [])
                         if d["name"] == prog["deck"]), {})
            n = meta.get("deck_count", "?")
            return prefix + f"{prog['meta_pct']:.1f}% of the Modern metagame ({n} decks tracked)"
        return prefix + "Not in the current top-20 snapshot"

    def _build_deck_tile(self, prog):
        tile = tk.Frame(self.dash_inner, relief="raised", borderwidth=2, bg=C("bg"),
                        cursor="hand2")
        self._pip_strip(tile, prog.get("colors"), dmax=46)
        art = self._get_deck_art(prog["deck"])
        if art is not None:
            img_lbl = tk.Label(tile, image=art, bg=C("bg"), cursor="hand2")
            img_lbl.pack(fill="x")
            self._tile_imgs.append(art)
        arch = prog.get("archetype") or "—"
        chip = tk.Label(tile, text=arch.upper(), font=F("xs_b"),
                        fg=ARCH_CHIP_FG.get(arch, "#ffffff"), bg=ARCH_CHIP.get(arch, "#707070"),
                        padx=6, pady=1, cursor="hand2")
        chip.place(relx=1.0, x=-6, y=6, anchor="ne")
        badge = "[BUILDABLE]" if prog["buildable"] else f"{prog['pct']:.0f}%"
        fg = C("accent") if prog["buildable"] else C("fg")
        tk.Label(tile, text=prog["deck"], font=F("l_b"), fg=C("fg"),
                 bg=C("bg"), wraplength=250, cursor="hand2").pack(pady=(6, 0))
        tk.Label(tile, text=self._deck_subtitle(prog), font=F("xs"),
                 fg=C("muted"), bg=C("bg"), cursor="hand2").pack()
        tk.Label(tile, text=badge, font=F("m_b"),
                 fg=fg, bg=C("bg"), cursor="hand2").pack(pady=(2, 0))
        bar = ttk.Progressbar(tile, length=230, maximum=100, value=prog["pct"])
        bar.pack(pady=4)
        tk.Label(tile, text=f"{prog['owned']}/{prog['total']} cards",
                 font=F("s"), fg=C("fg"), bg=C("bg"), cursor="hand2").pack()
        left = "Complete!" if prog["pct"] >= 100 else f"${prog['missing_value']:,.0f} to finish"
        tk.Label(tile, text=left, font=F("s_b"),
                 fg=C("accent") if prog["buildable"] else C("fg"),
                 bg=C("bg"), cursor="hand2").pack(pady=(0, 8))
        self._bind_tile_click(tile, prog)
        return tile

    def _bind_tile_click(self, widget, prog):
        def _open(_event=None):
            self.open_detail(prog)
        widget.bind("<Button-1>", _open)
        for child in widget.winfo_children():
            self._bind_tile_click(child, prog)

    def _build_collection_tab(self):
        toolbar = ttk.Frame(self.tab_coll)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Search:").pack(side="left")
        self.coll_search_var = tk.StringVar()
        self.coll_search_var.trace_add("write", lambda *a: self.refresh_collection())
        ttk.Entry(toolbar, textvariable=self.coll_search_var, width=30).pack(side="left", padx=(4, 12))
        ttk.Button(toolbar, text="Add card", command=self.add_card_dialog).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Remove selected", command=self.remove_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Clear collection", command=self.clear_collection).pack(side="left", padx=2)
        self.lbl_coll_count = ttk.Label(toolbar, text="")
        self.lbl_coll_count.pack(side="right")

        coll_frame = ttk.Frame(self.tab_coll)
        coll_frame.pack(fill="both", expand=True)
        # Printing-agnostic: one row per card name (all sets merged).
        cols = ("qty", "name")
        self.coll_tree = ttk.Treeview(coll_frame, columns=cols, show="headings", height=24)
        for c, w, h in [("qty", 80, "Qty"), ("name", 480, "Card")]:
            self.coll_tree.heading(c, text=h)
            self.coll_tree.column(c, width=w, anchor="center" if c != "name" else "w")
        coll_vsb = ttk.Scrollbar(coll_frame, orient="vertical", command=self.coll_tree.yview)
        coll_hsb = ttk.Scrollbar(coll_frame, orient="horizontal", command=self.coll_tree.xview)
        self.coll_tree.configure(yscrollcommand=coll_vsb.set, xscrollcommand=coll_hsb.set)
        self.coll_tree.grid(row=0, column=0, sticky="nsew")
        coll_vsb.grid(row=0, column=1, sticky="ns")
        coll_hsb.grid(row=1, column=0, sticky="ew")
        coll_frame.grid_rowconfigure(0, weight=1)
        coll_frame.grid_columnconfigure(0, weight=1)
        # Mousewheel scrolling (Windows / macOS / Linux)
        self.coll_tree.bind("<MouseWheel>", lambda e: self.coll_tree.yview_scroll(-1 * (e.delta // 120), "units"))
        self.coll_tree.bind("<Button-4>", lambda e: self.coll_tree.yview_scroll(-1, "units"))
        self.coll_tree.bind("<Button-5>", lambda e: self.coll_tree.yview_scroll(1, "units"))
        self._coll_keys = []  # parallel list of collection keys per row

    def _build_import_tab(self):
        info = ("Import a card list to build your cached collection (saved to my_collection.json).\n"
                "Supported: .txt  .dek  .csv  .xlsx  .docx  .doc  + clipboard paste.\n"
                "Line format:  '4 Lightning Bolt'  or  '4x Solitude (MH2) 12 *F*'\n"
                "All printings count as the same card — set/collector info is ignored.")
        ttk.Label(self.tab_import, text=info, wraplength=900).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(self.tab_import)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Upload file...", command=self.upload_file).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Paste from system clipboard", command=self.import_clipboard).pack(side="left", padx=8)
        ttk.Button(row, text="Import text below", command=self.import_textbox).pack(side="left", padx=8)

        row2 = ttk.Frame(self.tab_import)
        row2.pack(fill="x", pady=(0, 4))
        self.replace_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Replace mode: clear cached collection before importing (avoids duplicates)",
                        variable=self.replace_var).pack(side="left")
        ttk.Button(row2, text="Clear cached collection", command=self.clear_collection).pack(side="right")

        ttk.Label(self.tab_import, text="Or paste a list here (one card per line), then click 'Import text below':").pack(anchor="w", pady=(8, 2))
        self.paste_box = tk.Text(self.tab_import, height=18, wrap="word")
        self.paste_box.pack(fill="both", expand=True, pady=4)
        self.paste_box.insert("1.0", "4 Flooded Strand\n4 Quantum Riddler\n1 Solitude\n")
        self.lbl_import = ttk.Label(self.tab_import, text="", foreground=C("accent"))
        self.lbl_import.pack(anchor="w", pady=4)

    def snapshot_age_days(self) -> int | None:
        """Days since the loaded metagame snapshot (None if undated)."""
        snap = (getattr(self.store, "metagame", {}) or {}).get("snapshot_date", "")
        try:
            return (datetime.date.today() - datetime.date.fromisoformat(str(snap)[:10])).days
        except Exception:
            return None

    def _meta_header_text(self) -> str:
        meta = getattr(self.store, "metagame", {}) or {}
        snap = meta.get("snapshot_date", "unknown")
        timeframe = meta.get("timeframe", "")
        note = meta.get("note", "")
        txt = (f"Modern Metagame — last {timeframe} (snapshot {snap}) — "
               f"MTGGoldfish paper, sorted by META%.  {note}")
        age = self.snapshot_age_days()
        if age is not None and age > 7:
            txt += f"  Snapshot is {age} days old — consider Refresh live 7-day."
        return txt

    def _build_metagame_tab(self):
        self.lbl_meta_header = ttk.Label(self.tab_meta, text="", wraplength=980,
                                         foreground=C("muted"))
        self.lbl_meta_header.pack(anchor="w", pady=(0, 6))
        self.lbl_meta_header.config(text=self._meta_header_text())

        btnrow = ttk.Frame(self.tab_meta)
        btnrow.pack(fill="x", pady=(0, 6))
        ttk.Button(btnrow, text="Open MTGGoldfish metagame", command=self.open_metagame_page).pack(side="left", padx=(0, 8))
        ttk.Button(btnrow, text="Open selected deck", command=self.open_selected_deck).pack(side="left", padx=8)
        ttk.Button(btnrow, text="Reload snapshot", command=self.reload_metagame).pack(side="left", padx=8)
        ttk.Button(btnrow, text="Refresh live 7-day", command=self.refresh_live_snapshot).pack(side="left", padx=8)
        ttk.Button(btnrow, text="Save snapshot", command=self.save_snapshot_copy).pack(side="left", padx=8)
        ttk.Button(btnrow, text="Update prices", command=self.update_prices).pack(side="left", padx=8)
        ttk.Button(btnrow, text="Update mana data", command=self.update_mana).pack(side="left", padx=8)
        self.price_bar = ttk.Progressbar(btnrow, length=130, maximum=100, value=0)
        self.price_bar.pack(side="left", padx=(2, 0))

        # Custom rows (not a Treeview: cells are text-only, but mana symbols
        # need a dedicated image column). One row per deck: thumb | title +
        # keys + detail line | archetype | prices | have | symbols.
        self.meta_canvas = tk.Canvas(self.tab_meta, highlightthickness=0, bg=C("bg"))
        meta_vsb = ttk.Scrollbar(self.tab_meta, orient="vertical",
                                 command=self.meta_canvas.yview)
        # Fixed column headers (stay put while rows scroll). Pixel minsizes
        # mirror the row cells below; the Deck column takes the slack. The
        # header frame is width-locked to the canvas at layout time so both
        # grids share identical column geometry (see _meta_canvas_resized).
        header = tk.Frame(self.tab_meta, bg=C("bg"), height=26)
        header.pack(anchor="nw", padx=(8, 0), pady=(0, 2))
        # NOTE: children are grid-managed, so this must be grid_propagate
        # (pack_propagate only affects pack-managed children). The explicit
        # height is required: without it the frame collapses and the labels
        # clip to slivers.
        header.grid_propagate(False)
        tk.Label(header, text="", bg=C("bg")).grid(row=0, column=0)
        tk.Label(header, text="Deck", bg=C("bg"), font=F("xs_b"),
                 fg=C("muted"), anchor="w").grid(row=0, column=1, sticky="w")
        for _col, _txt, _min in ((2, "Archetype", 92), (3, "Paper / MTGO", 96),
                                 (4, "Have / To finish", 130), (5, "Colors", 130)):
            tk.Label(header, text=_txt, bg=C("bg"), font=F("xs_b"),
                     fg=C("muted")).grid(row=0, column=_col)
            header.grid_columnconfigure(_col, minsize=_min)
        header.grid_columnconfigure(0, minsize=70)
        header.grid_columnconfigure(1, weight=1)
        self._meta_header = header
        ttk.Separator(self.tab_meta, orient="horizontal").pack(fill="x", padx=6)
        self.meta_inner = tk.Frame(self.meta_canvas, bg=C("bg"))
        self.meta_inner.bind("<Configure>",
                             lambda e: self.meta_canvas.configure(scrollregion=self.meta_canvas.bbox("all")))
        self._meta_win = self.meta_canvas.create_window((0, 0), window=self.meta_inner, anchor="nw")
        self.meta_canvas.bind("<Configure>", self._meta_canvas_resized)
        self.meta_canvas.configure(yscrollcommand=meta_vsb.set)
        self.meta_canvas.pack(side="left", fill="both", expand=True)
        meta_vsb.pack(side="right", fill="y")
        self.meta_canvas.bind("<MouseWheel>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        self.meta_canvas.bind("<Button-4>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        self.meta_canvas.bind("<Button-5>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        meta_vsb.bind("<ButtonRelease-1>", lambda e: self._settle_canvas(self.meta_canvas))
        self._thumb_cache = {}

        self._meta_rows = []
        self._meta_row_frames = []
        self._meta_selected = None
        self._meta_imgs = []  # keeps PhotoImage refs alive
        self.refresh_metagame_table()

    def _meta_canvas_resized(self, event=None):
        """Keep rows full-width AND lock the header to the exact same width
        so both grids share identical column geometry."""
        try:
            w = event.width if event is not None else self.meta_canvas.winfo_width()
            self.meta_canvas.itemconfigure(self._meta_win, width=w)
            self._meta_header.configure(width=w)
        except Exception:
            pass

    @staticmethod
    def _meta_bg(tag: str) -> str:
        return {"done": C("meta_done"), "partial": C("meta_partial")}.get(tag, C("meta_plain"))

    def _paint_meta_row(self, frame, bg):
        try:
            frame.configure(bg=bg)
        except Exception:
            pass
        for child in frame.winfo_children():
            if getattr(child, "_keep_bg", False):
                continue
            try:
                child.configure(bg=bg)
            except Exception:
                pass
            self._paint_meta_row(child, bg)

    def _meta_detail_text(self, d, full):
        """Per-row detail line (color ratios live in the symbols column)."""
        key_cards = d.get("key_cards", [])
        owned_keys = [k for k in key_cards if self.store.owned_qty(k) > 0]
        if full is None:
            return f"Keys {len(owned_keys)}/{len(key_cards)} owned (no stored decklist)"
        return (f"Collection {full['owned']}/{full['total']} ({full['pct']:.1f}%) · "
                f"${full['missing_value']:,.0f} to finish · "
                f"Keys {len(owned_keys)}/{len(key_cards)} owned")

    def _build_meta_row(self, d, full, tag):
        bg = self._meta_bg(tag)
        row = tk.Frame(self.meta_inner, bg=bg, relief="flat", borderwidth=0,
                       pady=4)
        # Every cell is a fixed-width, non-propagating frame so all rows
        # share identical column geometry with each other and the header.
        thumb_cell = tk.Frame(row, bg=bg, width=70)
        thumb_cell.grid(row=0, column=0, rowspan=3, sticky="ns")
        thumb_cell.pack_propagate(False)
        thumb = self._get_thumb(d.get("thumb"))
        if thumb is not None:
            self._meta_imgs.append(thumb)
            tk.Label(thumb_cell, image=thumb, bg=bg).pack(expand=True)
        title = tk.Frame(row, bg=bg)
        title.grid(row=0, column=1, sticky="nw")
        tk.Label(title, text=d.get("name", ""), bg=bg,
                 font=F("l_b"), fg=C("fg")).pack(side="left")
        tk.Label(title, text=f"  {d.get('meta_pct', 0):.1f}% · {d.get('deck_count', '')} decks",
                 bg=bg, font=F("s"), fg=C("muted")).pack(side="left")
        tk.Label(row, text=", ".join(d.get("key_cards", [])), bg=bg,
                 font=F("s"), fg=C("muted"), anchor="w",
                 wraplength=420, justify="left").grid(row=1, column=1, sticky="nw")
        tk.Label(row, text=self._meta_detail_text(d, full), bg=bg,
                 font=F("s"), fg=C("fg"), anchor="w",
                 wraplength=420, justify="left").grid(row=2, column=1, sticky="nw")
        arch = d.get("archetype") or "—"
        chip_cell = tk.Frame(row, bg=bg, width=92)
        chip_cell.grid(row=0, column=2, rowspan=3, sticky="ns")
        chip_cell.pack_propagate(False)
        chip = tk.Label(chip_cell, text=arch.upper(), bg=ARCH_CHIP.get(arch, "#707070"),
                        fg=ARCH_CHIP_FG.get(arch, "#ffffff"), font=F("xs_b"), padx=6, pady=1)
        chip._keep_bg = True
        chip.pack(expand=True)
        price_cell = tk.Frame(row, bg=bg, width=96)
        price_cell.grid(row=0, column=3, rowspan=3, sticky="ns")
        price_cell.pack_propagate(False)
        price_inner = tk.Frame(price_cell, bg=bg)
        price_inner.pack(expand=True)
        tk.Label(price_inner, text=f"${d.get('paper_price', 0):,}", bg=bg,
                 font=F("m_b"), fg=C("fg")).pack()
        tk.Label(price_inner, text=f"{d.get('mtgo_tix', 0)} tix", bg=bg,
                 font=F("xs"), fg=C("muted")).pack()
        have_cell = tk.Frame(row, bg=bg, width=130)
        have_cell.grid(row=0, column=4, rowspan=3, sticky="ns")
        have_cell.pack_propagate(False)
        have_inner = tk.Frame(have_cell, bg=bg)
        have_inner.pack(expand=True)
        if full is None:
            key_cards = d.get("key_cards", [])
            owned_keys = sum(1 for k in key_cards if self.store.owned_qty(k) > 0)
            have_txt = f"{owned_keys}/{len(key_cards)} keys" if key_cards else "—"
            left_txt = "—"
        else:
            have_txt = f"{full['pct']:.0f}% ({full['owned']}/{full['total']})"
            left_txt = f"${full['missing_value']:,.0f}"
        tk.Label(have_inner, text=have_txt, bg=bg,
                 font=F("m_b"),
                 fg=C("accent") if tag == "done" else C("fg")).pack()
        tk.Label(have_inner, text=left_txt, bg=bg,
                 font=F("xs"), fg=C("muted")).pack()
        syms = top_symbols(self.store.deck_colors(d["name"]))
        sym_cell = tk.Frame(row, bg=bg, width=130)
        sym_cell.grid(row=0, column=5, rowspan=3, sticky="ns")
        sym_cell.pack_propagate(False)
        sym_inner = tk.Frame(sym_cell, bg=bg)
        sym_inner.pack(expand=True)
        if syms:
            ds = pip_diameters([s for _, s in syms], 32, dmin=13)
            for (letter, _share), dd in zip(syms, ds):
                img = make_pip_image(letter, dd)
                if img is None:
                    continue
                self._meta_imgs.append(img)
                tk.Label(sym_inner, image=img, bg=bg).pack(side="left", padx=2)
        else:
            tk.Label(sym_inner, text="—", bg=bg, font=F("s"),
                     fg=C("faint")).pack(side="left")
        # Fixed column grid shared by every row so cells line up with each
        # other and with the header above (col 1 takes the slack). col 0 is
        # 70px here and in the header (56px thumb + 6 + 8 padding).
        row.grid_columnconfigure(0, minsize=70)
        row.grid_columnconfigure(1, weight=1)
        row.grid_columnconfigure(2, minsize=92)
        row.grid_columnconfigure(3, minsize=96)
        row.grid_columnconfigure(4, minsize=130)
        row.grid_columnconfigure(5, minsize=130)
        return row

    def _get_thumb(self, relpath, size=(56, 42)):
        """Load a deck thumbnail as PhotoImage (cached). None if unavailable."""
        if not HAS_PIL or not relpath:
            return None
        key = (relpath, size)
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        try:
            im = Image.open(asset_path(relpath)).convert("RGB")
            im.thumbnail(size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
        except Exception:
            return None
        self._thumb_cache[key] = photo
        return photo

    def refresh_metagame_table(self):
        try:
            self.lbl_meta_header.config(text=self._meta_header_text())
        except Exception:
            pass
        for w in self.meta_inner.winfo_children():
            w.destroy()
        self._meta_rows = []
        self._meta_row_frames = []
        self._meta_selected = None
        self._meta_imgs = []
        decks = (getattr(self.store, "metagame", {}) or {}).get("decks", [])
        for d in sorted(decks, key=lambda x: x.get("meta_pct", 0), reverse=True):
            full = self.store.decklist_progress(d["name"])
            if full is None:
                key_cards = d.get("key_cards", [])
                owned_keys = sum(1 for k in key_cards if self.store.owned_qty(k) > 0)
                tag = "nokeys" if not key_cards else ("partial" if owned_keys else "none")
            else:
                tag = "done" if is_buildable(full) else ("partial" if full["pct"] > 0 else "none")
            row = self._build_meta_row(d, full, tag)
            row.pack(fill="x", pady=2)
            idx = len(self._meta_rows)
            self._meta_rows.append(d)
            self._meta_row_frames.append((row, tag))
            self._bind_meta_row(row, idx)
        self._bind_meta_wheel(self.meta_inner)

    def _bind_meta_row(self, widget, idx):
        widget.bind("<Button-1>", lambda e: self._select_meta_row(idx))
        widget.bind("<Double-1>", lambda e: self.open_selected_deck())
        for child in widget.winfo_children():
            self._bind_meta_row(child, idx)

    def _select_meta_row(self, idx):
        if self._meta_selected is not None and self._meta_selected < len(self._meta_row_frames):
            old_frame, old_tag = self._meta_row_frames[self._meta_selected]
            self._paint_meta_row(old_frame, self._meta_bg(old_tag))
        self._meta_selected = idx
        if idx is not None and idx < len(self._meta_row_frames):
            frame, _tag = self._meta_row_frames[idx]
            self._paint_meta_row(frame, C("meta_sel"))

    def _bind_meta_wheel(self, widget):
        widget.bind("<MouseWheel>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        widget.bind("<Button-4>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        widget.bind("<Button-5>", lambda e: self._smooth_scroll(self.meta_canvas, e))
        for child in widget.winfo_children():
            self._bind_meta_wheel(child)

    def open_metagame_page(self):
        webbrowser.open(METAGAME_URL)

    def open_selected_deck(self):
        if self._meta_selected is None or self._meta_selected >= len(self._meta_rows):
            messagebox.showinfo("Metagame", "Select a deck first.")
            return
        url = self._meta_rows[self._meta_selected].get("url", METAGAME_URL)
        webbrowser.open(url)

    def reload_metagame(self):
        self.store.metagame = self.store.load_metagame()
        self.refresh_metagame_table()
        self.refresh_stats()
        self.set_status("Metagame snapshot reloaded from modern_metagame.json.")

    def update_prices(self):
        """Scrape cheapest-printing prices for top-20 cards missing one."""
        try:
            import price_fetch
        except ImportError as e:
            messagebox.showerror("Update prices", f"price_fetch module missing:\n{e}")
            return
        todo = self.store.cards_missing_prices()
        if not todo:
            messagebox.showinfo("Update prices", "Every top-20 card already has a fresh price.")
            return
        n_stale = sum(1 for k in todo if self.store.is_price_stale(k))
        n_new = len(todo) - n_stale
        detail = f"{n_new} new" if not n_stale else f"{n_new} new + {n_stale} stale (>{PRICE_TTL_DAYS}d)"
        if not messagebox.askyesno(
                "Update prices",
                f"Fetch cheapest-printing prices for {len(todo)} cards you "
                f"still need to buy ({detail})?\nTakes about a minute. "
                f"Results are cached in prices.json."):
            return
        self._price_total = len(todo)
        self.price_bar["maximum"] = len(todo)
        self.price_bar["value"] = 0
        self.set_status(f"Pricing 0/{len(todo)}...")
        work = queue.Queue()

        def _worker():
            try:
                cache = price_fetch.load_cache()
                updated, failed = price_fetch.update_missing(
                    todo, cache, force=True,
                    progress_cb=lambda i, n, disp, ok: work.put(("p", i, n, disp)))
                work.put(("done", updated, failed))
            except Exception as e:  # noqa: BLE001 - report to main thread
                work.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_price_queue(work)

    def _poll_price_queue(self, work):
        try:
            while True:
                msg = work.get_nowait()
                if msg[0] == "p":
                    _, i, n, disp = msg
                    self.price_bar["value"] = i
                    self.set_status(f"Pricing {i}/{n}... {disp}")
                elif msg[0] == "done":
                    _, updated, failed = msg
                    self.price_bar["value"] = self._price_total
                    self.store.price_cache = self.store.load_price_cache()
                    self.store.price_map = self.store.build_price_map()
                    self.refresh_all()
                    self.set_status(f"Prices updated: {updated} priced, {len(failed)} failed.")
                    if failed:
                        messagebox.showwarning(
                            "Update prices",
                            f"{updated} priced, {len(failed)} failed:\n" + ", ".join(failed[:15]))
                    else:
                        messagebox.showinfo("Update prices", f"All {updated} prices updated.")
                    return
                elif msg[0] == "error":
                    messagebox.showerror("Update prices failed", msg[1])
                    self.set_status("Price update failed.")
                    return
        except queue.Empty:
            pass
        self.after(150, lambda: self._poll_price_queue(work))

    def update_mana(self):
        """Fetch mana-cost pip data for top-20 cards missing it (needed for
        color-identity symbols). Runs in a worker thread like price updates."""
        try:
            import mana_fetch
        except ImportError as e:
            messagebox.showerror("Update mana data", f"mana_fetch module missing:\n{e}")
            return
        todo = self.store.cards_missing_mana()
        if not todo:
            messagebox.showinfo("Update mana data", "Every top-20 card already has pip data.")
            return
        if not messagebox.askyesno(
                "Update mana data",
                f"Fetch mana costs for {len(todo)} cards?\n"
                f"Quick bulk lookup. Results are cached in mana_costs.json."):
            return
        self._price_total = len(todo)
        self.price_bar["maximum"] = len(todo)
        self.price_bar["value"] = 0
        self.set_status(f"Fetching mana 0/{len(todo)}...")
        work = queue.Queue()

        def _worker():
            try:
                cache = mana_fetch.load_cache()
                updated, failed = mana_fetch.update_missing(
                    todo, cache,
                    progress_cb=lambda i, n, disp, ok: work.put(("p", i, n, disp)))
                work.put(("done", updated, failed))
            except Exception as e:  # noqa: BLE001 - report to main thread
                work.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_mana_queue(work)

    def _poll_mana_queue(self, work):
        try:
            while True:
                msg = work.get_nowait()
                if msg[0] == "p":
                    _, i, n, disp = msg
                    self.price_bar["value"] = i
                    self.set_status(f"Fetching mana {i}/{n}... {disp}")
                elif msg[0] == "done":
                    _, updated, failed = msg
                    self.price_bar["value"] = self._price_total
                    self.store.mana_cache = self.store.load_mana_cache()
                    self._thumb_cache.clear()
                    self.refresh_all()
                    self.set_status(f"Mana data updated: {updated} fetched, {len(failed)} failed.")
                    if failed:
                        messagebox.showwarning(
                            "Update mana data",
                            f"{updated} fetched, {len(failed)} failed:\n" + ", ".join(failed[:15]))
                    else:
                        messagebox.showinfo("Update mana data", f"All {updated} cards fetched.")
                    return
                elif msg[0] == "error":
                    messagebox.showerror("Update mana data failed", msg[1])
                    self.set_status("Mana update failed.")
                    return
        except queue.Empty:
            pass
        self.after(150, lambda: self._poll_mana_queue(work))

    def refresh_live_snapshot(self):
        try:
            import snapshot_fetch
        except ImportError as e:
            messagebox.showerror("Refresh", f"snapshot_fetch module missing:\n{e}")
            return
        if not messagebox.askyesno("Refresh live 7-day",
                                    "Pull the live 7-day metagame from MTGGoldfish?\n"
                                    "Takes ~30-60s (deck list + card art).\n\n"
                                    "Card swaps are reverted: deck lists come back "
                                    "exactly as the new snapshot defines them."):
            return
        self.set_status("Pulling live 7-day metagame...")
        self.update_idletasks()
        try:
            snap, fname = snapshot_fetch.refresh_snapshot(period="7", limit=20, with_art=True)
        except Exception as e:
            messagebox.showerror("Refresh failed", str(e))
            self.set_status("Live refresh failed.")
            return
        self._thumb_cache.clear()
        self.store.metagame = self.store.load_metagame()
        n_swaps = sum(len(v) for v in (self.store.deck_overrides or {}).values())
        self.store.deck_overrides = {}
        self.store.save_overrides()
        self.refresh_all()
        self.set_status(f"Live 7-day snapshot saved: {fname.name} ({len(snap['decks'])} decks). "
                        f"{n_swaps} card swap(s) reverted to the fresh lists.")

    def refresh_all_data(self):
        """Refresh everything pulled from the internet, in dependency order:
        metagame snapshot -> sample decklists -> missing prices -> missing
        mana data -> matchup matrix. Runs in a worker thread with progress
        in the status bar; the UI redraws once at the end."""
        try:
            import snapshot_fetch
            import fetch_decklists
            import price_fetch
            import mana_fetch
            import matchup_fetch
        except ImportError as e:
            messagebox.showerror("Refresh all data", f"fetch module missing:\n{e}")
            return
        if not messagebox.askyesno(
                "Refresh all data",
                "Re-pull everything from the internet?\n\n"
                "1. Metagame snapshot + art\n"
                "2. Sample decklists (several minutes)\n"
                "3. Missing prices\n"
                "4. Missing mana data\n"
                "5. Matchup matrix\n\n"
                "Card swaps will be reverted to the fresh lists."):
            return
        price_todo = self.store.cards_missing_prices()
        mana_todo = self.store.cards_missing_mana()
        self.btn_refresh_all.config(state="disabled")
        work = queue.Queue()

        def _worker():
            results = {}

            def say(t):
                work.put(("status", t))

            try:
                say("Step 1/5: metagame snapshot + art...")
                snap, fname = snapshot_fetch.refresh_snapshot(period="7", limit=20, with_art=True)
                results["metagame"] = f"ok ({len(snap['decks'])} decks)"
                results["metagame_ok"] = True
            except Exception as e:  # noqa: BLE001 - collected for the summary
                results["metagame"] = f"FAILED: {e}"
                results["metagame_ok"] = False
            try:
                say("Step 2/5: sample decklists (several minutes)...")
                dl = fetch_decklists.fetch_all(limit=20)
                results["decklists"] = f"ok ({len(dl)} lists)"
            except Exception as e:  # noqa: BLE001 - collected for the summary
                results["decklists"] = f"FAILED: {e}"
            try:
                if price_todo:
                    say(f"Step 3/5: prices ({len(price_todo)} cards)...")
                    cache = price_fetch.load_cache()
                    updated, failed = price_fetch.update_missing(
                        price_todo, cache, force=True,
                        progress_cb=lambda i, n, disp, ok: work.put(("p", "prices", i, n, disp)))
                    results["prices"] = f"ok ({updated} priced, {len(failed)} failed)"
                else:
                    results["prices"] = "ok (nothing missing)"
            except Exception as e:  # noqa: BLE001 - collected for the summary
                results["prices"] = f"FAILED: {e}"
            try:
                if mana_todo:
                    say(f"Step 4/5: mana data ({len(mana_todo)} cards)...")
                    cache = mana_fetch.load_cache()
                    updated, failed = mana_fetch.update_missing(
                        mana_todo, cache,
                        progress_cb=lambda i, n, disp, ok: work.put(("p", "mana", i, n, disp)))
                    results["mana"] = f"ok ({updated} fetched, {len(failed)} failed)"
                else:
                    results["mana"] = "ok (nothing missing)"
            except Exception as e:  # noqa: BLE001 - collected for the summary
                results["mana"] = f"FAILED: {e}"
            try:
                say("Step 5/5: matchup matrix...")
                snap = matchup_fetch.build_snapshot()
                results["matchups"] = f"ok ({len(snap.get('decks', {}))} decks)"
            except Exception as e:  # noqa: BLE001 - collected for the summary
                results["matchups"] = f"FAILED: {e}"
            work.put(("done", results))

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_refresh_all(work)

    def _poll_refresh_all(self, work):
        try:
            while True:
                msg = work.get_nowait()
                if msg[0] == "status":
                    self.set_status(msg[1])
                elif msg[0] == "p":
                    _, phase, i, n, disp = msg
                    try:
                        self.price_bar["maximum"] = n
                        self.price_bar["value"] = i
                    except Exception:
                        pass
                    self.set_status(f"[{phase}] {i}/{n}... {disp}")
                elif msg[0] == "done":
                    self._finish_refresh_all(msg[1])
                    return
                elif msg[0] == "error":
                    messagebox.showerror("Refresh all data failed", msg[1])
                    self.btn_refresh_all.config(state="normal")
                    return
        except queue.Empty:
            pass
        self.after(150, lambda: self._poll_refresh_all(work))

    def _finish_refresh_all(self, results):
        try:
            if results.get("metagame_ok"):
                n_swaps = sum(len(v) for v in (self.store.deck_overrides or {}).values())
                self.store.deck_overrides = {}
                self.store.save_overrides()
            else:
                n_swaps = 0
            self._thumb_cache.clear()
            self.store.metagame = self.store.load_metagame()
            self.store.decklists = self.store.load_decklists()
            self.store.price_cache = self.store.load_price_cache()
            self.store.price_map = self.store.build_price_map()
            self.store.mana_cache = self.store.load_mana_cache()
            self.store.matchups = self.store.load_matchups()
            self.refresh_all()
        finally:
            self.btn_refresh_all.config(state="normal")
        lines = [f"{step}: {results.get(step, 'skipped')}" for step in
                 ("metagame", "decklists", "prices", "mana", "matchups")]
        if results.get("metagame_ok"):
            lines.append(f"{n_swaps} card swap(s) reverted to the fresh lists.")
        messagebox.showinfo("Refresh all data", "\n".join(lines))
        self.set_status("All internet data refreshed.")

    def save_snapshot_copy(self):
        try:
            with open(METAGAME_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Save snapshot", f"Could not read current snapshot:\n{e}")
            return
        import datetime
        stamp = data.get("snapshot_date", datetime.date.today().isoformat())
        tf = str(data.get("timeframe", "7 days")).replace(" ", "")
        d = SNAPSHOTS_DIR
        d.mkdir(parents=True, exist_ok=True)
        fname = d / f"metagame_{stamp}_{tf}.json"
        i = 2
        while fname.exists():
            fname = d / f"metagame_{stamp}_{tf}_{i}.json"
            i += 1
        write_json_atomic(fname, data)
        prune_snapshots()
        self.refresh_stats()
        self.set_status(f"Snapshot saved: {fname.name}")

    # ---------- statistics tab (canvas charts, stdlib only) ----------
    PIE_COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
                  "#edc948", "#b07aa1", "#ff9da7", "#9c755f"]

    def _build_stats_tab(self):
        self.lbl_stats_summary = ttk.Label(self.tab_stats, text="", wraplength=1000,
                                           font=F("m_b"))
        self.lbl_stats_summary.pack(anchor="w", pady=(0, 4))
        charts = ttk.Frame(self.tab_stats)
        charts.pack(fill="x", pady=2)
        left = ttk.Frame(charts)
        left.pack(side="left", padx=(0, 12))
        ttk.Label(left, text="Meta share — top 8 + rest", font=F("s_b")).pack(anchor="w")
        self.pie_canvas = tk.Canvas(left, width=430, height=290, bg=C("bg"), highlightthickness=1,
                                    highlightbackground="#ccc")
        self.pie_canvas.pack()
        right = ttk.Frame(charts)
        right.pack(side="left", fill="x", expand=True)
        ttk.Label(right, text="Top 10 decks by META% · overall winrate in color (blue bar = buildable)",
                  font=F("s_b")).pack(anchor="w")
        self.bar_canvas = tk.Canvas(right, width=560, height=290, bg=C("bg"), highlightthickness=1,
                                    highlightbackground="#ccc")
        self.bar_canvas.pack(fill="x")

        ttk.Label(self.tab_stats, text="Meta share by archetype",
                  font=F("s_b")).pack(anchor="w", pady=(6, 0))
        self.arch_canvas = tk.Canvas(self.tab_stats, width=1000, height=86, bg=C("bg"),
                                     highlightthickness=1, highlightbackground="#ccc")
        self.arch_canvas.pack(fill="x")

        self.lbl_trend_title = ttk.Label(self.tab_stats, text="Trending",
                                         font=F("s_b"))
        self.lbl_trend_title.pack(anchor="w", pady=(6, 2))
        tframe = ttk.Frame(self.tab_stats)
        tframe.pack(fill="both", expand=True)
        tcols = ("deck", "then", "now", "delta", "trend")
        self.trend_tree = ttk.Treeview(tframe, columns=tcols, show="headings", height=9)
        heads = {"deck": "Deck", "then": "Then %", "now": "Now %", "delta": "Change (pp)",
                 "trend": "Trend"}
        widths = {"deck": 260, "then": 90, "now": 90, "delta": 110, "trend": 100}
        for c in tcols:
            self.trend_tree.heading(c, text=heads[c])
            self.trend_tree.column(c, width=widths[c], anchor="center" if c != "deck" else "w")
        vsb = ttk.Scrollbar(tframe, orient="vertical", command=self.trend_tree.yview)
        self.trend_tree.configure(yscrollcommand=vsb.set)
        self.trend_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        theme.apply_tree_tags(self.trend_tree, theme.TREND_TAGS)

    def _draw_pie(self, decks):
        c = self.pie_canvas
        c.delete("all")
        top = decks[:8]
        top_sum = sum(d.get("meta_pct", 0) for d in top)
        slices = [(d["name"], d.get("meta_pct", 0), self.PIE_COLORS[i % len(self.PIE_COLORS)])
                  for i, d in enumerate(top)]
        rest = max(0.0, 100.0 - top_sum)
        slices.append(("Rest of meta", rest, "#bab0ac"))
        cx, cy, r = 125, 150, 105
        angle = 90.0
        for name, pct, color in slices:
            extent = -pct / 100.0 * 360.0
            if pct > 0:
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=angle, extent=extent,
                             fill=color, outline="white", width=2)
            angle += extent
        for i, (name, pct, color) in enumerate(slices):
            y = 28 + i * 28
            c.create_rectangle(248, y - 9, 264, y + 7, fill=color, outline="#888")
            label = name if len(name) <= 20 else name[:19] + "…"
            c.create_text(270, y, text=f"{label} {pct:.1f}%", anchor="w",
                          font=F("s"), fill=C("fg"))

    def _draw_bars(self, decks, buildable=None):
        c = self.bar_canvas
        c.delete("all")
        top = decks[:10]
        if not top:
            return
        max_pct = max(d.get("meta_pct", 0) for d in top) or 1
        if buildable is None:
            buildable = {p["deck"] for p in self.store.deck_progress_list() if p["buildable"]}
        x0, bw_max = 170, 250
        for i, d in enumerate(top):
            y = 14 + i * 27
            pct = d.get("meta_pct", 0)
            done = d["name"] in buildable
            bar_w = max(2, pct / max_pct * bw_max)
            name = d["name"] if len(d["name"]) <= 22 else d["name"][:21] + "…"
            c.create_text(x0 - 6, y + 7, text=name, anchor="e", font=F("s"), fill=C("fg"))
            c.create_rectangle(x0, y, x0 + bar_w, y + 14,
                               fill=C("bar_done") if done else C("bar_idle"), outline="")
            row = self.store.matchup_row(d["name"])
            ov = row["overall"] if row else None
            c.create_text(x0 + bar_w + 6, y + 7, text=f"{pct:.1f}%",
                          anchor="w", font=F("s"), fill=C("muted"))
            if ov is None:
                c.create_text(x0 + bar_w + 52, y + 7, text="no WR data",
                              anchor="w", font=F("xs"), fill=C("faint"))
            else:
                c.create_text(x0 + bar_w + 52, y + 7, text=f"{ov:.0f}% win",
                              anchor="w", font=F("s_b"),
                              fill=wr_text_color(ov))

    def _draw_archetypes(self, decks):
        c = self.arch_canvas
        c.delete("all")
        order = ["Aggro", "Tempo", "Midrange", "Control", "Combo", "Ramp"]
        agg, counts = {}, {}
        for d in decks:
            a = d.get("archetype") or "Unclassified"
            agg[a] = agg.get(a, 0) + d.get("meta_pct", 0)
            counts[a] = counts.get(a, 0) + 1
        present = [a for a in order + ["Unclassified"] if a in agg]
        x, x0, bw = 10, 10, 960
        for a in present:
            w = max(2, agg[a] / 100.0 * bw)
            c.create_rectangle(x, 12, x + w, 34, fill=ARCH_CHIP.get(a, "#707070"),
                               outline="white", width=1)
            x += w
        lx = 10
        for a in present:
            c.create_rectangle(lx, 52, lx + 14, 66, fill=ARCH_CHIP.get(a, "#707070"),
                               outline="#888")
            lx += 19
            txt = f"{a} {agg[a]:.1f}% ({counts[a]})"
            c.create_text(lx, 59, text=txt, anchor="w", font=F("s"), fill=C("fg"))
            lx += len(txt) * 7 + 18

    def refresh_stats(self, deck_list=None):
        if not hasattr(self, "pie_canvas"):
            return
        if deck_list is None:
            deck_list = self.store.deck_progress_list()
        buildable = {p["deck"] for p in deck_list if p["buildable"]}
        meta = getattr(self.store, "metagame", {}) or {}
        decks = sorted(meta.get("decks", []), key=lambda x: x.get("meta_pct", 0), reverse=True)
        if decks:
            total_n = sum(d.get("deck_count", 0) for d in decks)
            avg_price = sum(d.get("paper_price", 0) for d in decks) / len(decks)
            self.lbl_stats_summary.config(
                text=(f"Snapshot {meta.get('snapshot_date', '?')} (last {meta.get('timeframe', '?')}) — "
                      f"{len(decks)} decks, {total_n} tracked | "
                      f"Top-{len(decks)} avg paper ${avg_price:,.0f} | "
                      f"{len(buildable)}/{len(decks)} decks buildable (complete or <$20 to finish)"))
            self._draw_pie(decks)
            self._draw_bars(decks, buildable)
            self._draw_archetypes(decks)
        else:
            self.lbl_stats_summary.config(text="No metagame data loaded.")
            self.arch_canvas.delete("all")

        for r in self.trend_tree.get_children():
            self.trend_tree.delete(r)
        snaps = self.store.list_snapshots()
        if len(snaps) < 2:
            self.lbl_trend_title.config(
                text="Trending — need 2+ snapshots in snapshots/ (use 'Save snapshot' after each refresh)")
            self.trend_tree.insert("", "end", values=(
                "Not enough history yet", "—", "—", "—", "—"), tags=("flat",))
            return
        old, new = snaps[-2], snaps[-1]
        title = (f"Trending: {old['date']} ({old['timeframe']})  ->  "
                 f"{new['date']} ({new['timeframe']})")
        if old['timeframe'] != new['timeframe']:
            title += "  — different windows, moves partly reflect sample size"
        self.lbl_trend_title.config(text=title)
        for r in self.store.compare_snapshots(old["data"], new["data"]):
            if r["status"] == "NEW":
                then, now, delta, arrow = "—", f"{r['new']:.1f}%", "—", "NEW"
            elif r["status"] == "OUT":
                then, now, delta, arrow = f"{r['old']:.1f}%", "—", "—", "OUT"
            else:
                then, now = f"{r['old']:.1f}%", f"{r['new']:.1f}%"
                delta = f"{r['delta']:+.1f}pp"
                arrow = "UP" if r["status"] == "UP" else ("DOWN" if r["status"] == "DOWN" else "FLAT")
            tag = {"UP": "up", "DOWN": "down", "NEW": "new", "OUT": "out"}.get(r["status"], "flat")
            self.trend_tree.insert("", "end", values=(
                r["name"], then, now, delta, arrow), tags=(tag,))

    # ---------- matchups tab (20x20 winrate matrix, canvas-drawn) ----------
    # Base geometry; CW/RH grow to fill the window (clamped) via _mu_resize.
    MU_LW, MU_OW, MU_CW, MU_RH, MU_HH = 160, 72, 56, 34, 52
    MU_CW_MIN, MU_CW_MAX = 46, 76
    MU_RH_MIN, MU_RH_MAX = 28, 42
    # Cells below this many matches render faded (same number, less shout).
    MU_MIN_MATCHES = 10

    def _build_matchups_tab(self):
        meta = getattr(self.store, "matchups", {}) or {}
        snap = meta.get("snapshot_date", "never")
        info = (f"Winrates from mtgdecks.net Modern, last 15 days (pulled {snap}). "
                f"Rows read left-to-right: row deck's winrate vs column deck. "
                f"Pale cells have under {MU_MIN_MATCHES} matches. "
                f"Hover any cell for match counts.")
        ttk.Label(self.tab_mu, text=info, wraplength=980, foreground=C("muted")).pack(anchor="w", pady=(0, 6))
        bar = ttk.Frame(self.tab_mu)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Refresh matchups", command=self.refresh_matchups).pack(side="left")
        self.lbl_mu_status = ttk.Label(bar, text="", foreground=C("muted"))
        self.lbl_mu_status.pack(side="left", padx=(12, 0))
        frame = ttk.Frame(self.tab_mu)
        frame.pack(fill="both", expand=True)
        self.mu_canvas = tk.Canvas(frame, highlightthickness=0, bg=C("bg"))
        mu_vsb = ttk.Scrollbar(frame, orient="vertical", command=self.mu_canvas.yview)
        mu_hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.mu_canvas.xview)
        self.mu_canvas.configure(yscrollcommand=mu_vsb.set, xscrollcommand=mu_hsb.set)
        self.mu_canvas.grid(row=0, column=0, sticky="nsew")
        mu_vsb.grid(row=0, column=1, sticky="ns")
        mu_hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.mu_canvas.bind("<MouseWheel>", lambda e: self._smooth_scroll(self.mu_canvas, e))
        self.mu_canvas.bind("<Button-4>", lambda e: self._smooth_scroll(self.mu_canvas, e))
        self.mu_canvas.bind("<Button-5>", lambda e: self._smooth_scroll(self.mu_canvas, e))
        self.mu_canvas.bind("<Motion>", self._mu_hover)
        self.mu_canvas.bind("<Configure>", lambda e: self._mu_resize_soon())
        self._mu_cells = {}
        self._mu_resize_id = None
        self._draw_matchups()

    def _mu_resize_soon(self, delay_ms=250):
        try:
            if self._mu_resize_id is not None:
                self.after_cancel(self._mu_resize_id)
            self._mu_resize_id = self.after(delay_ms, self._mu_refit)
        except Exception:
            pass

    def _mu_refit(self):
        """Grow cells to fill the window (clamped); scrollbars cover the rest."""
        self._mu_resize_id = None
        try:
            decks = (getattr(self.store, "metagame", {}) or {}).get("decks", [])
            n = max(1, len(decks))
            w, h = self.mu_canvas.winfo_width(), self.mu_canvas.winfo_height()
            cw = min(self.MU_CW_MAX, max(self.MU_CW_MIN, (w - self.MU_LW - self.MU_OW - 24) / n))
            rh = min(self.MU_RH_MAX, max(self.MU_RH_MIN, (h - self.MU_HH) / n))
        except Exception:
            return
        if abs(cw - self.MU_CW) < 1 and abs(rh - self.MU_RH) < 1:
            return
        self.MU_CW, self.MU_RH = cw, rh
        try:
            self._draw_matchups()
        except Exception:
            pass

    def _mu_xy(self, j, half=False):
        x = self.MU_LW + 8 + self.MU_OW + 8 + j * self.MU_CW
        return x + (self.MU_CW / 2 if half else 0)

    def _draw_matchups(self):
        c = self.mu_canvas
        c.delete("all")
        self._mu_cells = {}
        decks = [d["name"] for d in (getattr(self.store, "metagame", {}) or {}).get("decks", [])]
        if not decks:
            return
        LW, OW, CW, RH, HH = self.MU_LW, self.MU_OW, self.MU_CW, self.MU_RH, self.MU_HH
        # column headers
        c.create_text(LW + 8 + OW / 2, HH / 2, text="OVERALL", font=F("s_b"),
                      fill=C("fg"))
        for j, name in enumerate(decks):
            x = self._mu_xy(j, half=True)
            c.create_text(x, HH / 2, text=short_deck_name(name), font=F("xs"),
                          fill=C("fg"), justify="center")
        for i, a in enumerate(decks):
            y = HH + i * RH
            c.create_text(LW - 6, y + RH / 2, text=a, font=F("m"),
                          fill=C("fg"), anchor="e")
            row = self.store.matchup_row(a)
            # overall column (separated look via slightly wider gap)
            if row is None:
                fill, fg = wr_colors(None)
                txt = "–"
                info = (a, None, None, None)
            else:
                fill, fg = wr_colors(row["overall"])
                if row["matches"] < self.MU_MIN_MATCHES:
                    fill = mu_fade(fill)
                txt = f"{row['overall']:.0f}%"
                info = (a, None, row["overall"], row["matches"])
            c.create_rectangle(LW + 8, y + 2, LW + 8 + OW, y + RH - 2,
                               fill=fill, outline="white", width=1)
            c.create_text(LW + 8 + OW / 2, y + RH / 2, text=txt,
                          font=F("m_b"), fill=fg)
            self._mu_cells[(i, -1)] = info
            for j, b in enumerate(decks):
                x = self._mu_xy(j)
                cell = self.store.matchup_cell(a, b)
                if cell == "mirror":
                    fill, fg, txt = "#555555", "#ffffff", "–"
                    info = (a, b, None, None)
                elif not cell:
                    fill, fg = wr_colors(None)
                    txt = "–"
                    info = (a, b, None, None)
                else:
                    fill, fg = wr_colors(cell["winrate"])
                    if cell["matches"] < self.MU_MIN_MATCHES:
                        fill = mu_fade(fill)
                    txt = f"{cell['winrate']:.0f}%"
                    info = (a, b, cell["winrate"], cell["matches"])
                c.create_rectangle(x, y + 2, x + CW, y + RH - 2,
                                   fill=fill, outline="white", width=1)
                c.create_text(x + CW / 2, y + RH / 2, text=txt,
                              font=F("m_b"), fill=fg)
                self._mu_cells[(i, j)] = info
        c.configure(scrollregion=c.bbox("all"))

    def _mu_hover(self, event):
        info = None
        try:
            ox0 = self.MU_LW + 8
            i = int((event.y - self.MU_HH) // self.MU_RH)
            if ox0 <= event.x < ox0 + self.MU_OW:
                info = self._mu_cells.get((i, -1))
            else:
                j = int((event.x - (ox0 + self.MU_OW + 8)) // self.MU_CW)
                info = self._mu_cells.get((i, j))
        except Exception:
            info = None
        if not info:
            return
        a, b, wr, matches = info
        if b is None:
            self.set_status(f"{a}: {wr:.0f}% overall ({matches} matches)"
                            if wr is not None else f"{a}: no matchup data")
        elif a == b or wr is None:
            self.set_status(f"{a} vs {b}: mirror matchup" if a == b else f"{a} vs {b}: no data")
        else:
            self.set_status(f"{a} {wr:.0f}% vs {b} ({matches} matches)")

    def refresh_matchups(self):
        try:
            import matchup_fetch
        except ImportError as e:
            messagebox.showerror("Refresh matchups", f"matchup_fetch module missing:\n{e}")
            return
        self.lbl_mu_status.config(text="Pulling matchup matrix...")
        self.update_idletasks()
        work = queue.Queue()

        def _worker():
            try:
                snap = matchup_fetch.build_snapshot()
                work.put(("done", snap))
            except Exception as e:  # noqa: BLE001 - report to main thread
                work.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_matchup_queue(work)

    def _poll_matchup_queue(self, work):
        try:
            msg = work.get_nowait()
        except queue.Empty:
            self.after(150, lambda: self._poll_matchup_queue(work))
            return
        if msg[0] == "done":
            snap = msg[1]
            self.store.matchups = self.store.load_matchups()
            self._draw_matchups()
            n = len(snap.get("decks", {}))
            self.lbl_mu_status.config(text=f"Updated {snap.get('snapshot_date')} ({n} decks mapped).")
            self.set_status(f"Matchup matrix updated ({n} decks).")
        else:
            messagebox.showerror("Refresh matchups failed", msg[1])
            self.lbl_mu_status.config(text="Refresh failed.")

    # ---------- actions ----------
    def set_status(self, msg: str):
        self.status.config(text=msg)

    def reload(self):
        self.store = GuiStore(PLAN_FILE, COLLECTION_FILE)
        self.refresh_all()
        self.set_status(f"Reloaded data + {self.store.collection.total_cards()} cards in collection.")

    def refresh_all(self):
        # One deck-list computation per refresh; shared by every tab.
        deck_list = self.store.deck_progress_list()
        ov = self.store.overall(deck_list)
        self.lbl_overall.config(
            text=(f"Collection: {ov['uniq_owned']}/{ov['uniq_total']} unique "
                  f"({ov['uniq_pct']:.1f}%)  |  {ov['copies_owned']}/{ov['copies_need']} copies "
                  f"({ov['copies_pct']:.1f}%)  |  missing ~${ov['missing_val']:,.0f}")
        )
        self.lbl_buildable.config(text=f"Decks buildable: {ov['buildable']}/{ov['decks_total']}")
        self.refresh_dashboard(deck_list)
        self.refresh_collection()
        if hasattr(self, "meta_inner"):
            self.refresh_metagame_table()
        if hasattr(self, "pie_canvas"):
            self.refresh_stats(deck_list)
        if hasattr(self, "shop_tree"):
            self.refresh_shop(deck_list)
        if hasattr(self, "mu_canvas"):
            self._draw_matchups()

    def refresh_dashboard(self, decks=None):
        # Tile grid of decks (no upgrade-phase references anywhere here):
        # Buildable decks sort first. Click a tile for its deck list.
        for w in self.dash_inner.winfo_children():
            w.destroy()
        self.phase_widgets = []
        self._tile_imgs = []
        if decks is None:
            decks = self.store.deck_progress_list()
        n_build = sum(1 for d in decks if d["buildable"])
        tk.Label(self.dash_inner,
                 text=f"{n_build}/{len(decks)} decks buildable (complete or under $20 to finish)  (click a tile for its deck list)",
                 font=F("m_b"), fg=C("fg"), bg=C("bg")).grid(row=0, column=0, columnspan=3,
                                                                               sticky="w", pady=(0, 6))
        for i, prog in enumerate(decks):
            self._build_deck_tile(prog).grid(row=1 + i // 3, column=i % 3,
                                             padx=6, pady=6, sticky="n")
        for c in range(3):
            self.dash_inner.grid_columnconfigure(c, weight=1)
        self._bind_dashboard_wheel(self.dash_inner)

    def open_detail(self, prog):
        DeckDetailWindow(self, {"name": prog["deck"],
                                "description": self._deck_subtitle(prog)},
                         prog, store=self.store, deck_name=prog["deck"],
                         on_change=self.refresh_all,
                         on_copy=lambda t: self.set_status("Shopping list copied to clipboard."))

    def refresh_collection(self):
        q = self.coll_search_var.get().strip().lower() if hasattr(self, "coll_search_var") else ""
        for r in self.coll_tree.get_children():
            self.coll_tree.delete(r)
        self._coll_keys = []
        items = list(self.store.collection.cards.items())
        items.sort(key=lambda kv: kv[1].name.lower())
        shown = 0
        for key, c in items:
            if q and q not in c.name.lower():
                continue
            self.coll_tree.insert("", "end", values=(c.quantity, c.name))
            self._coll_keys.append(key)
            shown += 1
        total = self.store.collection.total_cards()
        uniq = self.store.collection.unique_cards()
        self.lbl_coll_count.config(text=f"{shown} shown | {uniq} unique | {total} total")

    def add_card_dialog(self):
        name = simpledialog.askstring("Add card", "Card name (e.g. Solitude):", parent=self)
        if not name or not name.strip():
            return
        qty = simpledialog.askinteger("Add card", f"Quantity of '{name.strip()}':",
                                      parent=self, minvalue=1, maxvalue=99, initialvalue=1)
        if not qty:
            return
        self.store.add_cards([Card(name=name.strip(), quantity=int(qty))])
        self.refresh_all()
        self.set_status(f"Added {qty}x {name.strip()}.")

    def remove_selected(self):
        sel = self.coll_tree.selection()
        if not sel:
            messagebox.showinfo("Remove", "Select a row first.")
            return
        idxs = [self.coll_tree.index(i) for i in sel]
        for i in sorted(idxs, reverse=True):
            self.store.collection.pop(self._coll_keys[i])
        self.store.save_collection()
        self.refresh_all()
        self.set_status(f"Removed {len(idxs)} row(s).")

    def clear_collection(self):
        if messagebox.askyesno("Clear", "Delete ALL cards in cached collection?"):
            backup = backup_file(self.store.collection_file)
            self.store.collection = Collection()
            self.store.save_collection()
            self.refresh_all()
            note = f" Backup kept as {backup.name}." if backup else ""
            self.set_status(f"Collection cleared.{note}")

    def _prepare_import(self, action_desc: str) -> bool:
        """If replace mode is on, confirm and clear the cached collection first.

        Returns True if the import should proceed. Prevents duplicate
        quantities when re-uploading a full collection export.
        """
        if not self.replace_var.get():
            return True
        n = self.store.collection.total_cards()
        u = self.store.collection.unique_cards()
        if not messagebox.askyesno(
                "Replace collection",
                f"Replace mode is ON.\n\nDelete all {u} unique cards ({n} total) "
                f"from the cached collection and {action_desc} fresh?\n\n"
                f"(A backup is saved first. This avoids doubled-up quantities "
                f"from repeat uploads.)"):
            return False
        backup = backup_file(self.store.collection_file)
        self.store.collection = Collection()
        self.store.save_collection()
        if backup:
            self.set_status(f"Backed up collection as {backup.name}.")
        return True

    def _import_verb(self) -> str:
        return "Replaced collection with" if self.replace_var.get() else "Imported"

    def upload_file(self):
        path = filedialog.askopenfilename(title="Select card list file", filetypes=self.FILETYPES)
        if not path:
            return
        if not self._prepare_import("re-import the file"):
            return
        try:
            n = self.store.import_file(path)
            verb = self._import_verb()
            self.lbl_import.config(text=f"{verb} {n} entries from {Path(path).name}.")
            self.refresh_all()
            self.set_status(f"{verb} {n} entries from {Path(path).name}.")
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    def import_textbox(self):
        content = self.paste_box.get("1.0", "end")
        try:
            cards = FileParser.parse_text(content)
            if not cards:
                messagebox.showinfo("Import", "No cards found in text box.")
                return
            if not self._prepare_import("import the pasted list"):
                return
            n = self.store.add_cards(cards)
            verb = self._import_verb()
            self.lbl_import.config(text=f"{verb} {n} entries from text box.")
            self.refresh_all()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    def import_clipboard(self):
        try:
            content = self.clipboard_get()
        except Exception as e:
            messagebox.showerror("Clipboard", f"Could not read clipboard:\n{e}")
            return
        try:
            cards = FileParser.parse_text(content)
            if not cards:
                messagebox.showinfo("Clipboard", "Clipboard has no recognizable card lines.")
                return
            if not self._prepare_import("import the clipboard list"):
                return
            n = self.store.add_cards(cards)
            verb = self._import_verb()
            self.lbl_import.config(text=f"{verb} {n} entries from clipboard.")
            self.refresh_all()
            self.set_status(f"{verb} {n} entries from clipboard.")
        except Exception as e:
            messagebox.showerror("Import failed", str(e))


def main():
    if not HAS_TRACKER:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Missing module", f"Could not import tracker.py:\n{IMPORT_ERROR}")
        return
    if not PLAN_FILE.exists():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Missing data file", f"Data file not found:\n{PLAN_FILE}")
        return
    app = TrackerGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
