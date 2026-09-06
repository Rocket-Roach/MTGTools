#!/usr/bin/env python3
"""
Fetch a fresh Modern metagame snapshot from MTGGoldfish.

The site's timeframe selector is a session-bound form (POST /metagame/re_sort),
so this module replays it: fresh GET (cache-busted) -> form token -> POST
period=7 -> parse turbo-stream tiles -> Scryfall art crops -> save.

Usage:
    python snapshot_fetch.py [--period 7] [--limit 20]

Writes:
    modern_metagame.json                      (live file used by the GUI)
    snapshots/metagame_<date>_<period>days.json  (history for trend stats)
    assets/thumbs/<slug>-art.jpg              (deck art thumbnails)

Stdlib only.
"""

import html as htmlmod
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from paths import ROOT, DATA_DIR, THUMBS_DIR, SNAPSHOTS_DIR, METAGAME_FILE

SNAP_DIR = SNAPSHOTS_DIR
THUMB_DIR = THUMBS_DIR

BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              'Chrome/120.0 Safari/537.36')
API_UA = {'User-Agent': 'MTG-Deck-Tracker/1.0 (personal collection tool)',
          'Accept': 'application/json'}

ARCHETYPES = ("Aggro", "Tempo", "Midrange", "Control", "Combo", "Ramp")

ARCHETYPE = {
    "Goryo's Vengeance": "Combo",
    'Izzet Prowess': 'Aggro',
    'Eldrazi': 'Midrange',
    'Esper Blink': 'Midrange',
    'Affinity': 'Aggro',
    'Ruby Storm': 'Combo',
    'Mono-Green Eldrazi': 'Ramp',
    'Boros Energy': 'Midrange',
    'Living End': 'Combo',
    'Eldrazi Tron': 'Ramp',
    'Boros Ponza': 'Control',
    'Dimir Midrange': 'Midrange',
    '4c HollowOne': 'Aggro',
    'Domain Zoo': 'Aggro',
    'Tron': 'Ramp',
    'Amulet Titan': 'Combo',
    'Neobrand': 'Combo',
    'Devoted Combo': 'Combo',
    'Jeskai Energy': 'Control',
    'Boros Burn': 'Aggro',
}

# Conservative keyword fallback for decks with no curated label.
_ARCHETYPE_KEYWORDS = (
    (("storm", "brand", "combo", "living end", "belcher", "titan",
       "creativity", "footfalls", "oops", "yawgmoth"), "Combo"),
    (("burn", "prowess", "zoo", "hollow", "affinity", "energy",
       "goblins", "humans", "merfolk", "burn"), "Aggro"),
    (("tron", "ramp", "titan", "eldrazi ramp", "amulet"), "Ramp"),
    (("control", "miracles", "taking turns", "lantern"), "Control"),
    (("tempo", "murktide", "shadow"), "Tempo"),
)


def archetype_for(deck_name: str) -> str:
    """Curated label, else keyword guess, else 'Unclassified'."""
    if deck_name in ARCHETYPE:
        return ARCHETYPE[deck_name]
    low = deck_name.lower()
    for keywords, label in _ARCHETYPE_KEYWORDS:
        if any(k in low for k in keywords):
            return label
    return "Unclassified"


# Land name -> colors it can produce (WUBRG only; pure-colorless and
# any-color lands are omitted so the dominant *colored* symbol wins).
LAND_COLORS = {
    # Fetchlands
    'Flooded Strand': ('W', 'U'), 'Polluted Delta': ('U', 'B'),
    'Bloodstained Mire': ('B', 'R'), 'Wooded Foothills': ('R', 'G'),
    'Windswept Heath': ('W', 'G'), 'Marsh Flats': ('W', 'B'),
    'Scalding Tarn': ('U', 'R'), 'Verdant Catacombs': ('B', 'G'),
    'Misty Rainforest': ('U', 'G'), 'Arid Mesa': ('W', 'R'),
    # Shocklands
    'Hallowed Fountain': ('W', 'U'), 'Watery Grave': ('U', 'B'),
    'Blood Crypt': ('B', 'R'), 'Stomping Ground': ('R', 'G'),
    'Temple Garden': ('W', 'G'), 'Godless Shrine': ('W', 'B'),
    'Steam Vents': ('U', 'R'), 'Overgrown Tomb': ('B', 'G'),
    'Breeding Pool': ('U', 'G'), 'Sacred Foundry': ('W', 'R'),
    # Basics
    'Plains': ('W',), 'Island': ('U',), 'Swamp': ('B',),
    'Mountain': ('R',), 'Forest': ('G',),
    # Surveil lands
    'Lush Portico': ('W', 'U'), 'Undercity Sewers': ('U', 'B'),
    'Raucous Theater': ('B', 'R'), 'Thornspire Verge': ('R', 'G'),
    'Meticulous Archive': ('W', 'U'), 'Hedge Maze': ('G', 'U'),
    'Thundering Falls': ('U', 'R'),
    # Filter lands
    'Mystic Gate': ('W', 'U'), 'Sunken Ruins': ('U', 'B'),
    'Twilight Mire': ('B', 'G'), 'Flooded Grove': ('U', 'G'),
    'Grove of the Burnwillows': ('R', 'G'),
    # Fastlands / checklands seen in lists
    'Spirebluff Canal': ('U', 'R'),
    # Triomes
    'Indatha Triome': ('W', 'B', 'G'), 'Ketria Triome': ('G', 'U', 'R'),
    'Raugrin Triome': ('U', 'R', 'W'), 'Zagoth Triome': ('B', 'U', 'G'),
    'Savai Triome': ('R', 'W', 'B'),
    # Single-color utility
    'Otawara, Soaring City': ('U',),
    'Yavimaya, Cradle of Growth': ('G',),
    'Cori Mountain Monastery': ('G', 'U', 'R'),
    'Fiery Islet': ('U', 'R'),
}

