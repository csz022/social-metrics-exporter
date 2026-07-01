from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import argparse
import webbrowser
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_file, url_for
from dotenv import load_dotenv

from src.sheet_reader import inspect_sheet_urls


load_dotenv()

ROOT = Path(__file__).resolve().parent
JOB_ROOT = ROOT / ".cache" / "gui_jobs"

DEFAULT_OUTPUT = os.getenv("THREADS_OUTPUT", "output/social_metrics.csv")
DEFAULT_FAILED_OUTPUT = os.getenv("THREADS_FAILED_OUTPUT", "output/failed_urls.csv")
DEFAULT_OUTPUT_DIR = os.getenv("THREADS_OUTPUT_DIR", "output")
DEFAULT_OUTPUT_NAME = Path(DEFAULT_OUTPUT).name
DEFAULT_FAILED_OUTPUT_NAME = Path(DEFAULT_FAILED_OUTPUT).name
DEFAULT_SHEET = os.getenv("THREADS_SHEET", "")
DEFAULT_INPUT = os.getenv("THREADS_INPUT", "input/urls.txt")
DEFAULT_PLATFORM = os.getenv("THREADS_SHEET_PLATFORMS", "ALL")
DEFAULT_CONCURRENCY = os.getenv("THREADS_CONCURRENCY", "3")
DEFAULT_DELAY = os.getenv("THREADS_DELAY", "1")
DEFAULT_RETRIES = os.getenv("THREADS_RETRIES", "2")
DEFAULT_PROFILE_SEARCH = os.getenv("THREADS_PROFILE_SEARCH", "false").lower() in {"1", "true", "yes", "y", "on"}
DEFAULT_FETCH_FOLLOWERS = os.getenv("THREADS_FETCH_FOLLOWERS", "true").lower() in {"1", "true", "yes", "y", "on"}
DEFAULT_NETWORK_CAPTURE = os.getenv("THREADS_NETWORK_CAPTURE", "true").lower() in {"1", "true", "yes", "y", "on"}
DEFAULT_DEBUG = os.getenv("THREADS_DEBUG", "false").lower() in {"1", "true", "yes", "y", "on"}
DEFAULT_USE_LOGIN = os.getenv("THREADS_USE_LOGIN", "false").lower() in {"1", "true", "yes", "y", "on"}
DEFAULT_PROFILE_DIR = os.getenv("THREADS_PROFILE_DIR") or ".auth/threads_profile"
DEFAULT_AUTH_STATE = os.getenv("THREADS_AUTH_STATE") or ".auth/threads_state.json"


app = Flask(__name__)


@dataclass
class Job:
    job_id: str
    status: str = "queued"
    message: str = ""
    command: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    returncode: int | None = None
    total: int | None = None
    completed: int = 0
    current_step: str = ""
    current_item: str = ""
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=400))
    output_path: str = ""
    failed_output_path: str = ""
    workdir: str = ""
    error: str = ""
    form_defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or time.time()
        start = self.started_at or end
        return max(0.0, end - start)

    @property
    def progress_pct(self) -> int:
        if not self.total:
            return 0
        return min(100, int((self.completed / self.total) * 100))

    @property
    def output_exists(self) -> bool:
        return bool(self.output_path) and Path(self.output_path).exists()

    @property
    def failed_output_exists(self) -> bool:
        return bool(self.failed_output_path) and Path(self.failed_output_path).exists()


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()


