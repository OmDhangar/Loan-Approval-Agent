import unittest

from services.geolocation_service import geolocation_service


class GeoTests(unittest.TestCase):
    def test_haversine_distance_nonzero(self):
        km = geolocation_service.haversine_km(19.0760, 72.8777, 28.6139, 77.2090)
        self.assertGreater(km, 1000)


if __name__ == "__main__":
    unittest.main()
