# GUI 真實下載階段與程序隔離驗收 — 2026-11-05

產品程式驗收 HEAD：`c4f043c1fca5297f8ac6506bc6c2458b1e02eb54`，基於 dev `d2f3b234d9a5961440f7d67f2d406716e6d90219`。後續 commit 僅調整 CI 啟動方式與驗收文件，產品程式未變。

## 修正內容

- Service／HLS 在實際工作邊界發送階段事件，GUI 依序呈現「解析來源 → 下載中 → 檢查分段 → 合併影片 → 全片驗證」。重試可回到合併階段；只有全部 gate 通過、完成檔案發布之後才顯示「完成」。
- 下載期間仍逐段檢查；「檢查分段」對應全部片段下載完成後的銜接與時間軸整理，並不代表下載階段省略驗證。直連 MP4 只回報其實際具有的解析／下載階段。
- GUI 保留原有有界事件處理，下載 service 改在 `spawn` 程序執行；UI relay thread 只接收小型狀態事件，不傳影片內容。下載、分段驗證、網路解析不再與 Tk 共用同一個 GIL。
- 正常完成、子程序異常退出、啟動失敗都會釋放 GUI busy 狀態。沒有新增相依套件、關閉驗證或調整全域 GIL interval。
- CI 原本從 stdin 啟動 unittest，spawn 無法重新載入 `<stdin>`；改成 `python -c`，保留原有無系統 FFmpeg 的完整測試。

## 卡頓追查與取捨

舊版真站曾量到 233.793ms；本輪只加狀態回報仍重現 229.713ms。背景 observer 與 GUI 都曾延遲，部分 stack 落在 requests 的 macOS 系統代理查詢；也有落在 FFmpeg 等待與 idle worker 的樣本。時間重疊不能證明每個離群值全由同一原因造成。

先前固定 TS 工作量的三輪控制實驗（見 `GUI_GIL_ACCEPTANCE_2026-11-05.md`）顯示，同程序最大心跳間隔中位數 223.28ms，獨立程序 28.17ms；縮短 GIL interval 也改善，證明此類背景負載具有 GIL 競爭。

本輪曾試驗每個來源快取環境代理設定，300 次查詢縮到 1 次，但真站仍有 169.916ms 離群值，因此最終 diff 已移除該快取及其網路設定語意。採用程序隔離，涵蓋整個下載工作，包括第三方 Python／原生呼叫，而不只 TS 迴圈。

| 實驗 | 心跳 p95 | 最大間隔 | >100ms | 結果用途 |
|---|---:|---:|---:|---|
| dev 基準真站 | 21.291ms | 40.949ms | 0 | 本輪未重現歷史峰值 |
| 僅階段回報 | 21.435ms | 229.713ms | 4 | 重現問題；明文來源被嚴格拒絕 |
| 僅階段回報，換穩定明文來源 | 21.334ms | 902.847ms | 5 | 重型 observer 亦可能擾動，保留原始紀錄 |
| 代理快取實驗，已移除 | 21.308ms | 169.916ms | 1 | 不作最終產品驗收 |
| 最終 spawn 程序 | 22.209ms | 44.695ms | 0 | 最終產品程式真站驗收 |

基準輪兩部下載／獨立解碼均通過、GUI 顯示 2 / 2；完成後視窗留開，腳本未取消的 40 分鐘 watchdog 後來把總狀態寫成 `harness_error`。JSONL 保留此錯誤與各片通過結果；基準表僅引用工作期間已封存的心跳，未將它當成完整腳本通過。最終程序輪會完成後自動關閉，總狀態為 `passed`。

各輪網路與分析器成本不完全相同，不能把峰值差直接當成單一因素的因果百分比；隔離下載與 Tk 的 GIL 是程式結構保證，最終實測數字是本機該輪結果。

## 最終真站 GUI 驗收

macOS ARM64、CPython 3.14.5、原生 Tk。使用 GUI 的「開始下載」事件、正式 service／下載器；未替換 HTTP、來源解析、分段內容、AES 或 FFmpeg。測試包裝只收集階段、manifest hash、分段證據與執行結果。

