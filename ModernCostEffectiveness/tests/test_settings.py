"""Settings persistence + theme palette/font contracts (no Tk root needed)."""
import os
import tempfile
import unittest
from pathlib import Path

import bootstrap  # noqa: F401
import settings
import theme


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "settings.json"

    def test_defaults_when_missing(self):
        self.assertEqual(settings.load(self.path), settings.DEFAULTS)

    def test_roundtrip(self):
        data = dict(settings.DEFAULTS)
        data.update({"theme": "dark", "font_scale": 1.2, "last_tab": "Matchups",
                     "auto_refresh_days": 0})
        settings.save(data, self.path)
        self.assertEqual(settings.load(self.path), data)

    def test_unknown_keys_dropped_bad_values_clamped(self):
        settings.save({"theme": "neon", "font_scale": 9.9,
                       "auto_refresh_days": "soon", "bogus": 1}, self.path)
        loaded = settings.load(self.path)
        self.assertEqual(loaded["theme"], "light")
        self.assertEqual(loaded["font_scale"], settings.MAX_SCALE)
        self.assertEqual(loaded["auto_refresh_days"], 7)
        self.assertNotIn("bogus", loaded)

    def test_corrupt_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(settings.load(self.path), settings.DEFAULTS)


class ThemePaletteTest(unittest.TestCase):
    def test_same_keys_both_themes(self):
        self.assertEqual(set(theme.THEMES["light"]), set(theme.THEMES["dark"]))

    def test_set_theme_validation(self):
        self.assertEqual(theme.set_theme("dark"), "dark")
        self.assertEqual(theme.current(), "dark")
        self.assertEqual(theme.set_theme("neon"), "light")
        self.assertEqual(theme.C("bg"), theme.THEMES["light"]["bg"])
        theme.set_theme("light")

    def test_tag_maps_cover_tree_tags(self):
        for tag in ("done", "need", "section", "swapped"):
            self.assertIn(theme.DECK_TAGS[tag], theme.THEMES["light"])
        for tag in ("up", "down", "new", "out", "flat"):
            self.assertIn(theme.TREND_TAGS[tag], theme.THEMES["light"])

    def test_scale_clamps(self):
        self.assertEqual(theme.set_scale(9.9), 1.3)
        self.assertEqual(theme.set_scale(0.01), 0.8)
        self.assertEqual(theme.get_scale(), 0.8)
        theme.set_scale(1.0)


if __name__ == '__main__':
    unittest.main()