COLOR_ORDER = ('W', 'U', 'B', 'R', 'G')


def deck_colors(mainboard, sideboard=()):
    """Count colored pips from a decklist's lands (qty-weighted)."""
    counts = {}
    for ci in list(mainboard or []) + list(sideboard or []):
        colors = LAND_COLORS.get(ci.get('name', ''))
        if not colors:
            continue
        q = int(ci.get('qty', 1))
        for c in colors:
            counts[c] = counts.get(c, 0) + q
    return counts


def dominant_color(colors):
    """(letter, share) of the most represented color; ('C', 0.0) if none."""
    total = sum(colors.get(c, 0) for c in COLOR_ORDER)
    if total <= 0:
        return 'C', 0.0
    best = max(COLOR_ORDER, key=lambda c: (colors.get(c, 0), -COLOR_ORDER.index(c)))
    return best, colors.get(best, 0) / total

# Deck name -> card shown on its MTGGoldfish tile (first key card). Used for art.
# Unknown decks fall back to their first parsed key card automatically.
REP_CARD = {
    "Goryo's Vengeance": "Goryo's Vengeance",
    'Amulet Titan': 'Primeval Titan',
    'Neobrand': 'Griselbrand',
    'Devoted Combo': 'Devoted Druid',
    'Jeskai Energy': "Orim's Chant",
    'Boros Burn': 'Boros Charm',
    'Tron': 'Karn, the Great Creator',
    '4c HollowOne': 'Vengevine',
}


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# Card-content identity rules: {required card names} -> canonical deck name.
# A deck holding every required card is classified under the canonical name
# no matter what the source site called it.
DECK_IDENTITY_RULES = [
    ({'blade of the bloodchief', 'basking broodscale'}, 'Eldrazi Bloodchief Combo'),
]


def canonical_deck_name(name: str, card_names) -> str:
    """Apply identity rules; returns the canonical deck name."""
    have = {c.strip().lower() for c in card_names}
    for required, canonical in DECK_IDENTITY_RULES:
        if required <= have and name != canonical:
            return canonical
    return name


def _opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch_tiles_html(period: str = '7') -> str:
    """POST the site's re-sort form and return the turbo-stream HTML."""
    op = _opener()
    cb = str(random.randint(10 ** 8, 10 ** 9))
    page = op.open(urllib.request.Request(
        f'https://www.mtggoldfish.com/metagame/modern/full?cb={cb}',
        headers={'User-Agent': BROWSER_UA, 'Cache-Control': 'no-cache',
                 'Pragma': 'no-cache'}), timeout=40).read().decode('utf-8', 'replace')
    m = re.search(r'<form[^>]*action="/metagame/re_sort"[^>]*>(.*?)</form>', page, re.S)
    if not m:
        raise RuntimeError('re-sort form not found on metagame page')
    tm = re.search(r'name="authenticity_token" value="([^"]+)"', m.group(1))
    if not tm:
        raise RuntimeError('CSRF token not found in re-sort form')
    fields = {'authenticity_token': tm.group(1), 'period': str(period),
              'mformat': 'modern', 'subformat': '', 'page': '',
              'type': 'online', 'full': '1'}
    req = urllib.request.Request(
        'https://www.mtggoldfish.com/metagame/re_sort',
        data=urllib.parse.urlencode(fields).encode(),
        headers={'User-Agent': BROWSER_UA,
                 'Referer': 'https://www.mtggoldfish.com/metagame/modern/full',
                 'Origin': 'https://www.mtggoldfish.com',
                 'Accept': 'text/vnd.turbo-stream.html, text/html, */*',
                 'X-Requested-With': 'XMLHttpRequest'})
    try:
        return op.open(req, timeout=40).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'metagame re-sort failed (HTTP {e.code})') from e