PAGE_TEMPLATE = """
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Social Metrics</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f8;
      --surface: #ffffff;
      --line: #d6dbe1;
      --text: #111827;
      --muted: #5b6472;
      --accent: #1f6feb;
      --accent-weak: #e8f0fe;
      --danger: #c33a32;
      --ok: #157347;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", Arial, sans-serif;
      line-height: 1.5;
    }
    header {
      padding: 20px 24px 10px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 {
      margin: 0 0 4px;
      font-size: 20px;
      font-weight: 700;
    }
    .subtle { color: var(--muted); font-size: 13px; }
    main { padding: 20px 24px 28px; max-width: 1440px; margin: 0 auto; }
    .grid {
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 16px;
    }
    .form-row { margin-bottom: 12px; }
    label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 6px;
    }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    textarea { min-height: 154px; resize: vertical; }
    input:focus, textarea:focus, select:focus {
      outline: 2px solid var(--accent-weak);
      border-color: var(--accent);
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .stack { display: grid; gap: 8px; }
    .checks {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 4px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fafbfc;
      min-height: 44px;
    }
    .actions {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }
    button, .btn {
      appearance: none;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 10px 14px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
    }
    button.primary, .btn.primary {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      width: 100%;
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .statusbar {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      background: #fff;
    }
    .metric .label { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .metric .value { font-size: 18px; font-weight: 700; }
    .progress-wrap {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #edf1f5;
      height: 12px;
      overflow: hidden;
    }
    .progress-bar {
      height: 100%;
      background: linear-gradient(90deg, var(--accent), #4c8bf5);
      width: 0%;
      transition: width 180ms ease-out;
    }
    .progress-bar.running {
      background-size: 24px 24px;
      background-image: linear-gradient(
        135deg,
        rgba(255, 255, 255, 0.22) 25%,
        transparent 25%,
        transparent 50%,
        rgba(255, 255, 255, 0.22) 50%,
        rgba(255, 255, 255, 0.22) 75%,
        transparent 75%,
        transparent
      ), linear-gradient(90deg, var(--accent), #4c8bf5);
      animation: stripeMove 1s linear infinite;
    }
    .run-banner {
      display: none;
      align-items: center;
      gap: 10px;
      margin: 0 0 12px;
      padding: 10px 12px;
      border: 1px solid rgba(31, 111, 235, 0.18);
      border-radius: 6px;
      background: #f5f9ff;
      color: #123a7b;
      font-size: 13px;
      font-weight: 600;
    }
    .run-banner .meta {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .run-banner .meta .title {
      font-size: 13px;
      font-weight: 700;
    }
    .run-banner .meta .detail {
      font-size: 12px;
      font-weight: 500;
      color: #42609a;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 100%;
    }
    .run-banner.visible { display: flex; }
    .spinner {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      border: 2px solid rgba(31, 111, 235, 0.2);
      border-top-color: var(--accent);
      animation: spin 0.8s linear infinite;
      flex: 0 0 auto;
    }
    .status-live {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .status-live .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--ok);
      box-shadow: 0 0 0 0 rgba(21, 115, 71, 0.35);
      animation: pulse 1.4s infinite;
    }
    .activity {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .activity-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .activity-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 10px 12px;
      min-width: 0;
    }
    .activity-item .k {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }
    .activity-item .v {
      font-size: 13px;
      font-weight: 600;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .note {
      margin-top: 10px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfbfc;
      color: var(--muted);
      font-size: 13px;
    }
    .preview {
      margin: 0 0 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 12px;
      display: none;
      gap: 10px;
    }
    .preview.visible { display: grid; }
    .preview-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .preview-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 8px 10px;
      min-width: 0;
    }
    .preview-item .k {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 2px;
    }
    .preview-item .v {
      font-size: 13px;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .preview-samples {
      display: grid;
      gap: 6px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .preview-samples li {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 8px 10px;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .error {
      color: var(--danger);
      border-color: rgba(195, 58, 50, 0.25);
      background: #fff7f7;
    }
    .ok {
      color: var(--ok);
    }
    .downloads {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .downloads .btn {
      width: 100%;
      min-height: 46px;
    }
    .downloads .btn.primary {
      box-shadow: 0 8px 18px rgba(31, 98, 255, 0.16);
    }
    .downloads .btn.secondary {
      background: #f8fafc;
    }
    .small { font-size: 12px; color: var(--muted); }
    .inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .mono { font-family: SFMono-Regular, Consolas, Menlo, monospace; }
    .field-help {
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    details.advanced {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      padding: 10px 12px;
    }
    details.advanced summary {
      cursor: pointer;
      font-size: 13px;
      font-weight: 700;
      color: var(--muted);
    }
    details.advanced .advanced-body {
      margin-top: 12px;
    }
    .advanced-section-title {
      margin: 14px 0 8px;
      font-size: 13px;
      font-weight: 700;
      color: var(--text);
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    @keyframes pulse {
      0% { transform: scale(0.95); opacity: 0.55; box-shadow: 0 0 0 0 rgba(21, 115, 71, 0.32); }
      70% { transform: scale(1); opacity: 1; box-shadow: 0 0 0 8px rgba(21, 115, 71, 0); }
      100% { transform: scale(0.95); opacity: 0.55; box-shadow: 0 0 0 0 rgba(21, 115, 71, 0); }
    }
    @keyframes stripeMove {
      from { background-position: 0 0, 0 0; }
      to { background-position: 24px 0, 0 0; }
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: 1fr; }
      .statusbar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 640px) {
      main, header { padding-left: 14px; padding-right: 14px; }
      .row, .checks, .statusbar { grid-template-columns: 1fr; }
      .panel { padding: 14px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Social Metrics</h1>
    <div class="subtle">Local dashboard for sheet import, batch scraping, and CSV export.</div>
  </header>

  <main>
    <div class="grid">
      <section class="panel">
        <h2>Input</h2>
        <form id="batchForm" action="{{ url_for('start_job') }}" method="post" enctype="multipart/form-data">
          <div class="form-row">
            <label for="source_mode">Source</label>
            <select name="source_mode" id="source_mode">
              <option value="sheet" {% if form_defaults.source_mode == 'sheet' %}selected{% endif %}>Google Sheet</option>
              <option value="urls" {% if form_defaults.source_mode == 'urls' %}selected{% endif %}>URL list</option>
              <option value="xlsx" {% if form_defaults.source_mode == 'xlsx' %}selected{% endif %}>XLSX upload</option>
            </select>
          </div>

          <div class="form-row" data-source="sheet">
            <label for="sheet">Google Sheet URL</label>
            <input type="text" name="sheet" id="sheet" value="{{ form_defaults.sheet }}" placeholder="https://docs.google.com/spreadsheets/d/...">
          </div>

          <div class="form-row" data-source="xlsx">
            <label for="xlsx">Upload XLSX</label>
            <input type="file" name="xlsx" id="xlsx" accept=".xlsx">
          </div>

          <div class="form-row" data-source="urls">
            <label for="urls">URL list</label>
            <textarea name="urls" id="urls" placeholder="One URL per line.">{{ form_defaults.urls }}</textarea>
          </div>

          <div class="form-row" data-source="urls">
            <label for="urls_file">Or upload TXT</label>
            <input type="file" name="urls_file" id="urls_file" accept=".txt">
          </div>

          <div class="form-row">
            <label for="sheet_platforms">Platforms</label>
            <div class="inline">
              <select name="sheet_platforms" id="sheet_platforms">
                {% for value in ['ALL','THREADS','IG','FACEBOOK'] %}
                <option value="{{ value }}" {% if form_defaults.sheet_platforms == value %}selected{% endif %}>{{ value }}</option>
                {% endfor %}
              </select>
            </div>
          </div>

          <div class="preview" id="previewPanel" aria-live="polite">
            <div class="preview-grid">
              <div class="preview-item">
                <div class="k">Input status</div>
                <div class="v" id="previewStatus">Not checked</div>
              </div>
              <div class="preview-item">
                <div class="k">URLs</div>
                <div class="v" id="previewTotal">0</div>
              </div>
              <div class="preview-item">
                <div class="k">Platforms</div>
                <div class="v" id="previewPlatforms">-</div>
              </div>
              <div class="preview-item">
                <div class="k">Detected columns</div>
                <div class="v" id="previewColumns">-</div>
              </div>
            </div>
            <ul class="preview-samples" id="previewSamples"></ul>
          </div>

          <input type="hidden" name="output_dir" id="output_dir" value="{{ form_defaults.output_dir }}">
          <div class="form-row">
            <label for="output_name">Output filename</label>
            <input type="text" name="output_name" id="output_name" value="{{ form_defaults.output_name }}" placeholder="social_metrics.csv">
            <div class="field-help">CSV will be saved in the output folder.</div>
          </div>

          <details class="advanced">
            <summary>Advanced settings</summary>
            <div class="advanced-body">
              <div class="form-row">
                <label for="failed_output_name">Failed filename</label>
                <input type="text" name="failed_output_name" id="failed_output_name" value="{{ form_defaults.failed_output_name }}" placeholder="failed_urls.csv">
              </div>
              <div class="advanced-section-title">Batch settings</div>
              <div class="row">
                <div class="form-row">
                  <label for="concurrency">Concurrency</label>
                  <input type="number" min="1" max="10" name="concurrency" id="concurrency" value="{{ form_defaults.concurrency }}">
                </div>
                <div class="form-row">
                  <label for="delay">Delay seconds</label>
                  <input type="number" min="0" step="0.1" name="delay" id="delay" value="{{ form_defaults.delay }}">
                </div>
              </div>
              <div class="row">
                <div class="form-row">
                  <label for="retries">Retries</label>
                  <input type="number" min="0" max="5" name="retries" id="retries" value="{{ form_defaults.retries }}">
                </div>
                <div class="form-row">
                  <label for="profile_search_scrolls">Profile search scrolls</label>
                  <input type="number" min="0" max="50" name="profile_search_scrolls" id="profile_search_scrolls" value="{{ form_defaults.profile_search_scrolls }}">
                </div>
              </div>
              <div class="checks">
                {% for field, label in [
                  ('fetch_followers', 'Fetch followers'),
                  ('profile_search', 'Threads profile search'),
                  ('network_capture', 'Network capture'),
                  ('debug', 'Debug output'),
                  ('headful', 'Headful browser')
                ] %}
                <label class="check">
                  <input type="checkbox" name="{{ field }}" {% if form_defaults[field] %}checked{% endif %}>
                  <span>{{ label }}</span>
                </label>
                {% endfor %}
              </div>
              <div class="advanced-section-title">Login/session</div>
              <label class="check">
                <input type="checkbox" name="use_login" id="use_login" {% if form_defaults.use_login %}checked{% endif %}>
                <span>Use saved login/session</span>
              </label>
              <div class="form-row">
                <label for="profile_dir">Profile dir</label>
                <input type="text" name="profile_dir" id="profile_dir" value="{{ form_defaults.profile_dir }}" placeholder=".auth/threads_profile">
              </div>
              <div class="form-row">
                <label for="auth_state">Auth state</label>
                <input type="text" name="auth_state" id="auth_state" value="{{ form_defaults.auth_state }}" placeholder=".auth/threads_state.json">
              </div>
            </div>
          </details>

          <div class="actions">
            <button class="primary" id="startBtn" type="submit">Start batch</button>
            <div class="note" style="margin-top: 0;">
              First click checks the input. After the preview is ready, click Start batch again to run.
            </div>
          </div>
        </form>
      </section>

      <section class="panel">
        <h2>Run</h2>
        {% if active_job %}
          <div class="run-banner {% if active_job.status in ['queued', 'running'] %}visible{% endif %}" id="runBanner">
            <span class="spinner" aria-hidden="true"></span>
            <div class="meta">
              <div class="title" id="runBannerText">爬取中</div>
              <div class="detail" id="runBannerDetail">{{ active_job.current_item if active_job else '' }}</div>
            </div>
          </div>
          <div class="statusbar">
            <div class="metric">
              <div class="label">Status</div>
              <div class="value status-live" id="statusWrap">
                <span class="dot" id="statusDot" {% if active_job.status not in ['queued', 'running'] %}style="display:none"{% endif %}></span>
                <span id="status">{{ active_job.status }}</span>
              </div>
            </div>
            <div class="metric">
              <div class="label">Progress</div>
              <div class="value"><span id="completed">{{ active_job.completed }}</span> / <span id="total">{{ active_job.total or 0 }}</span></div>
            </div>
            <div class="metric">
              <div class="label">Elapsed</div>
              <div class="value" id="elapsed">{{ "%.1f"|format(active_job.elapsed_seconds) }}s</div>
            </div>
          </div>

          <div class="progress-wrap" aria-label="progress">
            <div class="progress-bar {% if active_job.status in ['queued', 'running'] %}running{% endif %}" id="progress" style="width: {{ active_job.progress_pct }}%;"></div>
          </div>

          <div class="downloads" id="downloads" style="display: {% if active_job.status == 'done' and (active_job.output_exists or active_job.failed_output_exists) %}grid{% else %}none{% endif %};">
            <a class="btn primary" id="downloadOutput" href="{{ url_for('download_file', job_id=active_job.job_id, kind='output') }}" {% if not active_job.output_exists %}style="display:none"{% endif %}>Download output CSV</a>
            <a class="btn secondary" id="downloadFailed" href="{{ url_for('download_file', job_id=active_job.job_id, kind='failed') }}" {% if not active_job.failed_output_exists %}style="display:none"{% endif %}>Download failed CSV</a>
          </div>

          {% if active_job.error %}
            <div class="note error">{{ active_job.error }}</div>
          {% endif %}
          <div class="activity">
            <div class="activity-grid">
              <div class="activity-item">
                <div class="k">Stage</div>
                <div class="v" id="stageText">{{ active_job.current_step if active_job else '' }}</div>
              </div>
              <div class="activity-item">
                <div class="k">Current item</div>
                <div class="v" id="itemText">{{ active_job.current_item if active_job else '' }}</div>
              </div>
            </div>
          </div>
        {% else %}
          <div class="note">No active job yet. Start a batch from the left panel.</div>
        {% endif %}
      </section>
    </div>
  </main>

  <script>
    const batchForm = document.getElementById('batchForm');
    const sourceMode = document.getElementById('source_mode');
    const sourceBlocks = document.querySelectorAll('[data-source]');
    const startBtn = document.getElementById('startBtn');
    const previewPanel = document.getElementById('previewPanel');
    const previewStatus = document.getElementById('previewStatus');
    const previewTotal = document.getElementById('previewTotal');
    const previewPlatforms = document.getElementById('previewPlatforms');
    const previewColumns = document.getElementById('previewColumns');
    const previewSamples = document.getElementById('previewSamples');
    let previewIsValid = false;

    function resetPreview() {
      previewIsValid = false;
      startBtn.disabled = false;
      startBtn.textContent = 'Start batch';
      previewPanel.classList.remove('visible', 'error');
      previewStatus.textContent = 'Not checked';
      previewTotal.textContent = '0';
      previewPlatforms.textContent = '-';
      previewColumns.textContent = '-';
      previewSamples.replaceChildren();
    }

    function renderPreview(data) {
      previewPanel.classList.add('visible');
      previewPanel.classList.toggle('error', !data.ok);
      previewStatus.textContent = data.ok ? 'Ready' : (data.error || 'Input error');
      previewTotal.textContent = data.total || 0;
      previewPlatforms.textContent = data.platforms || '-';
      previewColumns.textContent = data.columns || '-';
      previewSamples.replaceChildren();
      (data.samples || []).forEach(sample => {
        const item = document.createElement('li');
        item.textContent = sample;
        previewSamples.appendChild(item);
      });
      previewIsValid = Boolean(data.ok);
      startBtn.disabled = false;
      startBtn.textContent = previewIsValid ? 'Start batch' : 'Check input';
    }

    async function previewInput() {
      startBtn.disabled = true;
      startBtn.textContent = 'Checking input...';
      previewPanel.classList.add('visible');
      previewPanel.classList.remove('error');
      previewStatus.textContent = 'Checking input...';
      previewTotal.textContent = '0';
      previewPlatforms.textContent = '-';
      previewColumns.textContent = '-';
      previewSamples.replaceChildren();
      try {
        const res = await fetch('{{ url_for("preview_input") }}', {
          method: 'POST',
          body: new FormData(batchForm),
        });
        const data = await res.json();
        renderPreview(data);
      } catch (err) {
        renderPreview({ ok: false, error: err.message || 'Preview failed' });
      } finally {
        startBtn.disabled = false;
      }
    }

    function updateSourceVisibility() {
      const mode = sourceMode.value;
      sourceBlocks.forEach(block => {
        const visible = block.getAttribute('data-source') === mode;
        block.style.display = visible ? '' : 'none';
      });
      resetPreview();
    }
    sourceMode.addEventListener('change', updateSourceVisibility);
    ['sheet', 'xlsx', 'urls', 'urls_file', 'sheet_platforms'].forEach(id => {
      const field = document.getElementById(id);
      if (!field) return;
      field.addEventListener('input', resetPreview);
      field.addEventListener('change', resetPreview);
    });
    batchForm.addEventListener('submit', event => {
      if (!previewIsValid) {
        event.preventDefault();
        previewInput();
      }
    });
    updateSourceVisibility();

    let activeJobId = {{ active_job.job_id|tojson if active_job else 'null' }};
    async function refreshJob() {
      if (!activeJobId) return;
      const res = await fetch(`/api/jobs/${activeJobId}`);
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById('status').textContent = data.status;
      document.getElementById('completed').textContent = data.completed;
      document.getElementById('total').textContent = data.total || 0;
      document.getElementById('elapsed').textContent = `${data.elapsed_seconds.toFixed(1)}s`;
      document.getElementById('stageText').textContent = data.current_step || '';
      const itemText = document.getElementById('itemText');
      if (itemText) itemText.textContent = data.current_item || '';
      const progress = document.getElementById('progress');
      progress.style.width = `${data.progress_pct}%`;
      progress.classList.toggle('running', data.status === 'queued' || data.status === 'running');
      const banner = document.getElementById('runBanner');
      const bannerText = document.getElementById('runBannerText');
      const bannerDetail = document.getElementById('runBannerDetail');
      const statusDot = document.getElementById('statusDot');
      const isRunning = data.status === 'queued' || data.status === 'running';
      banner.classList.toggle('visible', isRunning);
      bannerText.textContent = isRunning
        ? `處理中：第 ${data.completed || 0} / ${data.total || 0} 筆`
        : (data.status === 'done' ? '已完成' : '執行中斷');
      bannerDetail.textContent = isRunning
        ? `${data.current_step || 'processing'}${data.current_item ? ` · ${data.current_item}` : ''}`
        : '';
      statusDot.style.display = isRunning ? '' : 'none';
      const downloads = document.getElementById('downloads');
      const downloadOutput = document.getElementById('downloadOutput');
      const downloadFailed = document.getElementById('downloadFailed');
      const hasDownloads = data.output_exists || data.failed_output_exists;
      downloads.style.display = data.status === 'done' && hasDownloads ? 'grid' : 'none';
      if (downloadOutput) downloadOutput.style.display = data.output_exists ? '' : 'none';
      if (downloadFailed) downloadFailed.style.display = data.failed_output_exists ? '' : 'none';
      if (data.status === 'done' || data.status === 'failed') {
        activeJobId = null;
      }
    }
    if (activeJobId) {
      setInterval(refreshJob, 1200);
    }
  </script>
</body>
</html>
"""


