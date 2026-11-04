import unittest
from unittest.mock import Mock

from anicat.errors import ParseError
from anicat.hls import parse_hls_episode, validate_playlist_url


class HlsParserTests(unittest.TestCase):
    def test_cc_query_preserves_signed_playlist(self):
        fetch = Mock()
        result = parse_hls_episode(
            '<h1 class="entry-title">動畫 142</h1>'
            '<iframe src="/_player?url=https%3A%2F%2Fcdn.example%2Findex.m3u8%3Ftoken%3Da%26e%3D1'
            '&amp;type=application/x-mpegURL"></iframe>',
            "https://anime1.cc/681899930-01-0142",
            fetch,
        )
        self.assertEqual(result, ("https://cdn.example/index.m3u8?token=a&e=1", "動畫 142"))
        fetch.assert_not_called()

    def test_in_fetches_player_and_resolves_relative_source(self):
        fetch = Mock(return_value='<video><source src="/media/index.m3u8"></video>')
        result = parse_hls_episode(
            '<h1 class="entry-title">動畫</h1><iframe src="/_player_x_/1182776"></iframe>',
            "https://anime1.in/2023-xian-ni-10142000",
            fetch,
        )
        self.assertEqual(result, ("https://anime1.in/media/index.m3u8", "動畫"))
        fetch.assert_called_once_with("https://anime1.in/_player_x_/1182776")

    def test_external_iframe_is_not_fetched(self):
        fetch = Mock()
        with self.assertRaises(ParseError):
            parse_hls_episode(
                '<h1 class="entry-title">動畫</h1>'
                '<iframe src="https://other.example/_player_x_/1"></iframe>',
                "https://anime1.in/show-1",
                fetch,
            )
        fetch.assert_not_called()

    def test_missing_title_fails_without_fetch(self):
        fetch = Mock()
        with self.assertRaises(ParseError):
            parse_hls_episode(
                '<iframe src="/_player_x_/1"></iframe>', "https://anime1.in/show-1", fetch
            )
        fetch.assert_not_called()

    def test_invalid_playlist_urls_are_rejected(self):
        for url in (
            "file:///tmp/a.m3u8",
            "ftp://cdn.example/a.m3u8",
            "https://user:pass@cdn.example/a.m3u8",
            "https://cdn.example/a.mp4",
            "https://cdn.example/a.m3u8\r\nHeader:x",
        ):
            with self.subTest(url=url), self.assertRaises(ParseError):
                validate_playlist_url(url, "https://anime1.in/")


class HlsSeasonTests(unittest.TestCase):
    def test_cc_pagination_excludes_sidebar_and_deduplicates(self):
        from anicat.hls import collect_hls_episode_urls

        pages = {
            "https://anime1.cc/123/": '<div class="entry-content"><ul class="eps">'
            '<a class="e-aa" href="/123-01-0002">2</a></ul><a href="2">下一頁</a></div>'
            '<a href="/999-01-0001">Other</a>',
            "https://anime1.cc/123/2": '<div class="entry-content"><ul class="eps">'
            '<a class="e-aa" href="/123-01-0002">2</a>'
            '<a class="e-aa" href="/456-01-0001">1</a></ul></div>',
        }
        self.assertEqual(
            collect_hls_episode_urls(pages.__getitem__, "https://anime1.cc/123/"),
            ["https://anime1.cc/123-01-0002", "https://anime1.cc/456-01-0001"],
        )

    def test_in_external_pagination_rejected(self):
        from anicat.hls import collect_hls_episode_urls

        fetch = Mock(
            return_value='<h2 class="entry-title">'
            '<a rel="bookmark" href="/show-10142000">show</a></h2>'
            '<div class="nav-previous"><a href="https://other.example/show/page/2">next</a></div>'
        )
        with self.assertRaisesRegex(ParseError, "outside"):
            collect_hls_episode_urls(fetch, "https://anime1.in/show/")
        fetch.assert_called_once()
