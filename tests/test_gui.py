import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from anicat.gui import run_download


class GuiWorkerTests(unittest.TestCase):
    @patch("anicat.gui.AniCatService")
    def test_parse_failure_always_releases_ui(self, factory):
        factory.return_value.collect_episode_urls.side_effect = ValueError("bad URL")
        events = Queue()
        run_download(["bad"], Path("/tmp/output"), events)
        self.assertIn("bad URL", events.get_nowait())
        self.assertIsNone(events.get_nowait())
        factory.return_value.download_many.assert_not_called()

    @patch("anicat.gui.AniCatService")
    def test_expanded_urls_are_downloaded(self, factory):
        factory.return_value.collect_episode_urls.return_value = ["https://anime1.me/1"]
        events = Queue()
        run_download(["https://anime1.me/?cat=1"], Path("/tmp/output"), events)
        self.assertIn("1", events.get_nowait())
        self.assertIsNone(events.get_nowait())
        self.assertEqual(
            factory.return_value.download_many.call_args.args[0], ["https://anime1.me/1"]
        )
