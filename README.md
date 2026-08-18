# AniCat-v2

> 穩定、快速、可續傳的 Anime1 命令列下載器。

AniCat-v2 是一個專注於 Anime1 下載體驗的 CLI 工具。它可以把單集、分類、季度或多個 URL 解析成乾淨的本機 MP4 檔案，並提供併發下載、`.part` 續傳、Rich 多任務進度列、串流中斷重試與安全檔名處理。


## 特色

- **支援單集與季度下載**：可輸入 `anime1.me` / `anime1.pw` 單集 URL、分類/季度 URL，也支援多 URL 批次下載。

- **併發下載**：用 `--concurrency` 控制同時下載數，整季下載更有效率。

- **Rich 多任務進度列**：顯示整體下載量、速度、完成集數，以及目前下載中的單檔進度。

- **預設支援續傳**：預設沿用 `.mp4.part` 暫存檔接續下載。

- **串流中斷重試**：使用 Range request 重新接續，並驗證 `Content-Range`。

- **支援來源**

    | 來源 | 單集 URL | 分類 / 季度 URL | 狀態 |
    |---|---|---|---|
    | `anime1.me` | `https://anime1.me/15651` | `https://anime1.me/category/...` | 支援 |
    | `anime1.pw` | `https://anime1.pw/349` | `https://anime1.pw/?cat=60`、`https://anime1.pw/<slug>` | 支援 |
    | `anime1.cc` | HLS / m3u8 | HLS / m3u8 | 尚未支援 |
    | `anime1.in` | HLS / m3u8 | HLS / m3u8 | 尚未支援 |


## 安裝方式

### A. GitHub Release 安裝（推薦）

```bash
python3 -m pip install "git+https://github.com/HeiTang/AniCat-v2.git@v0.2.0"
anicat --help
```

### B. 本機開發安裝

- 下載專案
    
    ```bash
    git clone https://github.com/HeiTang/AniCat-v2.git
    cd AniCat-v2
    ```

- 安裝套件

    ```bash
    # 使用 Poetry
    poetry install
    poetry run anicat --help

    # 使用一般 Python 環境
    python3 -m pip install .
    anicat --help
    ```

## 使用方法

```bash
anicat URL [URL ...] [OPTIONS]
anicat search KEYWORD
```

### 範例

0. 先搜尋再下載（不必自己去網站找 URL）：

    ```bash
    anicat search 碧藍之海
    ```

    ```text
    GRAND BLUE 碧藍之海 第三季 [連載中(07) · 2026 夏]
      -> https://anime1.me/?cat=1935
    GRAND BLUE 碧藍之海 第二季 [1-12 · 2025 夏 · 悠哈璃羽]
      -> https://anime1.me/?cat=1708
    + 2 result(s)
    ```

    把印出的網址直接餵回 `anicat` 即可下載整季。

1. 單集下載：

    ```bash
    anicat https://anime1.me/12345
    ```

2. 分類 / 季度下載：

    ```bash
    anicat https://anime1.me/category/your-category-slug
    anicat https://anime1.me/?cat=1935
    anicat https://anime1.pw/your-category-slug
    ```

3. 多 URL 批次下載：

    ```bash
    anicat https://anime1.me/12345 https://anime1.me/67890
    anicat https://anime1.me/12345,https://anime1.me/67890
    ```

### 常用參數

| 參數 | 說明 | 預設值 |
|---|---|---|
| `-o`, `--output DIR` | 指定輸出資料夾 | `./Anime1_Download` |
| `-c`, `--concurrency N` | 併發下載數 | `3` |
| `--timeout SECONDS` | HTTP 讀取逾時秒數 | `30` |
| `--connect-timeout SECONDS` | HTTP 連線逾時秒數 | `10` |
| `--retries N` | HTTP 與串流中斷重試次數 | `3` |
| `--chunk-size BYTES` | 下載分塊大小 | `524288` |
| `--overwrite` | 覆寫已完成的同名檔案 | `False` |
| `--no-resume` | 不沿用既有 `.part` 暫存檔 | `False` |
| `--no-progress` | 關閉進度列 | `False` |
| `-v`, `--verbose` | 顯示診斷 log；`-vv` 顯示 HTTP retry/debug 細節 | `False` |
| `-q`, `--quiet` | 只保留錯誤等級 log，下載摘要仍會輸出 | `False` |
| `-V`, `--version` | 顯示版本後離開 | - |

完整參數：
```bash
anicat --help
anicat search --help
```

### `anicat search`

| 參數 | 說明 |
|---|---|
| `KEYWORD` | 比對動畫標題的子字串，不分大小寫 |
| `-v`, `--verbose` | 顯示診斷 log |
| `-q`, `--quiet` | 只保留錯誤等級 log |

搜尋來源是 Anime1 的目錄索引 `animelist.json`，即時抓取不做快取。找不到相符項目時 exit code 為 `1`。

## 進階說明

### Python API

除了 CLI，AniCat-v2 也提供 typed package API：

```python
from pathlib import Path

from anicat import AniCatService, DownloadOptions

options = DownloadOptions(output_dir=Path("./Anime1_Download"))
service = AniCatService(options)

episode_urls = service.collect_episode_urls(["https://anime1.me/15651"])
reports = service.download_many(episode_urls)
```

搜尋目錄索引：

```python
from anicat import Anime1Client
from anicat.catalog import fetch_catalog, search_catalog

with Anime1Client() as client:
    matches = search_catalog(fetch_catalog(client), "碧藍之海")

for entry in matches:
    print(entry.title, entry.url)
```

### Exit Codes

| Code | 意義 |
|---|---|
| `0` | 全部下載成功，或目標檔案已存在而略過 |
| `1` | 至少一個 URL 失敗、沒有找到任何集數，或搜尋沒有相符項目 |
| `2` | CLI 使用方式或參數錯誤，例如沒有輸入 URL |

## 貢獻

歡迎任何形式的貢獻！請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md) 以了解專案規範與流程。

## License

本專案採用 GPL-3.0 License，詳見 [LICENSE](LICENSE) 文件。