def parse_tiles(html: str, limit: int = 20):
    tiles = re.split(r"<div class='archetype-tile' id='", html)[1:]
    decks = []
    for t in tiles[:limit]:
        t = "<div class='archetype-tile' id='" + t

        def grab(pat):
            mm = re.search(pat, t, re.S)
            return htmlmod.unescape(mm.group(1).strip()) if mm else ''

        name = grab(r"class='deck-price-paper'>\s*<a[^>]*>(.*?)</a>")
        um = re.search(r"class='deck-price-paper'>\s*<a href='([^']+)'", t)
        url = um.group(1) if um else ''
        keys = [htmlmod.unescape(k) for k in re.findall(r'<li>(.*?)</li>', t)[:3]]
        mp = re.search(r"metagame-percentage.*?statistic-value'>\s*([\d.]+)%\s*<span[^>]*>\s*\((\d+)\)", t, re.S)
        paper = grab(r"archetype-tile-statistic deck-price-paper.*?statistic-value'>\s*\$\s*([\d,]+)")
        mtgo = grab(r"archetype-tile-statistic deck-price-online.*?statistic-value'>\s*([\d,]+)")
        if not name or not mp:
            continue
        decks.append({
            'name': name,
            'meta_pct': float(mp.group(1)), 'deck_count': int(mp.group(2)),
            'paper_price': int(paper.replace(',', '')) if paper else 0,
            'mtgo_tix': int(mtgo.replace(',', '')) if mtgo else 0,
            'key_cards': keys,
            'url': 'https://www.mtggoldfish.com' + url,
            'thumb': None,
            'archetype': archetype_for(name),
        })
    return decks


def fetch_art(deck_name: str, rep_card: str):
    """Download Scryfall art_crop for a deck's face card. Returns rel path or None."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / (slug(deck_name) + '-art.jpg')
    if dest.exists() and dest.stat().st_size > 2000:
        return f'assets/thumbs/{dest.name}'
    q = urllib.parse.quote(rep_card, safe='')
    try:
        info = json.load(urllib.request.urlopen(urllib.request.Request(
            f'https://api.scryfall.com/cards/named?exact={q}', headers=API_UA),
            timeout=30))
        uris = info.get('image_uris') or info['card_faces'][0]['image_uris']
        art = uris.get('art_crop')
        if not art:
            return None
        data = urllib.request.urlopen(
            urllib.request.Request(art, headers={'User-Agent': API_UA['User-Agent']}),
            timeout=30).read()
        dest.write_bytes(data)
        time.sleep(0.15)
        return f'assets/thumbs/{dest.name}'
    except Exception:
        return None


def refresh_snapshot(period: str = '7', limit: int = 20, with_art: bool = True):
    html = fetch_tiles_html(period=period)
    decks = parse_tiles(html, limit=limit)
    if not decks:
        raise RuntimeError('no decks parsed from metagame response')
    if with_art:
        for d in decks:
            rep = REP_CARD.get(d['name']) or (d['key_cards'][0] if d['key_cards'] else None)
            if rep:
                d['thumb'] = fetch_art(d['name'], rep)
    today = date.today().isoformat()
    snap = {
        'source': 'https://www.mtggoldfish.com/metagame/modern#paper',
        'snapshot_date': today,
        'timeframe': f'{period} days',
        'timeframes_checked': [f'{period} days'],
        'note': (f'Pulled from the {period}-day view (period={period}). '
                 'Short windows have small samples, so shares move week to week.'),
        'decks': decks,
    }
    from paths import write_json_atomic, prune_snapshots
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(METAGAME_FILE, snap)
    fname = SNAP_DIR / f'metagame_{today}_{period}days.json'
    i = 2
    while fname.exists():
        fname = SNAP_DIR / f'metagame_{today}_{period}days_{i}.json'
        i += 1
    write_json_atomic(fname, snap)
    prune_snapshots()
    return snap, fname


def main(argv):
    period = '7'
    limit = 20
    for i, a in enumerate(argv):
        if a == '--period' and i + 1 < len(argv):
            period = argv[i + 1]
        if a == '--limit' and i + 1 < len(argv):
            limit = int(argv[i + 1])
    snap, fname = refresh_snapshot(period=period, limit=limit)
    print(f'saved {len(snap["decks"])} decks ({snap["timeframe"]}, {snap["snapshot_date"]}) -> {fname.name}')
    for d in snap['decks']:
        print(f"  {d['meta_pct']}% ({d['deck_count']}) | {d['name']}")


if __name__ == '__main__':
    main(sys.argv[1:])
