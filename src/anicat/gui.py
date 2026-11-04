"""Small desktop front end sharing the CLI's download service."""

from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from .models import JobReport
from .options import DownloadOptions
from .service import AniCatService
from .urls import split_urls


def run_download(urls: list[str], output: Path, events: Queue[str | None]) -> None:
    """Run off the UI thread; always signal completion, including failures."""

    try:
        service = AniCatService(DownloadOptions(output))
        episodes = service.collect_episode_urls(urls)
        if not episodes:
            raise ValueError("找不到可下載的集數")
        events.put(f"找到 {len(episodes)} 集，開始下載…")

        def done(report: JobReport) -> None:
            if report.error:
                events.put(f"失敗：{report.url}\n{report.error}")
            elif report.result:
                label = "已存在" if report.result.status == "skipped" else "完成"
                events.put(f"{label}：{report.result.path}")

        service.download_many(episodes, on_done=done)
    except Exception as error:
        events.put(f"錯誤：{error}")
    finally:
        events.put(None)


def main() -> int:
    """Open a native desktop window without requiring command flags."""

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("圖形介面需要 Python 的 Tk 支援。請安裝含 Tk 的 Python，或使用 anicat CLI。")
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"無法開啟圖形介面：{error}")
        return 1
    root.title("AniCat · 動畫下載")
    root.geometry("760x560")
    root.minsize(540, 440)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="貼上網址，下載到你的電腦", font=("", 20)).pack(anchor="w")
    ttk.Label(
        frame,
        text="單集或全集皆可；多個網址請換行。目前支援 anime1.me 與 anime1.pw。",
        wraplength=650,
    ).pack(anchor="w", pady=(8, 16))
    urls = tk.Text(frame, height=5, wrap="word")
    urls.pack(fill="x")
    ttk.Label(frame, text="儲存資料夾").pack(anchor="w", pady=(16, 4))
    output = tk.StringVar(value=str(Path.home() / "Downloads" / "AniCat"))
    row = ttk.Frame(frame)
    row.pack(fill="x")
    entry = ttk.Entry(row, textvariable=output)
    entry.pack(side="left", fill="x", expand=True)

    def choose() -> None:
        selected = filedialog.askdirectory(parent=root)
        if selected:
            output.set(selected)

    browse = ttk.Button(row, text="選擇…", command=choose)
    browse.pack(side="right", padx=(8, 0))
    status = tk.StringVar(value="就緒 · 已有檔案會跳過，不覆寫。")
    ttk.Label(frame, textvariable=status).pack(anchor="w", pady=12)
    log = tk.Text(frame, height=10, wrap="word", state="disabled")
    log.pack(fill="both", expand=True)
    events: Queue[str | None] = Queue()
    busy = False

    def start() -> None:
        nonlocal busy
        if busy:
            return
        values = split_urls([urls.get("1.0", "end")])
        if not values or not output.get().strip():
            messagebox.showerror("尚未填完", "請填入網址與儲存資料夾。", parent=root)
            return
        busy = True
        button.configure(state="disabled")
        status.set("解析與下載中… 完成結果會顯示在下方。")
        Thread(
            target=run_download, args=(values, Path(output.get()).expanduser(), events), daemon=True
        ).start()

    button = ttk.Button(frame, text="開始下載", command=start)
    button.pack(anchor="e", pady=(14, 0))

    def poll() -> None:
        nonlocal busy
        try:
            while True:
                message = events.get_nowait()
                if message is None:
                    busy = False
                    button.configure(state="normal")
                    status.set("處理結束 · 請查看下方各集結果。")
                else:
                    log.configure(state="normal")
                    log.insert("end", message + "\n")
                    log.see("end")
                    log.configure(state="disabled")
        except Empty:
            pass
        root.after(100, poll)

    def close() -> None:
        if busy:
            messagebox.showinfo("下載進行中", "請等待這批下載結束再關閉視窗。", parent=root)
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
