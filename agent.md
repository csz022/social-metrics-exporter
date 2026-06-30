# Agent Rules

This project handles social-media scraping, sheet ingestion, and CSV export.
Treat it as a sensitive local tool, not a public data product.

## Security

- Never commit `.env`, `.auth/`, `.cache/`, `output/`, or any temporary debug directories.
- Never expose credentials, session state, cookies, browser profiles, or login screenshots in commits, logs, or sample data.
- Never include real Google Sheet URLs, real post URLs, real account handles, or production output rows in public examples.
- Keep sample input and sample output synthetic or redacted.
- If a change adds a new local cache, auth artifact, or debug artifact, add it to `.gitignore` immediately.
- If a file contains secrets or session material, do not print its full contents in chat or logs.

## Repo Hygiene

- Keep the repository suitable for GitHub by excluding machine-specific and user-specific state.
- Prefer `README.md` and `agent.md` to document usage and safety constraints.
- Keep `env.example` free of secrets and real URLs.

## Scraping Behavior

- Preserve the default behavior of using public, visible, or explicitly authenticated data only.
- Do not add bypasses for login walls, private content, or hidden metrics.
- Use DOM-first extraction with text/meta/network fallbacks only where the page already exposes the data.
- Keep profile search and follower enrichment optional and controlled by flags or environment variables.

## Code Changes

- When adding any new output file, debug file, cache file, or auth artifact, update `.gitignore` in the same change.
- When adding a feature that increases surface area, add a short note to `README.md` explaining the expected runtime and any privacy implications.
- Prefer narrow, platform-specific parsers over broad heuristics when it improves accuracy without expanding data access.

## Testing Principles

- 抗退化性：測試要鎖住使用者可觀察的核心行為與資料輸出，避免既有功能在改動後靜默壞掉。
- 抗重構性：測試優先驗證公開函式、資料管線與輸出契約，不依賴脆弱的內部實作細節。
- 快速回饋：優先使用離線、合成資料與小型 fixture；需要外部網站、登入 session 或長時間爬取的檢查要隔離成手動或整合測試。
- 可維護性：測試資料保持精簡、語意清楚，避免真實帳號/網址/憑證；新增功能時同步補上對應的成功、失敗與邊界案例。
