# MTG Modern Deck Tracker

See which of the top 20 Modern metagame decks you can build from your
collection today — and exactly what the rest would cost to finish.

## Quick Start

```bash
# 1. Install the optional helpers (thumbnails, Excel/Word imports)
pip install -r requirements.txt

# 2. Launch the app (or double-click tracker_gui.bat)
python src\tracker_gui.py

# 3. First run: open the Import tab and load your collection,
#    then check the Dashboard to see what you can build.
```

## How Matching Works (read once)

- **Printings don't matter.** `4x Solitude (MH2) #12 *F*` imports as plain
  `4x Solitude`. Set codes, collector numbers, and foiling are ignored
  everywhere, and old printing-split collections merge automatically on load.
- **Double-faced cards match on either side.** `Boggart Trawler // Boggart
  Bog` in your collection satisfies a need for `Boggart Trawler`,
  `Boggart Bog`, or the full name — and mixed spellings merge into one row.
- **A deck is buildable** when you own 100% of its 75 cards, **or** the
  missing cards total **under $20**. Cards with unknown prices never count
  as free: any unpriced shortfall blocks buildable status (which is why
  **Update prices** unlocks more green rows).
- **Deck colors come from mana costs**, not lands: every colored pip in every
  card's cost is counted, weighted by quantity (hybrid/Phyrexian pips credit
  each color; double-faced cards use the front face; lands contribute nothing).

## Tabs

### 1. Dashboard — your 20 decks at a glance

One tile per top-20 deck, sorted with buildable decks first:

- **Symbol strip** — up to three true-artwork mana symbols, sized by pip
  share (50/25/25 renders 2:1:1).
- **Art, name, and meta line** (e.g. `10.2% of the Modern metagame`),
  plus an archetype chip (Aggro / Midrange / Control / Combo / Ramp).
- **`[BUILDABLE]` badge** (or completion %), a progress bar, `owned/total`
  cards, and `$X to finish` (or `Complete!`).

Click any tile to open its **deck window**: a 60-card Mainboard plus a
15-card Sideboard with Need / Have / Missing / $ each columns (green = owned,
red = missing, blue = swapped). From here you can:

| Button | What it does |
|---|---|
| Copy shopping list to clipboard | Copies every missing card as `4x Name` lines |
| Replace selected card… | Swaps one row for a card you own (see below) |
| Revert swaps | Restores this deck to its sample list |
| Close | Closes the window |

**Replacing a card:** select any row → Replace → type in the search box to
live-filter your collection (most-owned first) → pick a card → Replace (or
double-click). The swap keeps the same quantity (3x Scalding Tarn becomes 3x
Flooded Strand), is tagged `(replaces …)`, and immediately updates progress,
shopping lists, and tiles. Swaps are saved per deck in
`data/deck_overrides.json`. A **live 7-day refresh always reverts all swaps**,
since the underlying sample lists may have changed.

### 2. Collection — what you own

- One row per card (`Qty` + `Card`); the header shows
  `shown | unique | total`.
- **Search** filters as you type. **Add card** opens a name + quantity
  dialog. **Remove selected** deletes rows. **Clear collection** wipes it
  (with confirmation).
- Every change saves instantly to `data/my_collection.json`, shared with
  the CLI.

### 3. Import — get cards in

Three ways in, one collection out:

| Method | Details |
|---|---|
| Upload file… | `.txt` `.dek` `.csv` `.xlsx` `.docx` `.doc` file picker (Excel needs openpyxl, Word needs python-docx) |
| Paste box + Import text below | One card per line, e.g. `4 Lightning Bolt` |
| Paste from system clipboard | Imports whatever card list is on your clipboard |

- Imports **add** by default. Tick **Replace mode** to wipe the cached
  collection first (with a count confirmation) — use this when re-uploading
  a full export so quantities don't double up.
- **Clear cached collection** is also here for convenience.

### 4. Metagame — the top 20, row by row

A fixed header labels every column
(**Deck · Archetype · Paper / MTGO · Have / To finish · Colors**); rows show,
per deck: thumbnail, title with meta share and deck count, key cards, a
detail line (`Collection 56/75 (74.7%) · $175 to finish · Keys 2/3 owned`),
archetype chip, paper $ + MTGO tix, your Have % + $ left, and the top-three
scaled mana symbols.

- Row colors: **green** = buildable, cream = partial, white = untouched.
- Click a row to highlight it; **double-click** (or **Open selected deck**)
  opens its MTGGoldfish archetype page.

| Button | What it does |
|---|---|
| Open MTGGoldfish metagame | Opens the live metagame page in your browser |
| Open selected deck | Opens the selected row's archetype page |
| Reload snapshot | Re-reads files from disk |
| Refresh live 7-day | Re-pulls MTGGoldfish + art (~30–60s); **reverts all card swaps**; auto-saves a snapshot |
| Save snapshot | Archives the current view into `data/snapshots/` |
| Update prices | Scrapes cheapest-printing prices (Scryfall) for cards you still need (~1 min, threaded, cached in `prices.json`) |
| Update mana data | Fetches mana costs for pip/color data (Scryfall bulk lookup, cached in `mana_costs.json`) |

