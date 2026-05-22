import unittest

import requests

from anicat.errors import ParseError
from anicat.extractor import (
    extract_access_cookies,
    parse_episode_page,
    parse_season_page,
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

    def test_parse_episode_page_rejects_missing_video_data(self):
        with self.assertRaises(ParseError):
            parse_episode_page('<h2 class="entry-title">Demo</h2>')

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

    def test_extract_access_cookies_rejects_missing_values(self):
        response = requests.Response()
        response.cookies.set("e", "1")

        with self.assertRaises(ParseError):
            extract_access_cookies(response)


if __name__ == "__main__":
    unittest.main()
