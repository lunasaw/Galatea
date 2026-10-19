import unittest
import test_domain
from galatea_app.storage import Storage


class OperationsTests(unittest.TestCase):
    def test_failed_delivery_stops_retries_and_exposes_health(self):
        b = test_domain.DomainTests()
        b.setUp()
        try:
            for n in range(5):
                job = b.service.storage.claim("worker", now=1000 + n * 100)
                self.assertIsNotNone(job)
                b.service.storage.ack(
                    job["id"],
                    job["fencing_token"],
                    error="HOST_UNAVAILABLE",
                    now=1000 + n * 100,
                )
            self.assertIsNone(b.service.storage.claim("worker", now=10000))
            self.assertEqual(b.service.storage.health()["dead_letters"], 1)
        finally:
            b.tearDown()
