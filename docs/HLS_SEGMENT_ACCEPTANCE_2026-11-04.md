# HLS 分段完整性與 discontinuity 驗收 — 2026-11-04

本報告補充並保留[前一輪失敗紀錄](HLS_ACCEPTANCE_2026-11-04.md)，不以替換樣本掩蓋失敗。PR #16 維持開啟、不合併。

## 分流補驗結果（22:55，f130d2a）

**兩部都沒有找到「目前 PR 可下載」的頁面所列來源；不是所有來源都失效。** 使用當時最新 HEAD `f130d2a723725efedd8424e6c4c2c61e01e2bfd4` 的獨立程式快照，確認頁面所有分流並逐一執行正式 `download_hls_episode`。來源已可連線，但四個 media playlist 均明確宣告 `METHOD=AES-128`，被現有不支援加密 HLS 的驗證拒絕。

| 作品 | 頁面分流／播放器 | playlist 主機 | 宣告段數 | 最新結果 |
|---|---|---|---:|---|
| 聖鬥士星矢 [01] | 分流1，`/_player_x_/1329462` | `vv.jisuzyv.com` | 417 | AES-128；目前版本不支援 |
| 聖鬥士星矢 [01] | 分流2，`/_player_x_/1329454` | `hd.kuktxu.com` | 402 | AES-128；目前版本不支援 |
| 聖鬥士星矢 [01] | 分流3，`/_player_x_/1329458` | `play.xluuss.com` | 402 | AES-128；目前版本不支援 |
| 範馬刃牙 第二季 [27] | 頁面唯一 `/_player` | `vv.jisuzyv.com` | 404 | AES-128；目前版本不支援，沒有其他列出的分流 |

每個來源正式入口均取得兩次 playlist，回報 `unsupported HLS encryption method: AES-128 after 2 preflight attempt(s)`。**驗證／下載段數為 0，未產生 MP4、未執行全片解碼，不算驗收通過。** 表中段數只是 playlist 宣告值，不能當成完成下載。

分流2／3為驗收程式依頁面按鈕明確選取後直接呼叫現有下載後端；未新增 CLI 分流選擇或自動 fallback。這輪只更新驗收文件，未放寬加密格式拒絕。分流清單限於兩個指定作品頁當時列出的選項，未宣稱全網無其他來源。

[四筆分流補驗證據](hls-alternate-streams-2026-11-04.jsonl)包含頁面／播放器 SHA-256、選定分流、playlist 主機與 URL、兩次 manifest 雜湊、AES 方法、宣告段數、錯誤、空成品清單與時間。前輪連線逾時以及更早的 decode failure 保留在原紀錄中，最新阻擋原因更新為「來源可連線但 AES-128 不支援」。PR 繼續保持開啟，不合併。

## 前輪驗收版本與方法

下載、分段驗證、分組轉封裝與成品驗證均使用 `af8b56ed198b258265c8d63279a9d68f605d3a50` 的獨立 `git archive` 快照。`d5fb5a5` 啟動的兩部驗收在 CI 發現 FFmpeg 新版診斷格式差異後主動中止，不計通過或失敗；以下全部重新由 `af8b56e` 開始。每部使用新輸出目錄，實際重新取得完整來源，沒有利用既有成品跳過。環境：macOS ARM64、Python 3.14.5、FFmpeg 4.4。

1. 固定最終 media playlist 快照，逐段完整下載；FFmpeg 僅讀已驗證的本機片段，避免驗證後再次向動態 CDN 取得不同內容。
2. 檢查 HTTP 狀態、Content-Length／Content-Range／Content-Encoding、空資料與讀取中斷。逐段記錄大小、SHA-256、嘗試次數。
3. 檢查 188-byte TS framing、sync、transport error、adaptation field、各 PID continuity counter、邊界 DTS／PTS。重複 counter 只有相同 payload 才接受；B-frame 以 DTS 優先，避免把合法 PTS 重排序視為損壞。
4. 只有 playlist 明確標示的 discontinuity 才允許邊界重設；未標示的 counter／時間回退、片段內缺 packet、codec 損壞仍拒絕。TS 自身合法的 discontinuity indicator 依其語意處理。
5. 轉封裝前僅重寫 TS header 的 continuity-counter 低四位，維持後續 offset。驗收 runner 獨立遮罩這四位並逐 byte 比對原始與正規化資料，確認 payload 未改動。
6. 依明確 discontinuity 分組，分別以固定 48 kHz 雙聲道 AAC 處理，再 stream-copy 串接，避免 encoder 跨越音訊格式重設。每組與最終成品均檢查片長與所有診斷。
7. 所有未知 warning、`Packet corrupt`、AAC `Queue input is backward in time` 都是 fatal。僅接受已界定的音訊 mux DTS 校正（48 kHz 下不超過 4800 samples，100 ms），保留原診斷供查核，不直接忽略。
8. 成品先由下載器完整解碼，再由驗收 runner 獨立完整解碼；兩者都要求 exit code 0 且 stderr 空，通過才算成功。失敗不得覆寫既有 MP4。

