#!/usr/bin/env python3
"""
Fetch one representative MTGGoldfish decklist per deck in modern_metagame.json.

For each archetype page, takes the first linked /deck/<id> list and parses the
embedded deck_input blob ("4 Name ... sideboard 3 Name ..."). Quantities for
the same card name are summed (separate printings), then capped to the top 60
maindeck copies and top 15 sideboard copies.

Usage:
    python fetch_decklists.py [--limit 20]

Writes: decklists.json
    {"<Deck>": {"source_deck_id": 123, "source_url": "...",
                "mainboard": [{"name": ..., "qty": ...}],
                "sideboard": [{"name": ..., "qty": ...}]}}

Stdlib only. Be nice: ~4s pacing between requests.
"""

import html as htmlmod
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request

from paths import METAGAME_FILE as META_FILE, DECKLISTS_FILE as OUT_FILE

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9'}
PACE = 4


def new_session():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(urllib.request.Request('https://www.mtggoldfish.com/metagame/modern/full', headers=UA),
            timeout=40).read()
    return op


def get(op, url, referer, tries=4):
    last = None
    for a in range(tries):
        time.sleep(PACE)
        try:
            return op.open(urllib.request.Request(url, headers={**UA, 'Referer': referer}),
                           timeout=40).read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            last = e
            time.sleep(5 * (a + 1))
    raise RuntimeError(f'GET failed {url} (HTTP {last.code if last else "?"})')


def parse_blob(blob):
    """Embedded deck text -> (main [(qty,name)], side [(qty,name)]), aggregated."""
    blob = re.sub(r'\s+', ' ', htmlmod.unescape(blob)).strip()
    parts = re.split(r'\bsideboard\b', blob, maxsplit=1, flags=re.I)
    main_txt = parts[0]
    side_txt = parts[1] if len(parts) > 1 else ''

    def section(txt):
        out, qty, cur = [], None, []
        for tok in txt.split():
            if re.fullmatch(r'\d+', tok) and qty is None and not cur:
                qty = int(tok)
            elif re.fullmatch(r'\d+', tok) and cur:
                out.append((qty, ' '.join(cur)))
                qty, cur = int(tok), []
            else:
                if qty is None:
                    continue
                cur.append(tok)
        if qty is not None and cur:
            out.append((qty, ' '.join(cur)))
        agg = {}
        for q, n in out:
            agg[n] = agg.get(n, 0) + q
        return [(q, n) for n, q in agg.items()]

    def cap(entries, total, label):
        got, kept = 0, []
        for q, n in entries:
            if got >= total:
                print(f'    WARNING: {label} exceeds {total}, truncating')
                break
            take = min(q, total - got)
            kept.append((take, n))
            got += take
        return kept

    main = cap(section(main_txt), 60, 'mainboard')
    side = cap(section(side_txt), 15, 'sideboard')
    return main, side


def _rename_metagame_deck(old: str, new: str):
    """Keep modern_metagame.json in sync when a list is reclassified."""
    try:
        data = json.loads(META_FILE.read_text(encoding='utf-8'))
    except Exception:
        return
    for d in data.get('decks', []):
        if d.get('name') == old:
            d['name'] = new
    META_FILE.write_text(json.dumps(data, indent=1), encoding='utf-8')


def fetch_all(limit=20):
    meta = json.loads(META_FILE.read_text(encoding='utf-8'))
    decks = meta['decks'][:limit]
    try:
        existing = json.loads(OUT_FILE.read_text(encoding='utf-8'))
    except Exception:
        existing = {}
    op = new_session()
    for d in decks:
        name = d['name']
        arch = d['url'].split('#')[0]
        try:
            html = get(op, arch, 'https://www.mtggoldfish.com/metagame/modern/full')
            links = re.findall(r'href="(/deck/(\d+))"', html)
            if not links:
                print(f'{name}: no deck links found')
                continue
            deck_id = links[0][1]
            time.sleep(1)
            dh = get(op, f'https://www.mtggoldfish.com/deck/{deck_id}', arch)
            m = re.search(r'name="deck_input\[deck\]"[^>]*value="(.*?)"', dh, re.S)
            if not m:
                print(f'{name}: deck_input blob not found (deck {deck_id})')
                continue
            main, side = parse_blob(m.group(1))
            ms, ss = sum(q for q, _ in main), sum(q for q, _ in side)
            flag = '' if (ms, ss) == (60, 15) else '  <-- CHECK'
            print(f'{name}: {ms} main / {ss} side (deck {deck_id}){flag}')
            entry = {
                'source_deck_id': int(deck_id),
                'source_url': f'https://www.mtggoldfish.com/deck/{deck_id}',
                'mainboard': [{'name': n, 'qty': q} for q, n in main],
                'sideboard': [{'name': n, 'qty': q} for q, n in side],
            }
            # Card-content identity rules can rename the deck (e.g. a list
            # holding Blade of the Bloodchief + Basking Broodscale is
            # Eldrazi Bloodchief Combo whatever the site called it).
            from snapshot_fetch import canonical_deck_name
            canon = canonical_deck_name(
                name, [n for _, n in main] + [n for _, n in side])
            if canon != name:
                if canon in existing and canon != name:
                    print(f'  WARNING: canonical name {canon!r} collides; keeping {name!r}')
                else:
                    print(f'  renamed to {canon!r} by identity rule')
                    _rename_metagame_deck(name, canon)
                    if name in existing:
                        del existing[name]
                    name = canon
            existing[name] = entry
            OUT_FILE.write_text(json.dumps(existing, indent=1), encoding='utf-8')
        except Exception as e:
            print(f'{name}: FAILED {e}')
    print(f'done: {len(existing)} decklists in {OUT_FILE.name}')
    return existing


def main(argv):
    limit = 20
    for i, a in enumerate(argv):
        if a == '--limit' and i + 1 < len(argv):
            limit = int(argv[i + 1])
    fetch_all(limit=limit)


if __name__ == '__main__':
    main(sys.argv[1:])
