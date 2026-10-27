import unittest

from parcelpost.pricing.tariff import base_price, fuel_surcharge
from parcelpost.pricing.zones import zone_for, zone_multiplier


class ZoneTests(unittest.TestCase):
    def test_domestic(self):
        self.assertEqual(zone_for("MD", "md"), 0)

    def test_neighbour(self):
        self.assertEqual(zone_for("MD", "RO"), 1)
        self.assertEqual(zone_for("RO", "BG"), 1)

    def test_eu(self):
        self.assertEqual(zone_for("MD", "DE"), 2)

    def test_world(self):
        self.assertEqual(zone_for("MD", "US"), 3)

    def test_multipliers(self):
        self.assertEqual(zone_multiplier(0), 1.0)
        self.assertEqual(zone_multiplier(1), 1.4)
        self.assertEqual(zone_multiplier(2), 1.9)
        self.assertEqual(zone_multiplier(3), 3.2)
        self.assertEqual(zone_multiplier(7), 3.2)
        with self.assertRaises(ValueError):
            zone_multiplier(-1)


class TariffTests(unittest.TestCase):
    def test_band_edges(self):
        self.assertEqual(base_price(0.5), 4.50)
        self.assertEqual(base_price(1), 4.50)
        self.assertEqual(base_price(1.5), 6.20)
        self.assertEqual(base_price(2), 6.20)
        self.assertEqual(base_price(5), 9.80)
        self.assertEqual(base_price(10), 15.40)
        self.assertEqual(base_price(20), 24.00)

    def test_overweight(self):
        self.assertEqual(base_price(22), 26.70)
        self.assertEqual(base_price(30), 37.50)

    def test_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            base_price(0)

    def test_fuel_surcharge(self):
        self.assertEqual(fuel_surcharge(100, 90), 0.0)
        self.assertEqual(fuel_surcharge(100, 100), 0.0)
        self.assertEqual(fuel_surcharge(100, 110), 5.0)
        self.assertEqual(fuel_surcharge(200, 120), 20.0)
        self.assertEqual(fuel_surcharge(100, 200), 15.0)


if __name__ == "__main__":
    unittest.main()