def get_active_job() -> Job | None:
    with jobs_lock:
        for job in jobs.values():
            if job.status in {"queued", "running"}:
                return job
    return None


def create_job_id() -> str:
    return uuid.uuid4().hex[:12]


def ensure_job_root(job_id: str) -> Path:
    job_dir = JOB_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def build_form_defaults() -> dict[str, Any]:
    return {
        "source_mode": "sheet" if DEFAULT_SHEET else "urls",
        "sheet": DEFAULT_SHEET,
        "urls": "",
        "sheet_platforms": DEFAULT_PLATFORM,
        "output_dir": DEFAULT_OUTPUT_DIR,
        "output_name": DEFAULT_OUTPUT_NAME,
        "failed_output_name": DEFAULT_FAILED_OUTPUT_NAME,
        "concurrency": DEFAULT_CONCURRENCY,
        "delay": DEFAULT_DELAY,
        "retries": DEFAULT_RETRIES,
        "profile_search_scrolls": os.getenv("THREADS_PROFILE_SEARCH_SCROLLS", "12"),
        "fetch_followers": DEFAULT_FETCH_FOLLOWERS,
        "profile_search": DEFAULT_PROFILE_SEARCH,
        "network_capture": DEFAULT_NETWORK_CAPTURE,
        "debug": DEFAULT_DEBUG,
        "headful": False,
        "use_login": DEFAULT_USE_LOGIN,
        "profile_dir": DEFAULT_PROFILE_DIR,
        "auth_state": DEFAULT_AUTH_STATE,
    }


