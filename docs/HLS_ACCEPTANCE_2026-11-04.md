# HLS 實測驗收 — 2026-11-04

**本輪驗收有未通過項目，尚不建議合併或宣稱兩站全面支援。**

環境：macOS ARM64、Python 3.14.5、FFmpeg 4.4；CI 另驗證 Ubuntu Python 3.12／3.13。

起始 PR HEAD 為 `f6f427e`。最終下載／轉碼參數為 `754c0eb`；`8eb44ff` 另加入成品全片解碼拒絕條件，沒有更動下載／轉碼參數。成功樣本另套用該驗證，原始資料見 [JSONL](hls-acceptance-2026-11-04.jsonl)。每筆使用新目錄，不以既有檔案跳過代替完整下載。耗時欄為下載工作階段秒數，不含外部解碼。

## 最終完整下載批次

成功條件：下載器通過非空輸出、playlist EXTINF 片長與 FFmpeg 進度片長比對、診斷檢查，再完整解碼；return code 0 且 stderr 空才計入通過。逐筆 expected/output duration 未輸出到 runner，片長比對證據來自該版本下載器內部檢查，不能將此表當成逐幀或人工影音同步驗證。

| 來源 | 作品／單集 | bytes | 下載秒數 | 完整解碼及結果 |
|---|---|---:|---:|---|
| .cc | [巴斯×機械](https://anime1.cc/703359916-01-0006) | — | 2095.0 | 未通過：FFmpeg failed (0): [mpegts @ 0x155808200] Packet corrupt (stream = 0, dts = 25959896). after 2 attempt(s) |
| .cc | [師兄啊師兄](https://anime1.cc/703335246-01-0166) | — | 1426.4 | 未通過：FFmpeg failed (0): [mpegts @ 0x11e008200] Packet corrupt (stream = 0, dts = 10265090). after 2 attempt(s) |
| .cc | [諸界末日在線動態漫](https://anime1.cc/663219124-01-0213) | 46066004 | 53.7 | 通過：0／stderr 空 |
| .in | [大理寺日志3](https://anime1.in/2026-da-li-si-ri-zhi-3-10001000) | — | 941.3 | 未通過：FFmpeg failed (0): [mpegts @ 0x143015800] Packet corrupt (stream = 0, dts = 10677600). after 2 attempt(s) |
| .in | [巴斯×機械 audio regression](https://anime1.in/2026-ba-si-ji-xie-10001000) | — | 1621.3 | 未通過：FFmpeg failed (0): [mpegts @ 0x130008e00] Packet corrupt (stream = 0, dts = 32166000). after 2 attempt(s) |
| .in | [我被困在同一天一千年動態漫](https://anime1.in/2025-wo-bei-kun-zai-tong-yi-tian-yi-qian-nian-dong-tai-man-10192000) | 26417023 | 68.7 | 通過：0／stderr 空 |
| .in | [諸界末日在線動態漫](https://anime1.in/2025-zhu-jie-mo-ri-zai-xian-dong-tai-man-10133000) | 26030985 | 53.0 | 通過：0／stderr 空 |

最終結果：3/7 通過。

## 起始 HEAD 六部驗收

| 來源 | 作品／單集 | 原始結果 |
|---|---|---|
| .cc | [南方公園第一季](https://anime1.cc/681939613-01-0013) | FFmpeg failed (1): https://vip.ffzy-play9.com/20261104/52336_6eeeefc8/index.m3u8: Server returned 404 Not Found |
| .cc | [師兄啊師兄](https://anime1.cc/703335246-01-0166) | 完整下載與解碼通過 |
| .in | [巴斯×機械](https://anime1.in/2026-ba-si-ji-xie-10001000) | FFmpeg failed (1): [mp4 @ 0x128851a00] Non-monotonous DTS in output stream 0:1; previous: 17086464, current: 0; aborting. |
| .in | [諸界末日在線動態漫](https://anime1.in/2025-zhu-jie-mo-ri-zai-xian-dong-tai-man-10133000) | 完整下載與解碼通過 |
| .in | [聖鬥士星矢](https://anime1.in/1986-sheng-dou-shi-xing-shi-10001000) | FFmpeg failed (1): [tcp @ 0x124e1a3c0] Connection to tcp://vv.jisuzyv.com:443 failed: Operation timed out https://vv.jisuzyv.com/play/aKrYxmMe/index.m3u8: Operation timed out |
| .cc | [範馬刃牙 第二季](https://anime1.cc/783931802-01-0027) | [h264 @ 0x10f80b600] error while decoding MB 32 47, bytestream -5  |

## 修正及失敗／重試驗證

- 音訊格式切換：固定 48 kHz／雙聲道並保留時間軸；只允許不超過 100 ms 的音訊 mux 校正，其餘診斷拒絕成品。巴斯音訊診斷參數曾完整下載並解碼通過，但最終嚴格檢查仍可能因來源 Packet corrupt 拒絕，不可混為整體通過。
- 關閉 HLS HTTP persistent connection，避免片段跨 CDN 主機重導向時錯誤重用連線。
- 中段 404／截斷：真實 localhost HTTP HLS + FFmpeg 測試，確認拒絕且不覆寫既有 MP4。
- 中段先 503／讀取逾時，恢復後重跑：真實 HTTP + FFmpeg 測試，確認有限重試成功。
- 退出 0 但診斷或全片解碼有錯誤、片長不符、空輸出、缺 FFmpeg、重試耗盡：單元回歸確認拒絕或正確回報。
- 92 tests：90 通過、2 個上游 integration tests 跳過；真實本機 FFmpeg runtime 已執行。
- Ruff lint／format、Pyright、wheel／sdist build 通過；遠端 Python 3.12／3.13 CI 通過。
- 完整解碼使用 `ffmpeg -v error -xerror -i FILE -vsync 0 -enc_time_base -1 -f null -`，同時檢查 return code 與 stderr。

## 限制

- Packet corrupt 可能來自 discontinuity 或實際損壞。本版本保守拒絕，因為只有完整解碼無錯誤仍不足以證明中間片段未被略過；部分可播放來源也可能被拒絕。尚需更強的分段完整性證據才能安全放寬。
- 不保證永久 CDN 404、來源連線故障或固定損壞內容能靠重試修復。
- 未整季下載、未覆蓋所有分流、未做人工逐段影音同步審查或 Windows／Linux 真實上游驗收。
- HLS 不支援 .part 續傳，沒有即時位元組進度；timeout 是網路讀取閒置時間，不是整集總時限。
- 上游整集下載不放入 CI。所有影片只存本機 /tmp，未提交媒體檔。
