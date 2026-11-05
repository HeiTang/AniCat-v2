"""Small, focused desktop workspace sharing the CLI download service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import Literal

from .ffmpeg import find_ffmpeg
from .models import DownloadStage, JobReport
from .options import DownloadOptions
from .service import AniCatService
from .urls import split_urls

GuiItemState = DownloadStage | Literal["queued", "done", "skipped", "error"]


@dataclass(frozen=True)
class GuiEvent:
    """Thread-to-UI message; no guessed progress is sent."""

    kind: Literal["status", "queue", "item", "tool"]
    text: str = ""
    url: str = ""
    state: GuiItemState | None = None
    detail: str = ""
    total: int = 0


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


def run_download(urls: list[str], output: Path, events: Queue[GuiEvent | None]) -> None:
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
}


STAGE_DETAIL: dict[DownloadStage, str] = {
    "resolving": "正在解析作品與播放來源…",
    "downloading": "正在下載影片並檢查接收內容…",
    "verifying": "正在檢查分段銜接與整理時間軸…",
    "merging": "正在合併影片與處理音訊…",
    "validating": "正在完整解碼驗證，通過後才會完成…",
}


def main() -> int:
    """Open the AniCat desktop workspace."""

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print("圖形介面需要 Python 的 Tk 支援。請安裝含 Tk 的 Python，或使用 anicat CLI。")
        return 1

    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"無法開啟圖形介面：{error}")
        return 1

    root.title("AniCat · Download workspace")
    root.geometry("1060x720")
    root.minsize(820, 660)
    root.configure(bg=COLORS["paper"])

    title_font = ("Helvetica Neue", 18, "bold")
    body_font = ("Helvetica Neue", 11)
    small_font = ("Helvetica Neue", 9)
    mono_font = ("Menlo", 9)

    def label(parent: tk.Misc, text: str, *, font=body_font, fg=COLORS["ink"], **kwargs):
        return tk.Label(parent, text=text, font=font, fg=fg, bg=parent.cget("bg"), **kwargs)

    def card(parent: tk.Misc, *, bg=COLORS["surface"], **kwargs):
        return tk.Frame(
            parent,
            bg=bg,
            highlightbackground=COLORS["line"],
            highlightthickness=1,
            **kwargs,
        )

    outer = tk.Frame(root, bg=COLORS["paper"])
    outer.pack(fill="both", expand=True, padx=34, pady=20)
    header = tk.Frame(outer, bg=COLORS["paper"])
    header.pack(fill="x", pady=(0, 14))
    brand_row = tk.Frame(header, bg=COLORS["paper"])
    brand_row.pack(side="left")
    label(brand_row, "AniCat", font=("Helvetica Neue", 22, "bold")).pack(side="left")
    label(brand_row, ".", font=("Helvetica Neue", 22, "bold"), fg=COLORS["accent"]).pack(
        side="left"
    )
    label(header, "LOCAL DOWNLOAD WORKSPACE  /  01", font=mono_font, fg=COLORS["muted"]).pack(
        side="right", pady=7
    )
    tk.Frame(outer, bg=COLORS["line"], height=1).pack(fill="x", pady=(0, 14))

    content = tk.Frame(outer, bg=COLORS["paper"])
    content.pack(fill="both", expand=True)
    content.grid_columnconfigure(0, weight=6)
    content.grid_columnconfigure(1, weight=5)
    content.grid_rowconfigure(1, weight=1)

    intro = tk.Frame(content, bg=COLORS["paper"])
    intro.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
    label(intro, "01  /  DOWNLOAD", font=mono_font, fg=COLORS["accent"]).pack(anchor="w")
    label(intro, "把想看的，留在本機。", font=("Helvetica Neue", 22, "bold")).pack(
        anchor="w", pady=(4, 3)
    )
    label(
        intro,
        "貼上支援的 Anime1 網址，選好位置，其他交給 AniCat。",
        font=body_font,
        fg=COLORS["muted"],
        justify="left",
    ).pack(anchor="w")

    form = tk.Frame(content, bg=COLORS["paper"])
    form.grid(row=1, column=0, sticky="nsew", padx=(0, 22))
    form.grid_columnconfigure(0, weight=1)

    url_card = card(form)
    url_card.grid(row=0, column=0, sticky="ew")
    url_card.grid_columnconfigure(0, weight=1)
    url_top = tk.Frame(url_card, bg=COLORS["surface"])
    url_top.grid(row=0, column=0, sticky="ew", padx=20, pady=(17, 9))
    label(url_top, "網址佇列", font=title_font).pack(side="left")
    label(url_top, "一行一個網址", font=small_font, fg=COLORS["muted"]).pack(side="right", pady=4)
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
        padx=14,
        pady=12,
        highlightthickness=1,
        highlightbackground=COLORS["line"],
        highlightcolor=COLORS["accent"],
    )
    urls.grid(row=1, column=0, sticky="ew", padx=20)
    label(
        url_card,
        "支援單集、分類與季度網址；多個網址會一起加入下載佇列。",
        font=small_font,
        fg=COLORS["muted"],
        justify="left",
    ).grid(row=2, column=0, sticky="w", padx=20, pady=(9, 18))

    output_card = card(form)
    output_card.grid(row=1, column=0, sticky="ew", pady=(16, 0))
    output_card.grid_columnconfigure(0, weight=1)
    output_title = tk.Frame(output_card, bg=COLORS["surface"])
    output_title.grid(row=0, column=0, sticky="ew", padx=20, pady=(17, 9))
    label(output_title, "儲存位置", font=title_font).pack(side="left")
    label(output_title, "完成後即時顯示", font=small_font, fg=COLORS["muted"]).pack(
        side="right", pady=4
    )
    output = tk.StringVar(value=str(Path.home() / "Downloads" / "AniCat"))
    output_row = tk.Frame(output_card, bg=COLORS["surface"])
    output_row.grid(row=1, column=0, sticky="ew", padx=20)
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
        highlightthickness=1,
        highlightbackground=COLORS["line"],
        highlightcolor=COLORS["accent"],
    )
    entry.grid(row=0, column=0, sticky="ew", ipady=10, padx=(0, 10))

    def choose() -> None:
        selected = filedialog.askdirectory(parent=root, title="選擇 AniCat 儲存資料夾")
        if selected:
            output.set(selected)

    browse = tk.Button(
        output_row,
        text="選擇資料夾",
        command=choose,
        font=("Helvetica Neue", 10, "bold"),
        bg=COLORS["surface"],
        fg=COLORS["ink"],
        activebackground="#E9E5DB",
        activeforeground=COLORS["ink"],
        relief="flat",
        highlightbackground=COLORS["line"],
        highlightthickness=1,
        padx=14,
        pady=8,
        cursor="hand2",
    )
    browse.grid(row=0, column=1)
    label(
        output_card,
        "Direct MP4 來源會保留 .part 暫存檔；已有完成檔案會跳過。",
        font=small_font,
        fg=COLORS["muted"],
        justify="left",
    ).grid(row=2, column=0, sticky="w", padx=20, pady=(9, 18))

    state = {"busy": False, "error": False}
    events: Queue[GuiEvent | None] = Queue()
    item_rows: dict[str, tuple[tk.Frame, tk.Label, tk.Label, tk.Label]] = {}
    total = tk.IntVar(value=0)
    finished = tk.IntVar(value=0)
    summary = tk.StringVar(value="尚未開始")
    status_text = tk.StringVar(value="就緒")

    queue_panel = tk.Frame(content, bg=COLORS["dark"], padx=22, pady=20)
    queue_panel.grid(row=1, column=1, sticky="nsew")
    queue_panel.grid_rowconfigure(3, weight=1)
    queue_panel.grid_columnconfigure(0, weight=1)
    queue_header = tk.Frame(queue_panel, bg=COLORS["dark"])
    queue_header.grid(row=0, column=0, sticky="ew")
    label(queue_header, "02  /  QUEUE", font=mono_font, fg="#D88A70").pack(side="left")
    label(queue_header, "STATE", font=mono_font, fg=COLORS["dark_muted"]).pack(side="right")
    label(queue_panel, "這批下載", font=title_font, fg="#F5F2EA").grid(
        row=1, column=0, sticky="w", pady=(15, 0)
    )
    tk.Label(
        queue_panel,
        textvariable=summary,
        font=small_font,
        fg=COLORS["dark_muted"],
        bg=COLORS["dark"],
        anchor="w",
    ).grid(row=2, column=0, sticky="ew", pady=(4, 16))

    queue_canvas = tk.Canvas(queue_panel, bg=COLORS["dark"], highlightthickness=0)
    queue_canvas.grid(row=3, column=0, sticky="nsew")
    queue_scroll = tk.Scrollbar(
        queue_panel,
        orient="vertical",
        command=queue_canvas.yview,
        bg=COLORS["dark"],
        troughcolor=COLORS["dark"],
        activebackground=COLORS["dark_muted"],
        width=8,
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

    queue_canvas.bind("<MouseWheel>", scroll_queue)
    queue_canvas.bind("<Button-4>", lambda _event: queue_canvas.yview_scroll(-1, "units"))
    queue_canvas.bind("<Button-5>", lambda _event: queue_canvas.yview_scroll(1, "units"))

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
    empty_label.pack(anchor="w", pady=(3, 16))

    bottom = tk.Frame(queue_panel, bg=COLORS["dark"])
    bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(18, 0))
    bottom.grid_columnconfigure(0, weight=1)
    tk.Label(
        bottom,
        textvariable=status_text,
        font=("Helvetica Neue", 10, "bold"),
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
        row = tk.Frame(queue_inner, bg="#2B3B31", padx=12, pady=10)
        row.pack(fill="x", pady=(0, 8))
        badge = tk.Label(row, text="等待中", font=("Menlo", 8, "bold"), padx=7, pady=4)
        badge.pack(side="left", anchor="n")
        set_badge(badge, "queued")
        middle = tk.Frame(row, bg="#2B3B31")
        middle.pack(side="left", fill="x", expand=True, padx=(10, 8))
        title = tk.Label(
            middle,
            text=url,
            font=mono_font,
            fg="#F5F2EA",
            bg="#2B3B31",
            anchor="w",
            justify="left",
            wraplength=300,
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
            wraplength=300,
        )
        detail.pack(fill="x", pady=(4, 0))
        item_rows[url] = (row, badge, title, detail)
        empty_label.pack_forget()
        return item_rows[url]

    def clear_queue() -> None:
        for row, _badge, _title, _detail in item_rows.values():
            row.destroy()
        item_rows.clear()
        empty_label.pack(anchor="w", pady=(3, 16))
        total.set(0)
        finished.set(0)
        summary.set("尚未開始")
        state["error"] = False

    def handle(event: GuiEvent | None) -> None:
        if event is None:
            state["busy"] = False
            start_button.configure(text="開始下載")
            style_start()
            status_text.set("這批工作已結束" if not state["error"] else "有工作需要處理")
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
        state["busy"] = True
        state["error"] = False
        start_button.configure(text="下載中…")
        style_start()
        status_text.set("正在連線與解析…")
        Thread(
            target=run_download,
            args=(values, Path(output.get()).expanduser(), events),
            daemon=True,
        ).start()

    def style_start(*, hover: bool = False) -> None:
        busy = state["busy"]
        start_button.configure(
            bg=COLORS["accent_dark"] if busy or hover else COLORS["accent"],
            fg="#D9C1B8" if busy else "#FFFFFF",
            cursor="" if busy else "hand2",
        )

    def poll() -> None:
        yielded = process_gui_events(events, handle)
        root.after(1 if yielded else 100, poll)

    def show_tool_help() -> None:
        import webbrowser

        dialog = tk.Toplevel(root)
        dialog.title("準備影片處理工具")
        dialog.configure(bg=COLORS["paper"])
        dialog.transient(root)
        panel = tk.Frame(dialog, bg=COLORS["paper"], padx=24, pady=22)
        panel.pack(fill="both", expand=True)
        label(panel, "影片處理工具 FFmpeg", font=title_font).pack(anchor="w")
        label(
            panel,
            "一般安裝會一起準備好，不需要另外設定。\n"
            "若檢查失敗，下載不會開始。請依原本的安裝方式修復，完成後重新開啟。",
            wraplength=470,
            justify="left",
        ).pack(anchor="w", pady=(12, 14))
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
        buttons.pack(fill="x", pady=(14, 12))
        copy_button = tk.Button(buttons, text="複製修復指令", command=copy_command)
        copy_button.pack(side="left")
        tk.Button(
            buttons,
            text="其他安裝方式",
            command=lambda: webbrowser.open("https://ffmpeg.org/download.html"),
        ).pack(side="left", padx=8)
        tk.Button(buttons, text="關閉", command=dialog.destroy).pack(side="right")
        label(
            panel,
            "完成後關閉此說明，再按「開始下載」，程式會重新檢查。",
            font=small_font,
            wraplength=470,
            justify="left",
        ).pack(anchor="w")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        copy_button.focus_set()

    actions = tk.Frame(form, bg=COLORS["paper"])
    actions.grid(row=2, column=0, sticky="ew", pady=(18, 0))
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
        font=("Helvetica Neue", 9, "underline"),
        fg=COLORS["accent"],
        cursor="hand2",
        takefocus=True,
        highlightthickness=1,
        highlightbackground=COLORS["paper"],
        highlightcolor=COLORS["accent"],
    )
    tool_help.pack(anchor="w", pady=(4, 0))
    tool_help.bind("<Button-1>", lambda _event: show_tool_help())
    tool_help.bind("<Return>", lambda _event: show_tool_help())
    tool_help.bind("<space>", lambda _event: show_tool_help())
    start_button = tk.Label(
        actions,
        text="開始下載",
        font=("Helvetica Neue", 11, "bold"),
        bg=COLORS["accent"],
        fg="#FFFFFF",
        relief="flat",
        padx=20,
        pady=11,
        cursor="hand2",
        takefocus=True,
        highlightthickness=2,
        highlightbackground=COLORS["accent"],
        highlightcolor=COLORS["ink"],
    )
    start_button.grid(row=0, column=1, sticky="e")
    start_button.bind("<Button-1>", lambda _event: start())
    start_button.bind("<Return>", lambda _event: start())
    start_button.bind("<space>", lambda _event: start())
    start_button.bind("<Enter>", lambda _event: style_start(hover=True))
    start_button.bind("<Leave>", lambda _event: style_start())
    style_start()

    def close() -> None:
        if state["busy"]:
            messagebox.showinfo("下載進行中", "請等待這批工作結束後再關閉視窗。", parent=root)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
