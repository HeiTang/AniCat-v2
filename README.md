<p align="center">
  <img src="website/assets/anicat-logo-readme.svg" alt="AniCat 主 Logo：橘白像素貓" width="192" height="192">
</p>

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
    | `anime1.cc` | 三段數字單集網址 | 數字 ID 全集網址 | 開發版 HLS，需 FFmpeg |
    | `anime1.in` | 含集數的 slug 網址 | 作品 slug 全集網址 | 開發版 HLS，需 FFmpeg |


## 安裝方式

### A. GitHub Release 安裝（推薦）

```bash
python3 -m pip install "git+https://github.com/HeiTang/AniCat-v2.git@v0.3.0"
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
    # 使用 uv
    uv sync --locked
    uv run anicat --help

    # 使用一般 Python 環境
    python3 -m pip install .
    anicat --help
    ```

### C. 桌面介面（開發版）

完成本機安裝後可執行：

```bash
anicat-gui
```

視窗提供網址佇列、儲存資料夾選擇與右側逐集狀態卡，並共用 CLI 的下載服務。
狀態依實際工作更新：等待中、解析來源、下載中、檢查分段、合併影片、全片驗證，
通過驗證後才顯示完成；已存在或失敗也會明確標示，不用計時猜測百分比。
它需要 Python Tk 支援；若系統沒有 Tk，請改用 CLI。支援來源與 CLI 相同，
開發版支援 `anime1.me`、`anime1.pw`、`anime1.cc` 與 `anime1.in`。

一般安裝會透過 `imageio-ffmpeg` 一起安裝影片處理工具 FFmpeg，不需要另外設定 PATH。
桌面介面會在開始下載前檢查工具是否能正常執行；系統已有可用的 FFmpeg 時優先沿用，
否則使用套件內附的版本。檢查失敗會先顯示安裝說明，不會啟動下載。
可隨時點「工具安裝說明」，依原本安裝方式選擇修復指令：

```bash
# uv tool 安裝：不需要專案資料夾
uv tool upgrade anicat --reinstall-package imageio-ffmpeg

# pip 安裝：在原本安裝 AniCat 的 Python 環境執行
python -m pip install --force-reinstall "imageio-ffmpeg>=0.8.1,<0.9"
```

```bash
# 在專案資料夾執行
uv sync --locked --reinstall-package imageio-ffmpeg
```

常見 macOS、Windows、Linux 平台有內附執行檔的 wheel；未提供對應 wheel 的平台
仍需自行安裝 FFmpeg。工具來自 [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)，
其套件與內附 FFmpeg 的授權資訊由上游提供。

## HLS 來源（開發版）

`anime1.cc` 與 `anime1.in` 使用 HLS，需要可執行的 [FFmpeg](https://ffmpeg.org/download.html)。
程式會先使用 PATH 中通過 `ffmpeg -version` 檢查的系統版本；找不到時自動改用
`imageio-ffmpeg` 內附版本，不需要另外設定 PATH。若兩者都無法執行，請在專案資料夾修復依賴：

```bash
uv sync --locked --reinstall-package imageio-ffmpeg
```

未提供對應平台 wheel 時，才需要自行安裝 FFmpeg（macOS 使用 Homebrew 可執行
`brew install ffmpeg`）。
HLS 下載不使用 direct MP4 的 `.part` 續傳；影像直接複製，音訊轉成 AAC 並同步時間戳。
只有 FFmpeg 成功產生非空 MP4 後才會替換目的檔。
下載前會固定一次已結束的 media playlist snapshot，並把每個 segment 完整抓到暫存目錄；
每段都核對 HTTP `Content-Length`、非空、188-byte TS framing、TEI 與每個 PID 的內部 continuity
counter。中段 404、短讀取、掉 packet 或未通過結構檢查時，整集拒絕且不碰既有成品。
只有 playlist 明示 `#EXT-X-DISCONTINUITY` 的邊界才允許 CC/時間軸重設；FFmpeg 使用保留原始
段證據的 header-only CC 正規化副本，不會用 warning whitelist 掩蓋 Packet corrupt。
完成後仍以 FFmpeg progress duration 與 `#EXTINF` 總長比對，並做全片解碼驗證。
AES-128（identity key format）支援 CBC/PKCS7、key rotation、明示 IV 與 media-sequence 隱式 IV；
每個 key 都要求 HTTP 完整 16 bytes，僅留在記憶體，不會寫入 log 或暫存檔。live playlist、SAMPLE-AES、fMP4、
byte-range 與 `#EXT-X-GAP` 仍明確拒絕，不會當成成功。

```bash
anicat https://anime1.cc/681899930-01-0142
anicat https://anime1.in/2023-xian-ni-10142000
```

若 `anime1.in` 預設來源無法連線，可貼上該作品網址加上 `#other`，與原站一樣選用分流 2。
只會使用原頁列出的同站播放器；沒有該分流時會回報錯誤，不會偷偷切回預設來源。

HLS playlist 驗證或單次工作階段失敗時，`--retries` 會從頭重試有限次數；
完成片長、診斷與全片解碼驗證前不會替換既有檔案。工作階段外重新執行仍會從頭下載，HLS 不支援 `.part` 續傳。
`--timeout` 是來源連線/讀取閒置逾時，不是整集下載的總時限，避免正常長集數被誤殺。

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
     Title                        Episodes · Season                   URL
     ─────────────────────────────────────────────────────────────────────────────────────
     GRAND BLUE 碧藍之海 第三季   連載中(07) · 2026 夏                https://anime1.me/?cat=1935
     GRAND BLUE 碧藍之海 第二季   1-12 · 2025 夏 · 悠哈璃羽           https://anime1.me/?cat=1708
     碧藍之海                     1-12 · 2018 夏 · 悠哈璃羽&西農YUI   https://anime1.me/?cat=388

    + 3 result(s)
    ```

    URL 欄位固定寬度，終端機再窄也不會被裁切，可以直接複製貼上。

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

省略 `KEYWORD` 時會互動提問（與 `anicat` 省略 URL 時的行為一致）：

```text
$ anicat search
? Search keyword：碧藍之海
```

在管線或 CI 等非互動環境下不會卡住，直接以 exit code `2` 回報。

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

<p align="center">
  <img src="website/assets/anicat-mascot-readme.svg" alt="AniCat 吉祥物：繫橘色領巾的黑白像素貓" width="128" height="128">
</p>

## License

本專案採用 GPL-3.0 License，詳見 [LICENSE](LICENSE) 文件。

## 介紹網站（本機預覽）

`website/` 提供靜態介紹與操作說明，不需 Node 或前端建置工具。

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory website
```

開啟 `http://127.0.0.1:8765`。網站安裝範例指向已發布的 v0.3.0；
HLS 與 GUI 屬於另外的開發分支，不包含在網站 PR 或 v0.3.0 內。
網站尚未公開部署，PyPI 預計使用套件名稱 `anicat`，正式發布前仍使用 GitHub 安裝。
