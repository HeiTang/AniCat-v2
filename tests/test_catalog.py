import json
import unittest

from anicat.catalog import fetch_catalog, parse_catalog, search_catalog
from anicat.constants import ANIME_LIST_URL
from anicat.errors import ParseError

ME_ROW = [1935, "GRAND BLUE 碧藍之海 第三季", "連載中(07)", "2026", "夏", ""]
PW_ROW = [
    0,
    '<a href="https://anime1.pw/?cat=62">關於相同研討會的染谷同學是性感女優這檔事。</a>',
    "連載中(04)",
    "2026",
    "夏",
    "桜都",
]


class FakeCatalogClient:
    """Catalogue source that returns a canned payload without touching network."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.requested: list[str] = []

    def get_page(self, url: str) -> str:
        self.requested.append(url)
        return self.payload


class ParseCatalogTests(unittest.TestCase):
    def test_anime1_me_row_builds_category_url(self):
        entry = parse_catalog(json.dumps([ME_ROW]))[0]

        self.assertEqual(entry.anime_id, 1935)
        self.assertEqual(entry.title, "GRAND BLUE 碧藍之海 第三季")
        self.assertEqual(entry.episodes, "連載中(07)")
        self.assertEqual(entry.year, "2026")
        self.assertEqual(entry.season, "夏")
        self.assertEqual(entry.subtitle_group, "")
        self.assertEqual(entry.url, "https://anime1.me/?cat=1935")

    def test_anime1_pw_row_unwraps_anchor_into_title_and_url(self):
        entry = parse_catalog(json.dumps([PW_ROW]))[0]

        self.assertEqual(entry.anime_id, 0)
        self.assertEqual(entry.title, "關於相同研討會的染谷同學是性感女優這檔事。")
        self.assertEqual(entry.url, "https://anime1.pw/?cat=62")
        self.assertEqual(entry.subtitle_group, "桜都")

    def test_duplicate_titles_are_both_kept(self):
        rows = [
            [1608, "灰色：幻影扳機", "1-13", "2025", "冬", "桜都"],
            [539, "灰色：幻影扳機", "1-3", "2019", "冬", "喵萌奶茶屋"],
        ]

        entries = parse_catalog(json.dumps(rows))

        self.assertEqual([entry.anime_id for entry in entries], [1608, 539])

    def test_malformed_rows_are_skipped_without_losing_valid_rows(self):
        rows = [ME_ROW, "not-a-row", [1, "too", "short"], [None, "bad id", "", "", "", ""]]

        with self.assertLogs("anicat.catalog", level="WARNING"):
            entries = parse_catalog(json.dumps(rows))

        self.assertEqual([entry.anime_id for entry in entries], [1935])

    def test_anime1_pw_row_without_anchor_is_skipped(self):
        rows = [ME_ROW, [0, "plain title, no anchor", "1-8", "2024", "夏", "桜都"]]

        with self.assertLogs("anicat.catalog", level="WARNING"):
            entries = parse_catalog(json.dumps(rows))

        self.assertEqual([entry.anime_id for entry in entries], [1935])

    def test_empty_catalogue_is_allowed(self):
        self.assertEqual(parse_catalog("[]"), [])

    def test_payload_with_only_unusable_rows_raises_parse_error(self):
        with self.assertLogs("anicat.catalog", level="WARNING"), self.assertRaises(ParseError):
            parse_catalog(json.dumps(["nope", "still nope"]))

    def test_non_json_payload_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_catalog("<html>upstream error page</html>")

    def test_non_list_payload_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_catalog(json.dumps({"rows": []}))


class FetchCatalogTests(unittest.TestCase):
    def test_fetch_requests_the_catalogue_url(self):
        client = FakeCatalogClient(json.dumps([ME_ROW]))

        entries = fetch_catalog(client)

        self.assertEqual(client.requested, [ANIME_LIST_URL])
        self.assertEqual(len(entries), 1)


class SearchCatalogTests(unittest.TestCase):
    def setUp(self):
        self.entries = parse_catalog(json.dumps([ME_ROW, PW_ROW]))

    def test_search_matches_substring(self):
        matches = search_catalog(self.entries, "碧藍之海")

        self.assertEqual([entry.anime_id for entry in matches], [1935])

    def test_search_ignores_case(self):
        self.assertEqual(len(search_catalog(self.entries, "grand blue")), 1)

    def test_search_matches_pw_title_after_unwrapping(self):
        matches = search_catalog(self.entries, "染谷同學")

        self.assertEqual([entry.url for entry in matches], ["https://anime1.pw/?cat=62"])

    def test_search_does_not_match_markup_stripped_from_titles(self):
        self.assertEqual(search_catalog(self.entries, "href"), [])

    def test_search_without_match_returns_empty_list(self):
        self.assertEqual(search_catalog(self.entries, "no such anime"), [])


if __name__ == "__main__":
    unittest.main()