### 5. Statistics — the shape of the format

- **Summary line**: snapshot date/window, decks tracked, average paper price,
  buildable count.
- **Pie chart**: meta share of the top 8 decks plus the rest.
- **Bar chart**: top 10 by META% (blue bar = buildable), each with its
  overall winrate appended in red/gold/green.
- **Archetype bar**: meta share stacked by archetype with a legend.
- **Trending table**: newest two snapshots compared
  (UP / DOWN / NEW / OUT / FLAT with point changes). Needs 2+ files in
  `data/snapshots/`.

### 6. Matchups — who beats whom

A 20×20 winrate matrix from mtgdecks.net (last 15 days), color-coded
red → yellow → green, with a separate bold **Overall** column and grey
mirror diagonal. **Hover any cell** for the exact winrate and sample size
(e.g. `Izzet Prowess 44% vs Goryo's Vengeance (108 matches)`). Cells grow
with the window; scrollbars cover the rest.

Deck names differ between sources, so a curated mapping translates them —
all 20 resolve (Goryo's→Esper Reanimator, Neobrand→Neoform, Tron→Mono Blue
Tron, Affinity→Izzet Metalcraft, Eldrazi→Eldrazi Bloodchief Combo, plus
direct matches). **Refresh matchups** re-pulls the matrix
(CLI: `python src\matchup_fetch.py`).

## Files & Data

```
ModernCostEffectiveness/
  tracker_gui.bat      double-click to launch the app
  tracker.bat          double-click (or pass args) for the CLI
  README.md / requirements.txt
  src/                 tracker.py (models, parsers, CLI)
                       tracker_gui.py (the app)
                       paths.py (central file locations)
                       snapshot_fetch.py / fetch_decklists.py
                       price_fetch.py / matchup_fetch.py / mana_fetch.py
  data/                modern_metagame.json (top-20 meta stats)
                       decklists.json (60+15 sample list per deck)
                       prices.json (cheapest-printing price cache)
                       mana_costs.json (mana-cost pip cache)
                       matchups.json (winrate matrix + name mapping)
                       deck_overrides.json (your card swaps)
                       my_collection.json (your cards — backed up by OneDrive)
                       esper_blink_gateway_plan.json (price reference + CLI)
                       snapshots/ (dated metagame history)
  assets/thumbs/       deck art thumbnails
  assets/fonts/        bundled mana-symbol font
```

Card-content identity rules (`DECK_IDENTITY_RULES` in `snapshot_fetch.py`)
reclassify lists no matter what a source calls them — e.g. any list holding
Blade of the Bloodchief + Basking Broodscale is Eldrazi Bloodchief Combo —
enforced by `fetch_decklists.py` on every refresh.

## Command Line

`python src\tracker.py [command]` (or `tracker.bat [command]`):

| Command | Description |
|---|---|
| *(none)* | Interactive mode |
| `progress [filter]` | Show progress for all phases (filter by phase name) |
| `missing [filter]` | List missing cards with prices |
| `collection [search]` | Browse your collection |
| `import <file>` | Import cards from file |
| `clipboard` | Import from clipboard |

Fetch scripts (also behind GUI buttons):

| Script | Purpose |
|---|---|
| `python src\snapshot_fetch.py [--period 7] [--limit 20]` | Refresh the metagame snapshot + art |
| `python src\fetch_decklists.py [--limit 20]` | Refresh the 60+15 sample lists |
| `python src\price_fetch.py --missing` | Price cards you still need |
| `python src\mana_fetch.py --missing` | Fetch mana-cost pip data |
| `python src\matchup_fetch.py` | Refresh the winrate matrix |

Example CLI workflow:

```bash
python src\tracker.py import my_cards.csv
python src\tracker.py progress
python src\tracker.py missing "Phase 1"
```

(The CLI still tracks the original Esper Blink upgrade plan in phases —
see `Phases Tracked` below. The GUI tracks the top 20 directly.)

### Import formats

| Format | Extension | Notes |
|---|---|---|
| Plain text / deck list | `.txt` | One card per line |
| MTG Arena / MTGO | `.dek` | Standard deck format |
| Spreadsheet | `.csv` | Name + Quantity columns used (Set/Foil columns ignored) |
| Excel | `.xlsx` | Name + Quantity auto-detected (needs openpyxl) |
| Word | `.docx` | Tables and paragraphs (needs python-docx) |
| Clipboard | — | Copy any list, run `clipboard` |

Card lines: `4 Lightning Bolt`, `Lightning Bolt` (=1x),
`4x Solitude (MH2) 12 *F*` (=4x Solitude — set info ignored).

### Phases tracked (CLI)

1. Core Manabase → 2. Core Staples → 3. Esper Blink → 4a. Goryo's Vengeance →
   4b. Esper Reanimator → 4c. Dimir Midrange → 4d. Domain Zoo →
   4e. Azorius Blink → 4f. Jeskai Blink

## Requirements

- Python 3.8+
- `pip install -r requirements.txt` for thumbnails (Pillow), Excel (openpyxl),
  Word (python-docx), and clipboard (pyperclip) support
