import unittest
from unittest.mock import patch

from anicat.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_missing_url_does_not_prompt_when_stdin_is_not_tty(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input", side_effect=AssertionError("input should not run")),
        ):
            self.assertEqual(main([]), 2)

    def test_invalid_options_return_argument_error(self):
        self.assertEqual(main(["--timeout", "0", "https://anime1.me/1"]), 2)

    def test_logging_flags_parse(self):
        args = build_parser().parse_args(["-vv", "--quiet", "https://anime1.me/1"])

        self.assertEqual(args.verbose, 2)
        self.assertTrue(args.quiet)


if __name__ == "__main__":
    unittest.main()
