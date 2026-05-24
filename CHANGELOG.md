# Changelog

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
- README 改為繁體中文產品導向文件。

### Quality

- 加入 Ruff format/check、Pyright standard、unittest、compileall、Poetry check。
- 加入 GitHub Actions Python 3.12 / 3.13 matrix 與 Poetry cache。
- 加入 pre-commit local hooks，與 CI 品質門檻保持一致。