def build_submitted_form_defaults(
    *,
    source_mode: str,
    sheet: str,
    urls: str,
    sheet_platforms: str,
    output_dir: str,
    output_name: str,
    failed_output_name: str,
    concurrency: str,
    delay: str,
    retries: str,
    profile_search_scrolls: str,
    use_login: bool,
    profile_dir: str,
    auth_state: str,
) -> dict[str, Any]:
    defaults = build_form_defaults()
    defaults.update(
        {
            "source_mode": source_mode,
            "sheet": sheet,
            "urls": urls,
            "sheet_platforms": sheet_platforms,
            "output_dir": output_dir,
            "output_name": output_name,
            "failed_output_name": failed_output_name,
            "concurrency": concurrency,
            "delay": delay,
            "retries": retries,
            "profile_search_scrolls": profile_search_scrolls,
            "fetch_followers": bool(request.form.get("fetch_followers")),
            "profile_search": bool(request.form.get("profile_search")),
            "network_capture": bool(request.form.get("network_capture")),
            "debug": bool(request.form.get("debug")),
            "headful": bool(request.form.get("headful")),
            "use_login": use_login,
            "profile_dir": profile_dir or DEFAULT_PROFILE_DIR,
            "auth_state": auth_state or DEFAULT_AUTH_STATE,
        }
    )
    return defaults


