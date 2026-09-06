"""GuiStore logic against tiny fixtures (no network, no real files)."""
import json
import tempfile
import unittest
from pathlib import Path

import bootstrap  # noqa: F401
from tracker import Card
from tracker_gui import GuiStore


def make_store():
    tmp = tempfile.mkdtemp()
    plan = {"phases": [], "summary": {}, "metadata": {}}
    plan_file = Path(tmp) / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    s = GuiStore(plan_file, Path(tmp) / "collection.json")
    s.metagame = {"decks": [
        {"name": "Alpha", "meta_pct": 10.0},
        {"name": "Beta", "meta_pct": 5.0},
    ]}
    s.decklists = {
        "Alpha": {"mainboard": [{"name": "Bolt", "qty": 4},
                                {"name": "Mountain", "qty": 10}],
                  "sideboard": [{"name": "Relic", "qty": 2}]},
        "Beta": {"mainboard": [{"name": "Bolt", "qty": 4},
                               {"name": "Unpriced Gem", "qty": 1}],
                 "sideboard": []},
    }
    s.price_map = {"bolt": 2.0, "mountain": 0.5, "relic": 3.0}
    return s


class ProgressTest(unittest.TestCase):
    def test_empty_collection(self):
        s = make_store()
        decks = s.deck_progress_list()
        self.assertEqual(len(decks), 2)
        self.assertTrue(all(d["pct"] == 0 for d in decks))
        # $20 rule literally applies at 0%: Alpha's whole list costs
        # 4x$2+10x$0.50+2x$3 = $19 < $20, so it counts as buildable.
        by_name = {d["deck"]: d for d in decks}
        self.assertTrue(by_name["Alpha"]["buildable"])
        self.assertFalse(by_name["Beta"]["buildable"])  # unpriced gem blocks

    def test_partial_and_buildable(self):
        s = make_store()
        s.collection.add(Card("Bolt", 4))
        s.collection.add(Card("Mountain", 10))
        s.collection.add(Card("Relic", 2))
        decks = {d["deck"]: d for d in s.deck_progress_list()}
        self.assertTrue(decks["Alpha"]["buildable"])
        self.assertFalse(decks["Beta"]["buildable"])  # Unpriced Gem missing
        self.assertEqual(decks["Alpha"]["owned"], decks["Alpha"]["total"])

    def test_overall_counts(self):
        s = make_store()
        s.collection.add(Card("Bolt", 4))
        ov = s.overall()
        # Bolt appears in both decks but counts once (max 4)
        self.assertEqual(ov["copies_need"], 4 + 10 + 2 + 1)
        self.assertEqual(ov["copies_owned"], 4)

    def test_under_20_rule(self):
        s = make_store()
        s.collection.add(Card("Bolt", 4))
        s.collection.add(Card("Mountain", 10))
        # Alpha missing only 2x Relic @ $3 = $6 -> buildable
        self.assertTrue(next(d for d in s.deck_progress_list()
                             if d["deck"] == "Alpha")["buildable"])


class SwapTest(unittest.TestCase):
    def test_set_chain_revert(self):
        s = make_store()
        s.save_overrides = lambda: None  # keep fixtures off disk
        self.assertTrue(s.set_override("Alpha", "Bolt", "Shock"))
        self.assertFalse(s.set_override("Alpha", "Bolt", "Bolt"))
        prog = s.decklist_progress("Alpha")
        row = next(r for r in prog["rows"] if r.get("orig") == "Bolt")
        self.assertEqual((row["name"], row["need"]), ("Shock", 4))
        self.assertEqual(row["substituted_from"], "Bolt")
        s.set_override("Alpha", "Bolt", "Mountain")
        row = next(r for r in s.decklist_progress("Alpha")["rows"]
                   if r.get("orig") == "Bolt")
        self.assertEqual(row["name"], "Mountain")
        self.assertTrue(s.clear_overrides("Alpha"))
        self.assertFalse(s.clear_overrides("Alpha"))
        row = next(r for r in s.decklist_progress("Alpha")["rows"]
                   if r["name"] == "Bolt")
        self.assertIsNone(row["substituted_from"])


class PriceTTLTest(unittest.TestCase):
    def _aged(self, days):
        import datetime
        return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    def test_stale_detection(self):
        s = make_store()
        s.price_cache = {
            'old': {'price': 5.0, 'updated': self._aged(30)},
            'fresh': {'price': 5.0, 'updated': self._aged(0)},
            'failed': {'price': None, 'updated': self._aged(30)},
        }
        self.assertTrue(s.is_price_stale('old'))
        self.assertFalse(s.is_price_stale('fresh'))
        self.assertFalse(s.is_price_stale('failed'))
        self.assertFalse(s.is_price_stale('missing'))
        self.assertEqual(s.price_age_days('missing'), None)

    def test_stale_counts_as_missing(self):
        s = make_store()
        s.price_map = {'mountain': 0.5}
        s.price_cache = {'mountain': {'price': 0.5, 'updated': self._aged(30)}}
        missing = s.cards_missing_prices()
        self.assertIn('mountain', missing)  # stale -> re-check
        s.price_cache = {'mountain': {'price': 0.5, 'updated': self._aged(0)}}
        self.assertNotIn('mountain', s.cards_missing_prices())


