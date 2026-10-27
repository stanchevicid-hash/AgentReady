import unittest

from parcelpost import quote


class QuoteTests(unittest.TestCase):
    def test_domestic_quote(self):
        q = quote(2.5, (30, 20, 10), "MD", "MD")
        self.assertEqual(q["billable_weight"], 2.5)
        self.assertEqual(q["zone"], 0)
        self.assertEqual(q["price"], 9.80)
        self.assertEqual(q["total"], 9.80)

    def test_neighbour_quote_with_discounts(self):
        q = quote(2.5, (30, 20, 10), "MD", "RO", fuel_index=110, shipments_last_year=60, promo="WELCOME10")
        self.assertEqual(q["zone"], 1)
        self.assertEqual(q["price"], 13.72)
        self.assertEqual(q["fuel_surcharge"], 0.69)
        self.assertEqual(q["subtotal"], 14.41)
        self.assertEqual(q["total"], 11.53)

    def test_bulky_parcel_billed_by_volume(self):
        q = quote(1.0, (60, 50, 40), "MD", "US")
        self.assertEqual(q["billable_weight"], 24.0)
        self.assertEqual(q["zone"], 3)
        self.assertEqual(q["price"], 94.08)


if __name__ == "__main__":
    unittest.main()
