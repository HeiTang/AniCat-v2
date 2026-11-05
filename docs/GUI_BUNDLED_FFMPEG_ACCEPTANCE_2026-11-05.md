# GUI 與套件內附 FFmpeg 整合驗收 — 2026-11-05

本輪將 #15 的 GUI／工具自動定位與 #16 的 HLS／AES-128 backend 放入獨立 `feature/gui-hls-integration` 分支，沒有合入 dev、沒有合併 PR。

## 實測版本

- GUI 原 PR 程式：`c9a7e06f2878acd0584c013cb6ea47ae1f3ca279`。
- HLS 原 PR 程式：`852a3dcf253e0d8f7ccfbaad3df72da54fe749d6`。
- 實際完整驗收整合 HEAD：`e9f715edde14705b29f2e86c45632661735b41b0`，已確認 GUI／HLS／共用定位檔案分別與原 PR 相同。
- 後續 `2e4694f`／`4bf53ca` 只更新 CI，runtime 與實測版本相同；最終報告提交亦不更動 runtime。
- 實際執行檔：`ffmpeg version 9.0 Copyright (c) 2000-2026 the FFmpeg developers`。SHA-256：`591260c945d0eef150e3bf82b0ef988bd36a9cecc18ff05d6679617159f0a95e`。

## 無系統 FFmpeg 的測試環境

- macOS ARM64，Python 3.14.5；以該整合 HEAD 的獨立 `git archive` 載入程式碼。
- 僅對驗收 GUI 子程序清空 PATH，移除 `IMAGEIO_FFMPEG_EXE` override；未修改使用者系統設定。
- 啟動前斷言 `shutil.which("ffmpeg") is None`，並確認定位出的絕對路徑位於專案虛擬環境的 `imageio_ffmpeg/binaries`。
- 記錄執行檔 SHA-256、版本，以及 GUI 檢查和每個 HLS backend 工作實際取得的執行檔，所有轉封裝命令均斷言使用同一份內附執行檔。

## 真正 GUI 入口

驗收程式開啟實際 Tk `gui.main()`，在網址欄填入每輪列出的原頁 URL（前兩輪各四部，最後一輪三部）、設定新的 `/tmp` 輸出資料夾，再觸發原本「開始下載」按鈕的事件綁定。沒有直接呼叫 service 取代 GUI，也沒有 mock resolver、下載器、key、segment 或來源回應。

只包裝函式收集證據；原函式的輸入／結果與錯誤均原樣傳遞。觀察真實 GUI queue／逐集完成事件與畫面上的完成集數。核心完成其全片解碼後，runner 再使用同一個內附 FFmpeg，對每個 MP4 獨立完整解碼並同時檢查 exit code 與 stderr。

## 完整下載與解碼結果

**最新一輪 GUI 3/3 完整通過，共 1673 段，其中 402 段為 AES-128。** GUI 與三個 HLS 工作共四次定位，均返回同一份套件執行檔；GUI 真實完成事件為 3 筆，實際畫面文字含「影片處理工具：已就緒」「3 / 3 集已完成」「這批工作已結束」。

| 作品／來源 | 明文／AES | 完整驗證段數 | MP4 bytes | 獨立全片解碼 |
|---|---|---:|---:|---|
| .cc 師兄啊師兄 [166] | 明文 HLS | 657/657 | 469,469,421 | 0／stderr 空 |
| .in 巴斯×機械 [01] | 明文 HLS | 614/614 | 379,341,166 | 0／stderr 空 |
| .in 聖鬥士星矢 [01]，分流2（#other） | AES-128 | 402/402 | 387,704,037 | 0／stderr 空 |

前兩輪保留：`c9ae894` 為 3/4 通過（兩部明文與 .cc 範馬刃牙 AES；聖鬥士預設來源逾時）；`282a371` 為 2/4 通過（兩部明文；兩個預設 AES 來源均在 4 次 preflight 後逾時）。這些不是 FFmpeg 缺少或 warning 被忽略。

最新一輪改用原站已提供且可連線的 `#other` 第二分流；#16 現在遵循原站這個網址語意。沒有測試用 playlist 注入或自動 fallback；同站播放器限制仍在，要求的分流缺少時明確報錯。範馬刃牙原預設來源仍保留未完成紀錄，不能把改測聖鬥士第二分流當成它通過最新版本。

## 完整性與診斷

- 每段完整下載／HTTP 檢查、AES 解密、TS framing／sync／TEI／continuity／邊界時間軸驗證保持原行為。
- 明確 discontinuity 分組、header-only CC 正規化、每組／成品片長與完整 warning 分類保持啟用。
- runner 遮罩 CC nibble 後逐 byte 比對其餘 payload；逐段大小、密文／明文 SHA-256、嘗試次數與邊界證據一併保存。
- Packet corrupt、AAC queue、未知 warning、超界 DTS 校正及 decode stderr 仍拒絕，不為切換 FFmpeg 執行檔放寬。

- #16 121 tests：119 通過、2 個既有 integration skips；整合分支 125 tests：123 通過、2 skips。
- 真實 PATH-empty localhost HLS／AES runtime 與套件 FFmpeg 9 已執行，舊系統 FFmpeg 4.4 clear／AES fixtures 亦通過。
- FFmpeg 9 移除 `-vsync`／不接受舊 timebase；9+ 使用 `-fps_mode passthrough -enc_time_base demux`，舊版保留 `-vsync 0 -enc_time_base -1`。Linux `n9.0.2` 與無數字 nightly ABI 識別均有回歸測試，版本探測失敗仍執行全片解碼，不跳過 gate。
- CI 不再 apt 安裝系統 FFmpeg。測試子程序清空 PATH／override、斷言 which 為 None，印出套件執行檔與版本，再跑完整 unittest suite。Ruff format／lint、Pyright、wheel／sdist build 與 Python 3.12／3.13 CI 均通過。

## 證據與限制

[11 筆完整嘗試及分段紀錄](gui-bundled-ffmpeg-results-2026-11-05.jsonl)與[三輪 GUI／環境紀錄](gui-bundled-ffmpeg-runs-2026-11-05.jsonl)保留各自精確 HEAD、空 PATH／which 結果、執行檔路徑／雜湊、定位 consumer、GUI 按鈕／queue／逐集事件、畫面 summary、逐段明文／密文大小與雜湊、邊界與 payload identity，以及 FFmpeg 診斷和獨立 decode 結果。

`record_sha256` 為該筆其餘欄位以排序鍵 JSON 序列化後的雜湊；`local_run_sha256` 為本機原始 GUI metadata 指紋。雜湊是本次取得資料的稽核指紋，不是上游提供的可信校驗值。影片與金鑰不提交。

原 PR #15、#16 仍分別基於 dev，只有測試分支做程式整合；整合分支沒有合入 dev。

上游快照可能變動，本次成功不代表同一份歷史損壞內容被修復。未做人工全片影音同步審查、整季或所有分流下載，以及 Windows／Linux 原生 GUI 真實上游驗收。Linux CI 使用產生的 localhost fixtures。key 與媒體不提交。