def make_log_text(job: Job | None) -> str:
    if not job:
        return "Waiting for a job..."
    lines = list(job.log_lines)
    if not lines:
        return "Job started. Waiting for CLI output..."
    return "\n".join(lines)


def start_subprocess(
    job: Job,
    cmd: list[str],
    job_dir: Path,
    env: dict[str, str],
    output_path: str,
    failed_output_path: str,
) -> None:
    log_path = job_dir / "run.log"
    job.log_lines.clear()
    job.log_lines.append("Starting batch...")

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip("\n")
                if not line:
                    continue
                log_file.write(line + "\n")
                log_file.flush()
                with jobs_lock:
                    job.log_lines.append(line)
                    _update_job_progress(job, line)
            proc.wait()
            with jobs_lock:
                job.returncode = proc.returncode
                job.finished_at = time.time()
                job.status = "done" if proc.returncode == 0 else "failed"
                if proc.returncode != 0:
                    job.error = f"Process exited with code {proc.returncode}"
                job.log_lines.append(f"Process exited with code {proc.returncode}")
    except Exception as exc:
        with jobs_lock:
            job.finished_at = time.time()
            job.status = "failed"
            job.error = str(exc)
            job.log_lines.append(f"ERROR: {exc}")


def _update_job_progress(job: Job, line: str) -> None:
    total_match = re.search(r"Loaded (\d+) URL\(s\)", line)
    if total_match:
        job.total = int(total_match.group(1))
        job.current_step = "loading inputs"
        return
    if line == "Resolving input source...":
        job.current_step = "resolving input source"
        return
    if line == "Reading Google Sheet export...":
        job.current_step = "reading Google Sheet export"
        return
    if line == "Reading URL list...":
        job.current_step = "reading URL list"
        return
    input_ready_match = re.search(r"^Input ready: (\d+) URL\(s\)", line)
    if input_ready_match:
        job.total = int(input_ready_match.group(1))
        job.current_step = "input ready"
        return
    progress_match = re.search(r"^\[(\d+)/(\d+)\]\s+\[(.*?)\]\s+([0-9.]+)s\s+(https?://\S+)", line)
    if progress_match:
        job.completed = int(progress_match.group(1))
        job.total = int(progress_match.group(2))
        job.current_step = progress_match.group(3)
        job.current_item = _shorten_url(progress_match.group(5))
        return
    if line.startswith("Fetching follower counts for "):
        job.current_step = "fetching Threads followers"
        return
    if line.startswith("Fetching social follower counts for "):
        job.current_step = "fetching social followers"
        return
    if line.startswith("Done. Wrote "):
        job.current_step = "finished"
        return


