import os
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from anicat.gui import GuiEvent, process_gui_events, run_download, run_download_isolated
from anicat.models import DownloadResult, Episode, JobReport


def drain(events: Queue[GuiEvent | None]) -> list[GuiEvent | None]:
    values: list[GuiEvent | None] = []
    while not events.empty():
        values.append(events.get_nowait())
    return values


def _worker_reports(urls, output, events):
    events.put(GuiEvent("item", url=urls[0], state="validating"))
    events.put(GuiEvent("item", url=urls[0], state="done"))
    events.put(None)


def _worker_crashes(urls, output, events):
    os._exit(7)


class GuiProcessTests(unittest.TestCase):
    @patch("anicat.gui.run_download", _worker_reports)
    def test_spawned_worker_preserves_stage_and_completion_order(self):
        events: Queue[GuiEvent | None] = Queue()
        run_download_isolated(["https://anime1.me/1"], Path("/tmp/output"), events)
        values = drain(events)
        self.assertEqual(
            [event.state for event in values if event is not None], ["validating", "done"]
        )
        self.assertEqual(values.count(None), 1)
        self.assertIsNone(values[-1])

    @patch("anicat.gui.run_download", _worker_crashes)
    def test_worker_crash_reports_error_and_releases_ui(self):
        events: Queue[GuiEvent | None] = Queue()
        run_download_isolated([], Path("/tmp/output"), events)
        values = drain(events)
        self.assertEqual(len(values), 2)
        error = values[0]
        assert error is not None
        self.assertEqual(error.kind, "status")
        self.assertIn("退出碼 7", error.detail)
        self.assertIsNone(values[-1])

    @patch("anicat.gui.get_context", side_effect=OSError("spawn unavailable"))
    def test_start_failure_also_releases_ui(self, context):
        events: Queue[GuiEvent | None] = Queue()
        run_download_isolated([], Path("/tmp/output"), events)
        values = drain(events)
        error = values[0]
        assert error is not None
        self.assertIn("spawn unavailable", error.detail)
        self.assertIsNone(values[-1])


class GuiEventProcessingTests(unittest.TestCase):
    @patch("anicat.gui.monotonic", side_effect=[0, 0.009, 1, 1, 1])
    def test_yields_then_preserves_item_and_completion_order(self, clock):
        events: Queue[GuiEvent | None] = Queue()
        queued = GuiEvent("item", url="https://anime1.me/1", state="queued")
        done = GuiEvent("item", url="https://anime1.me/1", state="done")
        for event in (queued, done, None):
            events.put(event)
        handled: list[GuiEvent | None] = []

        self.assertTrue(process_gui_events(events, handled.append))
        self.assertEqual(handled, [queued])
        self.assertEqual(events.qsize(), 2)
        self.assertFalse(process_gui_events(events, handled.append))
        self.assertEqual(handled, [queued, done, None])
        self.assertTrue(events.empty())

    @patch("anicat.gui.monotonic", return_value=0)
    def test_fast_handlers_still_yield_before_tk_redraw_work_accumulates(self, clock):
        events: Queue[GuiEvent | None] = Queue()
        for index in range(12):
            events.put(GuiEvent("item", url=str(index), state="queued"))
        handled: list[GuiEvent | None] = []

        self.assertTrue(process_gui_events(events, handled.append))
        self.assertEqual(len(handled), 8)
        self.assertEqual(events.qsize(), 4)
        self.assertFalse(process_gui_events(events, handled.append))
        self.assertEqual(
            [event.url for event in handled if event is not None], list(map(str, range(12)))
        )

    def test_empty_queue_does_not_call_handler(self):
        events: Queue[GuiEvent | None] = Queue()
        handled: list[GuiEvent | None] = []
        self.assertFalse(process_gui_events(events, handled.append))
        self.assertEqual(handled, [])


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
    def test_real_stage_callbacks_become_gui_item_states(self, factory):
        url = "https://anime1.cc/1-1-1"
        factory.return_value.collect_episode_urls.return_value = [url]

        def download(_urls, *, on_done, on_stage):
            for stage in ("resolving", "downloading", "verifying", "merging", "validating"):
                on_stage(url, stage)
            on_done(JobReport(url=url, error="decode rejected"))

        factory.return_value.download_many.side_effect = download
        events: Queue[GuiEvent | None] = Queue()
        run_download([url], Path("/tmp/output"), events)
        values = drain(events)
        items = [event for event in values if event is not None and event.kind == "item"]
        self.assertEqual(
            [event.state for event in items],
            ["queued", "resolving", "downloading", "verifying", "merging", "validating", "error"],
        )
        self.assertTrue(all(event.detail for event in items[1:]))
        self.assertNotIn("done", [event.state for event in items])
        self.assertIsNone(values[-1])

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
        factory.return_value.download_many.side_effect = lambda _urls, on_done, on_stage: on_done(
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
