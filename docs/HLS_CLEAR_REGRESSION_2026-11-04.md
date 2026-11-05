# AES 改動後的明文 HLS 回歸驗收 — 2026-11-04

以 PR #16 當時最新 HEAD `26afffae23a0361abcf4c25661d9feb6bc3647a8` 的獨立 `git archive` 快照，重跑前輪五部明文來源。此版本程式碼與 AES 實作 `5d6783c` 相同。各部重新解析原頁、建立新輸出目錄、完整下載，沒有使用既有檔案跳過或替代樣本。PR 維持開啟，不合併。

環境：macOS ARM64、Python 3.14.5、FFmpeg 4.4、cryptography 50.0.2。五部使用一般 `AniCatService.download_one` 入口與頁面預設來源；網路 timeout 20 秒、retries 1，最多同時兩部。

## 第一輪網路失敗與重跑

第一輪五部都在分段下載階段中止：師兄／.cc 巴斯／大理寺出現 `kkzycdn.com:65` DNS 解析失敗，.in 巴斯為遠端關閉連線，南方公園為 `No route to host`。未到全片解碼，不算通過。後續唯讀檢查確認 DNS 恢復，三個 `kkzycdn.com:65` 實際分段端點回傳 HTTP 200，才在相同 HEAD、新目錄重新完整下載五部。沒有變更 DNS、路由、TLS 驗證或程式碼；保留第一輪及重跑全部紀錄。

## 最新 HEAD 完整實測

**第二輪 5/5 完整通過，共 3173 個明文分段；在本批樣本未發現 AES 改動造成回歸。** 五部的 encrypted_segments／key_declarations 均為 0，確認走明文路徑。首輪五筆網路拒絕不計通過，另保留在機器紀錄。

| 來源 | 作品／單集 | 驗證／宣告段數 | MP4 bytes | 秒數 | 結果 |
|---|---|---:|---:|---:|---|
| .cc | [師兄啊師兄 [166]](https://anime1.cc/703335246-01-0166) | 657/657 | 469,434,535 | 265.4 | 通過：完整解碼 0／stderr 空 |
| .cc | [巴斯×機械 [06]](https://anime1.cc/703359916-01-0006) | 1070/1070 | 394,640,624 | 344.6 | 通過：完整解碼 0／stderr 空 |
| .in | [大理寺日志3 [01]](https://anime1.in/2026-da-li-si-ri-zhi-3-10001000) | 495/495 | 176,374,451 | 184.0 | 通過：完整解碼 0／stderr 空 |
| .in | [巴斯×機械 [01]](https://anime1.in/2026-ba-si-ji-xie-10001000) | 614/614 | 379,105,811 | 125.7 | 通過：完整解碼 0／stderr 空 |
| .cc | [南方公園 [13]](https://anime1.cc/681939613-01-0013) | 337/337 | 123,039,102 | 85.4 | 通過：完整解碼 0／stderr 空 |

共 6 筆可恢復音訊 mux 校正，範圍 3.667–19.333 ms，均符合既有 100 ms 界限；未知／fatal warning 為 0。診斷原文保留，沒有新增忽略規則。

耗時包含來源解析、下載、逐段驗證、轉封裝、下載器內部全片解碼，以及 runner 額外全片解碼；不是純下載速度。

## 回歸判定與證據

- 實際記錄每部加密段數及 key 宣告數，確認本輪走明文路徑。
- 逐段完整 HTTP 下載，再做 TS framing／sync／TEI／continuity／邊界時間軸驗證。FFmpeg 僅讀已驗證的本機快照。
- 明確 discontinuity 分組處理與 CC header 正規化保持原行為。runner 遮罩 CC 低四位後逐 byte 比對，確認其餘資料沒有改動。
- 每組及成品均檢查片長與完整診斷。未知 warning、Packet corrupt、AAC queue warning 仍拒絕；符合原定界限的小幅音訊 mux 校正保留原始診斷供查核。
- 成功要求下載器與 runner 各一次完整解碼，均 exit code 0、stderr 空。

[10 筆機器紀錄](hls-clear-regression-2026-11-04.jsonl)以 `round` 區分首輪五筆網路失敗與恢復後五筆成功，包含實測 HEAD、時間、來源頁 URL、playlist 雜湊、明文／加密段數、key 宣告數、逐段大小／SHA-256／嘗試次數、邊界分類、CC 正規化、payload identity、各組片長／診斷，以及獨立全片 decode 結果。

runner 實際逐 byte 比對 header 以外資料；另核對 3173 個 segment ledger 與 payload identity 記錄的段數、索引、大小與原始雜湊一致。`record_sha256` 是該筆其餘欄位以排序鍵 JSON 序列化後的雜湊；`local_evidence_sha256` 為本機原始 evidence（含路徑）指紋。這些是本次取得資料的稽核雜湊，並非上游提供的可信參考值。媒體只存本機 `/tmp`。

## 自動驗證與範圍

程式碼沒有因本輪驗收變更；最終文件提交會重新執行 Python 3.12／3.13 CI，包含 Ruff format／lint、Pyright、112 個測試（110 通過、2 個既有 integration skip）、真實 localhost 明文／AES FFmpeg fixtures 與 wheel／sdist build。

[AES 兩部完整實測](HLS_AES_ACCEPTANCE_2026-11-04.md)使用 `5d6783c`，與本輪 runtime 程式碼相同；保留當時的確切 HEAD，不混稱七部都在同一 Git SHA 執行。

上游 playlist／CDN 內容可能變動；本輪成功只代表當次快照通過。完整性相對於取得的 playlist，不證明上游未省略內容，也不等於人工全片影音同步審查。未做整季、所有分流或 Windows／Linux 真實上游驗收。歷次失敗紀錄均保留。