def _shorten_url(url: str, limit: int = 88) -> str:
    if len(url) <= limit:
        return url
    head = max(24, limit - 18)
    tail = max(12, limit - head - 3)
    return f"{url[:head]}...{url[-tail:]}"


def _selected_platforms(value: str) -> set[str]:
    platforms = {part.strip().upper() for part in value.split(",") if part.strip()}
    if not platforms or "ALL" in platforms:
        return {"THREADS", "IG", "FACEBOOK"}
    return platforms


def _platform_from_url(url: str) -> str:
    lowered = url.lower()
    if "instagram." in lowered:
        return "IG"
    if "facebook." in lowered or "fb.watch" in lowered or "fb.com" in lowered:
        return "FACEBOOK"
    if "threads." in lowered:
        return "THREADS"
    return "UNKNOWN"


def _google_sheet_url_from_text(text: str) -> str:
    for line in text.splitlines():
        value = line.strip()
        if re.search(r"https?://docs\.google\.com/spreadsheets/d/", value):
            return value
    return ""


def _preview_payload(rows: list[Any], metadata: dict[str, object] | None = None) -> dict[str, object]:
    counts = Counter(str(row.platform) for row in rows)
    samples = [
        f"{row.platform} · row {row.row_number} · {_shorten_url(row.url, 96)}"
        for row in rows[:5]
    ]
    columns = "-"
    if metadata:
        column_values = metadata.get("columns") or {}
        if isinstance(column_values, dict):
            columns = ", ".join(f"{key}: {value}" for key, value in column_values.items())
    return {
        "ok": bool(rows),
        "total": len(rows),
        "platforms": ", ".join(f"{key}: {counts[key]}" for key in sorted(counts)) or "-",
        "columns": columns,
        "samples": samples,
        "error": "" if rows else "No supported social URLs found.",
    }


