# Contributing

AniCat-v2 是一個 CLI 下載工具，不是一次性的腳本集合。任何改動都應該維持清楚的模組邊界、可測試性與可重現的 release 流程。

## 專案結構

```txt
.
├── src/anicat/
│   ├── cli.py                  # CLI 參數、輸出、exit code
│   ├── service.py              # URL 展開、併發下載協調、錯誤隔離
│   ├── client.py               # HTTP session、timeout、retry、page/API/video request
│   ├── extractor.py            # provider routing、HTML/API parser
│   ├── downloader.py           # Range 續傳、Content-Range 驗證、原子寫入
│   ├── progress.py             # Rich 進度列
│   ├── options.py              # options 與 validation
│   ├── models.py               # domain models / progress events
│   ├── urls.py                 # URL classification / source detection / dedupe
│   ├── constants.py            # package metadata / default values
│   ├── errors.py               # domain-specific exceptions
│   ├── logging_config.py       # logging level 與 handler 設定
│   └── py.typed                # PEP 561 typed package marker
├── tests/                      # 單元測試與 opt-in integration smoke test
├── .github/workflows/ci.yml    # CI quality gate
├── .pre-commit-config.yaml     # Pre-commit Hooks
├── pyproject.toml
├── poetry.lock
├── CHANGELOG.md
├── README.md
└── CONTRIBUTING.md
```

## 開發環境

- 需求：

  - Python `>=3.12,<4.0`
  - Poetry

- 安裝：

   ```bash
   poetry install --with dev
   poetry run pre-commit install
   ```

- 常用檢查：

   ```bash
   poetry run ruff format --check .
   poetry run ruff check .
   poetry run pyright
   poetry run python -m unittest
   poetry run python -m compileall src/anicat tests
   poetry check
   ```

- 完整 pre-commit：

   ```bash
   poetry run pre-commit run --all-files
   ```

## 設計原則

- **CLI 保持薄**：`cli.py` 只處理參數、輸出與 exit code，不放下載邏輯。
- **Service 負責協調**：`service.py` 負責 URL 展開、併發、client lifecycle 與錯誤隔離。
- **Extractor 只解析**：`extractor.py` 負責把頁面/API response 轉成 `Episode`，不做檔案寫入。
- **Downloader 不知道 Anime1**：`downloader.py` 只處理 stream、Range、`.part`、驗證與檔案輸出。
- **Client 只做 HTTP**：`client.py` 不放 parser 邏輯，也不決定下載檔名。
- **依賴反轉**：跨模組互動優先用 `Protocol` 或清楚的 domain model，不讓測試依賴真實網路。
- **小 Patch 優先**：不要把 bug fix、重構、文件改寫、release 準備混在同一個 commit。

## 新增或修改 Provider

> 不要把新網域當成舊網域 alias 硬塞。先判斷來源的下載模型：

| 模型 | 處理方式 |
|---|---|
| direct MP4 | 可沿用目前 downloader |
| API 回傳 MP4 | 新增 provider-specific extractor |
| HLS / m3u8 | 需要新的 HLS backend，不要塞進 MP4 downloader |

新增 Provider 時至少要補：

- URL classification tests
- parser unit tests
- service / downloader 邊界測試
- README 支援來源表格
- CHANGELOG

真實網站測試必須是 opt-in smoke test，不進預設 CI。

## 測試策略

- 預設測試不能依賴外部網站。

   ```bash
   poetry run python -m unittest
   ```

- 真實 Anime1 smoke test 只在手動指定 env var 時執行：

   ```bash
   ANICAT_RUN_INTEGRATION=1 poetry run python -m unittest tests.test_integration_smoke
   ANICAT_RUN_PW_INTEGRATION=1 poetry run python -m unittest tests.test_integration_smoke
   ```

- 測試要求：

   - parser 測試用手寫 HTML fixture 覆蓋 selector 與 edge cases。
   - downloader 測試必須覆蓋 resume、Range、`Content-Range`、server 忽略 Range、incomplete stream。
   - CLI 測試要覆蓋 exit code 與非 TTY 行為。
   - 新增 options 時要補 validation tests。
   - 若要加入真實 HTML fixture，只保留 parser 必要 DOM 片段，不提交整頁 response、cookie、短期 token 或下載影片。

## 程式風格

- 使用型別註記，並讓 `pyright` standard 通過。
- 使用 `ruff format`，不要手動套不同格式風格。
- 避免過度抽象；等第三個 provider 或重複 pattern 明確出現再抽共用基底。
- 不用一個字母變數名，除非是非常局部且語意明確的數學/迭代場景。
- 不新增 license/copyright header。

## 文件更新規則

以下改動需要同步更新文件：

| 改動 | 需要更新 |
|---|---|
| 新 CLI 參數 | `README.md` 常用參數 / 常用指令 |
| 新支援來源 | `README.md` 支援來源 |
| 使用者可見 bug fix | PR 說明的 release note 摘要 |
| 新 release | 維護者於 release 前更新 `pyproject.toml`、`CHANGELOG.md`、`README.md` |

> 一般 PR 不需要直接修改 `CHANGELOG.md`。`CHANGELOG.md` 是 release artifact，由維護者在 RC / release 前根據已合併 PR 統一整理。

## Commit 與 PR

使用 Conventional Commits：

```text
feat: add anime1.pw direct source support
fix: reject invalid anime1.pw system paths
test: cover direct MP4 source selection
docs: refresh README installation guide
chore: prepare release candidate
refactor: split provider-specific extractors
```

PR 說明至少包含：

- 改了什麼
- 為什麼改
- 怎麼驗證
- 是否影響 CLI / README
- 是否需要 migration
- 使用者可見改動的 release note 摘要

## Release 流程

RC / release 前確認：

1. 更新 `pyproject.toml` 版本。
2. 更新 `CHANGELOG.md`。
3. 更新 `README.md` 的 release tag 安裝指令。
4. 跑完整品質門檻：

   ```bash
   poetry run ruff format --check .
   poetry run ruff check .
   poetry run pyright
   poetry run python -m unittest
   poetry run python -m compileall src/anicat tests
   poetry check
   poetry build
   ```

5. 需要時跑 opt-in smoke test。
6. Commit release prep。
7. 建立 tag，例如：

   ```bash
   git tag v0.2.0-rc.1
   git push origin main --tags
   ```

8. GitHub Release 勾選 pre-release（如果是 RC）。

版本規則：

- Git tag 使用 SemVer / RC 樣式：`v0.2.0-rc.1`
- Python package version 使用 PEP 440：`0.2.0rc1`

## 不要提交

- 下載下來的影片。
- `.part` 暫存檔。
- cookie、token、API key。
- 含短期簽名的完整真實 response。
- build/cache 產物，例如 `dist/`、`.ruff_cache/`、`__pycache__/`。

## 安全邊界

- 不繞過站方驗證或存取控制。
- 不提交可重放的私密 token / cookie。
- 不把 HLS playlist token 寫進 repo。
- 對外部網站的測試必須 opt-in，避免 CI 依賴第三方服務。
