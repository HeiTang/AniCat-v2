import unittest

from anicat.errors import AniCatError
from anicat.urls import dedupe, ensure_supported_url, is_episode_url, is_season_url, split_urls


class UrlTests(unittest.TestCase):
    def test_split_urls_accepts_commas_and_spaces(self):
        self.assertEqual(
            split_urls([" https://anime1.me/1,https://anime1.me/2 ", "https://anime1.me/3"]),
            ["https://anime1.me/1", "https://anime1.me/2", "https://anime1.me/3"],
        )

    def test_classifies_supported_urls(self):
        self.assertTrue(is_episode_url("https://anime1.me/15651"))
        self.assertTrue(is_season_url("https://anime1.me/category/2021/example"))

    def test_rejects_unsupported_urls(self):
        with self.assertRaises(AniCatError):
            ensure_supported_url("https://example.com/15651")

    def test_dedupe_keeps_order(self):
        self.assertEqual(dedupe(["a", "b", "a", "c"]), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