def _preview_url_text(urls_text: str, platforms: set[str]) -> dict[str, object]:
    rows = []
    seen: set[str] = set()
    for index, line in enumerate(urls_text.splitlines(), start=1):
        url = line.strip()
        if not url or url.startswith("#") or url in seen:
            continue
        seen.add(url)
        platform = _platform_from_url(url)
        if platform == "UNKNOWN" or platform not in platforms:
            continue
        rows.append(
            type(
                "PreviewRow",
                (),
                {"platform": platform, "row_number": index, "url": url},
            )()
        )
    payload = _preview_payload(rows)
    payload["columns"] = "URL list"
    return payload


@app.route("/")
def index() -> str:
    active_job = get_active_job()
    form_defaults = active_job.form_defaults if active_job and active_job.form_defaults else build_form_defaults()
    return render_template_string(
        PAGE_TEMPLATE,
        active_job=active_job,
        log_text=make_log_text(active_job),
        form_defaults=form_defaults,
        mode_label=active_job.status if active_job else "idle",
    )


@app.route("/preview", methods=["POST"])
def preview_input():
    source_mode = (request.form.get("source_mode") or "sheet").strip().lower()
    platforms = _selected_platforms(request.form.get("sheet_platforms") or DEFAULT_PLATFORM)
    try:
        if source_mode == "sheet":
            sheet_url = (request.form.get("sheet") or DEFAULT_SHEET).strip()
            if not sheet_url:
                return jsonify({"ok": False, "error": "Missing Google Sheet URL."}), 400
            rows, metadata = inspect_sheet_urls(sheet_url, platforms=platforms)
            return jsonify(_preview_payload(rows, metadata))
        if source_mode == "xlsx":
            uploaded = request.files.get("xlsx")
            if not uploaded or not uploaded.filename:
                return jsonify({"ok": False, "error": "Missing XLSX file."}), 400
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temp_file:
                temp_path = Path(temp_file.name)
            try:
                uploaded.save(temp_path)
                rows, metadata = inspect_sheet_urls(temp_path, platforms=platforms)
                return jsonify(_preview_payload(rows, metadata))
            finally:
                temp_path.unlink(missing_ok=True)
        urls_text = (request.form.get("urls") or "").strip()
        uploaded = request.files.get("urls_file")
        if uploaded and uploaded.filename:
            urls_text = "\n".join([urls_text, uploaded.read().decode("utf-8")]).strip()
        if not urls_text:
            return jsonify({"ok": False, "error": "Missing URL list."}), 400
        sheet_url = _google_sheet_url_from_text(urls_text)
        if sheet_url:
            rows, metadata = inspect_sheet_urls(sheet_url, platforms=platforms)
            payload = _preview_payload(rows, metadata)
            payload["columns"] = f"Google Sheet URL detected · {payload['columns']}"
            return jsonify(payload)
        return jsonify(_preview_url_text(urls_text, platforms))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/start", methods=["POST"])
