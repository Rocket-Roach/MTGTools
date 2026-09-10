"""Hypergeometric odds + hand-simulator helpers (pure, no GUI)."""
import random
import unittest

import bootstrap  # noqa: F401
from tracker_gui import (hypergeom_pmf, hypergeom_dist, hypergeom_at_least,
                         hypergeom_at_most, build_sim_pool, draw_cards,
                         sim_land_count, wilson_ci, hypergeom_targets)


class HypergeomTest(unittest.TestCase):
    def test_classic_opener(self):
        # 4-of in a 60-card deck, opening 7: the textbook ~40%.
        self.assertAlmostEqual(hypergeom_at_least(60, 4, 7, 1), 0.3995, places=3)

    def test_distribution_sums_to_one(self):
        total = sum(p for _, p in hypergeom_dist(60, 4, 7))
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_complementary_identities(self):
        # P(>=k) + P(<=k-1) == 1, and exactly-k matches the distribution row.
        self.assertAlmostEqual(
            hypergeom_at_least(60, 10, 7, 3) + hypergeom_at_most(60, 10, 7, 2),
            1.0, places=9)
        row = dict(hypergeom_dist(99, 4, 12))
        self.assertAlmostEqual(row[2], hypergeom_pmf(99, 4, 12, 2), places=12)

    def test_edge_cases(self):
        self.assertEqual(hypergeom_pmf(60, 4, 7, 0) + hypergeom_at_least(60, 4, 7, 1), 1.0)
        self.assertEqual(hypergeom_at_least(60, 0, 7, 1), 0.0)   # no successes exist
        self.assertEqual(hypergeom_at_most(60, 60, 7, 6), 0.0)   # all 7 must hit
        self.assertEqual(hypergeom_at_most(60, 60, 7, 7), 1.0)

    def test_invalid_never_raises(self):
        self.assertEqual(hypergeom_pmf(60, 61, 7, 1), 0.0)   # K > N
        self.assertEqual(hypergeom_pmf(60, 4, 61, 1), 0.0)   # n > N
        self.assertEqual(hypergeom_pmf("abc", 4, 7, 1), 0.0)
        self.assertEqual(hypergeom_at_least(60, 4, 7, "x"), 0.0)
        self.assertEqual(hypergeom_dist(60, -1, 7), [])


class SimPoolTest(unittest.TestCase):
    def test_pool_applies_swaps(self):
        lists = {"D": {"mainboard": [{"name": "Bolt", "qty": 4},
                                     {"name": "Mountain", "qty": 10}],
                        "sideboard": [{"name": "Relic", "qty": 2}]}}
        pool = build_sim_pool(lists, {"D": {"Bolt": "Spike"}}, "D")
        self.assertEqual(len(pool), 14)          # mainboard only
        self.assertEqual(pool.count("Spike"), 4)
        self.assertNotIn("Bolt", pool)
        self.assertNotIn("Relic", pool)

    def test_missing_list(self):
        self.assertEqual(build_sim_pool({}, {}, "Nope"), [])

    def test_draw_seeded(self):
        pool = [f"Card {i}" for i in range(60)]
        a = draw_cards(pool, 7, random.Random(0))
        b = draw_cards(pool, 7, random.Random(0))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 7)
        self.assertEqual(len(set(a)), 7)         # no replacement
        self.assertTrue(all(c in pool for c in a))

    def test_land_heuristic(self):
        cache = {"mountain": {"cost": "", "pips": {}},
                 "bolt": {"cost": "{R}", "pips": {"R": 1}}}
        self.assertEqual(sim_land_count(["Mountain", "Mountain", "Bolt"], cache), 2)
        self.assertEqual(sim_land_count([], cache), 0)

    def test_land_exact_via_type_line(self):
        from tracker_gui import is_land
        cache = {"mountain": {"cost": "", "pips": {}, "type_line": "Basic Land — Mountain",
                              "cmc": 0, "colors": []},
                 "living end": {"cost": "", "pips": {}, "type_line": "Sorcery",
                                "cmc": 0, "colors": []},
                 "bolt": {"cost": "{R}", "pips": {"R": 1}, "type_line": "Instant",
                          "cmc": 1, "colors": ["R"]}}
        # Living End is costless but NOT a land — the old heuristic got this wrong.
        self.assertTrue(is_land("Mountain", cache))
        self.assertFalse(is_land("Living End", cache))
        self.assertFalse(is_land("Bolt", cache))
        # legacy fallback: uncached cards count as lands (as v1 always did)
        self.assertTrue(is_land("Never Heard Of It", cache))
        self.assertEqual(sim_land_count(["Mountain", "Living End", "Bolt"], cache), 1)

    def test_mdfc_counts_as_both(self):
        from tracker_gui import is_land, land_spell_faces
        cache = {"boggart trawler": {"cost": "{2}{B}", "pips": {"B": 1},
                                     "type_line": "Creature — Goblin // Land",
                                     "cmc": 3.0, "colors": ["B"]},
                 "mountain": {"cost": "", "pips": {},
                              "type_line": "Basic Land — Mountain",
                              "cmc": 0, "colors": []},
                 "bolt": {"cost": "{R}", "pips": {"R": 1},
                          "type_line": "Instant", "cmc": 1, "colors": ["R"]}}
        self.assertEqual(land_spell_faces("Boggart Trawler", cache), (True, True))
        self.assertEqual(land_spell_faces("Mountain", cache), (True, False))
        self.assertEqual(land_spell_faces("Bolt", cache), (False, True))
        self.assertTrue(is_land("Boggart Trawler", cache))


