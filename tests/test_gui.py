import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from anicat.gui import GuiEvent, run_download
from anicat.models import DownloadResult, Episode, JobReport


def drain(events: Queue[GuiEvent | None]) -> list[GuiEvent | None]:
    values: list[GuiEvent | None] = []
    while not events.empty():
        values.append(events.get_nowait())
    return values


class GuiWorkerTests(unittest.TestCase):
    def setUp(self):
        detector = patch("anicat.gui.find_ffmpeg", return_value="/bundled/ffmpeg")
        self.find_ffmpeg = detector.start()
        self.addCleanup(detector.stop)

    @patch("anicat.gui.AniCatService")
    def test_missing_ffmpeg_stops_before_resolving_or_downloading(self, factory):
        self.find_ffmpeg.return_value = None
        events: Queue[GuiEvent | None] = Queue()
        run_download(["https://anime1.me/1"], Path("/tmp/output"), events)
        values = drain(events)
        self.assertIn(GuiEvent("tool", text="missing"), values)
        self.assertIsNone(values[-1])
        factory.assert_not_called()

    @patch("anicat.gui.AniCatService")
    def test_parse_failure_always_releases_ui(self, factory):
        factory.return_value.collect_episode_urls.side_effect = ValueError("bad URL")
        events: Queue[GuiEvent | None] = Queue()
        run_download(["bad"], Path("/tmp/output"), events)
        values = drain(events)
        self.assertEqual(values[0], GuiEvent("status", text="正在檢查影片處理工具…"))
        self.assertIn(GuiEvent("tool", text="ready"), values)
        self.assertIn(GuiEvent("status", text="這批工作沒有完成", detail="bad URL"), values)
        self.assertIsNone(values[-1])
        factory.return_value.download_many.assert_not_called()

    @patch("anicat.gui.AniCatService")
    def test_expanded_urls_are_queued_before_download(self, factory):
        factory.return_value.collect_episode_urls.return_value = ["https://anime1.me/1"]
        events: Queue[GuiEvent | None] = Queue()
        run_download(["https://anime1.me/?cat=1"], Path("/tmp/output"), events)
        values = drain(events)
        status_event = values[0]
        assert isinstance(status_event, GuiEvent)
        self.assertEqual(status_event.kind, "status")
        queue_event = values[3]
        assert isinstance(queue_event, GuiEvent)
        self.assertEqual(queue_event, GuiEvent("queue", text="已排入 1 集", total=1))
        self.assertEqual(values[4], GuiEvent("item", url="https://anime1.me/1", state="queued"))
        self.assertIsNone(values[-1])
        self.assertEqual(
            factory.return_value.download_many.call_args.kwargs["on_done"].__name__, "done"
        )

    @patch("anicat.gui.AniCatService")
    def test_job_result_becomes_observable_item_state(self, factory):
        url = "https://anime1.me/1"
        factory.return_value.collect_episode_urls.return_value = [url]
        result = DownloadResult(
            Episode(url, "episode", "https://cdn.example/episode.mp4"),
            Path("/tmp/output/episode.mp4"),
            "skipped",
            10,
            10,
        )
        factory.return_value.download_many.side_effect = lambda _urls, on_done: on_done(
            JobReport(url=url, result=result)
        )
        events: Queue[GuiEvent | None] = Queue()
        run_download([url], Path("/tmp/output"), events)
        values = drain(events)
        self.assertIn(
            GuiEvent(
                "item",
                url=url,
                state="skipped",
                detail="/tmp/output/episode.mp4",
            ),
            values,
        )
        self.assertIsNone(values[-1])


if __name__ == "__main__":
    unittest.main()
