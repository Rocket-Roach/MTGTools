# Agent Context — MTG Modern Deck Tracker (resume prompt)

Paste everything below the line into a fresh session to pick up this project.

---

You are resuming work on the **MTG Modern Deck Tracker**, a Python/tkinter
desktop app (Windows, Python 3.10, run from source, no build step).

## 1. Where things are

```
C:\Users\Orion\OneDrive\MTGApps\ModernCostEffectiveness\
  tracker_gui.bat / tracker.bat   launchers (GUI uses `py -3`→`python` fallback, always pauses)
  README.md / requirements.txt / CONTEXT.md (this file)
  src/      tracker.py            Card/Collection/FileParser + phase-based CLI
            tracker_gui.py        the app (~1800 lines, 6 tabs)
            paths.py              ALL file locations live here — use it, never hardcode
            snapshot_fetch.py     metagame pull + ARCHETYPE/LAND_COLORS/color math + DECK_IDENTITY_RULES
            fetch_decklists.py    60+15 sample lists per deck (enforces identity rules)
            price_fetch.py        Scryfall cheapest-printing prices
            mana_fetch.py         Scryfall bulk mana costs → pip counts
            matchup_fetch.py      mtgdecks.net winrate matrix + ours→theirs mapping
  data/     modern_metagame.json (top-20 stats) / decklists.json (60+15 each)
            my_collection.json (USER DATA — never delete/recreate, only move carefully)
            prices.json / mana_costs.json / matchups.json / deck_overrides.json
            esper_blink_gateway_plan.json (legacy price ref + CLI phases only)
            snapshots/ (metagame_2026-09-04_30days.json, metagame_2026-09-05_7days.json)
  assets/thumbs/ (*-art.jpg deck art) / assets/fonts/mana.ttf (symbol font)
```

First step in any session: `ls` root + `python -m py_compile src/*.py`-equivalent,
then launch via `tracker_gui.bat` flow or instantiate `TrackerGUI()` headless
with `after(500, destroy)` + `mainloop()`.

## 2. Core semantics (do not break these)

- **Matching is name-normalized, printing-agnostic, face-aware.**
  `Card.key()` = stripped lowercase name. `card_faces()` splits `//` names.
  `Collection` keeps a face→entry index (`_face_index`/`_face_qty`); ALL
  mutation MUST go through `add()/remove()/pop()/clear()` or the index rots
  (GUI `remove_selected` uses `pop()` — never `del cards[k]`).
  `get_quantity()` counts entries sharing any face, once each.
- **Buildable** = 100% owned OR missing total < $20 (`BUILDABLE_USD`,
  `is_buildable()`), with a guard: any *unpriced* shortfall blocks it.
- **Colors** = qty-weighted colored pips from cached mana costs (hybrid/
  Phyrexian credit each color; MDFCs use front face; lands contribute nothing).
  `dominant_color()` / `top_symbols()` / `pip_diameters()` are shared helpers.
- **Deck identity**: `DECK_IDENTITY_RULES` reclassifies lists by card content
  (e.g. Blade of the Bloodchief + Basking Broodscale → Eldrazi Bloodchief
  Combo); `fetch_decklists.py` renames keys AND patches modern_metagame.json.
- **Swaps** (`deck_overrides.json`, per-deck orig→replacement, same qty) flow
  through progress/shopping/tiles; **any live 7-day refresh clears them**.
- **Prices** = cheapest printing (Scryfall), scraped only for cards the user
  still needs; cached in prices.json. Mana pips cached in mana_costs.json.

## 3. Scraping knowledge (hard-won, re-read before touching fetchers)

- MTGGoldfish metagame page **defaults to 30-day**. 7-day requires POSTing
  `/metagame/re_sort` with a session cookie jar + the CSRF token from THAT
  form + a cache-buster query (cached pages carry stale tokens → HTTP 422).
- Archetype pages 500 without an established session (visit metagame first).
  Deck lists come from the first `/deck/<id>` link's hidden `deck_input[deck]`
  field: `"4 Name … sideboard 3 Name …"` (normalize whitespace first, split on
  `\bsideboard\b`, aggregate split printings, verify 60/15 totals).
  Pace requests ≥4s or you will get throttled (transient 500s).
- Scryfall demands **both** `User-Agent` **and** `Accept` headers (else 400).
  Bulk `/cards/collection` takes 75/request. Full `"A // B"` names do NOT
  match — retry with the first face. MDFCs have top-level `mana_cost=None`;
  use `card_faces[0]` or the fuzzy endpoint as fallback.
- Mana-font glyph codepoints were parsed from the Mana project's mana.css:
  W=U+E600 U=U+E601 B=U+E602 R=U+E603 G=U+E604 C=U+E904.
- mtgdecks.net winrate table is server-rendered (`table#winrates`,
  `tr.item[data-name/data-winrate/data-matches]`, `td.winrate-cell`).
  Name mapping lives in `matchup_fetch.OURS_TO_THEIRS` (currently 20/20;
  Goryo's→Esper Reanimator, Neobrand→Neoform, Tron→Mono Blue Tron,
  Affinity→Izzet Metalcraft, Eldrazi→Eldrazi Bloodchief Combo).

## 4. Tkinter gotchas learned the painful way

- `pack_propagate(False)` and `grid_propagate(False)` are SEPARATE — match
  the call to the children's geometry manager. Fixed-size frames ALSO need
  explicit height (or `sticky="ns"`) + centered content, or content clips to
  slivers/dots. Always assert rendered heights, not just widths/text.
- Treeview cells are text-only → image columns require custom canvas/frame
  rows (see Metagame tab).
- `PhotoImage` needs a live Tk root AND a held reference; separate `Tk()`
  instances don't share images (kept smoke tests to one root).
- Canvas image scrolling: 1-unit wheel steps + settle repaint
  (`update_idletasks` + guarded full `update()`) on drag-release and
  debounced wheel-idle. Shared helpers: `_smooth_scroll`, `_settle_canvas*`.
- Header/row column alignment: identical pixel minsize grids both sides +
  header width-locked to canvas width in `<Configure>`.
- `messagebox` blocks headless tests → monkeypatch it in smoke scripts.
- Windows console is cp1252: never print unicode in scripts (use repr or
  codepoints). PowerShell: no `&&`, quote carefully, prefer script files
  over `-c` one-liners, use `cmd /c` for .bat files.

## 5. How to verify (keep this green)

- `python -m py_compile src/*.py`
- Launch `TrackerGUI()` headless: 6 tabs, 20 dashboard tiles, 20 metagame
  rows, trend rows, matrix 881 canvas items; open/close a deck popup
  (Mainboard 60 / Sideboard 15); exercise hover/selection paths.
- Spot-check numbers against `data/*.json`, never eyeball alone.
- Scratch verification scripts live in `$env:TEMP\opencode` (deletable).

## 6. Last known state (2026-09-05)

- Snapshot: 7-day top 20 (Goryo's #1 10.2%). Collection 1357 unique rows,
  3/20 buildable. Prices 178/179 (Twilight Mire failed once — self-heals).
  Mana 356/356. Matchups 20/20.
- Known cosmetic risks (user's machine only): circled mana glyphs were
  removed in favor of image pips; if any symbol ever shows tofu, say so.
- If dashboard scroll trails persist *after* stopping a drag, it's
  driver-level — next step would be tile pagination, not more repaint hacks.