class WilsonTest(unittest.TestCase):
    def test_typical_matchup(self):
        # 60 of 108 ≈ 55.6%: 95% interval roughly 46–65%.
        lo, hi = wilson_ci(60, 108)
        self.assertAlmostEqual(lo, 0.461, places=2)
        self.assertAlmostEqual(hi, 0.646, places=2)
        self.assertLess(lo, 60 / 108)
        self.assertGreater(hi, 60 / 108)

    def test_edges(self):
        self.assertEqual(wilson_ci(0, 0), (None, None))
        self.assertEqual(wilson_ci(5, 0), (None, None))
        self.assertEqual(wilson_ci("x", 10), (None, None))
        lo, _ = wilson_ci(0, 50)
        self.assertEqual(lo, 0.0)
        _, hi = wilson_ci(50, 50)
        self.assertEqual(hi, 1.0)
        # more matches -> tighter interval
        w1 = wilson_ci(55, 100)
        w2 = wilson_ci(550, 1000)
        self.assertGreater((w1[1] - w1[0]), (w2[1] - w2[0]))


class MakeRecordTest(unittest.TestCase):
    def test_normal_card(self):
        from mana_fetch import make_record
        rec = make_record({"name": "Lightning Bolt", "mana_cost": "{R}",
                           "type_line": "Instant", "cmc": 1.0, "colors": ["R"]})
        self.assertEqual(rec, {"cost": "{R}", "pips": {"R": 1},
                               "type_line": "Instant", "cmc": 1.0,
                               "colors": ["R"]})

    def test_costless_land(self):
        from mana_fetch import make_record
        rec = make_record({"name": "Mountain", "type_line": "Basic Land — Mountain",
                           "cmc": 0.0, "colors": []})
        self.assertEqual(rec["cost"], "")
        self.assertEqual(rec["type_line"], "Basic Land — Mountain")

    def test_mdfc_front_face(self):
        from mana_fetch import make_record
        rec = make_record({"name": "Boggart Trawler // Boggart Bog",
                           "cmc": 3.0, "colors": ["B"],
                           "card_faces": [
                               {"name": "Boggart Trawler", "mana_cost": "{2}{B}",
                                "type_line": "Creature — Goblin"},
                               {"name": "Boggart Bog", "mana_cost": "",
                                "type_line": "Land"}]})
        self.assertEqual(rec["cost"], "{2}{B}")
        self.assertEqual(rec["type_line"], "Creature — Goblin")

    def test_garbage(self):
        from mana_fetch import make_record
        self.assertIsNone(make_record(None))
        self.assertIsNone(make_record("Bolt"))


class HypergeomTargetsTest(unittest.TestCase):
    def test_lands_cards_nonland(self):
        cache = {"mountain": {"cost": "", "pips": {}},
                 "bolt": {"cost": "{R}", "pips": {"R": 1}}}
        pool = ["Mountain"] * 10 + ["Bolt"] * 4
        targets = hypergeom_targets(pool, cache)
        self.assertEqual(targets[0], ("Land — 10 in deck", 10))
        self.assertIn(("10x Mountain", 10), targets)
        self.assertIn(("4x Bolt", 4), targets)
        self.assertEqual(targets[-1], ("Non-land — 4 in deck", 4))
        # copies add up: every pool card counted exactly once in card rows
        card_rows = [c for _, c in targets[1:-1]]
        self.assertEqual(sum(card_rows), len(pool))

    def test_empty(self):
        self.assertEqual(hypergeom_targets([], {}), [])

    def test_mdfc_in_both_buckets(self):
        cache = {"boggart trawler": {"cost": "{2}{B}", "pips": {"B": 1},
                                     "type_line": "Creature — Goblin // Land",
                                     "cmc": 3.0, "colors": []},
                 "mountain": {"cost": "", "pips": {},
                              "type_line": "Basic Land — Mountain",
                              "cmc": 0, "colors": []},
                 "bolt": {"cost": "{R}", "pips": {"R": 1},
                          "type_line": "Instant", "cmc": 1, "colors": ["R"]}}
        pool = ["Mountain"] * 10 + ["Boggart Trawler"] * 2 + ["Bolt"] * 4
        targets = dict(hypergeom_targets(pool, cache))
        self.assertEqual(targets["Land — 12 in deck (incl. 2 MDFCs)"], 12)
        self.assertEqual(targets["Non-land — 6 in deck"], 6)
        self.assertEqual(targets["2x Boggart Trawler"], 2)


if __name__ == "__main__":
    unittest.main()
