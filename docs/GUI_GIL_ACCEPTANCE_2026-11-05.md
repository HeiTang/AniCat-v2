# GUI 下載卡頓／GIL 追查與驗收 — 2026-11-05

- Before：#18 `9e8dba13afcd3301fc7b1625bcfeb8885a6806b3`；修正程式：`a6596ce120a4ea2e760995b65328094f0d49275d`。後續驗收文件 commit 不改程式。
- Apple M3 / 16 GiB、macOS 26.6.2 ARM64、CPython 3.14.5（GIL enabled）、原生 Tk。
- 問題：先前實際下載時曾量到約 578ms 心跳間隔，而 poll 約 16ms，僅從時間軸重疊尚不足以證明背景驗證／GIL 是根因。

## 因果對照

- 相同真實 MPEG-TS segment，12 個 CPU threads，各驗證 30 次；每輪都處理 1,122,840 packets。沒有省略 TS 檢查。
- 同時量測 Tk 20ms heartbeat 與不操作 UI 的 observer thread（20ms wait）；固定工作量、新程序、三輪。
- 分別改變工作所在程序、GIL switch interval、主動讓出策略；不是只觀察階段重疊。
- 表格是每輪最大間隔的三輪中位數；GIL interval 的改動僅限診斷程序。

| 控制條件 | Tk 最大間隔中位數 | observer 最大間隔中位數 | 工作時間中位數 |
|---|---:|---:|---:|
| Same process / 5ms GIL | 223.28ms | 101.92ms | 0.669s |
| Separate process | 28.17ms | 25.03ms | 0.777s |
| Same process / 0.5ms GIL | 39.36ms | 48.26ms | 0.678s |
| sleep(0) prototype | 124.20ms | 117.97ms | 0.725s |
| sleep(1ms) final | 36.74ms | 36.39ms | 0.698s |

- 同程序 CPU threads 同時拖慢 Tk 與純計時 observer；移至獨立程序或只縮短 GIL interval 都大幅降低延遲，確認這類驗證負載的卡頓包含 GIL 競爭。
- 不能由此追溯先前那一筆 578ms 的所有時間都花在哪裡，也不能排除字型、OS 排程與其他因素。
- sleep(0) 改善不穩定，未採用；最終只加入小範圍的協作排程，不更動全域 GIL switch interval，也未引入 multiprocessing 到產品。

## 修正

- TS 逐封包檢查與 header-only continuity 正規化迴圈，每完成 1024 packets（188 KiB）暫停 1ms，讓 GUI 及其他 threads 有機會取得 GIL。
- 每個 packet 仍必須通過全部原有檢查，未改 predicate、continuity 判定、payload 處理、hash、AES、重試、warning 或全片解碼 gate。
- `hls_downloader.py`、`hls.py`、`ffmpeg.py` 與 before 逐 byte 相同。`hls_segments.py` 其他函式的 AST 相同；只有 inspect/normalization 迴圈加入排程讓出。

## 實際 GUI 完整下載（交錯 before/after）

- 每輪 3 集（2 明文、1 AES），每集 120 秒 720p/30fps、60 個 2 秒 TS segments。
- 使用真實 GUI start、worker、service、片段下載、TS 驗證、音訊轉碼及全片解碼；僅 page client 導向 localhost 合成頁面。
- 兩個 HTTP 情境：每次 GET 延遲 20ms，以及不額外延遲的快速來源；各 before/after 交錯 3 輪。
- 每輪獨立程序、新輸出目錄，保留 bundled FFmpeg 9.0，PATH 空白、未設定 IMAGEIO_FFMPEG_EXE；畫面必須顯示 3/3 完成。

| HTTP 額外延遲 | 版本 | 3 集完成中位數 | heartbeat p95 中位數 | 最大間隔中位數 | 各輪最大間隔範圍 |
|---:|---|---:|---:|---:|---:|
| 20ms | before | 3.633s | 23.25ms | 27.80ms | 27.63–64.84ms |
| 20ms | after | 3.684s | 22.49ms | 33.63ms | 33.18–35.60ms |
| 0ms | before | 3.720s | 25.04ms | 45.48ms | 40.62–70.69ms |
| 0ms | after | 3.927s | 23.45ms | 38.63ms | 36.23–49.12ms |

- 本次 before/after 沒有重現歷史 578ms；沒有宣稱用單次未重現就證明修好。GIL 影響由上方固定工作量實驗確認。
- 一般 20ms 延遲情境的最大間隔中位數沒有改善，最大離群值縮小；快速來源情境與 CPU 壓力實驗顯示改善。不是每種負載都更快。
- after 六輪的最大 heartbeat 為約 33–49ms；仍不是硬即時保證。
- 吞吐取捨：固定驗證負載耗時中位數增加約 4%；實際 GUI 三集完成中位數約增加 1.4%（20ms）／5.6%（快速來源）。

## 完整性差異驗證

- 60 個來源片段、177,566 packets，before/after 全部 SegmentInspection 證據及 60 個正規化輸出 SHA256 完全相同。
- 額外在第 1024 packet 注入 sync error、第 2048 packet 注入 TEI，以及截斷尾端；錯誤型別、代碼、訊息完全相同。
- 差異驗證 canonical SHA256：`a472aab124bc5f13c13b64f58b808b36df39facd06ed735e624348a7e8a12a92`。
- 新增大分段測試確認讓出後的 packet 仍檢查、完整 hash／CC 證據正確；擴充 header-only normalization 測試跨過兩個 yield 邊界，逐 packet 驗證 payload 不變。

## 回歸與 backend 效能

- 完整 suite（PATH 空白）：155 tests，152 passed / 3 opt-in live skipped；含 AES key/padding/ciphertext、key rotation、重試、discontinuity、音訊格式切換、嚴格診斷拒絕等既有測試。
- Ruff lint/format、Pyright、locked install、wheel/sdist build 通過。
- 修正後 backend：明文/AES，1／3 集，各三輪，共 24 集完整下載與全片解碼。

| 格式 | 同時集數 | 完成時間中位數 |
|---|---:|---:|
| clear | 1 | 2.575s |
| aes | 1 | 2.623s |
| clear | 3 | 4.144s |
| aes | 3 | 4.367s |

- 本表為修正後獨立回歸測量，不能與不同時間的舊 benchmark 當成嚴格因果比較。1ms 讓出的吞吐成本並非零；上方交錯 GUI 與固定負載對照才是本輪取捨依據。
- 本輪 candidate 共 18 集 GUI + 24 集 backend 完整下載／解碼；before GUI 另 18 集。皆重複使用相同合成素材，不代表不同作品。

## 證據與限制

- 原始結果：`gui-gil-performance-2026-11-05.jsonl`（41 records、每筆 record_sha256）；prototype 額外標記，沒有當成最終程式結果。
- 本機重跑／原始腳本：`/tmp/anicat-gil-perf-20261105/{gil_probe.py,run_probe.py,run_after_probe.py,gui_load.py,run_actual.py,run_fast.py,differential.py,bench.py}`；fixture 沿用 `/tmp/anicat-perf-20261105/fixture`。
- 未重跑真站影片、未測冷開機與 Windows/Linux 原生 GUI；不保證其他硬體／負載的最大延遲上限。
- #18 保持 OPEN，未合併到 dev/main。
