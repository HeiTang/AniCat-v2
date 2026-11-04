# Changelog

## Unreleased

### Added

- HLS 下載會在 FFmpeg 前確認 media playlist 已結束，並比對輸出與 `#EXTINF` 總長；另以 `--retries` 有限次數重試 playlist 驗證與完整工作階段，避免缺片輸出被誤視為成功。
- HLS 會固定 playlist snapshot、完整抓取並驗證每個 MPEG-TS segment；只在明示 `EXT-X-DISCONTINUITY` 邊界做 header-only continuity counter 正規化，缺片與未標記 discontinuity 一律拒絕。
- 新增 `anime1.cc` 與 `anime1.in` 的 HLS 單集解析、全集分頁展開及 FFmpeg MP4 輸出。
- 支援 `anime1.pw` 單集、`?cat=` 分類頁與 slug 分類頁下載；此來源直接解析頁面 MP4 source，沿用現有 Range 續傳下載流程。

### Changed

- HLS 來源的音訊改用 AAC 重編碼並同步時間戳，以處理上游分段的非遞增 DTS。
- HLS 音訊固定為 48 kHz／雙聲道並保留時間軸，避免音訊格式切換時重設 PTS；拒絕錯誤診斷，只允許小於等於 100 ms 的音訊 mux 校正。
- 關閉 HLS HTTP 連線重用，處理片段跨 CDN 主機重導向。
- HLS 下載須通過非空檔案、片長、診斷與全片解碼驗證後才替換既有成品；解碼退出碼 0 但 stderr 有錯誤也會拒絕。
- HLS `timeout` 僅限制來源讀取閒置時間，不設定整集 FFmpeg 總時限。
- `anime1.pw` 頁面解析改用 GET 抓取 HTML，避免依賴 WordPress / Cloudflare 對 POST 頁面請求的相容行為。
- direct video parser 會優先選擇 `video/mp4` source；若頁面提供多個 source 會記錄 warning。

### Fixed

- 分類頁 HTML 無法解析出 episode link 時改為回報 `ParseError`，避免 selector 失效時 silent 回傳 0 集。

## 0.1.0 - 2026-05-24

### Added

- 採用 `src/` layout 與 `anicat` CLI entry point。
- 支援單集、季度、多 URL 下載。
- 支援併發下載、`.part` 續傳、原子寫入與安全檔名。
- 支援 Rich 多任務進度列、`--verbose/-v`、`--quiet/-q`。
- 支援 `-V/--version` 顯示 CLI 版本。
- 支援 `--connect-timeout` 分別控制連線與讀取逾時。
- 支援 stream 中斷後 Range retry，並使用 `If-Range` / `Content-Range` 保護 append 正確性。
- 支援 server 忽略 Range request 時重置單檔進度，不讓總進度倒退。
- 提供 typed Python API：`from anicat import AniCatService, DownloadOptions`。
- 新增 Anime1 integration smoke test scaffold，預設略過，手動以 `ANICAT_RUN_INTEGRATION=1` 啟用。

### Changed

- 以 Poetry 管理 package 與 dev tooling。
- 統一專案預設值與常數來源，降低 magic numbers。
- 併發下載改為每個 worker thread 重用一個 HTTP client/session，降低 connection pool 浪費。
- 強化 Anime1 `Set-Cookie` fallback parser，支援 comma-joined header 與 `expires` 內含逗號的情境。
- 移除 `lxml` 必要依賴，改用 BeautifulSoup 的標準 `html.parser` fallback，降低 Windows 安裝失敗機率。
- README 改為繁體中文產品導向文件。

### Quality

- 加入 Ruff format/check、Pyright standard、unittest、compileall、Poetry check。
- 加入 GitHub Actions Python 3.12 / 3.13 matrix 與 Poetry cache。
- 加入 pre-commit local hooks，與 CI 品質門檻保持一致。
