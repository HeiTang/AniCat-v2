# AniCat-v2

Anime1 下載器。現在核心是可測試 package，CLI 只是薄入口。

## 安裝

```bash
poetry install
```

或使用 `requirements.txt`：

```bash
python3 -m pip install -r requirements.txt
```

## 使用

```bash
poetry run anicat https://anime1.me/15651
poetry run anicat https://anime1.me/category/... -o ./Anime1_Download -c 3
```

相容舊入口：

```bash
python3 anime1.py https://anime1.me/15651
```

支援：

- 單集 URL：`https://anime1.me/15651`
- 分類/季度 URL：`https://anime1.me/category/...`
- 多 URL：可用空白或逗號分隔
- 併發下載：`--concurrency 3`
- Rich 多任務進度列：總體下載量 + 併發中的單檔進度
- 斷點續傳：預設啟用，暫存檔為 `*.mp4.part`
- 覆寫既有檔案：`--overwrite`
- 停用續傳：`--no-resume`
- 停用進度列：`--no-progress`

## 架構

- `anicat.client`：HTTP session、timeout、retry/backoff、cookie 管理。
- `anicat.extractor`：HTML/API 解析，只負責把上游資料轉成 domain model。
- `anicat.downloader`：檔名清理、`.part` 原子下載、resume、檔案完整性檢查。
- `anicat.service`：use case orchestration，負責 URL 展開與併發 job 隔離。
- `anicat.progress`：Rich-based 多任務進度 UI，和下載核心解耦。
- `anicat.cli`：CLI 參數解析與輸出，不放業務邏輯。
- `tests`：離線單元測試，不依賴 Anime1 網路狀態。
- 採用 `src/` layout，讓測試與 CLI 走安裝後的 package，避免 repo root import shadowing。

## 驗證

```bash
poetry run ruff format --check .
poetry run ruff check .
poetry run pyright
poetry run python -m unittest
poetry run python -m compileall src/anicat anime1.py tests
poetry check
```
