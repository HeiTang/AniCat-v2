# AES-128 HLS 驗收 — 2026-11-04

本輪延續[分段完整性驗收](HLS_SEGMENT_ACCEPTANCE_2026-11-04.md)與[四個加密分流補驗](hls-alternate-streams-2026-11-04.jsonl)。先前的逾時與 AES-128 拒絕紀錄保留；本輪驗證新增解密支援後的行為。PR #16 保持開啟、不合併。

## 版本與驗收方法

實測版本：`5d6783cccb712fd95450d98910d6a64c187db1e7`；以獨立 `git archive` 快照執行。環境：macOS ARM64、Python 3.14.5、FFmpeg 4.4。每部新建輸出目錄，重新取得完整來源，不以既有 MP4 跳過。

- 以 RFC 8216 的 AES-128 CBC／PKCS7 解密完整片段，處理 relative key URI、key rotation、`METHOD=NONE`、明確 IV 與 media-sequence 推導 IV；SAMPLE-AES 等其他格式不納入本輪。
- HTTP 密文完整性檢查後才解密；解密結果逐段經既有 TS framing／sync／transport error／continuity counter／邊界時間軸檢查。未驗證片段不得交給 FFmpeg。
- 保留 playlist 快照與逐段證據；正規化只更動 TS continuity-counter header nibble，驗收 runner 逐 byte 確認解密後 payload 未被改動。
- 保留明確 discontinuity 分組轉封裝、每組／最終片長檢查與完整 warning 分類。未知 warning、Packet corrupt、AAC queue warning 仍 fatal；只有原先界定的音訊 mux 小幅校正可恢復且保留診斷。
- 下載器與 runner 各做一次全片解碼，兩者均要求 exit code 0、stderr 空才計通過。失敗不得覆寫既有成品。
- 加密 key 只在記憶體使用；不提交 key、媒體、signed CDN URLs 或含 key 值的診斷。

## 兩部完整下載結果

**兩部均完整通過，共 821 個 AES-128 分段。** 使用原頁預設分流與一般 `AniCatService.download_one` 入口，未手動注入 playlist、未使用替代樣本。每部一次完整下載；逐段資料、轉封裝及兩次全片解碼均成功。

| 作品／單集 | AES 解密／驗證段數 | MP4 bytes | 耗時秒 | 獨立全片解碼 |
|---|---:|---:|---:|---|
| .in 聖鬥士星矢 [01] | 417/417 | 384,436,662 | 214.6 | 通過：0／stderr 空 |
| .cc 範馬刃牙 第二季 [27] | 404/404 | 246,805,524 | 183.2 | 通過：0／stderr 空 |

本輪兩部皆無 discontinuity，解密後 TS 不需重寫 CC header；runner 確認原始明文與本機 playlist 使用的片段逐 byte 相同。轉封裝 fatal／recoverable 診斷皆空，未靠放寬 warning 過關。耗時含 runner 額外全片解碼。

## 故障與自動測試

- 112 tests：110 通過、2 個既有 integration skips。完整 suite 保留既有明文 HLS、缺段／損壞、warning／decode 拒絕與成品保護測試。
- 新增三個 AES localhost runtime cases，實際使用 HTTP、加密片段與 FFmpeg，涵蓋 key 503 後恢復、不同 key URI rotation、同 URI 不同宣告換 key、錯 key／IV、15-byte key、密文截斷、最後 block／padding 損壞。
- Parser 回歸涵蓋 relative key URI、明確／隱式 IV、短 hex IV、media-sequence、METHOD=NONE、非法 URI／IV／KEYFORMAT／版本拒絕。
- Ruff format／lint、Pyright、wheel／sdist build 通過；依賴 `cryptography==50.0.2` 由 uv.lock 鎖定。
- Python 3.12／3.13 CI 在實作 HEAD 全綠。
- `src/anicat/hls_downloader.py` 與新增 AES 前相同；warning 分類、100 ms 音訊 mux 校正界限、片長及全片 decode gate 沒有改動。

## 證據與限制

[兩筆完整分段紀錄](hls-aes-acceptance-2026-11-04.jsonl)包含實測版本、時間、來源頁 URL、playlist SHA-256、821 段密文／明文大小與 SHA-256、分段嘗試次數、key 宣告數量、邊界分類、payload identity、轉封裝片長與診斷、獨立 decode exit code／stderr。

每筆 `record_sha256` 為其餘欄位以排序鍵 JSON 序列化後的雜湊；`local_evidence_sha256` 是本機原始 evidence（含路徑）指紋。雜湊是本次取得資料的稽核證據，不是上游提供的可信參考校驗值。

前輪五部明文上游作品通過的版本仍為 `af8b56e`，本輪未宣稱把它們全部以 AES HEAD 重跑。新的明文／加密 localhost 回歸都由本輪 CI 執行。SAMPLE-AES、非 identity KEYFORMAT、fMP4、byte-range、LL-HLS、GAP 與 I-frame-only 仍明確拒絕。

完整性相對於當次 playlist 快照，不證明上游從未省略內容。未做人工全片影音同步審查、整季下載或 Windows／Linux 真實上游驗收。上游內容可能變動，本次通過不等於修復同一份歷史損壞資料。

協定依據：[RFC 8216 §4.3.2.4、§5](https://www.rfc-editor.org/rfc/rfc8216)。
