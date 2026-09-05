#!/usr/bin/env python3
"""
Matchup winrates from mtgdecks.net (Modern, last 15 days).

Parses the server-rendered winrate table:
  <tr class="item" data-name=... data-winrate=... data-matches=...>
  <td class="winrate-cell" data-winrate=...> <b>50</b> ... N matches

Our top-20 deck names don't match theirs 1:1, so OURS_TO_THEIRS maps them
(their Esper Reanimator column even uses Goryo's Vengeance art).

Usage:
    python matchup_fetch.py

Writes: data/matchups.json
    {"source":..., "snapshot_date":..., "range":...,
     "mapping": {ours: theirs}, "unmapped": [...],
     "columns": [their opponent names in table order],
     "decks": {ours: {"as": theirs, "overall": pct, "matches": n,
                       "vs": {their_opp: {"winrate": pct, "matches": n}}}}}
Stdlib only.
"""

import html as htmlmod
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

from paths import DATA_DIR

OUT_FILE = DATA_DIR / "matchups.json"
URL = 'https://mtgdecks.net/Modern/winrates/range:last15days'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}

# Our top-20 name -> mtgdecks.net archetype name.
OURS_TO_THEIRS = {
    "Goryo's Vengeance": 'Esper Reanimator',
    'Izzet Prowess': 'Izzet Prowess',
    'Eldrazi Bloodchief Combo': 'Eldrazi Bloodchief Combo',
    'Esper Blink': 'Esper Blink',
    'Affinity': 'Izzet Metalcraft',
    'Ruby Storm': 'Ruby Storm',
    'Mono-Green Eldrazi': 'Eldrazi Ramp',
    'Boros Energy': 'Boros Energy',
    'Living End': 'Living End',
    'Eldrazi Tron': 'Eldrazi Tron',
    'Boros Ponza': 'Boros Wildfire',
    'Dimir Midrange': 'Dimir Frog',
    '4c HollowOne': 'Hollow One',
    'Domain Zoo': 'Domain Aggro',
    'Tron': 'Mono Blue Tron',
    'Amulet Titan': 'Amulet Titan',
    'Neobrand': 'Neoform',
    'Devoted Combo': 'Devoted Druid Combo',
    'Jeskai Energy': 'Jeskai Ocelot',
    'Boros Burn': 'Burn',
}


def _clean(s: str) -> str:
    s = re.sub(r'<img[^>]*>', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return htmlmod.unescape(s).strip()


def _num(s: str) -> int:
    m = re.search(r'([\d,]+)', s or '')
    return int(m.group(1).replace(',', '')) if m else 0


def fetch_html() -> str:
    req = urllib.request.Request(URL, headers=UA)
    return urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'replace')


def parse_table(html: str):
    thead = re.search(r'<thead>(.*?)</thead>', html, re.S).group(1)
    ths = re.findall(r'<th[^>]*>(.*?)</th>', thead, re.S)
    columns = [_clean(t) for t in ths[2:]]  # ths[0] empty, ths[1] == Overall
    rows = {}
    order = []
    for m in re.finditer(r'<tr class="item"[^>]*data-name="([^"]*)"[^>]*data-winrate="([^"]*)"[^>]*data-matches="([^"]*)"[^>]*>(.*?)</tr>', html, re.S):
        raw_name, wr, matches, body = m.groups()
        name = htmlmod.unescape(raw_name)
        cells = re.findall(r'<td class="winrate-cell[^"]*" data-winrate="([^"]*)">(.*?)</td>', body, re.S)
        parsed = []
        for cwr, cbody in cells:
            b = re.search(r'<b>([^<]*)</b>', cbody)
            mn = re.search(r'([\d,]+)\s+matches', cbody)
            try:
                pct = float(cwr)
            except ValueError:
                pct = float(b.group(1)) if b else 0.0
            parsed.append({'winrate': pct, 'matches': _num(mn.group(1)) if mn else 0})
        # cells[0] is the Overall column; the rest align with `columns`
        vs = {}
        for opp, cell in zip(columns, parsed[1:]):
            vs[opp] = cell
        try:
            overall = round(float(wr) * 100, 1)
        except ValueError:
            overall = parsed[0]['winrate'] if parsed else 0.0
        rows[name] = {'overall': overall, 'matches': _num(matches), 'vs': vs}
        order.append(name)
    return columns, rows, order


def build_snapshot():
    html = fetch_html()
    columns, rows, _ = parse_table(html)
    if not rows:
        raise RuntimeError('no matchup rows parsed')
    decks, unmapped = {}, []
    for ours, theirs in OURS_TO_THEIRS.items():
        if not theirs or theirs not in rows:
            unmapped.append(ours)
            continue
        r = rows[theirs]
        decks[ours] = {'as': theirs, 'overall': r['overall'],
                       'matches': r['matches'], 'vs': r['vs']}
    snap = {
        'source': URL,
        'snapshot_date': date.today().isoformat(),
        'range': 'last15days',
        'note': ('Overall winrate + pairwise matrix vs the tracked archetypes. '
                 'Ours-to-theirs name mapping applied where names differ.'),
        'mapping': {k: v for k, v in OURS_TO_THEIRS.items() if v},
        'unmapped': unmapped,
        'columns': columns,
        'decks': decks,
    }
    OUT_FILE.write_text(json.dumps(snap, indent=1), encoding='utf-8')
    return snap


def main(argv):
    snap = build_snapshot()
    print(f"saved {len(snap['decks'])}/{len(OURS_TO_THEIRS)} mapped decks "
          f"({', '.join(snap['unmapped']) or 'none'} unmapped)")
    for name, d in snap['decks'].items():
        print(f"  {name} [{d['as']}]: {d['overall']}% ({d['matches']} matches)")


if __name__ == '__main__':
    main(sys.argv[1:])
