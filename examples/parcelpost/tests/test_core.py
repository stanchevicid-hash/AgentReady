import unittest

from parcelpost.core._impl.dims import billable_weight, volumetric_weight
from parcelpost.core.discounts import apply_promo, loyalty_discount


class DimsTests(unittest.TestCase):
    def test_volumetric(self):
        self.assertEqual(volumetric_weight((50, 40, 30)), 12.0)
        self.assertEqual(volumetric_weight((10, 10, 10)), 0.2)

    def test_volumetric_validation(self):
        with self.assertRaises(ValueError):
            volumetric_weight((10, 10))
        with self.assertRaises(ValueError):
            volumetric_weight((10, 0, 10))

    def test_billable_uses_heavier(self):
        self.assertEqual(billable_weight(3.0, (50, 40, 30)), 12.0)
        self.assertEqual(billable_weight(3.0, (10, 10, 10)), 3.0)

    def test_billable_rounds_up_to_half_kg(self):
        self.assertEqual(billable_weight(2.1, (10, 10, 10)), 2.5)
        self.assertEqual(billable_weight(2.5, (10, 10, 10)), 2.5)
        self.assertEqual(billable_weight(2.6, (10, 10, 10)), 3.0)


class DiscountTests(unittest.TestCase):
    def test_loyalty_tiers(self):
        self.assertEqual(loyalty_discount(0), 0.0)
        self.assertEqual(loyalty_discount(9), 0.0)
        self.assertEqual(loyalty_discount(10), 0.05)
        self.assertEqual(loyalty_discount(49), 0.05)
        self.assertEqual(loyalty_discount(50), 0.10)
        self.assertEqual(loyalty_discount(199), 0.10)
        self.assertEqual(loyalty_discount(200), 0.12)

    def test_promo(self):
        self.assertEqual(apply_promo(100, "welcome10"), 90.0)
        self.assertEqual(apply_promo(100, " SPRING15 "), 85.0)
        self.assertEqual(apply_promo(100, "BOGUS"), 100.0)
        self.assertEqual(apply_promo(100, None), 100.0)

    def test_combined_cap(self):
        self.assertEqual(apply_promo(100, "SPRING15", loyalty_rate=0.05), 80.0)
        self.assertEqual(apply_promo(100, "SPRING15", loyalty_rate=0.12), 75.0)


if __name__ == "__main__":
    unittest.main()
