"""Line parsers, deck-blob parsing, deck identity rules."""
import unittest

import bootstrap  # noqa: F401
from tracker import FileParser
from fetch_decklists import parse_blob
from snapshot_fetch import canonical_deck_name


def parse(line):
    c = FileParser._parse_line(line)
    return (c.quantity, c.name) if c else None


class LineParserTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse('4 Flooded Strand'), (4, 'Flooded Strand'))

    def test_bare_name(self):
        self.assertEqual(parse('Lightning Bolt'), (1, 'Lightning Bolt'))

    def test_set_foil_stripped(self):
        self.assertEqual(parse('4x Solitude (MH2) #12 *F*'), (4, 'Solitude'))

    def test_sideboard_prefix(self):
        self.assertEqual(parse('SB: 2 Force of Negation'), (2, 'Force of Negation'))

    def test_dfc_intact(self):
        self.assertEqual(parse('2x Boggart Trawler // Boggart Bog (DSK) 152'),
                         (2, 'Boggart Trawler // Boggart Bog'))

    def test_trailing_digits_kept_without_set(self):
        self.assertEqual(parse('1 1996 World Champion'), (1, '1996 World Champion'))

    def test_blank_and_comments(self):
        self.assertIsNone(parse(''))
        self.assertIsNone(parse('   '))
        self.assertIsNone(parse('# comment'))
        self.assertIsNone(parse('// comment'))

    def test_text_block(self):
        cards = FileParser.parse_text('4 Flooded Strand\n\n# hi\nSolitude\n')
        self.assertEqual([(c.quantity, c.name) for c in cards],
                         [(4, 'Flooded Strand'), (1, 'Solitude')])


class BlobParserTest(unittest.TestCase):
    BLOB = ('4 Atraxa, Grand Unifier 3 Quantum Riddler 1 Quantum Riddler '
            '4 Flooded Strand sideboard 3 Wrath of the Skies 2 Mystical Dispute')

    def test_split_and_aggregate(self):
        main, side, dropped = parse_blob(self.BLOB)
        self.assertEqual(sum(q for q, _ in main), 12)
        self.assertEqual(sum(q for q, _ in side), 5)
        self.assertEqual(dropped, [])
        self.assertIn((4, 'Quantum Riddler'), main)  # 3+1 aggregated

    def test_caps_and_records_cuts(self):
        import contextlib
        import io
        blob = ' '.join([f'4 Card{i:02d}' for i in range(20)]) + ' sideboard ' + \
               ' '.join([f'3 SB{i:02d}' for i in range(10)])
        with contextlib.redirect_stdout(io.StringIO()):
            main, side, dropped = parse_blob(blob)
        self.assertEqual(sum(q for q, _ in main), 60)
        self.assertEqual(sum(q for q, _ in side), 15)
        self.assertTrue(dropped)
        self.assertTrue(all('(mainboard)' in d or '(sideboard)' in d for d in dropped))


class IdentityRuleTest(unittest.TestCase):
    BOTH = ['Blade of the Bloodchief', 'Basking Broodscale', 'Forest']

    def test_rule_fires(self):
        self.assertEqual(canonical_deck_name('Eldrazi', self.BOTH),
                         'Eldrazi Bloodchief Combo')

    def test_rule_fires_any_name(self):
        self.assertEqual(canonical_deck_name('Something Else', self.BOTH),
                         'Eldrazi Bloodchief Combo')

    def test_partial_does_not_fire(self):
        self.assertEqual(canonical_deck_name('Eldrazi', ['Basking Broodscale']),
                         'Eldrazi')

    def test_already_canonical(self):
        self.assertEqual(canonical_deck_name('Eldrazi Bloodchief Combo', self.BOTH),
                         'Eldrazi Bloodchief Combo')

    def test_unrelated(self):
        self.assertEqual(canonical_deck_name('Burn', ['Lightning Bolt']), 'Burn')


if __name__ == '__main__':
    unittest.main()