def start_job() -> Response:
    active_job = get_active_job()
    if active_job:
        return Response("A job is already running.", status=409)

    source_mode = (request.form.get("source_mode") or "sheet").strip().lower()
    submitted_source_mode = source_mode
    job_id = create_job_id()
    job_dir = ensure_job_root(job_id)
    job = Job(job_id=job_id)
    job.started_at = time.time()
    job.status = "running"
    job.workdir = str(job_dir)

    cmd = [sys.executable, "-u", "src/main.py"]
    output_dir = (request.form.get("output_dir") or DEFAULT_OUTPUT_DIR).strip() or DEFAULT_OUTPUT_DIR
    output_name = (request.form.get("output_name") or DEFAULT_OUTPUT_NAME).strip() or DEFAULT_OUTPUT_NAME
    failed_output_name = (request.form.get("failed_output_name") or DEFAULT_FAILED_OUTPUT_NAME).strip() or DEFAULT_FAILED_OUTPUT_NAME
    output_path = str(Path(output_dir) / output_name)
    failed_output_path = str(Path(output_dir) / failed_output_name)
    sheet_platforms = (request.form.get("sheet_platforms") or DEFAULT_PLATFORM).strip()
    concurrency = (request.form.get("concurrency") or DEFAULT_CONCURRENCY).strip()
    delay = (request.form.get("delay") or DEFAULT_DELAY).strip()
    retries = (request.form.get("retries") or DEFAULT_RETRIES).strip()
    profile_search_scrolls = (request.form.get("profile_search_scrolls") or "12").strip()
    use_login = bool(request.form.get("use_login"))
    profile_dir = (request.form.get("profile_dir") or DEFAULT_PROFILE_DIR).strip() if use_login else ""
    auth_state = (request.form.get("auth_state") or DEFAULT_AUTH_STATE).strip() if use_login else ""
    sheet_url = (request.form.get("sheet") or DEFAULT_SHEET).strip()
    urls_text = (request.form.get("urls") or "").strip()

    common_flags = [
        ("--output", output_path),
        ("--failed-output", failed_output_path),
        ("--concurrency", concurrency),
        ("--delay", delay),
        ("--retries", retries),
        ("--profile-search-scrolls", profile_search_scrolls),
    ]
    if request.form.get("headful"):
        cmd.append("--headful")
    if request.form.get("debug"):
        pass
    else:
        cmd.append("--no-debug")
    if not request.form.get("fetch_followers"):
        cmd.append("--no-fetch-followers")
    if request.form.get("profile_search"):
        cmd.append("--profile-search")
    if not request.form.get("network_capture"):
        cmd.append("--no-network-capture")
    if use_login and profile_dir:
        common_flags.extend([("--profile-dir", profile_dir)])
    if use_login and auth_state:
        common_flags.extend([("--auth-state", auth_state)])

    if source_mode == "sheet":
        if not sheet_url:
            return Response("Missing Google Sheet URL.", status=400)
        cmd.extend(["--sheet", sheet_url, "--sheet-platforms", sheet_platforms])
    elif source_mode == "xlsx":
        uploaded = request.files.get("xlsx")
        if not uploaded or not uploaded.filename:
            return Response("Missing XLSX file.", status=400)
        xlsx_path = job_dir / "input.xlsx"
        uploaded.save(xlsx_path)
        cmd.extend(["--sheet", str(xlsx_path), "--sheet-platforms", sheet_platforms])
    else:
        uploaded = request.files.get("urls_file")
        if uploaded and uploaded.filename:
            urls_text = "\n".join([urls_text, uploaded.read().decode("utf-8")]).strip()
        if not urls_text:
            return Response("Missing URL list.", status=400)
        sheet_url = _google_sheet_url_from_text(urls_text)
        if sheet_url:
            cmd.extend(["--sheet", sheet_url, "--sheet-platforms", sheet_platforms])
            source_mode = "sheet"
        else:
            input_path = job_dir / "urls.txt"
            input_path.write_text(urls_text + "\n", encoding="utf-8")
            cmd.extend(["--input", str(input_path)])

    for flag, value in common_flags:
        cmd.extend([flag, value])

    job.command = cmd
    job.output_path = output_path
    job.failed_output_path = failed_output_path
    job.form_defaults = build_submitted_form_defaults(
        source_mode=submitted_source_mode,
        sheet=sheet_url,
        urls=urls_text if submitted_source_mode == "urls" else "",
        sheet_platforms=sheet_platforms,
        output_dir=output_dir,
        output_name=output_name,
        failed_output_name=failed_output_name,
        concurrency=concurrency,
        delay=delay,
        retries=retries,
        profile_search_scrolls=profile_search_scrolls,
        use_login=use_login,
        profile_dir=profile_dir,
        auth_state=auth_state,
    )

    env = os.environ.copy()
    env["THREADS_RESUME"] = "false"
    if not use_login:
        env["THREADS_PROFILE_DIR"] = ""
        env["THREADS_AUTH_STATE"] = ""
        env["THREADS_USERNAME"] = ""
        env["THREADS_PASSWORD"] = ""
    if source_mode == "urls":
        env["THREADS_SHEET"] = ""
    else:
        env["THREADS_INPUT"] = ""

    with jobs_lock:
        jobs[job_id] = job

    thread = threading.Thread(
        target=start_subprocess,
        args=(job, cmd, job_dir, env, output_path, failed_output_path),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("job_view", job_id=job_id))


@app.route("/job/<job_id>")
def job_view(job_id: str) -> str:
    job = jobs.get(job_id)
    form_defaults = job.form_defaults if job and job.form_defaults else build_form_defaults()
    return render_template_string(
        PAGE_TEMPLATE,
        active_job=job,
        log_text=make_log_text(job),
        form_defaults=form_defaults,
        mode_label=job.status if job else "idle",
    )


@app.route("/api/jobs/<job_id>")
def job_api(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "job_id": job.job_id,
            "status": job.status,
            "message": job.message,
            "returncode": job.returncode,
            "elapsed_seconds": job.elapsed_seconds,
            "progress_pct": job.progress_pct,
            "completed": job.completed,
            "total": job.total,
            "current_step": job.current_step,
            "current_item": job.current_item,
            "log_text": make_log_text(job),
            "output_path": job.output_path,
            "failed_output_path": job.failed_output_path,
            "output_exists": job.output_exists,
            "failed_output_exists": job.failed_output_exists,
            "error": job.error,
        }
    )


@app.route("/download/<job_id>/<kind>")
def download_file(job_id: str, kind: str):
    job = jobs.get(job_id)
    if not job:
        return Response("Not found", status=404)
    if kind == "output":
        path = Path(job.output_path)
    elif kind == "failed":
        path = Path(job.failed_output_path)
    else:
        return Response("Invalid kind", status=400)
    if not path.exists():
        return Response("File not ready", status=404)
    return send_file(path, as_attachment=True, download_name=path.name)


def main() -> int:
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description="Local GUI for social metrics scraping.")
    parser.add_argument("--host", default=os.getenv("GUI_HOST", "127.0.0.1"), help="Host to bind the GUI server.")
    parser.add_argument("--port", type=int, default=int(os.getenv("GUI_PORT", "5000")), help="Port to bind the GUI server.")
    parser.add_argument("--open-browser", action="store_true", help="Open the default browser after startup.")
    args = parser.parse_args()
    if args.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
