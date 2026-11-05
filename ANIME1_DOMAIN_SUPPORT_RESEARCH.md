# Anime1 多域名支援研究

原始研究與現行實作摘要（2026-11-04）。

## 現況

- `anime1.me`：既有 API／direct MP4 流程。
- `anime1.pw`：既有 direct MP4 流程，可用 Range 續傳。
- `anime1.cc`：單集頁使用 `/_player?url=...index.m3u8`，全集頁有數字 ID 與跨 ID 的分頁劇集清單。
- `anime1.in`：作品 slug 頁列出含集數的單集網址，播放器使用 `/_player_x_/` 或 `/_player_y_/`，來源為 HLS。

## 本 PR

新增 provider 分流、`.cc`／`.in` URL 分類、單集播放器解析、全集分頁展開，以及 FFmpeg HLS → MP4 後端。

HLS 輸出影像直接複製，音訊以 AAC 重編碼並同步時間戳，避免上游分段非遞增 DTS。通過非空檔案、playlist 片長比對與 FFmpeg 診斷檢查後才替換目的檔；HLS 不沿用 direct MP4 的 `.part` 續傳。

## 驗證限制

- 靜態 parser、下載器、服務分流與真實 FFmpeg runtime 測試涵蓋。
- 真實站點單集下載需外部網路與 FFmpeg，CI 不執行上游整集下載。
- HLS 失敗後重新執行會從頭下載，`--retries` 對 playlist 與 FFmpeg 工作階段做有限次數的完整重試。