## 前輪完整下載結果（af8b56e）

**本輪 5/7 通過；前一輪四個 discontinuity 拒絕項目均通過，兩部 CDN 逾時仍未通過。**

| 來源 | 作品／單集 | 驗證／宣告段數 | MP4 bytes | 秒數 | 結果 |
|---|---|---:|---:|---:|---|
| .cc | [師兄啊師兄 [166]](https://anime1.cc/703335246-01-0166) | 657/657 | 469,433,141 | 282.8 | 通過：完整解碼 0／stderr 空 |
| .cc | [巴斯×機械 [06]](https://anime1.cc/703359916-01-0006) | 1070/1070 | 394,652,426 | 332.1 | 通過：完整解碼 0／stderr 空 |
| .in | [大理寺日志3 [01]](https://anime1.in/2026-da-li-si-ri-zhi-3-10001000) | 495/495 | 176,386,152 | 100.8 | 通過：完整解碼 0／stderr 空 |
| .in | [巴斯×機械 [01]](https://anime1.in/2026-ba-si-ji-xie-10001000) | 614/614 | 379,105,811 | 287.5 | 通過：完整解碼 0／stderr 空 |
| .cc | [南方公園 [13]](https://anime1.cc/681939613-01-0013) | 337/337 | 123,039,102 | 77.3 | 通過：完整解碼 0／stderr 空 |
| .in | [聖鬥士星矢 [01]](https://anime1.in/1986-sheng-dou-shi-xing-shi-10001000) | 0/— | — | 40.6 | 未通過：CDN 連線逾時，2 次 preflight 嘗試均失敗，尚未取得分段 |
| .cc | [範馬刃牙 第二季 [27]](https://anime1.cc/783931802-01-0027) | 0/— | — | 41.2 | 未通過：CDN 連線逾時，2 次 preflight 嘗試均失敗，尚未取得分段 |


來源 playlist／CDN 內容會變動；本次重跑若通過先前 404 或 decode failure 的作品，只證明新快照通過，不能宣稱程式修復了同一份上游損壞資料。舊失敗紀錄繼續保留。

秒數含來源解析、下載、驗證、轉封裝與 runner 的額外全片解碼；不是純下載速度。片長容差為 `max(0.25 秒, EXTINF 總長 × 0.001)`，逐段驗證與全片解碼另外把關，不以片長近似取代缺片檢查。

## 故障與重試回歸

- 真實 localhost HTTP + FFmpeg：中段 404、64-byte 截斷、刪掉整個 188-byte packet、保留 TS header 但破壞 codec payload，均拒絕且保留原成品。
- 中段先回 503、先讀取逾時後恢復：有限重試成功；重試從該段 byte 0 開始。
- 真實 FFmpeg fixtures：明確標記的 CC reset 可恢復；44.1 kHz 單聲道切到 48 kHz 雙聲道可完成並通過內部全片解碼。
- 單元測試：未標示邊界 reset、內部 counter gap、零退出碼但有 warning／decode error、空輸出、片長不符、重試耗盡與大幅 DTS 校正仍拒絕。
- 本機 107 tests：105 通過、2 個既有上游 integration skips；三個 FFmpeg runtime cases 實際執行。Ruff format／lint、Pyright、wheel／sdist build 通過。

## 證據與限制

機器紀錄見 [JSONL](hls-segment-acceptance-2026-11-04.jsonl)。每筆包含實測 HEAD、時間、URL、playlist 雜湊、宣告／驗證段數、逐段大小與雜湊、重試、邊界分類、CC header 改寫、payload identity hash，以及各組 FFmpeg 片長與診斷。這些雜湊是本次下載的稽核證據，並非上游提供的可信校驗值。`record_sha256` 為該筆其餘欄位以排序鍵 JSON 序列化後的雜湊；`local_evidence_sha256` 是本機原始 evidence 檔指紋（含本機路徑），不等於 JSONL record 指紋。影片與 TS 只存本機 `/tmp`，未提交媒體或 CDN 簽名網址。

- 完整性是相對於取得的 playlist 快照；無法證明上游原本就沒有從 playlist 省略內容，也不等於人工逐段影音同步審查。
- 本次僅支援未加密 MPEG-TS VOD；AES／SAMPLE-AES、fMP4、byte-range、LL-HLS、GAP、I-frame-only 明確拒絕。
- HTTP 404、持續逾時或來源本身損壞不會因 discontinuity 修正而變成成功。
- 未做整季下載、所有分流、Windows／Linux 真實上游或全片人工觀影驗收。Linux CI 執行的是產生的 localhost fixture。
- HLS 沒有 `.part` 續傳或即時 byte 進度；分段暫存增加磁碟需求。網路 timeout 是讀取閒置時間，不是整部總時限。

協定依據：[RFC 8216 §3、§4.3.2.3](https://www.rfc-editor.org/rfc/rfc8216)，時間軸／格式切換應由 discontinuity 標示；這不是全面豁免 transport 或 decode 錯誤。
