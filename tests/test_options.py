import unittest
from pathlib import Path
from typing import Any

from anicat.options import DownloadOptions


class DownloadOptionsTests(unittest.TestCase):
    def test_rejects_invalid_values(self):
        cases: list[dict[str, Any]] = [
            {"concurrency": 0},
            {"timeout": 0},
            {"timeout": -1},
            {"connect_timeout": 0},
            {"connect_timeout": -1},
            {"retries": -1},
            {"chunk_size": 0},
        ]

        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    DownloadOptions(output_dir=Path("unused"), **kwargs)

    def test_validated_properties_do_not_coerce_values(self):
        options = DownloadOptions(
            output_dir=Path("unused"),
            concurrency=2,
            connect_timeout=3,
            timeout=4,
            chunk_size=2,
        )

        self.assertEqual(options.worker_count, 2)
        self.assertEqual(options.request_timeout, (3, 4))
        self.assertEqual(options.safe_chunk_size, 2)


if __name__ == "__main__":
    unittest.main()
