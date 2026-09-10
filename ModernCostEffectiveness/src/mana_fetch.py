#!/usr/bin/env python3
"""
Card data via the Scryfall bulk endpoint: mana cost + colored-pip counts
({2}{G}{G/U} -> G:2, U:1) plus type line, mana value, and colors.

Records look like {'cost', 'pips', 'type_line', 'cmc', 'colors', 'updated'}.
Pips drive color-identity ratios; type_line gives exact land detection
(replacing the old no-mana-cost heuristic); cmc/colors enable curve and
filter features. Cached in data/mana_costs.json so repeat runs only fetch
what's missing. Cost/type data is static per card, so unlike prices these
records never expire — entries are only refetched when fields are absent.

Usage:
    python mana_fetch.py --missing   # every top-20 card lacking card data
    python mana_fetch.py "Lightning Bolt" "Wear // Tear"

Stdlib only. Bulk endpoint takes 75 cards per request.
"""

import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from paths import DATA_DIR, DECKLISTS_FILE

CACHE_FILE = DATA_DIR / "mana_costs.json"

UA = {'User-Agent': 'MTG-Deck-Tracker/1.0 (personal collection tool)',
      'Accept': 'application/json'}
BATCH = 75
DELAY = 0.15


def count_pips(mana_cost: str) -> dict:
    """Colored pips in a cost string. Hybrid/Phyrexian count each color
    ({G/U} -> G+1, U+1). Generic/X/colorless are ignored."""
    pips = {}
    if not mana_cost:
        return pips
    for group in re.findall(r'\{([^}]*)\}', mana_cost):
        for part in group.split('/'):
            part = part.strip()
            if part in ('W', 'U', 'B', 'R', 'G'):
                pips[part] = pips.get(part, 0) + 1
    return pips


def _post_names(names):
    """Bulk rounds. Returns ({lower_name: full Scryfall card}, [unmatched])."""
    found, missing = {}, []
    for i in range(0, len(names), BATCH):
        chunk = names[i:i + BATCH]
        payload = json.dumps({'identifiers': [{'name': n} for n in chunk]}).encode()
        req = urllib.request.Request(
            'https://api.scryfall.com/cards/collection', data=payload,
            headers={**UA, 'Content-Type': 'application/json'})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=40))
        except Exception:
            missing.extend(chunk)
            continue
        finally:
            time.sleep(DELAY)
        for card in resp.get('data', []):
            found[card.get('name', '').strip().lower()] = card
        missing.extend(nf.get('name', '') for nf in resp.get('not_found', []))
    return found, missing


def _fuzzy_card(name):
    """Single-card fuzzy lookup (full Scryfall object). None on failure."""
    url = ('https://api.scryfall.com/cards/named?fuzzy='
           + urllib.parse.quote(name.strip(), safe=''))
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=30))
    except Exception:
        return None
    finally:
        time.sleep(DELAY)


def make_record(card):
    """Extract our cached record from a Scryfall card object (or None).

    MDFCs/split cards use the FRONT face for cost (matches how pips are
    counted); type_line stays the full top-level line ('A // B') while
    colors stay the top-level union."""
    if not isinstance(card, dict):
        return None
    cost = card.get('mana_cost')
    type_line = card.get('type_line') or ''
    faces = card.get('card_faces')
    if (not cost or not type_line) and faces:
        cost = cost or faces[0].get('mana_cost')
        type_line = type_line or faces[0].get('type_line', '')
    # Costless cards (lands, Living End) simply have no mana_cost key.
    cost = cost or ''
    try:
        cmc = float(card.get('cmc', 0) or 0)
    except (TypeError, ValueError):
        cmc = 0.0
    colors = card.get('colors') or []
    return {'cost': cost, 'pips': count_pips(cost),
            'type_line': type_line, 'cmc': cmc,
            'colors': [c for c in colors if c in ('W', 'U', 'B', 'R', 'G')]}


def fetch_cards(names):
    """{requested name: Scryfall card object or None} via /cards/collection.

    Split cards ('Wear // Tear') aren't matched whole, so those fall back
    to a first-face query ('Wear'). Anything still missing falls back to
    single fuzzy lookups.
    """
    names = [n for n in names if n]
    found, missing = _post_names(names)
    retry = {}
    for n in missing:
        faces = [p.strip() for p in n.split('//')]
        if len(faces) > 1 and faces[0]:
            retry[n] = faces[0]
    if retry:
        found2, _ = _post_names(list(retry.values()))
        for orig, face in retry.items():
            fl = face.strip().lower()
            for resp_name, card in found2.items():
                if resp_name.split('//')[0].strip() == fl:
                    found[orig.strip().lower()] = card
                    break
    still = [n for n in names if found.get(n.strip().lower()) is None]
    for n in still:
        card = _fuzzy_card(n)
        if card:
            found[n.strip().lower()] = card
    return {n: found.get(n.strip().lower()) for n in names}


def load_cache():
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_cache(cache):
    from paths import write_json_atomic
    write_json_atomic(CACHE_FILE, cache)


def _cached(cache, lname):
    v = cache.get(lname)
    return (isinstance(v, dict) and isinstance(v.get('pips'), dict)
            and 'type_line' in v and 'cmc' in v)


def display_map(limit_decks=20):
    """{lower_name: display_name} for every card in the stored decklists."""
    try:
        dl = json.loads(DECKLISTS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    disp = {}
    for d in list(dl.values())[:limit_decks]:
        for section in ('mainboard', 'sideboard'):
            for ci in d.get(section, []):
                disp.setdefault(ci['name'].strip().lower(), ci['name'])
    return disp


def update_missing(name_map, cache=None, progress_cb=None):
    """Fetch card data for {lower: display} missing from cache.
    Records without type_line/cmc count as missing, so old pip-only
    entries self-heal into full records on the next update.
    Returns (updated, failed_display_names)."""
    if cache is None:
        cache = load_cache()
    todo = [(k, v) for k, v in name_map.items() if not _cached(cache, k)]
    today = date.today().isoformat()
    updated, failed = 0, []
    cards = fetch_cards([disp for _, disp in todo])
    for i, (lname, display) in enumerate(todo):
        rec = make_record(cards.get(display))
        if rec is None:
            failed.append(display)
        else:
            rec['updated'] = today
            cache[lname] = rec
            updated += 1
        if progress_cb:
            progress_cb(i + 1, len(todo), display, rec is not None)
    save_cache(cache)
    return updated, failed


def main(argv):
    if '--missing' in argv:
        disp = display_map()
        cache = load_cache()
        todo = {k: v for k, v in disp.items() if not _cached(cache, k)}
        print(f'{len(todo)} cards missing card data')

        def progress(i, n, display, ok):
            print(f'[{i}/{n}] {display}: {"ok" if ok else "FAILED"}')

        updated, failed = update_missing(todo, cache, progress_cb=progress)
        print(f'done: {updated} fetched, {len(failed)} failed')
        if failed:
            print('failed:', failed)
    else:
        for name in argv:
            if name.startswith('--'):
                continue
            rec = make_record(fetch_cards([name]).get(name))
            print(f'{name}: {rec}')


if __name__ == '__main__':
    main(sys.argv[1:])
