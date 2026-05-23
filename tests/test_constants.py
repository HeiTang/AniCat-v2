import unittest
from pathlib import Path

from anicat.cli import build_parser
from anicat.constants import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_OUTPUT_DIR_NAME,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRIES,
)
from anicat.options import DownloadOptions


class ConstantsTests(unittest.TestCase):
    def test_cli_defaults_match_shared_constants(self):
        args = build_parser().parse_args([])

        self.assertEqual(args.output.name, DEFAULT_OUTPUT_DIR_NAME)
        self.assertEqual(args.concurrency, DEFAULT_CONCURRENCY)
        self.assertEqual(args.timeout, DEFAULT_READ_TIMEOUT)
        self.assertEqual(args.retries, DEFAULT_RETRIES)
        self.assertEqual(args.chunk_size, DEFAULT_CHUNK_SIZE)

    def test_options_defaults_match_shared_constants(self):
        options = DownloadOptions(output_dir=Path("unused"))

        self.assertEqual(options.concurrency, DEFAULT_CONCURRENCY)
        self.assertEqual(options.timeout, DEFAULT_READ_TIMEOUT)
        self.assertEqual(options.retries, DEFAULT_RETRIES)
        self.assertEqual(options.chunk_size, DEFAULT_CHUNK_SIZE)


if __name__ == "__main__":
    unittest.main()
