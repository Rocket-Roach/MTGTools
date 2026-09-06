"""Card matching: names, faces, merging, index consistency."""
import unittest

import bootstrap  # noqa: F401  (adds src/ to sys.path)
from tracker import Card, Collection, card_faces


class FacesTest(unittest.TestCase):
    def test_single(self):
        self.assertEqual(card_faces('Lightning Bolt'), {'lightning bolt'})

    def test_split(self):
        self.assertEqual(card_faces('Boggart Trawler // Boggart Bog'),
                         {'boggart trawler', 'boggart bog'})

    def test_spacing_variants(self):
        self.assertEqual(card_faces('Wear//Tear'), {'wear', 'tear'})
        self.assertEqual(card_faces('  Wear  //  Tear  '), {'wear', 'tear'})

    def test_empty(self):
        self.assertEqual(card_faces(''), {''})


class CollectionTest(unittest.TestCase):
    def test_exact_merge(self):
        c = Collection()
        c.add(Card('Solitude', 2))
        c.add(Card('solitude', 2))
        self.assertEqual(c.unique_cards(), 1)
        self.assertEqual(c.get_quantity('Solitude'), 4)

    def test_face_merge_prefers_full_name(self):
        c = Collection()
        c.add(Card('Wear', 2))
        c.add(Card('Wear // Tear', 2))
        self.assertEqual(c.unique_cards(), 1)
        self.assertEqual(c.total_cards(), 4)
        self.assertIn('//', list(c.cards.values())[0].name)

    def test_face_merge_reverse_order(self):
        c = Collection()
        c.add(Card('Wear // Tear', 2))
        c.add(Card('Wear', 2))
        self.assertEqual(c.unique_cards(), 1)
        self.assertEqual(c.total_cards(), 4)

    def test_any_side_fulfills(self):
        c = Collection()
        c.add(Card('Boggart Trawler // Boggart Bog', 4))
        self.assertEqual(c.get_quantity('Boggart Trawler'), 4)
        self.assertEqual(c.get_quantity('Boggart Bog'), 4)
        self.assertEqual(c.get_quantity('Boggart Trawler // Boggart Bog'), 4)
        self.assertEqual(c.get_quantity('Lightning Bolt'), 0)

    def test_front_face_entry_fulfills_full_name(self):
        c = Collection()
        c.add(Card('Boggart Trawler', 4))
        self.assertEqual(c.get_quantity('Boggart Trawler // Boggart Bog'), 4)
        self.assertEqual(c.get_quantity('Boggart Bog'), 0)

    def test_entry_counted_once_for_multi_face_query(self):
        c = Collection()
        c.add(Card('Wear // Tear', 4))
        # one entry, one count — never doubled across faces
        self.assertEqual(c.get_quantity('Wear // Tear'), 4)

    def test_pop_and_remove_keep_index(self):
        c = Collection()
        c.add(Card('Wear // Tear', 4))
        c.remove(Card('Wear // Tear'), 1)
        self.assertEqual(c.get_quantity('Tear'), 3)
        c.pop('wear // tear')
        self.assertEqual(c.get_quantity('Wear'), 0)
        self.assertEqual(c.unique_cards(), 0)

    def test_clear(self):
        c = Collection()
        c.add(Card('Wear // Tear', 4))
        c.clear()
        self.assertEqual(c.get_quantity('Wear'), 0)

    def test_old_printing_split_keys_migrate(self):
        old = {
            'a|MH2|12|nonfoil': {'name': 'Solitude', 'quantity': 1,
                                 'set_code': 'MH2', 'collector_number': '12', 'foil': False},
            'b|MOM|6|foil': {'name': 'solitude', 'quantity': 2,
                             'set_code': '', 'collector_number': '', 'foil': True},
        }
        c = Collection.from_dict(old)
        self.assertEqual(c.unique_cards(), 1)
        self.assertEqual(c.get_quantity('solitude'), 3)

    def test_empty_name(self):
        self.assertEqual(Collection().get_quantity(''), 0)
        self.assertEqual(Collection().get_quantity('   '), 0)


if __name__ == '__main__':
    unittest.main()
