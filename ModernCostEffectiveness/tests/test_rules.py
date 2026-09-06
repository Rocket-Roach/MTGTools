"""Pure display/rule helpers: buildable, prices, pips, colors, labels."""
import unittest

import bootstrap  # noqa: F401
from tracker_gui import (is_buildable, fmt_price, top_symbols, pip_diameters,
                         wr_colors, wr_text_color, short_deck_name, mu_fade)


def _prog(pct, missing, rows, total=75):
    return {'pct': pct, 'missing_value': missing, 'total': total, 'rows': rows}


def _row(short, price):
    return {'short': short, 'price': price}


class BuildableTest(unittest.TestCase):
    def test_complete(self):
        self.assertTrue(is_buildable(_prog(100, 0, [_row(0, 5)])))

    def test_under_threshold(self):
        self.assertTrue(is_buildable(_prog(97, 5, [_row(1, 5)])))

    def test_just_under(self):
        self.assertTrue(is_buildable(_prog(97, 19.99, [_row(1, 19.99)])))

    def test_at_threshold_is_not_less(self):
        self.assertFalse(is_buildable(_prog(97, 20, [_row(1, 20)])))

    def test_over(self):
        self.assertFalse(is_buildable(_prog(90, 25, [_row(1, 25)])))

    def test_unpriced_shortfall_blocks(self):
        self.assertFalse(is_buildable(_prog(90, 5, [_row(2, 0), _row(1, 5)])))

    def test_all_unknown_zero_still_blocked(self):
        self.assertFalse(is_buildable(_prog(90, 0, [_row(2, 0)])))

    def test_empty_prog(self):
        self.assertFalse(is_buildable({'pct': 0, 'missing_value': 0, 'total': 0, 'rows': []}))
        self.assertFalse(is_buildable(None))


class PriceFormatTest(unittest.TestCase):
    def test_dollars(self):
        self.assertEqual(fmt_price(12), '$12')

    def test_cents(self):
        self.assertEqual(fmt_price(0.35), '$0.35')

    def test_unknown(self):
        self.assertEqual(fmt_price(0), '-')
        self.assertEqual(fmt_price(None), '-')


class PipMathTest(unittest.TestCase):
    def test_linear_ratios(self):
        self.assertEqual(pip_diameters([0.5, 0.25, 0.25], 44), [44, 22, 22])

    def test_equal(self):
        self.assertEqual(pip_diameters([1 / 3] * 3, 44), [44, 44, 44])

    def test_floor(self):
        self.assertEqual(pip_diameters([0.9, 0.05, 0.05], 44), [44, 14, 14])

    def test_empty(self):
        self.assertEqual(pip_diameters([], 44), [])

    def test_top_symbols(self):
        self.assertEqual([l for l, _ in top_symbols({'R': 25, 'U': 21, 'W': 20})],
                         ['R', 'U', 'W'])

    def test_top_symbols_tie_break(self):
        syms = top_symbols({'R': 25, 'U': 21, 'W': 20, 'G': 20})
        self.assertEqual([l for l, _ in syms], ['R', 'U', 'G'])

    def test_top_symbols_empty(self):
        self.assertEqual(top_symbols({}), [])
        self.assertEqual(top_symbols(None), [])


class WinrateColorTest(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(wr_colors(None)[0], '#3a3f4a')
        self.assertEqual(wr_colors(0)[0], '#b03a2e')
        self.assertEqual(wr_colors(39.9)[0], '#b03a2e')
        self.assertEqual(wr_colors(40)[0], '#e67e22')
        self.assertEqual(wr_colors(47)[0], '#f1c40f')
        self.assertEqual(wr_colors(53)[0], '#7dc48a')
        self.assertEqual(wr_colors(100)[0], '#239b56')

    def test_text_colors(self):
        self.assertEqual(wr_text_color(None), '#999999')
        self.assertEqual(wr_text_color(40), '#c0392b')
        self.assertEqual(wr_text_color(50), '#7d6608')
        self.assertEqual(wr_text_color(60), '#1e8449')

    def test_short_names(self):
        self.assertEqual(short_deck_name('Tron'), 'Tron')
        self.assertEqual(short_deck_name('Mono-Green Eldrazi'), 'Mono-Green\nEldrazi')
        self.assertTrue(short_deck_name('Supercalifragilistic').count('\n') == 0)

    def test_fade(self):
        faded = mu_fade('#b03a2e')
        self.assertRegex(faded, r'^#[0-9a-f]{6}$')
        self.assertNotEqual(faded, '#b03a2e')
        self.assertEqual(mu_fade('garbage'), 'garbage')


if __name__ == '__main__':
    unittest.main()
