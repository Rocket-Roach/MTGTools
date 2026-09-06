"""Invariants on the real repo data files (catches corrupt/partial data)."""
import json
import os
import unittest
from pathlib import Path

import bootstrap  # noqa: F401

REPO = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = REPO / "data"


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


class DecklistsTest(unittest.TestCase):
    def test_twenty_decks_sixty_fifteen(self):
        dl = load("decklists.json")
        self.assertEqual(len(dl), 20)
        for name, e in dl.items():
            ms = sum(c["qty"] for c in e["mainboard"])
            ss = sum(c["qty"] for c in e["sideboard"])
            self.assertEqual((ms, ss), (60, 15), name)
            self.assertTrue(e.get("source_url", "").startswith("https://"))


class MetagameTest(unittest.TestCase):
    def test_top_twenty_shape(self):
        meta = load("modern_metagame.json")
        self.assertEqual(len(meta["decks"]), 20)
        for d in meta["decks"]:
            for key in ("name", "meta_pct", "deck_count", "paper_price",
                        "mtgo_tix", "key_cards", "url", "archetype"):
                self.assertIn(key, d, (d.get("name"), key))
        pcts = [d["meta_pct"] for d in meta["decks"]]
        self.assertEqual(pcts, sorted(pcts, reverse=True))

    def test_thumbs_exist(self):
        meta = load("modern_metagame.json")
        for d in meta["decks"]:
            thumb = d.get("thumb")
            if thumb:
                self.assertTrue((REPO / thumb).exists(), thumb)


class SnapshotsTest(unittest.TestCase):
    def test_history_parses(self):
        files = sorted((DATA / "snapshots").glob("metagame_*.json"))
        self.assertGreaterEqual(len(files), 1)
        for f in files:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertTrue(data.get("decks"))
            self.assertIn("snapshot_date", data)


class MatchupsTest(unittest.TestCase):
    def test_mapping_covers_metagame(self):
        meta = load("modern_metagame.json")
        mu = load("matchups.json")
        ours = [d["name"] for d in meta["decks"]]
        for name in ours:
            self.assertIn(name, mu["decks"], name)
        for name, row in mu["decks"].items():
            self.assertGreater(row["matches"], 0)
            self.assertGreaterEqual(row["overall"], 0)


class PricesTest(unittest.TestCase):
    def test_cache_shape(self):
        prices = load("prices.json")
        self.assertTrue(prices)
        bad = [k for k, v in prices.items()
               if not (isinstance(v, dict) and "price" in v and "updated" in v)]
        self.assertEqual(bad, [])

    def test_mana_shape(self):
        mana = load("mana_costs.json")
        self.assertTrue(mana)
        for k, v in list(mana.items())[:50]:
            self.assertIn("pips", v, k)
            self.assertIsInstance(v["pips"], dict)


if __name__ == '__main__':
    unittest.main()
