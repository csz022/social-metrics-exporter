# Social Metrics Exporter

本機執行的社群貼文成效匯出工具。支援 URL list、Google Sheet、xlsx 上傳，使用 Playwright 開啟使用者可見的公開或已登入頁面，輸出可回填的 CSV。

這份 README 給安裝、部署、維護使用。

## Requirements

- Python 3.11+
- `uv`，推薦
- Playwright Chromium

## Install

推薦：

```bash
uv sync
uv run playwright install chromium
```

不用 `uv` 時可改用 pip：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

GUI：

```bash
uv run python gui_app.py --port 5001
```

CLI：

```bash
uv run python src/main.py --input input/urls.txt
```

啟動腳本：

- macOS：`run_gui_mac.command`
- Windows：`run_gui.bat`

這兩個腳本會建立 `.env`、同步依賴、安裝 Chromium，然後開啟 `http://127.0.0.1:5001`。

## Input

### URL list

`input/urls.txt` 每行一個 URL，空行和 `#` 註解會略過。這個檔案是本機資料，已被 `.gitignore` 排除；初始化時可以從範本複製：

```bash
cp input/urls.example.txt input/urls.txt
```

```text
https://www.threads.com/@example/post/POST_ID
```

### Google Sheet / xlsx

Sheet 至少需要：

- `文章標題`
- `來源`

URL 可以放在：

- `文章標題` 的 hyperlink
- `網址` 欄
- 其他欄位中的社群 URL，fallback scan 會嘗試尋找

支援平台值包含 `THREADS`、`IG`、`FACEBOOK`，也會辨識常見中文名稱如 `串文`、`臉書`。

CLI 範例：

```bash
uv run python src/main.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=..." --sheet-platforms ALL
uv run python src/main.py --sheet local_file.xlsx --sheet-platforms THREADS
uv run python src/main.py --sheet local_file.xlsx --dry-run
```

## Output

主報表：

```text
output/social_metrics.csv
```

欄位：

```text
網址,fb標題,討論串總則數,點閱數/按讚數,瀏覽數,分享,粉絲團追蹤人數,觸及
```

欄位來源：

| 欄位 | 來源 |
| --- | --- |
| 網址 | 輸入 URL |
| fb標題 | 貼文文字，方便回填核對 |
| 討論串總則數 | 回覆數 |
| 點閱數/按讚數 | 按讚數 |
| 瀏覽數 | 頁面有顯示才抓，否則 `N/A` |
| 分享 | 轉發數 + 引用數 |
| 粉絲團追蹤人數 | IG / Facebook profile followers；Threads 預設不補，填 `N/A` |
| 觸及 | 目前通常抓不到，填 `N/A` |

失敗清單：

```text
output/failed_urls.csv
```

欄位：

```text
post_url,status,reason
```

常見 status：

| status | 意義 |
| --- | --- |
| `post_not_loaded` | 貼文 URL 被導回 profile、貼文不存在、私人權限，或公開模式看不到 |
| `login_required` | 頁面要求登入 |
| `not_found` | 頁面不存在 |
| `timeout` | 載入逾時 |
| `parse_failed` | 頁面載入但解析訊號不足 |

## Login

公開模式抓不到時，可先建立本機登入狀態：

```bash
uv run python src/main.py --login
```

瀏覽器打開後登入 Threads / Instagram，回到 terminal 按 Enter。之後 GUI 勾選 `Use saved login/session`。

登入資料存在 `.auth/`，不要提交或傳給別人。

## Config

`.env` 是本機設定，`.env.example` 是範本。常用設定：

```env
THREADS_INPUT=input/urls.txt
THREADS_OUTPUT=output/social_metrics.csv
THREADS_FAILED_OUTPUT=output/failed_urls.csv
THREADS_SHEET=
THREADS_SHEET_PLATFORMS=ALL
THREADS_CONCURRENCY=4
THREADS_DELAY=0
THREADS_RETRIES=1
THREADS_DEBUG=false
THREADS_FETCH_FOLLOWERS=true  # only enriches IG/Facebook followers by default
THREADS_PROFILE_SEARCH=false
THREADS_NETWORK_CAPTURE=true
THREADS_USE_LOGIN=false
THREADS_AUTH_STATE=.auth/threads_state.json
THREADS_PROFILE_DIR=.auth/threads_profile
```

## Test

快速測試：

```bash
uv run python -m unittest discover -v
```

檢查輸入但不爬資料：

```bash
uv run python src/main.py --dry-run
uv run python src/main.py --sheet local_file.xlsx --dry-run
```

## Repo Hygiene

不要提交：

```text
.env
.auth/
.cache/
.venv/
input/urls.txt
output/
tmp_*/
```

這些已在 `.gitignore` 中排除。

## Public Repo Notes

這個 repo 可以公開的是程式碼、文件、測試和範例資料。不要公開 `.env`、登入 session、真實客戶/專案輸入 URL、CSV 輸出、debug HTML/network dump。

使用時只抓自己有權限查看的公開或已授權頁面，不要繞過登入、隱私設定、平台限制或大量高頻請求。平台條款可能限制自動化抓取；公開程式碼本身和實際使用行為要分開評估。
