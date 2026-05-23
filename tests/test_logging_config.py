import logging
import unittest

from anicat.logging_config import log_level


class LoggingConfigTests(unittest.TestCase):
    def test_log_level_maps_verbosity(self):
        self.assertEqual(log_level(verbose=0, quiet=False), logging.ERROR)
        self.assertEqual(log_level(verbose=1, quiet=False), logging.INFO)
        self.assertEqual(log_level(verbose=2, quiet=False), logging.DEBUG)
        self.assertEqual(log_level(verbose=3, quiet=False), logging.DEBUG)

    def test_quiet_wins_over_verbose(self):
        self.assertEqual(log_level(verbose=2, quiet=True), logging.ERROR)


if __name__ == "__main__":
    unittest.main()
