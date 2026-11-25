"""Small, focused desktop workspace sharing the CLI download service."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import Literal, Protocol

from .ffmpeg import FFMPEG_CREATION_FLAGS, find_ffmpeg
from .models import DownloadStage, JobReport
from .options import DownloadOptions
from .service import AniCatService
from .urls import split_urls

GuiItemState = DownloadStage | Literal["queued", "done", "skipped", "error", "cancelled"]


@dataclass(frozen=True)
class GuiEvent:
    """Thread-to-UI message; no guessed progress is sent."""

    kind: Literal["status", "queue", "item", "tool", "stop_error"]
    text: str = ""
    url: str = ""
    state: GuiItemState | None = None
    detail: str = ""
    total: int = 0


class GuiEventSink(Protocol):
    def put(self, item: GuiEvent | None) -> None:
        """Send an event to the UI or its process relay."""

        ...


def process_gui_events(
    events: Queue[GuiEvent | None], handle: Callable[[GuiEvent | None], None]
) -> bool:
    """Preserve event order while yielding after eight events or an 8ms slice."""

    deadline = monotonic() + 0.008
    # Bound deferred widget layout/redraw work as well as Python handler time.
    for _ in range(8):
        try:
            event = events.get_nowait()
        except Empty:
            return False
        handle(event)
        if monotonic() >= deadline:
            return True
    return True


def _download_worker(
    urls: list[str],
    output: Path,
    events: GuiEventSink,
    download: Callable[[list[str], Path, GuiEventSink], None],
) -> None:
    if sys.platform != "win32":
        os.setsid()
    download(urls, output, events)


def stop_download_worker(process: BaseProcess) -> None:
    """Stop only this worker and its FFmpeg descendants, then reap the worker."""
    if process.pid is None:
        return
    if sys.platform == "win32":
        result = subprocess.run(
            [
                str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            capture_output=True,
            timeout=10,
            creationflags=FFMPEG_CREATION_FLAGS,
        )
        if result.returncode and process.is_alive():
            raise RuntimeError("無法停止下載工作，請重試。")
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.is_alive():
                process.terminate()
    process.join(timeout=2)
    if sys.platform != "win32":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS may reject signalling a group whose remaining members have exited.
            if process.is_alive():
                raise
    if process.is_alive():
        process.kill()
    process.join(timeout=2)
    if process.is_alive():
        raise RuntimeError("下載工作仍在執行，請再次停止。")


def run_download(urls: list[str], output: Path, events: GuiEventSink) -> None:
    """Run off the UI thread and send only observable state changes."""

    try:
        events.put(GuiEvent("status", text="正在檢查影片處理工具…"))
        if find_ffmpeg() is None:
            events.put(GuiEvent("tool", text="missing"))
            return
        events.put(GuiEvent("tool", text="ready"))
        events.put(GuiEvent("status", text="正在整理網址…"))
        service = AniCatService(DownloadOptions(output))
        episodes = service.collect_episode_urls(urls)
        if not episodes:
            raise ValueError("找不到可下載的集數")
        events.put(GuiEvent("queue", text=f"已排入 {len(episodes)} 集", total=len(episodes)))
        for url in episodes:
            events.put(GuiEvent("item", url=url, state="queued"))

        def done(report: JobReport) -> None:
            if report.error:
                events.put(GuiEvent("item", url=report.url, state="error", detail=report.error))
            elif report.result:
                state: GuiItemState = "skipped" if report.result.status == "skipped" else "done"
                events.put(
                    GuiEvent(
                        "item",
                        url=report.url,
                        state=state,
                        detail=str(report.result.path),
                    )
                )

        def stage(url: str, value: DownloadStage) -> None:
            events.put(GuiEvent("item", url=url, state=value, detail=STAGE_DETAIL[value]))

        service.download_many(episodes, on_done=done, on_stage=stage)
    except Exception as error:
        events.put(GuiEvent("status", text="這批工作沒有完成", detail=str(error)))
    finally:
        events.put(None)


def run_download_isolated(
    urls: list[str], output: Path, events: GuiEventSink, cancel: Event | None = None
) -> None:
    """Relay a spawned worker's events without sharing its GIL with Tk."""

    channel = None
    process = None
    needs_join = False
    cancel = cancel if cancel is not None else Event()

    def stop_worker() -> bool:
        nonlocal needs_join
        assert process is not None and channel is not None
        while True:
            try:
                stop_download_worker(process)
                return True
            except Exception as error:
                cancel.clear()
                events.put(GuiEvent("stop_error", detail=str(error)))
                # Keep relaying so the worker can flush its Queue and exit naturally.
                while not cancel.is_set():
                    try:
                        event = channel.get(timeout=0.1)
                    except Empty:
                        if process.is_alive():
                            continue
                        process.join()
                        needs_join = False
                        raise RuntimeError(
                            f"下載工作程序意外結束（退出碼 {process.exitcode}），請重試。"
                        ) from None
                    if event is None:
                        process.join()
                        needs_join = False
                        if process.exitcode != 0:
                            raise RuntimeError(
                                f"下載工作程序異常退出（退出碼 {process.exitcode}），請重試。"
                            ) from None
                        cancel.clear()
                        return False
                    events.put(event)

    try:
        context = get_context("spawn")
        channel = context.Queue()
        process = context.Process(
            target=_download_worker, args=(urls, output, channel, run_download), daemon=True
        )
        process.start()
        needs_join = True
        while True:
            if cancel.is_set():
                stopped = stop_worker()
                needs_join = False
                if not stopped:
                    break
                cancel.set()
                # HLS intermediates belong to this worker; direct MP4 .part files stay resumable.
                for temporary in output.glob(f".anicat-hls-{process.pid}-*"):
                    if temporary.is_dir() and not temporary.is_symlink():
                        shutil.rmtree(temporary)
                events.put(GuiEvent("status", text="已停止下載"))
                break
            try:
                event = channel.get(timeout=0.1)
            except Empty:
                if not process.is_alive():
                    raise RuntimeError(
                        f"下載工作程序意外結束（退出碼 {process.exitcode}），請重試。"
                    ) from None
                continue
            if event is None:
                process.join()
                needs_join = False
                if process.exitcode != 0:
                    raise RuntimeError(
                        f"下載工作程序異常退出（退出碼 {process.exitcode}），請重試。"
                    )
                break
            events.put(event)
    except Exception as error:
        events.put(GuiEvent("status", text="這批工作沒有完成", detail=str(error)))
    finally:
        try:
            if process is not None:
                if needs_join:
                    stop_worker()
                process.close()
            if channel is not None:
                channel.close()
                channel.join_thread()
        finally:
            events.put(None)


