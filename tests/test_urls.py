import unittest

from anicat.errors import AniCatError
from anicat.urls import (
    dedupe,
    ensure_supported_url,
    is_episode_url,
    is_season_url,
    source_kind,
    split_urls,
)


class UrlTests(unittest.TestCase):
    def test_split_urls_accepts_commas_and_spaces(self):
        self.assertEqual(
            split_urls(
                [
                    " https://anime1.me/1,https://anime1.me/2 ",
                    "https://anime1.me/3\nhttps://anime1.me/4",
                ]
            ),
            [
                "https://anime1.me/1",
                "https://anime1.me/2",
                "https://anime1.me/3",
                "https://anime1.me/4",
            ],
        )

    def test_classifies_supported_urls(self):
        self.assertTrue(is_episode_url("https://anime1.me/15651"))
        self.assertTrue(is_season_url("https://anime1.me/category/2021/example"))
        self.assertTrue(is_episode_url("https://anime1.pw/349"))
        self.assertTrue(is_season_url("https://anime1.pw/?cat=60"))
        self.assertTrue(
            is_season_url(
                "https://anime1.pw/"
                "%E6%A3%AE%E6%9E%97%E8%A3%A1%E7%9A%84%E7%86%8A%E5%85%88%E7%94%9F"
                "%EF%BC%8C%E5%86%AC%E7%9C%A0%E4%B8%AD%E3%80%82?cat=27"
            )
        )
        self.assertTrue(
            is_season_url(
                "https://anime1.pw/"
                "%E6%A3%AE%E6%9E%97%E8%A3%A1%E7%9A%84%E7%86%8A%E5%85%88%E7%94%9F"
                "%EF%BC%8C%E5%86%AC%E7%9C%A0%E4%B8%AD%E3%80%82"
            )
        )
        self.assertTrue(is_season_url("https://anime1.pw/english-category"))
        self.assertTrue(
            is_season_url(
                "https://anime1.pw/"
                "%E3%82%B5%E3%83%B3%E3%83%97%E3%83%AB%E3%82%AB%E3%83%86%E3%82%B4%E3%83%AA"
            )
        )
        self.assertEqual(source_kind("https://anime1.me/15651"), "anime1_me")
        self.assertEqual(source_kind("https://anime1.pw/349"), "anime1_pw")

    def test_anime1_me_category_query_is_a_season_url(self):
        # The catalogue index links seasons as ?cat=N rather than /category/...
        self.assertTrue(is_season_url("https://anime1.me/?cat=1935"))
        self.assertTrue(is_season_url("https://anime1.me/?cat=1935&paged=2"))
        ensure_supported_url("https://anime1.me/?cat=1935")

    def test_anime1_me_episode_path_wins_over_category_query(self):
        self.assertFalse(is_season_url("https://anime1.me/28979?cat=1935"))
        self.assertTrue(is_episode_url("https://anime1.me/28979?cat=1935"))
        self.assertFalse(is_season_url("https://anime1.me/?cat=abc"))

    def test_rejects_unsupported_urls(self):
        with self.assertRaises(AniCatError):
            ensure_supported_url("https://example.com/15651")
        self.assertFalse(is_episode_url("https://anime1.cc/681899930-01-0142"))
        self.assertFalse(is_episode_url("https://anime1.in/2023-xian-ni-10142000"))
        self.assertFalse(is_season_url("https://anime1.pw/"))
        self.assertFalse(is_season_url("https://anime1.pw/349?cat=60"))
        self.assertFalse(is_season_url("https://anime1.pw/wp-login.php"))
        self.assertFalse(is_season_url("https://anime1.pw/wp-content/uploads/foo.jpg"))
        self.assertFalse(is_season_url("https://anime1.pw/feed/"))
        self.assertFalse(is_season_url("https://anime1.pw/page/2/"))
        self.assertFalse(is_season_url("https://anime1.pw/about"))
        self.assertFalse(is_season_url("https://anime1.pw/category%2Fsmuggled"))

    def test_rejects_episode_url_with_trailing_garbage(self):
        self.assertFalse(is_episode_url("https://anime1.me/15651abc"))
        self.assertFalse(is_episode_url("https://anime1.me/15651/extra"))
        self.assertFalse(is_episode_url("https://anime1.pw/349/extra"))

    def test_dedupe_keeps_order(self):
        self.assertEqual(dedupe(["a", "b", "a", "c"]), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
