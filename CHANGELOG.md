# Changelog

## 0.1.0 - 2026-05-24

### Added

- 採用 `src/` layout 與 `anicat` CLI entry point。
- 支援單集、季度、多 URL 下載。
- 支援併發下載、`.part` 續傳、原子寫入與安全檔名。
- 支援 Rich 多任務進度列、`--verbose/-v`、`--quiet/-q`。
- 支援 stream 中斷後 Range retry，並使用 `If-Range` / `Content-Range` 保護 append 正確性。
- 提供 typed Python API：`from anicat import AniCatService, DownloadOptions`。
- 新增 Anime1 integration smoke test scaffold，預設略過，手動以 `ANICAT_RUN_INTEGRATION=1` 啟用。

### Changed

- 以 Poetry 管理 package 與 dev tooling。
- 統一專案預設值與常數來源，降低 magic numbers。
- README 改為繁體中文產品導向文件。

### Quality

- 加入 Ruff format/check、Pyright standard、unittest、compileall、Poetry check。
- 加入 GitHub Actions Python 3.12 / 3.13 matrix 與 Poetry cache。
- 加入 pre-commit local hooks，與 CI 品質門檻保持一致。
