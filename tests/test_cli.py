import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from anicat import AnimeEntry, __version__
from anicat.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, build_parser, main, options_from_args


class CliTests(unittest.TestCase):
    def test_missing_url_does_not_prompt_when_stdin_is_not_tty(self):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input", side_effect=AssertionError("input should not run")),
        ):
            self.assertEqual(main([]), EXIT_USAGE)

    def test_invalid_options_return_argument_error(self):
        self.assertEqual(main(["--timeout", "0", "https://anime1.me/1"]), EXIT_USAGE)

    def test_invalid_connect_timeout_returns_argument_error(self):
        self.assertEqual(main(["--connect-timeout", "0", "https://anime1.me/1"]), EXIT_USAGE)

    def test_timeout_flags_build_request_timeout_tuple(self):
        args = build_parser().parse_args(
            [
                "--connect-timeout",
                "2",
                "--timeout",
                "9",
                "https://anime1.me/1",
            ]
        )

        self.assertEqual(options_from_args(args).request_timeout, (2, 9))

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

    def test_search_subcommand_prints_titles_and_category_urls(self):
        entries = [
            AnimeEntry(
                anime_id=1935,
                title="GRAND BLUE 碧藍之海 第三季",
                episodes="連載中(07)",
                year="2026",
                season="夏",
                subtitle_group="",
                url="https://anime1.me/?cat=1935",
            )
        ]
        stdout = StringIO()

        with (
            # Pin the render width so table wrapping cannot break these assertions.
            patch.dict(os.environ, {"COLUMNS": "200"}),
            patch("anicat.cli.Anime1Client"),
            patch("anicat.cli.fetch_catalog", return_value=entries),
            redirect_stdout(stdout),
        ):
            self.assertEqual(main(["search", "碧藍之海"]), EXIT_OK)

        output = stdout.getvalue()
        self.assertIn("GRAND BLUE 碧藍之海 第三季", output)
        self.assertIn("https://anime1.me/?cat=1935", output)
        self.assertIn("1 result(s)", output)

    def test_search_without_match_returns_failure(self):
        with (
            patch("anicat.cli.Anime1Client"),
            patch("anicat.cli.fetch_catalog", return_value=[]),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(main(["search", "no such anime"]), EXIT_FAILURE)

    def test_non_search_first_argument_still_uses_the_download_parser(self):
        with patch("anicat.cli.search_main", side_effect=AssertionError("search should not run")):
            self.assertEqual(main(["--timeout", "0", "https://anime1.me/1"]), EXIT_USAGE)

    def test_exit_code_constants_match_documented_values(self):
        self.assertEqual(EXIT_OK, 0)
        self.assertEqual(EXIT_FAILURE, 1)
        self.assertEqual(EXIT_USAGE, 2)


if __name__ == "__main__":
    unittest.main()
