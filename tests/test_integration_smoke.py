import os
import unittest

from anicat.catalog import fetch_catalog
from anicat.client import Anime1Client
from anicat.extractor import Anime1Extractor, parse_season_page
from anicat.urls import is_episode_url

RUN_INTEGRATION = os.environ.get("ANICAT_RUN_INTEGRATION") == "1"
RUN_PW_INTEGRATION = os.environ.get("ANICAT_RUN_PW_INTEGRATION") == "1"
SMOKE_URL = os.environ.get("ANICAT_SMOKE_URL", "https://anime1.me/28979")
PW_SMOKE_URL = os.environ.get("ANICAT_PW_SMOKE_URL", "https://anime1.pw/349")
# Left empty on purpose: the season URL is derived from the live catalogue so it
# cannot go stale the way a pinned category would.
SMOKE_SEASON_URL = os.environ.get("ANICAT_SMOKE_SEASON_URL", "")


def current_season_url(client: Anime1Client) -> str:
    """Return the season category URL of the newest entry in the catalogue."""

    if SMOKE_SEASON_URL:
        return SMOKE_SEASON_URL
    newest = next(entry for entry in fetch_catalog(client) if entry.anime_id)
    return f"https://anime1.me/category/{newest.year}年{newest.season}季"


@unittest.skipUnless(RUN_INTEGRATION, "set ANICAT_RUN_INTEGRATION=1 to run Anime1 smoke tests")
class Anime1SmokeTests(unittest.TestCase):
    def test_episode_range_request_supports_resume_contract(self):
        client = Anime1Client(timeout=(10.0, 30.0), retries=1)
        try:
            episode = Anime1Extractor(client).episode(SMOKE_URL)
            response = client.request(
                "GET",
                episode.stream_url,
                cookies=dict(episode.cookies),
                headers={
                    "Accept-Encoding": "identity",
                    "Range": "bytes=0-1",
                },
                stream=True,
            )
            try:
                body = next(response.iter_content(chunk_size=2), b"")

                self.assertEqual(response.status_code, 206)
                self.assertRegex(response.headers.get("Content-Range", ""), r"^bytes 0-1/\d+$")
                self.assertEqual(len(body), 2)
            finally:
                response.close()
        finally:
            client.close()

    def test_season_crawl_follows_pagination(self):
        # The episode test never touches the season selectors, so an upstream
        # layout change would otherwise break whole-season downloads silently.
        client = Anime1Client(timeout=(10.0, 30.0), retries=1)
        try:
            season_url = current_season_url(client)
            first_page = parse_season_page(client.post_page(season_url))
            all_urls = Anime1Extractor(client).season_episode_urls(season_url)

            self.assertTrue(first_page.episode_urls)
            self.assertIsNotNone(first_page.next_url)
            self.assertGreater(len(all_urls), len(first_page.episode_urls))
            self.assertTrue(all(is_episode_url(url) for url in all_urls))
        finally:
            client.close()


@unittest.skipUnless(
    RUN_PW_INTEGRATION,
    "set ANICAT_RUN_PW_INTEGRATION=1 to run Anime1.pw smoke tests",
)
class Anime1PwSmokeTests(unittest.TestCase):
    def test_direct_video_source_supports_resume_contract(self):
        client = Anime1Client(timeout=(10.0, 30.0), retries=1)
        try:
            episode = Anime1Extractor(client).episode(PW_SMOKE_URL)
            response = client.request(
                "GET",
                episode.stream_url,
                headers={
                    "Accept-Encoding": "identity",
                    "Range": "bytes=0-1",
                },
                stream=True,
            )
            try:
                body = next(response.iter_content(chunk_size=2), b"")

                self.assertEqual(response.status_code, 206)
                self.assertRegex(response.headers.get("Content-Range", ""), r"^bytes 0-1/\d+$")
                self.assertEqual(len(body), 2)
            finally:
                response.close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