class SnapshotCompareTest(unittest.TestCase):
    def test_statuses_and_order(self):
        s = make_store()
        old = {"decks": [{"name": "A", "meta_pct": 5.0}, {"name": "B", "meta_pct": 5.0},
                         {"name": "Gone", "meta_pct": 3.0}]}
        new = {"decks": [{"name": "A", "meta_pct": 7.0}, {"name": "B", "meta_pct": 5.0},
                         {"name": "New", "meta_pct": 1.0}]}
        rows = {r["name"]: r for r in GuiStore.compare_snapshots(old, new)}
        self.assertEqual(rows["A"]["status"], "UP")
        self.assertEqual(rows["B"]["status"], "FLAT")
        self.assertEqual(rows["New"]["status"], "NEW")
        self.assertEqual(rows["Gone"]["status"], "OUT")


class MatchupStoreTest(unittest.TestCase):
    def test_cell_resolution(self):
        s = make_store()
        # decks keyed by OUR names (as matchup_fetch writes them)
        s.matchups = {"mapping": {"Alpha": "A-Prime", "Beta": "B-Prime"},
                      "decks": {"Alpha": {"overall": 55.0, "matches": 100,
                                          "vs": {"B-Prime": {"winrate": 60, "matches": 20}}}}}
        self.assertEqual(s.matchup_cell("Alpha", "Alpha"), "mirror")
        self.assertEqual(s.matchup_cell("Alpha", "Beta"), {"winrate": 60, "matches": 20})
        self.assertIsNone(s.matchup_cell("Beta", "Alpha"))
        self.assertIsNone(s.matchup_cell("Alpha", "Nobody"))


class ShopTest(unittest.TestCase):
    def test_shared_first_unpriced_last(self):
        s = make_store()
        s.collection.add(Card("Bolt", 4))  # covers Bolt everywhere
        pri = s.shopping_priorities()
        names = [p["name"] for p in pri]
        # Mountain needed by Alpha only; Unpriced Gem by Beta only, unpriced
        self.assertIn("Mountain", names)
        self.assertEqual(names[-1], "Unpriced Gem")
        mtn = next(p for p in pri if p["name"] == "Mountain")
        self.assertEqual((mtn["decks"], mtn["buy"], mtn["total"]), (1, 10, 5.0))

    def test_unlock_order(self):
        s = make_store()
        s.collection.add(Card("Bolt", 4))
        s.collection.add(Card("Mountain", 10))
        s.collection.add(Card("Relic", 2))
        order = s.deck_unlock_order()
        # Alpha buildable ($6 missing) -> excluded; Beta blocked by unpriced gem
        self.assertEqual([u["deck"] for u in order], ["Beta"])
        self.assertTrue(order[0]["unpriced"])


class PathsUtilTest(unittest.TestCase):
    def test_atomic_write_and_backup(self):
        import paths
        tmp = Path(tempfile.mkdtemp())
        target = tmp / "data.json"
        paths.write_json_atomic(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
        self.assertFalse((tmp / "data.json.tmp").exists())
        orig_dir, orig_keep = paths.BACKUPS_DIR, paths.BACKUP_KEEP
        paths.BACKUPS_DIR = tmp / "backups"
        try:
            b1 = paths.backup_file(target, keep=2)
            self.assertTrue(b1 and b1.exists())
            paths.write_json_atomic(target, {"a": 2})
            paths.backup_file(target, keep=2)
            paths.write_json_atomic(target, {"a": 3})
            paths.backup_file(target, keep=2)
            left = sorted(p.name for p in (tmp / "backups").glob("data_*.json"))
            self.assertEqual(len(left), 2)
            self.assertIsNone(paths.backup_file(tmp / "missing.json"))
        finally:
            paths.BACKUPS_DIR = orig_dir

    def test_prune_snapshots(self):
        import paths
        tmp = Path(tempfile.mkdtemp()) / "snaps"
        tmp.mkdir()
        for i in range(12):
            (tmp / f"metagame_2026-09-{i + 1:02d}_7days.json").write_text("{}", encoding="utf-8")
        (tmp / "notes.txt").write_text("keep me", encoding="utf-8")
        orig = paths.SNAPSHOTS_DIR
        paths.SNAPSHOTS_DIR = tmp
        try:
            paths.prune_snapshots(keep=10)
            left = sorted(p.name for p in tmp.glob("metagame_*.json"))
            self.assertEqual(len(left), 10)
            self.assertTrue((tmp / "notes.txt").exists())
            self.assertEqual(left[0], "metagame_2026-09-03_7days.json")
        finally:
            paths.SNAPSHOTS_DIR = orig


if __name__ == '__main__':
    unittest.main()
