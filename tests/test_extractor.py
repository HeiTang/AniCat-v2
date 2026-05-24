import unittest
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup, FeatureNotFound

from anicat.errors import ParseError
from anicat.extractor import (
    extract_access_cookies,
    parse_episode_page,
    parse_html,
    parse_season_page,
    parse_set_cookie_header,
    parse_stream_url,
)


class ExtractorTests(unittest.TestCase):
    def test_parse_season_page_extracts_episode_and_next_urls(self):
        page = parse_season_page(
            """
            <h2 class="entry-title"><a rel="bookmark" href="/1">A</a></h2>
            <h2 class="entry-title"><a rel="bookmark" href="https://anime1.me/2">B</a></h2>
            <div class="nav-previous"><a href="/page/2">next</a></div>
            """
        )

        self.assertEqual(page.episode_urls, ["/1", "https://anime1.me/2"])
        self.assertEqual(page.next_url, "/page/2")

    def test_parse_episode_page_extracts_api_request_and_title(self):
        data_apireq, title = parse_episode_page(
            """
            <h2 class="entry-title"> Demo Episode </h2>
            <video class="video-js" data-apireq="abc123"></video>
            """
        )

        self.assertEqual(data_apireq, "abc123")
        self.assertEqual(title, "Demo Episode")

    def test_parse_html_falls_back_when_lxml_is_unavailable(self):
        parsers: list[str] = []

        def fake_beautiful_soup(html: str, parser: str) -> BeautifulSoup:
            parsers.append(parser)
            if parser == "lxml":
                raise FeatureNotFound("lxml unavailable")
            return BeautifulSoup(html, parser)

        with patch("anicat.extractor.BeautifulSoup", side_effect=fake_beautiful_soup):
            soup = parse_html('<h2 class="entry-title">Demo</h2>')

        title = soup.select_one("h2.entry-title")
        assert title is not None
        self.assertEqual(parsers, ["lxml", "html.parser"])
        self.assertEqual(title.get_text(strip=True), "Demo")

    def test_parse_episode_page_rejects_missing_video_data(self):
        with self.assertRaises(ParseError):
            parse_episode_page('<h2 class="entry-title">Demo</h2>')

    def test_parse_episode_page_uses_first_video_with_api_request(self):
        data_apireq, title = parse_episode_page(
            """
            <h2 class="entry-title">Demo</h2>
            <video class="video-js"></video>
            <video class="video-js" data-apireq="real-request"></video>
            """
        )

        self.assertEqual(data_apireq, "real-request")
        self.assertEqual(title, "Demo")

    def test_parse_episode_page_warns_when_multiple_video_candidates_exist(self):
        with self.assertLogs("anicat.extractor", level="WARNING") as logs:
            data_apireq, title = parse_episode_page(
                """
                <h2 class="entry-title">Demo</h2>
                <video class="video-js" data-apireq="first"></video>
                <video class="video-js" data-apireq="second"></video>
                """
            )

        self.assertEqual(data_apireq, "first")
        self.assertEqual(title, "Demo")
        self.assertIn("2 video candidates", logs.output[0])

    def test_parse_stream_url_accepts_dict_or_list_shape(self):
        self.assertEqual(
            parse_stream_url({"s": {"src": "//cdn.example/video.mp4"}}), "//cdn.example/video.mp4"
        )
        self.assertEqual(parse_stream_url({"s": [{"src": "/video.mp4"}]}), "/video.mp4")

    def test_parse_stream_url_rejects_missing_src(self):
        with self.assertRaises(ParseError):
            parse_stream_url({"s": []})

    def test_extract_access_cookies_from_cookie_jar(self):
        response = requests.Response()
        response.cookies.set("e", "1")
        response.cookies.set("p", "2")
        response.cookies.set("h", "3")

        self.assertEqual(extract_access_cookies(response), {"e": "1", "p": "2", "h": "3"})

    def test_extract_access_cookies_from_header(self):
        response = requests.Response()
        response.headers["set-cookie"] = "e=1; Path=/; p=2; Path=/; h=3; Path=/"

        self.assertEqual(extract_access_cookies(response), {"e": "1", "p": "2", "h": "3"})

    def test_extract_access_cookies_from_comma_joined_set_cookie_header(self):
        response = requests.Response()
        response.headers["set-cookie"] = (
            "e=token-e; expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/; HttpOnly, "
            "p=token-p; expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/; HttpOnly, "
            "h=token-h; Path=/; Secure; SameSite=None"
        )

        self.assertEqual(
            extract_access_cookies(response),
            {"e": "token-e", "p": "token-p", "h": "token-h"},
        )

    def test_extract_access_cookies_merges_cookie_jar_and_header_fallback(self):
        response = requests.Response()
        response.cookies.set("e", "jar-e")
        response.headers["set-cookie"] = "p=header-p; Path=/; HttpOnly, h=header-h; Path=/"

        self.assertEqual(
            extract_access_cookies(response),
            {"e": "jar-e", "p": "header-p", "h": "header-h"},
        )

    def test_parse_set_cookie_header_preserves_expires_commas(self):
        parsed = parse_set_cookie_header(
            "e=1; expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/; HttpOnly, p=2; Path=/"
        )

        self.assertEqual(parsed["e"].value, "1")
        self.assertEqual(parsed["p"].value, "2")

    def test_extract_access_cookies_rejects_missing_values(self):
        response = requests.Response()
        response.cookies.set("e", "1")

        with self.assertRaises(ParseError):
            extract_access_cookies(response)


if __name__ == "__main__":
    unittest.main()
