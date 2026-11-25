"""Entry point for the frozen desktop app."""

import os
import sys
from multiprocessing import freeze_support
from pathlib import Path
from queue import Queue

from anicat.gui import GuiEvent, main, run_download_isolated


def smoke_test() -> int:
    """Build the real GUI and check the frozen worker's bundled FFmpeg."""
    dpi_values = (None, 96, 120, 144, 192) if sys.platform == "win32" else (None,)
    for dpi in dpi_values:
        if main(smoke_test=True, _smoke_dpi=dpi) != 0:
            return 1
    os.environ["PATH"] = ""
    os.environ.pop("IMAGEIO_FFMPEG_EXE", None)

    events: Queue[GuiEvent | None] = Queue()
    run_download_isolated([], Path.cwd(), events)
    ready = any(event.kind == "tool" and event.text == "ready" for event in iter(events.get, None))
    return 0 if ready else 1


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(smoke_test() if sys.argv[1:] == ["--smoke-test"] else main())
