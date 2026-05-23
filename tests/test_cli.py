import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from anicat import __version__
from anicat.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, build_parser, main


class CliTests(unittest.TestCase):
    def test_missing_url_does_not_prompt_when_stdin_is_not_tty(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input", side_effect=AssertionError("input should not run")),
        ):
            self.assertEqual(main([]), EXIT_USAGE)

    def test_invalid_options_return_argument_error(self):
        self.assertEqual(main(["--timeout", "0", "https://anime1.me/1"]), EXIT_USAGE)

    def test_verbose_flag_counts_diagnostic_level(self):
        args = build_parser().parse_args(["-vv", "https://anime1.me/1"])

        self.assertEqual(args.verbose, 2)
        self.assertFalse(args.quiet)

    def test_verbose_and_quiet_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            build_parser().parse_args(["-vv", "--quiet", "https://anime1.me/1"])

    def test_version_flag_prints_package_version(self):
        stdout = StringIO()

        with self.assertRaises(SystemExit) as error, redirect_stdout(stdout):
            build_parser().parse_args(["--version"])

        self.assertEqual(error.exception.code, 0)
        self.assertIn(__version__, stdout.getvalue())

    def test_quiet_flag_parse(self):
        args = build_parser().parse_args(["--quiet", "https://anime1.me/1"])

        self.assertEqual(args.verbose, 0)
        self.assertTrue(args.quiet)

    def test_exit_code_constants_match_documented_values(self):
        self.assertEqual(EXIT_OK, 0)
        self.assertEqual(EXIT_FAILURE, 1)
        self.assertEqual(EXIT_USAGE, 2)


if __name__ == "__main__":
    unittest.main()
