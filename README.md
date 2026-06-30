# Social Metrics Exporter

Local social post metrics exporter for Threads, Instagram, and Facebook. The tool accepts URL lists, Google Sheets, or local xlsx files, opens visible public or authenticated pages with Playwright, and writes a normalized CSV report.

The exporter only writes scraped fields. It does not calculate campaign value, weighted engagement, unit price, or other business formulas.

## Requirements

- Python 3.11+
- `uv` recommended
- Playwright Chromium

## Installation

Recommended setup:

```bash
uv sync
uv run playwright install chromium
```

Alternative pip setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

GUI:

```bash
uv run python gui_app.py --port 5001
```

CLI:

```bash
uv run python src/main.py --input input/urls.txt
```

Platform launchers:

- macOS: `run_gui_mac.command`
- Windows: `run_gui.bat`

The launchers prepare dependencies, install Chromium, and open `http://127.0.0.1:5001`.

## Input

### URL list

Create an input file from the example:

```bash
cp input/urls.example.txt input/urls.txt
```

Use one URL per line. Blank lines and lines starting with `#` are ignored.

```text
https://www.threads.com/@example/post/POST_ID
```

### Google Sheet / xlsx

Required columns:

- `文章標題`
- `來源`

URL detection:

- hyperlink on `文章標題`
- `網址` column
- supported social URL in another cell, found through fallback scanning

Supported platform values include `THREADS`, `IG`, and `FACEBOOK`. Common Chinese labels such as `串文` and `臉書` are normalized automatically.

CLI examples:

```bash
uv run python src/main.py --sheet "https://docs.google.com/spreadsheets/d/.../edit?gid=..." --sheet-platforms ALL
uv run python src/main.py --sheet local_file.xlsx --sheet-platforms THREADS
```

## Output

Report CSV:

```text
output/social_metrics.csv
```

Columns:

```text
網址,fb標題,討論串總則數,點閱數/按讚數,瀏覽數,分享,粉絲團追蹤人數,觸及
```

Column mapping:

| Column | Source |
| --- | --- |
| 網址 | input URL |
| fb標題 | post text for row matching |
| 討論串總則數 | Threads replies; Facebook / Instagram comments |
| 點閱數/按讚數 | Threads likes; Facebook reactions/likes; Instagram likes |
| 瀏覽數 | captured when visible on the page, otherwise `N/A` |
| 分享 | Threads reposts + quotes; Facebook shares; Instagram is `0` |
| 粉絲團追蹤人數 | IG / Facebook profile followers; Threads is `N/A` |
| 觸及 | `N/A` unless a visible reach value is available |

Failed URL CSV:

```text
output/failed_urls.csv
```

Columns:

```text
post_url,status,reason
```

Common statuses:

| status | Meaning |
| --- | --- |
| `post_not_loaded` | post URL redirected to profile, post is unavailable, permission is missing, or public mode cannot load it |
| `login_required` | page requires login |
| `not_found` | page does not exist |
| `timeout` | page load timed out |
| `parse_failed` | page loaded but did not contain enough parseable signals |

## Login

When public mode cannot load a page, create a local browser session state:

```bash
uv run python src/main.py --login
```

After the browser opens, log in to the required platform and return to the terminal to press Enter. In the GUI, enable `Use saved login/session`.

Session files are stored under `.auth/`.

## Config

`.env` contains local runtime settings. `.env.example` documents the supported keys.

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

## Testing

Run the test suite:

```bash
uv run python -m unittest discover -v
```

## Repository Hygiene

The repository should not include local runtime state or generated output:

```text
.env
.auth/
.cache/
.venv/
input/urls.txt
output/
tmp_*/
```
