import os
import unittest

from anicat.client import Anime1Client
from anicat.extractor import Anime1Extractor

RUN_INTEGRATION = os.environ.get("ANICAT_RUN_INTEGRATION") == "1"
SMOKE_URL = os.environ.get("ANICAT_SMOKE_URL", "https://anime1.me/28979")


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


if __name__ == "__main__":
    unittest.main()
