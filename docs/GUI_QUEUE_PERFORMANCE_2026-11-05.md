# GUI 佇列回應效能驗收 — 2026-11-05

> 此文件保留首次佇列修正的歷史驗收；後續 GIL 追查、排程修正及最新結果見 [GUI_GIL_ACCEPTANCE_2026-11-05.md](GUI_GIL_ACCEPTANCE_2026-11-05.md)。

- 基底：dev `6e6954f7b305cc236c052fb2bc495d941f7d5342`；程式碼候選：`e536fccf977a6c879950f27951ae84b3e4b407da`。後續文件提交不改程式。
- macOS 26.6.2 ARM64、Apple M3／16 GiB、Python 3.14.5；原生 Tk，套件內附 FFmpeg 9.0。
- 目的：限制單次 UI 事件處理與延後的 widget layout/redraw 工作量，保留 FIFO、逐集狀態與最後完成訊號。

## 修改

- 每輪至多處理 8 個事件，或耗時約 8ms 就交還控制權；有待處理事件時 1ms 後繼續，閒置時仍為 100ms polling。
- 8ms 為兩事件之間的軟限制；無法中斷單一 Tk 呼叫、OS 排程或背景 thread 持有 GIL 的時間。
- 移除狀態更新時重設相同網址 label 的操作。未改變版面、字型或下載並行數。
- 初版只限制 handler 時間仍會累積 Tk redraw，300 集心跳曾達約 180ms，因此另限制每輪事件數。字型實驗沒有穩定改善，未納入。

## GUI 佇列效能

- 同一份 native Tk harness 交錯執行 before/after，各 3 輪；每輪新程序、新視窗。
- 合成 queue burst：先 N 個 queued，再 N 個 done，最後 None；不下載媒體。
- 10ms heartbeat 量測實際回呼間隔；確認可見畫面為 N / N 完成，沒有丟失完成訊號。
- 表格為三輪「單輪最大值」的中位數；完成時間包含顯示全部狀態，不是下載時間。

| 集數 | 版本 | poll 最大值中位數 | 心跳最大間隔中位數 | 所有狀態顯示完成 |
|---:|---|---:|---:|---:|
| 3 | before | 4.94ms | 15.21ms | 0.038s |
| 3 | after | 4.91ms | 19.86ms | 0.041s |
| 100 | before | 48.10ms | 55.27ms | 0.067s |
| 100 | after | 5.97ms | 20.32ms | 0.308s |
| 300 | before | 129.00ms | 129.87ms | 0.157s |
| 300 | after | 5.83ms | 32.58ms | 1.591s |

- 100／300 集的長單次回呼明顯縮短；3 集差異小、受排程變動影響，沒有宣稱所有小佇列都更快。
- 取捨：300 集全部狀態顯示由約 0.16s 增為約 1.6s，期間持續交還控制權。下載 thread 不等待這些逐筆 UI 更新。

## 下載與嚴格驗證回歸

- `uv run python -m unittest`（PATH 空白、移除 IMAGEIO_FFMPEG_EXE）：154 tests，151 passed、3 opt-in live skipped。
- 新增 3 個 GUI unit tests：時間到分批讓出、即使 handler 很快仍受事件數限制、跨批次保留 queued → done → None 順序與空佇列。
- 原有缺少 FFmpeg 時停止下載、解析失敗釋放 UI、下載狀態回報測試皆通過。
- HLS/AES runtime：錯誤 key／padding／截斷密文、key rotation、隱式 IV、同 URI key rotation、重試、discontinuity、音訊格式切換，以及 bundled FFmpeg 等既有測試皆通過。
- `src/anicat/hls.py`、`hls_segments.py`、`hls_downloader.py`、`ffmpeg.py` 與基底逐 byte 相同；沒有放寬 warning、片長、TS 完整性或全片解碼 gate。
- Ruff format/lint、Pyright、locked 安裝與 wheel/sdist build 通過。

### 真實 GUI + 本機完整下載

- 每輪 3 集（2 明文、1 AES）；每集為 120 秒 720p/30fps、60 個 2 秒 TS segments。
- localhost 每 GET 延遲 20ms，不限制頻寬；只把 Anime1 HTML page client 導向本機 fixture，其餘 real GUI start/worker/service/HLS/FFmpeg/完成狀態維持原程式。
- 使用全新輸出目錄；每輪可見 3 / 3 完成，沒有以 skip 代替下載。

| Run | 完成時間 | heartbeat p95 | 最大心跳間隔 | poll 最大值 |
|---:|---:|---:|---:|---:|
| 0 | 5.949s | 25.49ms | 577.61ms | 16.32ms |
| 1 | 4.514s | 22.50ms | 100.80ms | 27.56ms |
| 2 | 4.822s | 24.23ms | 57.11ms | 16.87ms |
| 6 | 6.290s | 31.76ms | 147.53ms | 28.58ms |

- 實際下載時仍有比 poll 長得多的 UI 間隔，包含一次約 578ms；不能宣稱整個 GUI 已完全不卡。
- Run 6 的最長約 148ms 間隔與 download_segments／write_local_playlist 階段重疊，顯示仍需另外 profile 背景驗證與 UI 的排程／GIL 競爭；重疊不能單獨證明根因。
- 此 PR 解決已重現的「事件一次清空」阻塞，未改為 multiprocessing，也未減少驗證。

### 相同合成片 backend 完整處理（三輪中位數）

| 格式 | 同時集數 | 完成時間 |
|---|---:|---:|
| clear | 1 | 2.464s |
| aes | 1 | 2.309s |
| clear | 3 | 3.697s |
| aes | 3 | 4.391s |

- backend 共 24 集、GUI 共 12 集完成下載與全片驗證。合成片重複使用，不代表 36 部不同作品。
- 來源碼與 backend 都未改，耗時與原 dev 測量範圍相近；未針對 OS 排程／CPU load 做實驗室隔離。

## 證據與限制

- 原始結果：`gui-queue-performance-2026-11-05.jsonl`；每列含 code HEAD 與 record_sha256。
- 本機可重跑 harness：`/tmp/anicat-queue-perf-20261105/queue_bench.py`、`run_compare.py`、`gui_load.py`、`bench.py`；合成媒體位於 `/tmp/anicat-perf-20261105/fixture`。
- 佇列測試透過 QUEUE_COUNT、QUEUE_TAG 與 PYTHONPATH 選擇數量／輸出名稱／before-after source；原生 launcher 與 PYTHONHOME 環境沿用這次已驗證的 macOS Python/Tk。
- 未重新下載真站影片，未測 Windows/Linux、冷開機後首次字型載入、整季真站與全 process-tree 記憶體峰值。
- main／dev 均未合併此修正；此文件隨獨立 feature PR 提交。
