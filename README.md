<p align="center">
  <img src="website/assets/anicat-logo-readme.svg" alt="AniCat 主 Logo：橘白像素貓" width="192" height="192">
</p>

# AniCat-v2

> 把 Anime1 的單集與整季影片存到電腦。

貼上作品網址即可下載成 MP4。支援多網址批次下載、併發下載與逐集狀態，直接 MP4 來源中斷後可接續下載。


## 特色

- **支援單集與季度下載**：可貼上單集、分類或季度網址，也能一次下載多個網址。

- **併發下載**：用 `--concurrency` 控制同時下載數，整季下載更有效率。

- **Rich 多任務進度列**：顯示整體下載量、速度、完成集數，以及目前下載中的單檔進度。

- **下載續傳**：直接 MP4 中斷後可從暫存檔接續下載。

- **支援來源**

    | 來源 | 單集 URL | 分類 / 季度 URL | 下載方式 |
    |---|---|---|---|
    | `anime1.me` | `https://anime1.me/15651` | `https://anime1.me/category/...` | 直接 MP4，可續傳 |
    | `anime1.pw` | `https://anime1.pw/349` | `https://anime1.pw/?cat=60`、`https://anime1.pw/<slug>` | 直接 MP4，可續傳 |
    | `anime1.cc` | 三段數字單集網址 | 數字 ID 全集網址 | HLS，中斷後從頭下載 |
    | `anime1.in` | 含集數的 slug 網址 | 作品 slug 全集網址 | HLS，中斷後從頭下載 |


## 安裝方式

選下列其中一種方式即可使用。開發環境請見 [CONTRIBUTING.md](CONTRIBUTING.md#開發環境)。

### 下載桌面 App

到 [GitHub Releases](https://github.com/HeiTang/AniCat-v2/releases/tag/v0.4.0) 下載對應 macOS 或 Windows 的 ZIP，完整解壓縮後開啟 AniCat。

### 安裝命令列版

準備 Git 與 [uv](https://docs.astral.sh/uv/getting-started/installation/)，在終端機執行：

```bash
uv tool install "git+https://github.com/HeiTang/AniCat-v2.git@v0.4.0"
anicat --help
```

## 使用方法

```text
anicat "<URL>" ["<URL>" ...] [OPTIONS]
anicat search [<關鍵字>]
```

### 範例

- 先搜尋再下載（不必自己去網站找 URL）：

    ```bash
    anicat search 碧藍之海
    ```

    ```text
     Title                        Episodes · Season                   URL
     ─────────────────────────────────────────────────────────────────────────────────────
     GRAND BLUE 碧藍之海 第三季   連載中(07) · 2026 夏                https://anime1.me/?cat=1935
     GRAND BLUE 碧藍之海 第二季   1-12 · 2025 夏 · 悠哈璃羽           https://anime1.me/?cat=1708
     碧藍之海                    1-12 · 2018 夏 · 悠哈璃羽&西農YUI    https://anime1.me/?cat=388

    + 3 result(s)
    ```

    把結果中的網址接在 `anicat` 後即可下載整季。

- 單集下載：

    ```bash
    anicat "https://anime1.me/12345"
    ```

- 分類 / 季度下載：

    ```bash
    anicat "https://anime1.me/category/your-category-slug"
    anicat "https://anime1.me/?cat=1935"
    anicat "https://anime1.pw/your-category-slug"
    ```

- 多 URL 批次下載：

    ```bash
    anicat "https://anime1.me/12345" "https://anime1.me/67890"
    anicat "https://anime1.me/12345","https://anime1.me/67890"
    ```

### 常用參數

- anicat --help

    | 參數 | 說明 | 預設值 |
    |---|---|---|
    | `-o`, `--output DIR` | 指定輸出資料夾 | `./Anime1_Download` |
    | `-c`, `--concurrency N` | 併發下載數 | `3` |
    | `--timeout SECONDS` | HTTP 讀取逾時秒數 | `30` |
    | `--connect-timeout SECONDS` | HTTP 連線逾時秒數 | `10` |
    | `--retries N` | HTTP 與串流中斷重試次數 | `3` |
    | `--chunk-size BYTES` | 下載分塊大小 | `524288` |
    | `--overwrite` | 覆寫已完成的同名檔案 | `False` |
    | `--no-resume` | 刪除既有暫存檔，重新下載 | `False` |
    | `--no-progress` | 關閉進度列 | `False` |
    | `-v`, `--verbose` | 顯示診斷 log；`-vv` 顯示 HTTP retry/debug 細節 | `False` |
    | `-q`, `--quiet` | 只保留錯誤等級 log，下載摘要仍會輸出 | `False` |
    | `-V`, `--version` | 顯示版本後離開 | - |

- anicat search --help

    | 參數 | 說明 | 預設值 |
    |---|---|---|
    | `KEYWORD` | 比對動畫標題的子字串，不分大小寫 | - |
    | `-v`, `--verbose` | 顯示診斷 log | `False` |
    | `-q`, `--quiet` | 只保留錯誤等級 log | `False` |

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
