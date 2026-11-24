import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from queue import Queue
from threading import Event, Timer
from unittest.mock import patch

from anicat.gui import (
    GuiEvent,
    process_gui_events,
    run_download,
    run_download_isolated,
    stop_download_worker,
)
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


def _worker_waits(urls, output, events):
    (output / "video.mp4.part").write_bytes(b"partial")
    work = output / f".anicat-hls-{os.getpid()}-test"
    work.mkdir()
    (work / "segment.ts").write_bytes(b"segment")
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; from pathlib import Path; time.sleep(1); "
            f"Path({str(output / 'orphan.txt')!r}).write_text('still running')",
        ]
    )
    events.put(GuiEvent("status", text="worker ready"))
    time.sleep(30)


def _worker_burst(urls, output, events):
    events.put(GuiEvent("status", text="worker ready"))
    for index in range(1000):
        events.put(GuiEvent("item", url=str(index), state="done", detail="x" * 500))
    events.put(None)
    if urls == ["crash"]:
        events.close()
        events.join_thread()
        os._exit(7)


class GuiProcessTests(unittest.TestCase):
    @patch("anicat.gui.run_download", _worker_burst)
    def test_failed_stop_drains_large_queue_and_accepts_natural_completion(self):
        self.check_failed_stop_natural_exit([])

    @patch("anicat.gui.run_download", _worker_burst)
    def test_failed_stop_still_reports_abnormal_exit_after_completion_signal(self):
        self.check_failed_stop_natural_exit(["crash"])

    def check_failed_stop_natural_exit(self, urls):
        cancel = Event()
        events: Queue[GuiEvent | None] = Queue()

        class Sink:
            def put(self, item: GuiEvent | None) -> None:
                events.put(item)
                if item is not None and item.text == "worker ready":
                    cancel.set()

        attempts = []

        def fail_first_stop(process):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("taskkill failed")
            stop_download_worker(process)

        # A broken queue-draining path must fail the test instead of hanging CI.
        watchdog = Timer(10, cancel.set)
        watchdog.start()
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("anicat.gui.stop_download_worker", side_effect=fail_first_stop),
            ):
                run_download_isolated(urls, Path(directory), Sink(), cancel)
        finally:
            watchdog.cancel()
            watchdog.join()
        values = drain(events)
        self.assertEqual(
            [event.url for event in values if event is not None and event.kind == "item"],
            [str(index) for index in range(1000)],
        )
        self.assertEqual(values.count(None), 1)
        self.assertIsNone(values[-1])
        self.assertFalse(cancel.is_set(), "natural completion was misreported as cancelled")
        self.assertNotIn(GuiEvent("status", text="已停止下載"), values)
        if urls:
            self.assertTrue(any(event and "退出碼 7" in event.detail for event in values))
        else:
            self.assertEqual(len(attempts), 1, "needed another stop after work had finished")
            self.assertFalse(
                any(event and event.kind == "status" and event.detail for event in values)
            )

    @patch("anicat.gui.stop_download_worker")
    @patch("anicat.gui.get_context")
    def test_failed_stop_keeps_control_until_retry_succeeds(self, context_factory, stop):
        process = context_factory.return_value.Process.return_value
        process.is_alive.return_value = True
        cancel = Event()
        cancel.set()
        events: Queue[GuiEvent | None] = Queue()
        stop_succeeded = False

        def try_stop(_process):
            nonlocal stop_succeeded
            if stop.call_count == 1:
                raise OSError("taskkill failed")
            stop_succeeded = True
            process.is_alive.return_value = False

        stop.side_effect = try_stop

        test = self

        class Sink:
            def put(self, item: GuiEvent | None) -> None:
                if item is None:
                    test.assertTrue(stop_succeeded, "released UI while worker was alive")
                events.put(item)
                if item is not None and item.kind == "stop_error":
                    test.assertTrue(process.is_alive())
                    test.assertFalse(cancel.is_set())
                    process.close.assert_not_called()
                    cancel.set()  # User retries the stop button.

        with tempfile.TemporaryDirectory() as directory:
            run_download_isolated([], Path(directory), Sink(), cancel)
        values = drain(events)
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(values[0], GuiEvent("stop_error", detail="taskkill failed"))
        self.assertIn(GuiEvent("status", text="已停止下載"), values)
        self.assertEqual(values.count(None), 1)
        self.assertIsNone(values[-1])
        process.close.assert_called_once()

    @patch("anicat.gui.run_download", _worker_waits)
    def test_cancel_stops_worker_and_descendants_preserving_other_files(self):
        cancel = Event()
        events: Queue[GuiEvent | None] = Queue()

        class Sink:
            def put(self, item: GuiEvent | None) -> None:
                events.put(item)
                if item is not None and item.text == "worker ready":
                    cancel.set()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            completed = output / "completed.mp4"
            completed.write_bytes(b"completed")
            unrelated = output / ".anicat-hls-other-worker"
            unrelated.mkdir()
            run_download_isolated([], output, Sink(), cancel)
            values = drain(events)
            self.assertIn(GuiEvent("status", text="已停止下載"), values)
            self.assertEqual(values.count(None), 1)
            self.assertIsNone(values[-1])
            self.assertEqual(completed.read_bytes(), b"completed")
            self.assertEqual((output / "video.mp4.part").read_bytes(), b"partial")
            self.assertEqual(list(output.glob(".anicat-hls-*")), [unrelated])
            time.sleep(1.2)
            self.assertFalse((output / "orphan.txt").exists())
            cancel.clear()
            with patch("anicat.gui.run_download", _worker_reports):
                run_download_isolated(["https://anime1.me/1"], output, events, cancel)
            restarted = drain(events)
            first, second = restarted[:2]
            assert first is not None and second is not None
            self.assertEqual(first.state, "validating")
            self.assertEqual(second.state, "done")
            self.assertIsNone(restarted[-1])

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
                detail=str(result.path),
            ),
            values,
        )
        self.assertIsNone(values[-1])


if __name__ == "__main__":
    unittest.main()