COLORS = {
    "paper": "#F5F1E8",
    "surface": "#FFFCF6",
    "ink": "#202820",
    "muted": "#687264",
    "line": "#D9D6CC",
    "accent": "#BD4C2D",
    "accent_dark": "#8F351F",
    "dark": "#203027",
    "dark_muted": "#AFB9AA",
    "queued_bg": "#E6EBE0",
    "queued_fg": "#4D5A4A",
    "done_bg": "#DCE9D7",
    "done_fg": "#2F603B",
    "skipped_bg": "#EEE8D9",
    "skipped_fg": "#6A5C3B",
    "error_bg": "#F5DDD5",
    "error_fg": "#8D321F",
}

STATUS_COPY: dict[GuiItemState, tuple[str, str, str]] = {
    "queued": ("等待中", COLORS["queued_bg"], COLORS["queued_fg"]),
    "resolving": ("解析來源", COLORS["queued_bg"], COLORS["queued_fg"]),
    "downloading": ("下載中", COLORS["queued_bg"], COLORS["queued_fg"]),
    "verifying": ("檢查分段", COLORS["queued_bg"], COLORS["queued_fg"]),
    "merging": ("合併影片", COLORS["queued_bg"], COLORS["queued_fg"]),
    "validating": ("全片驗證", COLORS["queued_bg"], COLORS["queued_fg"]),
    "done": ("完成", COLORS["done_bg"], COLORS["done_fg"]),
    "skipped": ("已存在", COLORS["skipped_bg"], COLORS["skipped_fg"]),
    "error": ("失敗", COLORS["error_bg"], COLORS["error_fg"]),
    "cancelled": ("已停止", COLORS["skipped_bg"], COLORS["skipped_fg"]),
}


STAGE_DETAIL: dict[DownloadStage, str] = {
    "resolving": "正在解析作品與播放來源…",
    "downloading": "正在下載影片並檢查接收內容…",
    "verifying": "正在檢查分段銜接與整理時間軸…",
    "merging": "正在合併影片與處理音訊…",
    "validating": "正在完整解碼驗證，通過後才會完成…",
}