`PATH=''`、未設定 `IMAGEIO_FFMPEG_EXE`，`shutil.which('ffmpeg') is None`；GUI 子程序與 HLS 都使用套件內附 FFmpeg 9.0。父子程序 PID 分開記錄，確定實際經過產品的 spawn relay。

| 作品／來源 | 格式 | 分段 | 檔案大小 | 內部全片解碼 | 獨立全片解碼 |
|---|---|---:|---:|---|---|
| 巴斯×機械 [01]，anime1.in | 明文 HLS | 614 / 614 | 379,341,166 bytes | 通過 | exit 0、stderr 空白 |
| 聖鬥士星矢 [01]，anime1.in `#other` | AES-128 HLS | 402 / 402，全部加密 | 387,704,037 bytes | 通過 | exit 0、stderr 空白 |

兩列都實際讀到 mapped GUI widget 的六種狀態（含完成），並確認畫面顯示 2 / 2 集已完成。20ms 心跳共 9,130 筆，p95 22.209ms、最大 44.695ms、>100ms 為 0；poll 最大 9.761ms。19 次文字寫入／讀回／還原探針最大 0.200ms，這是 widget 操作成本，不當成人工輸入端到端延遲。這是原生 Tk 自動化驗收，不宣稱人工點擊／跨平台 UI 驗收。

獨立解碼指令：`ffmpeg -nostdin -v error -xerror -i <output> -fps_mode passthrough -enc_time_base demux -f null -`。通過標準為 exit 0 且 stderr 空白；不是只檢查檔案存在、probe 或 Range smoke。

## 嚴格檢查與失敗紀錄

- 與 dev 比較，`hls_segments.py`、`hls.py`、`ffmpeg.py` 逐 byte 相同；`hls_downloader.py` 除頂層下載流程新增 stage callback 外，其餘 helper AST 全部相同，包括 warning 分類、時間長度及全片解碼 gate。
- 《師兄 [166]》本輪重跑時 media playlist hash 從 `bf5faf...` 變為 `9a70ca...`，廣告／discontinuity 位置改變。657 段下載與檢查完成，但合併出現 video `Non-monotonic DTS`，四次嘗試全被 `fatal_ffmpeg_warning` 拒絕，沒有發布完成 MP4、沒有顯示全片驗證或完成。master hash 相同不代表內容相同。
- 此失敗保存在 `after` 原始紀錄與 JSONL。之後以另一個明文作品驗收，不把原失敗改成通過。新增 regression 確認這類 warning 即使 FFmpeg exit 0 仍拒絕，且保留原有檔案。
- 既有狹窄的可恢復 AAC timestamp rounding 判定維持原樣；相關 warning 全部留在每次 FFmpeg job 紀錄，不是忽略所有 warning。

## 回歸、證據與限制

- 完整 suite：160 tests，157 passed / 3 opt-in live skipped；包含實際 spawn 事件順序、crash 與啟動失敗、stage 對應、拒絕後不得提前完成，以及原有 AES／key rotation／損壞／重試測試。
- PATH 空白的 CI 等價啟動方式測試通過；Ruff lint／format、Pyright、wheel／sdist build 與 README 差異範圍檢查通過。
- 版本化摘要：`gui-stage-acceptance-2026-11-05.jsonl`；每筆附 canonical SHA256，保存各實驗與失敗案例。逐段與 normalization 完整 ledger 留於本機，摘要附其 hash／筆數，沒有上傳金鑰或影片。
- 原始證據：`/tmp/anicat-gui-stages-perf-20261105/{before,after,after-retry,final,process-final}`；最終執行腳本為同目錄 `live_gui_process.py`／`worker_profile.py`，另有獨立程序 observer。
- 未測 Windows／Linux 原生 GUI；新增程序會有啟動與記憶體成本。本輪未做冷啟動／記憶體成本基準，也不保證其他 OS 負載下的最大延遲上限。未合併 dev 或 main。
