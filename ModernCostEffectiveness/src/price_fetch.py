#!/usr/bin/env python3
"""
Cheapest-printing paper prices via the Scryfall API.

For each card name, searches all printings (unique=prints, paginated) and
keeps the lowest non-null USD paper price (foil only as a fallback).
Results are cached in prices.json so repeat runs only fetch what's missing.

Usage:
    python price_fetch.py "Lightning Bolt" "Wear // Tear"
    python price_fetch.py --missing   # price every top-20 card lacking one

Stdlib only. Be nice: ~0.12s pacing between requests.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from paths import DECKLISTS_FILE, PRICES_FILE, COLLECTION_FILE, PLAN_FILE

CACHE_FILE = PRICES_FILE

UA = {'User-Agent': 'MTG-Deck-Tracker/1.0 (personal collection tool)',
      'Accept': 'application/json'}
DELAY = 0.12
MAX_PAGES = 5


def cheapest_usd(card_name: str):
    """Lowest USD paper price across all printings, or None."""
    query = '!\"' + card_name.strip() + '\"'
    url = ('https://api.scryfall.com/cards/search?q='
           + urllib.parse.quote(query, safe='') + '&unique=prints')
    best = None
    pages = 0
    try:
        while url and pages < MAX_PAGES:
            pages += 1
            info = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30))
            for printing in info.get('data', []):
                prices = printing.get('prices', {}) or {}
                usd = prices.get('usd')
                if usd is not None:
                    try:
                        p = float(usd)
                    except (TypeError, ValueError):
                        continue
                    if best is None or p < best:
                        best = p
            url = info.get('next_page') if info.get('has_more') else None
            if url:
                time.sleep(DELAY)
        if best is None:
            # Foil-only fallback (e.g. promo-only cards).
            best = _cheapest_foil_fallback(card_name)
        return best
    except Exception:
        return None


def _cheapest_foil_fallback(card_name: str):
    try:
        query = '!\"' + card_name.strip() + '\"'
        url = ('https://api.scryfall.com/cards/search?q='
               + urllib.parse.quote(query, safe='') + '&unique=prints')
        info = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=30))
        best = None
        for printing in info.get('data', []):
            usd_foil = (printing.get('prices', {}) or {}).get('usd_foil')
            if usd_foil is not None:
                try:
                    p = float(usd_foil)
                except (TypeError, ValueError):
                    continue
                if best is None or p < best:
                    best = p
        return best
    except Exception:
        return None


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
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=1)


def _load_collection():
    """my_collection.json as a face-aware Collection (once), or None."""
    try:
        from tracker import Collection
        path = COLLECTION_FILE
        if path.exists():
            return Collection.from_dict(json.loads(path.read_text(encoding='utf-8')))
    except Exception:
        pass
    return None


def missing_names(limit_decks=20):
    """Unique top-20 card names (lowercased) lacking a price that the
    collection doesn't already cover (owned < most needed copies)."""
    try:
        dl = json.loads(DECKLISTS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []
    try:
        plan = json.loads(PLAN_FILE.read_text(encoding='utf-8'))
    except Exception:
        plan = {'phases': []}
    have = set()
    for ph in plan.get('phases', []):
        for ci in ph.get('cards', []):
            if ci.get('price'):
                have.add(ci['name'].strip().lower())
    cache = load_cache()
    for k, v in cache.items():
        if isinstance(v, dict) and v.get('price'):
            have.add(k)
    need = {}
    for d in list(dl.values())[:limit_decks]:
        for section in ('mainboard', 'sideboard'):
            for ci in d.get(section, []):
                key = ci['name'].strip().lower()
                q = int(ci.get('qty', 1))
                if key not in need or q > need[key]:
                    need[key] = q
    coll = _load_collection()

    def owned(key):
        return coll.get_quantity(key) if coll is not None else 0

    return sorted(k for k, q in need.items()
                  if k not in have and owned(k) < q)


def _cached_price(cache, lname):
    v = cache.get(lname)
    if isinstance(v, dict):
        return v.get('price')
    return v


def update_missing(name_map, cache=None, progress_cb=None, delay=DELAY):
    """Fetch cheapest prices for {lower_name: display_name} missing from cache.

    Returns (updated, failed_display_names). Cache is saved on completion.
    """
    if cache is None:
        cache = load_cache()
    today = date.today().isoformat()
    items = list(name_map.items())
    updated, failed = 0, []
    for i, (lname, display) in enumerate(items):
        if _cached_price(cache, lname):
            if progress_cb:
                progress_cb(i + 1, len(items), display, True)
            continue
        price = cheapest_usd(display)
        cache[lname] = {'price': round(price, 2) if price is not None else None,
                        'updated': today}
        if price is not None:
            updated += 1
        else:
            failed.append(display)
        if progress_cb:
            progress_cb(i + 1, len(items), display, price is not None)
        time.sleep(delay)
    save_cache(cache)
    return updated, failed


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


def main(argv):
    if '--missing' in argv:
        disp = display_map()
        cache = load_cache()
        todo = {k: v for k, v in disp.items() if not _cached_price(cache, k)}
        print(f'{len(todo)} cards missing prices')

        def progress(i, n, display, ok):
            print(f'[{i}/{n}] {display}: {"ok" if ok else "FAILED"}')

        updated, failed = update_missing(todo, cache, progress_cb=progress)
        print(f'done: {updated} priced, {len(failed)} failed')
        if failed:
            print('failed:', failed)
    else:
        for name in argv:
            if name.startswith('--'):
                continue
            print(f'{name}: {cheapest_usd(name)}')


if __name__ == '__main__':
    main(sys.argv[1:])