def main(*, smoke_test: bool = False, _smoke_dpi: int | None = None) -> int:
    """Open the AniCat desktop workspace."""

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from tkinter import font as tkfont
    except ImportError:
        print("圖形介面需要 Python 的 Tk 支援。請安裝含 Tk 的 Python，或使用 anicat CLI。")
        return 1

    if sys.platform == "win32":
        import ctypes

        if not getattr(sys, "frozen", False):
            ctypes.windll.user32.SetProcessDPIAware()
        # Keep the taskbar identity separate from the Python/Tk host.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.github.heitang.anicat")

    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"無法開啟圖形介面：{error}")
        return 1

    dpi = root.winfo_fpixels("1i") if sys.platform == "win32" else 96.0
    if smoke_test and _smoke_dpi is not None:
        dpi = float(_smoke_dpi)
        root.tk.call("tk", "scaling", dpi / 72)
    scale = dpi / 96

    def px(value: int) -> int:
        return round(value * scale)

    root.title("AniCat · Download workspace")
    root.geometry(f"{px(1060)}x{px(720)}")
    root.minsize(px(820), px(660))
    root.configure(bg=COLORS["paper"])

    assets = Path(__file__).with_name("assets")
    app_icon = tk.PhotoImage(master=root, file=str(assets / "anicat-logo-mark.png"))
    if sys.platform == "win32":
        root.iconphoto(True, app_icon)
    ratio = Fraction(scale).limit_denominator(8)

    def image_at_dpi(image: tk.PhotoImage, divisor: int) -> tk.PhotoImage:
        return image.zoom(ratio.numerator).subsample(divisor * ratio.denominator)

    logo = image_at_dpi(app_icon, 8)
    mascot_source = tk.PhotoImage(master=root, file=str(assets / "anicat-mascot-mark.png"))
    mascot = image_at_dpi(mascot_source, 4)

    body_family, mono_family = "Helvetica Neue", "Menlo"
    if sys.platform == "win32":
        families = set(tkfont.families(root))
        body_family = next(
            (
                name
                for name in ("Microsoft JhengHei UI", "Microsoft JhengHei", "Segoe UI")
                if name in families
            ),
            tkfont.nametofont("TkDefaultFont", root).actual("family"),
        )
        mono_family = (
            "Consolas"
            if "Consolas" in families
            else tkfont.nametofont("TkFixedFont", root).actual("family")
        )

    title_font = (body_family, 18, "bold")
    body_font = (body_family, 11)
    small_font = (body_family, 9)
    mono_font = (mono_family, 9)

    def label(parent: tk.Misc, text: str, *, font=body_font, fg=COLORS["ink"], **kwargs):
        return tk.Label(parent, text=text, font=font, fg=fg, bg=parent.cget("bg"), **kwargs)

    def card(parent: tk.Misc, *, bg=COLORS["surface"], **kwargs):
        return tk.Frame(
            parent,
            bg=bg,
            highlightbackground=COLORS["line"],
            highlightthickness=px(1),
            **kwargs,
        )

    outer = tk.Frame(root, bg=COLORS["paper"])
    outer.pack(fill="both", expand=True, padx=px(34), pady=px(20))
    header = tk.Frame(outer, bg=COLORS["paper"])
    header.pack(fill="x", pady=(0, px(14)))
    brand_row = tk.Frame(header, bg=COLORS["paper"])
    brand_row.pack(side="left")
    label(brand_row, "", image=logo).pack(side="left", padx=(0, px(8)))
    brand_font = (body_family, 22, "bold")
    label(brand_row, "AniCat", font=brand_font, padx=0, borderwidth=0, highlightthickness=0).pack(
        side="left"
    )
    label(
        brand_row,
        ".",
        font=brand_font,
        fg=COLORS["accent"],
        padx=0,
        borderwidth=0,
        highlightthickness=0,
    ).pack(side="left")
    label(header, "LOCAL DOWNLOAD WORKSPACE  /  01", font=mono_font, fg=COLORS["muted"]).pack(
        side="right", pady=px(7)
    )
    tk.Frame(outer, bg=COLORS["line"], height=px(1)).pack(fill="x", pady=(0, px(14)))

    content = tk.Frame(outer, bg=COLORS["paper"])
    content.pack(fill="both", expand=True)
    content.grid_columnconfigure(0, weight=6)
    content.grid_columnconfigure(1, weight=5)
    content.grid_rowconfigure(1, weight=1)

    intro = tk.Frame(content, bg=COLORS["paper"])
    intro.grid(row=0, column=0, sticky="ew", pady=(0, px(14)))
    label(intro, "01  /  DOWNLOAD", font=mono_font, fg=COLORS["accent"]).pack(anchor="w")
    label(intro, "把想看的，留在本機。", font=(body_family, 22, "bold")).pack(
        anchor="w", pady=(px(4), px(3))
    )
    label(
        intro,
        "貼上支援的 Anime1 網址，選好位置，其他交給 AniCat。",
        font=body_font,
        fg=COLORS["muted"],
        justify="left",
    ).pack(anchor="w")

    form = tk.Frame(content, bg=COLORS["paper"])
    form.grid(row=1, column=0, sticky="nsew", padx=(0, px(22)))
    form.grid_columnconfigure(0, weight=1)

    url_card = card(form)
    url_card.grid(row=0, column=0, sticky="ew")
    url_card.grid_columnconfigure(0, weight=1)
    url_top = tk.Frame(url_card, bg=COLORS["surface"])
    url_top.grid(row=0, column=0, sticky="ew", padx=px(20), pady=(px(17), px(9)))
    label(url_top, "網址佇列", font=title_font).pack(side="left")
    label(url_top, "一行一個網址", font=small_font, fg=COLORS["muted"]).pack(
        side="right", pady=px(4)
    )
    urls = tk.Text(
        url_card,
        height=4,
        wrap="word",
        font=mono_font,
        bg="#F8F5ED",
        fg=COLORS["ink"],
        insertbackground=COLORS["accent"],
        relief="flat",
        borderwidth=0,
        padx=px(14),
        pady=px(12),
        highlightthickness=px(1),
        highlightbackground=COLORS["line"],
        highlightcolor=COLORS["accent"],
    )
    urls.grid(row=1, column=0, sticky="ew", padx=px(20))
    label(
        url_card,
        "支援單集、分類與季度網址；多個網址會一起加入下載佇列。",
        font=small_font,
        fg=COLORS["muted"],
        justify="left",
    ).grid(row=2, column=0, sticky="w", padx=px(20), pady=(px(9), px(18)))

    output_card = card(form)
    output_card.grid(row=1, column=0, sticky="ew", pady=(px(16), 0))
    output_card.grid_columnconfigure(0, weight=1)
    output_title = tk.Frame(output_card, bg=COLORS["surface"])
    output_title.grid(row=0, column=0, sticky="ew", padx=px(20), pady=(px(17), px(9)))
    label(output_title, "儲存位置", font=title_font).pack(side="left")
    label(output_title, "完成後即時顯示", font=small_font, fg=COLORS["muted"]).pack(
        side="right", pady=px(4)
    )
    output = tk.StringVar(value=str(Path.home() / "Downloads" / "AniCat"))
    output_row = tk.Frame(output_card, bg=COLORS["surface"])
    output_row.grid(row=1, column=0, sticky="ew", padx=px(20))
    output_row.grid_columnconfigure(0, weight=1)
    entry = tk.Entry(
        output_row,
        textvariable=output,
        font=mono_font,
        bg="#F8F5ED",
        fg=COLORS["ink"],
        relief="flat",
        borderwidth=0,
        insertbackground=COLORS["accent"],
        highlightthickness=px(1),
        highlightbackground=COLORS["line"],
        highlightcolor=COLORS["accent"],
    )
    entry.grid(row=0, column=0, sticky="ew", ipady=px(10), padx=(0, px(10)))

    def choose() -> None:
        selected = filedialog.askdirectory(parent=root, title="選擇 AniCat 儲存資料夾")
        if selected:
            output.set(selected)

    browse = tk.Button(
        output_row,
        text="選擇資料夾",
        command=choose,
        font=(body_family, 10, "bold"),
        bg=COLORS["surface"],
        fg=COLORS["ink"],
        activebackground="#E9E5DB",
        activeforeground=COLORS["ink"],
        relief="flat",
        highlightbackground=COLORS["line"],
        highlightthickness=px(1),
        padx=px(14),
        pady=px(8),
        cursor="hand2",
    )
    browse.grid(row=0, column=1)
    label(
        output_card,
        "Direct MP4 來源會保留 .part 暫存檔；已有完成檔案會跳過。",
        font=small_font,
        fg=COLORS["muted"],
        justify="left",
    ).grid(row=2, column=0, sticky="w", padx=px(20), pady=(px(9), px(18)))

    state = {"busy": False, "error": False, "closing": False, "closed": False}
    cancel = Event()
    events: Queue[GuiEvent | None] = Queue()
    item_rows: dict[str, tuple[tk.Frame, tk.Label, tk.Label, tk.Label]] = {}
    total = tk.IntVar(value=0)
    finished = tk.IntVar(value=0)
    summary = tk.StringVar(value="尚未開始")
    status_text = tk.StringVar(value="就緒")

    label(content, "", image=mascot, borderwidth=0, highlightthickness=0, padx=0, pady=0).grid(
        row=0, column=1, sticky="se", padx=(0, px(22))
    )
    queue_panel = tk.Frame(content, bg=COLORS["dark"], padx=px(22), pady=px(20))
    queue_panel.grid(row=1, column=1, sticky="nsew")
    queue_panel.grid_rowconfigure(3, weight=1)
    queue_panel.grid_columnconfigure(0, weight=1)
    queue_header = tk.Frame(queue_panel, bg=COLORS["dark"])
    queue_header.grid(row=0, column=0, sticky="ew")
    label(queue_header, "02  /  QUEUE", font=mono_font, fg="#D88A70").pack(side="left")
    label(queue_header, "STATE", font=mono_font, fg=COLORS["dark_muted"]).pack(side="right")
    label(queue_panel, "這批下載", font=title_font, fg="#F5F2EA").grid(
        row=1, column=0, sticky="w", pady=(px(15), 0)
    )
    tk.Label(
        queue_panel,
        textvariable=summary,
        font=small_font,
        fg=COLORS["dark_muted"],
        bg=COLORS["dark"],
        anchor="w",
    ).grid(row=2, column=0, sticky="ew", pady=(px(4), px(16)))

    queue_canvas = tk.Canvas(queue_panel, bg=COLORS["dark"], highlightthickness=0)
    queue_canvas.grid(row=3, column=0, sticky="nsew")
    queue_scroll = tk.Scrollbar(
        queue_panel,
        orient="vertical",
        command=queue_canvas.yview,
        bg=COLORS["dark"],
        troughcolor=COLORS["dark"],
        activebackground=COLORS["dark_muted"],
        width=px(8),
        bd=0,
        highlightthickness=0,
        relief="flat",
    )
    queue_scroll.grid(row=3, column=1, sticky="ns")
    queue_scroll.grid_remove()
    queue_canvas.configure(yscrollcommand=queue_scroll.set)
    queue_inner = tk.Frame(queue_canvas, bg=COLORS["dark"])
    queue_window = queue_canvas.create_window((0, 0), window=queue_inner, anchor="nw")

    def update_queue_scroll(_event=None) -> None:
        queue_canvas.configure(scrollregion=queue_canvas.bbox("all"))
        overflow = queue_inner.winfo_reqheight() > queue_canvas.winfo_height()
        if overflow:
            queue_scroll.grid()
        else:
            queue_scroll.grid_remove()

    queue_inner.bind("<Configure>", update_queue_scroll)

    def scroll_queue(event) -> str:
        direction = -1 if event.delta > 0 else 1
        queue_canvas.yview_scroll(direction, "units")
        return "break"

    def queue_wheel(event):
        widget = event.widget
        while widget is not None:
            if widget == queue_canvas:
                if event.num in (4, 5):
                    queue_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
                    return "break"
                return scroll_queue(event)
            widget = getattr(widget, "master", None)
        return None

    root.bind("<MouseWheel>", queue_wheel)
    if sys.platform not in ("win32", "darwin"):
        root.bind("<Button-4>", queue_wheel)
        root.bind("<Button-5>", queue_wheel)

    def resize_queue(event) -> None:
        queue_canvas.itemconfigure(queue_window, width=event.width)
        update_queue_scroll()

    queue_canvas.bind("<Configure>", resize_queue)
    empty_label = label(
        queue_inner,
        "貼上網址後，這裡會顯示\n每一集的實際狀態。",
        font=body_font,
        fg=COLORS["dark_muted"],
        justify="left",
    )
    empty_label.pack(anchor="w", pady=(px(3), px(16)))

    bottom = tk.Frame(queue_panel, bg=COLORS["dark"])
    bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(px(18), 0))
    bottom.grid_columnconfigure(0, weight=1)
    tk.Label(
        bottom,
        textvariable=status_text,
        font=(body_family, 10, "bold"),
        fg="#F5F2EA",
        bg=COLORS["dark"],
        anchor="w",
    ).grid(row=0, column=0, sticky="w")

    def set_badge(badge: tk.Label, state_name: GuiItemState) -> None:
        text, bg, fg = STATUS_COPY[state_name]
        badge.configure(text=text, bg=bg, fg=fg)

    def ensure_item(url: str) -> tuple[tk.Frame, tk.Label, tk.Label, tk.Label]:
        existing = item_rows.get(url)
        if existing:
            return existing
        row = tk.Frame(queue_inner, bg="#2B3B31", padx=px(12), pady=px(10))
        row.pack(fill="x", pady=(0, px(8)))
        # Reserve four CJK characters so stage changes do not reflow the row.
        badge = tk.Label(
            row, text="等待中", font=(body_family, 8, "bold"), width=8, padx=px(7), pady=px(4)
        )
        badge.pack(side="left", anchor="n")
        set_badge(badge, "queued")
        middle = tk.Frame(row, bg="#2B3B31")
        middle.pack(side="left", fill="x", expand=True, padx=(px(10), px(8)))
        title = tk.Label(
            middle,
            text=url,
            font=mono_font,
            fg="#F5F2EA",
            bg="#2B3B31",
            anchor="w",
            justify="left",
            width=1,
            padx=0,
            borderwidth=0,
        )
        title.pack(fill="x")
        detail = tk.Label(
            middle,
            text="等待開始",
            font=small_font,
            fg=COLORS["dark_muted"],
            bg="#2B3B31",
            anchor="w",
            justify="left",
            width=1,
            padx=0,
            borderwidth=0,
        )
        detail.pack(fill="x", pady=(px(4), 0))

        def wrap_text(event) -> None:
            # Wrap inside the allocated column, including after DPI scaling or resizing.
            title.configure(wraplength=max(1, event.width))
            detail.configure(wraplength=max(1, event.width))

        middle.bind("<Configure>", wrap_text)
        item_rows[url] = (row, badge, title, detail)
        empty_label.pack_forget()
        return item_rows[url]

    def clear_queue() -> None:
        for row, _badge, _title, _detail in item_rows.values():
            row.destroy()
        item_rows.clear()
        empty_label.pack(anchor="w", pady=(px(3), px(16)))
        total.set(0)
        finished.set(0)
        summary.set("尚未開始")
        state["error"] = False

    def handle(event: GuiEvent | None) -> None:
        if event is None:
            state["busy"] = False
            if cancel.is_set():
                for _row, badge, _title, detail in item_rows.values():
                    if badge.cget("text") not in ("完成", "已存在", "失敗"):
                        set_badge(badge, "cancelled")
                        detail.configure(text="已停止；重新開始即可重試。")
            style_download()
            if state["closing"]:
                root.destroy()
                return
            state["closing"] = False
            status_text.set(
                "有工作需要處理"
                if state["error"]
                else "已停止下載"
                if cancel.is_set()
                else "這批工作已結束"
            )
        elif event.kind == "stop_error":
            state["closing"] = False
            style_download()
            status_text.set("停止失敗，請再次按「停止下載」")
            messagebox.showerror("無法停止下載", event.detail, parent=root)
        elif event.kind == "tool":
            if event.text == "missing":
                state["error"] = True
                tool_status.set("影片處理工具未就緒，尚未開始下載")
                show_tool_help()
            else:
                tool_status.set("影片處理工具：已就緒")
        elif event.kind == "status":
            status_text.set(event.text)
            if event.detail:
                state["error"] = True
                messagebox.showerror("下載無法開始", event.detail, parent=root)
        elif event.kind == "queue":
            total.set(event.total)
            summary.set(f"0 / {event.total} 集已完成")
            status_text.set("正在下載與驗證，請查看各集狀態")
        elif event.kind == "item" and event.url and event.state:
            _row, badge, _title, detail = ensure_item(event.url)
            set_badge(badge, event.state)
            if event.state == "error":
                state["error"] = True
                detail.configure(text=event.detail or "下載失敗", fg="#F0A18C")
            elif event.state == "done":
                finished.set(finished.get() + 1)
                detail.configure(text=event.detail, fg="#BBD1BA")
            elif event.state == "skipped":
                finished.set(finished.get() + 1)
                detail.configure(text=event.detail, fg="#D7CBA7")
            else:
                detail.configure(text=event.detail or "等待下載", fg=COLORS["dark_muted"])
            summary.set(f"{finished.get()} / {total.get()} 集已完成")

    def start() -> None:
        if state["busy"]:
            return
        values = split_urls([urls.get("1.0", "end")])
        if not values:
            messagebox.showerror("需要網址", "請貼上一個 Anime1 單集或全集網址。", parent=root)
            urls.focus_set()
            return
        if not output.get().strip():
            messagebox.showerror("需要儲存位置", "請選擇影片儲存資料夾。", parent=root)
            entry.focus_set()
            return
        clear_queue()
        cancel.clear()
        state["busy"] = True
        state["error"] = False
        style_download()
        status_text.set("正在連線與解析…")
        Thread(
            target=run_download_isolated,
            args=(values, Path(output.get()).expanduser(), events, cancel),
            daemon=True,
        ).start()

    def style_download(*, hover: bool = False) -> None:
        stopping = state["busy"] and cancel.is_set()
        download_button.configure(
            text="正在停止…" if stopping else "停止下載" if state["busy"] else "開始下載",
            state="disabled" if stopping else "normal",
            bg=COLORS["accent_dark"] if stopping or hover else COLORS["accent"],
            fg="#D9C1B8" if stopping else "#FFFFFF",
            disabledforeground="#D9C1B8",
            cursor="" if stopping else "hand2",
            takefocus=not stopping,
        )

    poll_id: str | None = None

    def poll() -> None:
        nonlocal poll_id
        poll_id = None
        yielded = process_gui_events(events, handle)
        if not state["closed"]:
            poll_id = root.after(1 if yielded else 100, poll)

    def window_destroyed(event) -> None:
        if event.widget == root:
            state["closed"] = True
            if poll_id is not None:
                root.after_cancel(poll_id)

    root.bind("<Destroy>", window_destroyed)

    def show_tool_help() -> None:
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "影片處理工具 FFmpeg",
                "桌面 App 已內附 FFmpeg。如果檢查失敗，請重新下載並完整解壓縮桌面 App。",
                parent=root,
            )
            return

        import webbrowser

        dialog = tk.Toplevel(root)
        dialog.title("準備影片處理工具")
        dialog.configure(bg=COLORS["paper"])
        dialog.transient(root)
        panel = tk.Frame(dialog, bg=COLORS["paper"], padx=px(24), pady=px(22))
        panel.pack(fill="both", expand=True)
        label(panel, "影片處理工具 FFmpeg", font=title_font).pack(anchor="w")
        label(
            panel,
            "一般安裝會一起準備好，不需要另外設定。\n"
            "若檢查失敗，下載不會開始。請依原本的安裝方式修復，完成後重新開啟。",
            wraplength=px(470),
            justify="left",
        ).pack(anchor="w", pady=(px(12), px(14)))
        command = (
            "# uv tool 安裝（不需要專案資料夾）\n"
            "uv tool upgrade anicat --reinstall-package imageio-ffmpeg\n\n"
            "# 專案開發環境：在專案資料夾執行\n"
            "uv sync --locked --reinstall-package imageio-ffmpeg\n\n"
            "# pip 安裝：在原本安裝 AniCat 的 Python 環境執行\n"
            'python -m pip install --force-reinstall "imageio-ffmpeg>=0.8.1,<0.9"'
        )
        command_text = tk.Text(panel, height=10, width=66, font=mono_font, wrap="word")
        command_text.insert("1.0", command)
        command_text.configure(state="disabled")
        command_text.pack(fill="x")

        def copy_command() -> None:
            root.clipboard_clear()
            root.clipboard_append(command)
            copy_button.configure(text="已複製")

        buttons = tk.Frame(panel, bg=COLORS["paper"])
        buttons.pack(fill="x", pady=(px(14), px(12)))
        copy_button = tk.Button(buttons, text="複製修復指令", command=copy_command)
        copy_button.pack(side="left")
        tk.Button(
            buttons,
            text="其他安裝方式",
            command=lambda: webbrowser.open("https://ffmpeg.org/download.html"),
        ).pack(side="left", padx=px(8))
        tk.Button(buttons, text="關閉", command=dialog.destroy).pack(side="right")
        label(
            panel,
            "完成後關閉此說明，再按「開始下載」，程式會重新檢查。",
            font=small_font,
            wraplength=px(470),
            justify="left",
        ).pack(anchor="w")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        copy_button.focus_set()

    actions = tk.Frame(form, bg=COLORS["paper"])
    actions.grid(row=2, column=0, sticky="ew", pady=(px(18), 0))
    actions.grid_columnconfigure(0, weight=1)
    tool_info = tk.Frame(actions, bg=COLORS["paper"])
    tool_info.grid(row=0, column=0, sticky="w")
    tool_status = tk.StringVar(value="開始前會自動檢查影片處理工具")
    label(tool_info, "", textvariable=tool_status, font=small_font, fg=COLORS["muted"]).pack(
        anchor="w"
    )
    tool_help = label(
        tool_info,
        "工具安裝說明",
        font=(body_family, 9, "underline"),
        fg=COLORS["accent"],
        cursor="hand2",
        takefocus=True,
        highlightthickness=px(1),
        highlightbackground=COLORS["paper"],
        highlightcolor=COLORS["accent"],
    )
    tool_help.pack(anchor="w", pady=(px(4), 0))
    tool_help.bind("<Button-1>", lambda _event: show_tool_help())
    tool_help.bind("<Return>", lambda _event: show_tool_help())
    tool_help.bind("<space>", lambda _event: show_tool_help())
    download_button = tk.Label(
        actions,
        text="開始下載",
        font=(body_family, 11, "bold"),
        bg=COLORS["accent"],
        fg="#FFFFFF",
        relief="flat",
        borderwidth=0,
        padx=px(20),
        pady=px(11),
        cursor="hand2",
        takefocus=True,
        highlightthickness=px(2),
        highlightbackground=COLORS["accent"],
        highlightcolor=COLORS["ink"],
    )

    def stop() -> None:
        if state["busy"] and not cancel.is_set():
            cancel.set()
            style_download()
            status_text.set("正在停止下載…")

    def toggle_download(_event=None) -> None:
        if state["busy"]:
            stop()
        else:
            start()

    download_button.grid(row=0, column=1, sticky="ew")
    download_button.bind("<Button-1>", toggle_download)
    download_button.bind("<Return>", toggle_download)
    download_button.bind("<space>", toggle_download)
    download_button.bind("<Enter>", lambda _event: style_download(hover=True))
    download_button.bind("<Leave>", lambda _event: style_download())
    style_download()

    def close() -> None:
        if state["busy"]:
            if messagebox.askyesno("下載進行中", "停止下載並關閉 AniCat？", parent=root):
                state["closing"] = True
                stop()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    poll_id = root.after(100, poll)
    smoke_errors: list[BaseException] = []
    if smoke_test:

        def smoke_failed(_kind, error, _traceback) -> None:
            print(f"GUI smoke test failed: {error}", file=sys.stderr)
            smoke_errors.append(error)
            root.destroy()

        def check_ui() -> None:
            if sys.platform == "win32":
                awareness = ctypes.c_int()
                result = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
                assert result == 0 and awareness.value == 1, "App is not system-DPI aware"
            assert root.minsize() == (px(820), px(660)), "window DPI scale mismatch"
            assert abs(logo.width() - app_icon.width() * scale / 8) <= 1
            assert abs(mascot.height() - mascot_source.height() * scale / 4) <= 1
            handle(GuiEvent("queue", total=12))
            for index in range(12):
                handle(GuiEvent("item", url=f"https://anime1.me/{index}", state="queued"))
            long_path = (
                r"C:\Users\Orange\AppData\Local\Temp\AniCat-PR25-8e8432a"
                r"\acceptance-20261125-201745\output-stop\你又被殺了呢，偵探大人 [08].mp4"
            )
            path_label = item_rows["https://anime1.me/0"][3]
            path_label.configure(text=long_path)
            root.update_idletasks()
            queue_width = queue_canvas.winfo_width()
            wrap_widths = []
            for width in (queue_width, queue_width - px(80), queue_width):
                queue_canvas.event_generate("<Configure>", width=width)
                root.update_idletasks()
                for _row, _badge, title, detail in item_rows.values():
                    for text_label in (title, detail):
                        assert 0 < int(text_label.cget("wraplength")) <= text_label.winfo_width(), (
                            "queue text wraps outside its visible column"
                        )
                assert path_label.cget("text") == long_path, "queue path was truncated"
                assert path_label.winfo_height() > tkfont.Font(font=small_font).metrics("linespace")
                wrap_widths.append(int(path_label.cget("wraplength")))
            assert wrap_widths[1] < wrap_widths[0] == wrap_widths[2], (
                f"queue wrap did not resize: {wrap_widths}"
            )
            root.update_idletasks()
            # A wheel event over a row must reach the queue's top-level binding.
            item_rows["https://anime1.me/0"][2].event_generate("<MouseWheel>", delta=-120)
            assert queue_canvas.yview()[0] > 0, "queue wheel did not scroll"
            handle(GuiEvent("item", url="https://anime1.me/0", state="error", detail="test"))
            handle(GuiEvent("item", url="https://anime1.me/1", state="downloading"))
            assert download_button.cget("text") == "開始下載"
            state["busy"] = True
            style_download()
            assert download_button.cget("text") == "停止下載"
            download_button.focus_force()
            download_button.event_generate("<Return>")
            assert cancel.is_set(), "keyboard stop did not cancel"
            assert download_button.cget("text") == "正在停止…"
            assert download_button.cget("state") == "disabled"
            handle(None)
            assert download_button.cget("text") == "開始下載"
            assert download_button.cget("state") == "normal"
            assert item_rows["https://anime1.me/0"][1].cget("text") == "失敗"
            assert item_rows["https://anime1.me/1"][1].cget("text") == "已停止"
            destroyed = []
            root.bind("<Destroy>", lambda event: destroyed.append(event.widget == root), add="+")
            state["closing"] = True
            handle(None)
            assert any(destroyed), "earlier download error prevented close"

        root.report_callback_exception = smoke_failed
        root.after(250, check_ui)
    root.mainloop()
    return 1 if smoke_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
