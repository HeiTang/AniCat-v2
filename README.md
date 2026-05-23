# AniCat-v2

> 穩定、快速、可續傳的 Anime1 命令列下載器。

AniCat-v2 可以把 Anime1 的單集或季度連結下載成乾淨的本機 MP4 檔案。這版已經不是單檔腳本雛形，而是具備套件化核心、型別檢查、離線測試、CI 與 Rich 多任務進度列的下載工具。

## 特色

- **支援單集與季度下載**：可輸入單集 URL、分類/季度 URL，也支援多 URL 批次下載。

- **併發下載**：用 `--concurrency` 控制同時下載數，整季下載更有效率。

- **Rich 多任務進度列**：顯示整體下載量、速度、完成集數，以及目前下載中的單檔進度。

- **預設支援續傳**：中斷時保留 `*.mp4.part` 暫存檔，下次執行會接續下載。

- **串流中斷自動續傳**：下載途中斷線會用 Range request 重試，並驗證 `Content-Range` 避免錯誤 append。

- **原子寫入**：下載完成後才轉成 `.mp4`，避免未完成檔案被誤認成完整影片。

- **安全檔名處理**：自動清理跨平台非法字元與 Windows 保留名稱。

- **產品級專案結構**：採用 `src/` layout、CLI entry point、型別檢查、單元測試與 CI。

## 快速開始

```bash
poetry install
poetry run anicat https://anime1.me/15651
```

下載整個分類/季度：

```bash
poetry run anicat https://anime1.me/category/... --output ./Anime1_Download --concurrency 3
```

多個 URL 可用空白或逗號分隔：

```bash
poetry run anicat https://anime1.me/15651 https://anime1.me/15603
poetry run anicat https://anime1.me/15651,https://anime1.me/15603
```

### 不使用 Poetry 安裝

Poetry 是建議的開發與執行方式；若只想用一般 Python 環境安裝：

```bash
python3 -m pip install .
anicat https://anime1.me/15651
```

## 常用參數

查看完整說明：

```bash
poetry run anicat --help
```

常用參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| `-o`, `--output DIR` | 指定下載目錄 | `./Anime1_Download` |
| `-c`, `--concurrency N` | 併發下載數 | `3` |
| `--timeout SECONDS` | HTTP 讀取逾時秒數 | `30` |
| `--retries N` | HTTP 與串流中斷重試次數 | `3` |
| `--chunk-size BYTES` | 下載分塊大小 | `524288` |
| `--overwrite` | 覆寫已完成的同名檔案 | `False` |
| `--no-resume` | 不要沿用既有 `.part` 暫存檔 | `False` |
| `--no-progress` | 關閉 Rich 進度列，適合只看文字輸出的場景 | `False` |
| `-v`, `--verbose` | 顯示診斷 log；`-vv` 會顯示 HTTP retry/debug 細節 | `False` |
| `-q`, `--quiet` | 只保留錯誤等級的診斷 log，下載摘要輸出不受影響 | `False` |

## Exit Codes

| Code | 意義 |
|---|---|
| `0` | 全部下載成功，或目標檔案已存在而略過 |
| `1` | 至少一個 URL 失敗，或沒有找到任何集數 |
| `2` | CLI 使用方式或參數錯誤，例如沒有輸入 URL |

## 專案架構

```txt
src/anicat/
    client.py       # HTTP session、逾時、重試、cookie
    extractor.py    # Anime1 HTML/API 解析
    downloader.py   # 續傳、原子寫入、安全檔名
    service.py      # URL 展開與併發下載協調
    progress.py     # Rich 多任務進度列
    cli.py          # 薄 CLI 入口，只處理參數與輸出
tests/              # 離線單元測試，不依賴 Anime1 網路狀態
```

設計原則：

- **CLI 保持輕薄**：CLI 只處理參數與輸出，不放下載業務邏輯。

- **核心可測試**：解析、下載、URL 處理都能用 fixture/mock 離線測試。

- **依賴邊界清楚**：使用 `Protocol` 定義 HTTP 與下載邊界。

- **錯誤彼此隔離**：單集失敗不會拖垮整批下載，最後統一輸出摘要。

## Python API

AniCat-v2 也提供 typed package API：

```python
from pathlib import Path

from anicat import AniCatService, DownloadOptions

options = DownloadOptions(output_dir=Path("./Anime1_Download"))
service = AniCatService(options)
episode_urls = service.collect_episode_urls(["https://anime1.me/15651"])
reports = service.download_many(episode_urls)
```

## 品質檢查

本地與 CI 使用同一組品質門檻：

```bash
poetry install --with dev
poetry run ruff format --check .
poetry run ruff check .
poetry run pyright
poetry run python -m unittest
poetry run python -m compileall src/anicat tests
poetry check
```

真實 Anime1 smoke test 預設不跑，避免 CI 依賴外部網站；需要時手動開啟：

```bash
ANICAT_RUN_INTEGRATION=1 poetry run python -m unittest tests.test_integration_smoke
ANICAT_RUN_INTEGRATION=1 ANICAT_SMOKE_URL=https://anime1.me/28979 poetry run python -m unittest tests.test_integration_smoke
```

GitHub Actions 會在 push / pull request 時執行格式檢查、lint、型別檢查、單元測試與語法編譯檢查。

## 備註

- 單元測試不依賴 Anime1 網路狀態。
- 真實 Anime1 smoke test 只測解析與 Range contract，不下載完整影片。
- 若 Anime1 HTML/API 結構改變，優先修 `extractor` 與對應 fixture tests。
