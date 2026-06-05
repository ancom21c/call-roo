from __future__ import annotations

import base64
import binascii
import copy
import hmac
import json
import logging
import mimetypes
import shlex
import subprocess
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from callroo_printer.config import AppConfig, load_config

LOGGER = logging.getLogger(__name__)

DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 3001
DEFAULT_PREVIEW_LIMIT = 200
DEFAULT_LOG_LINES = 200
DEFAULT_SNAPSHOT_CACHE_SECONDS = 8.0
LOG_STALE_SECONDS = 180
SERVICE_NAME = "callroo-printer.service"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3"}
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

_DASHBOARD_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>callroo dashboard</title>
  <style>
    :root {
      --bg: #f4efe8;
      --bg-strong: #e6ddd0;
      --panel: rgba(255, 251, 246, 0.86);
      --panel-strong: rgba(255, 251, 246, 0.96);
      --stroke: rgba(70, 52, 34, 0.14);
      --text: #26190e;
      --muted: #6d5a4c;
      --accent: #b64926;
      --accent-soft: rgba(182, 73, 38, 0.12);
      --ok: #2d6a4f;
      --warn: #a55d1d;
      --danger: #9f2a20;
      --shadow: 0 18px 40px rgba(65, 45, 28, 0.12);
      --radius: 24px;
      --radius-sm: 16px;
      --font-ui: "SUIT Variable", "Pretendard Variable", "Noto Sans KR", sans-serif;
      --font-mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: var(--font-ui);
      background:
        radial-gradient(circle at top left, rgba(182, 73, 38, 0.18), transparent 32%),
        radial-gradient(circle at 85% 12%, rgba(95, 134, 111, 0.18), transparent 28%),
        linear-gradient(180deg, #f8f3ec 0%, var(--bg) 42%, #efe6db 100%);
    }

    body.dialog-open {
      overflow: hidden;
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(90deg, rgba(255, 255, 255, 0.18) 1px, transparent 1px),
        linear-gradient(rgba(255, 255, 255, 0.12) 1px, transparent 1px);
      background-size: 32px 32px;
      mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.22), transparent 70%);
    }

    main {
      width: min(1360px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 72px;
    }

    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.9fr);
      gap: 16px;
      margin-bottom: 16px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .hero-copy {
      padding: 28px;
      position: relative;
      overflow: hidden;
    }

    .hero-copy::after {
      content: "";
      position: absolute;
      width: 240px;
      height: 240px;
      right: -70px;
      top: -60px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(182, 73, 38, 0.18), transparent 70%);
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(38, 25, 14, 0.06);
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    h1 {
      margin: 0 0 8px;
      font-size: clamp(28px, 4.6vw, 52px);
      line-height: 1;
      letter-spacing: 0;
    }

    .hero-subtitle {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
      font-weight: 700;
    }

    .hero-copy p {
      margin: 0;
      max-width: 42rem;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.7;
    }

    .status-rail {
      padding: 20px;
      display: grid;
      gap: 12px;
      align-content: start;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      width: fit-content;
      padding: 10px 14px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
      background: rgba(38, 25, 14, 0.08);
      color: var(--muted);
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: currentColor;
      box-shadow: 0 0 0 6px color-mix(in srgb, currentColor 18%, transparent);
    }

    .status-pill.level-healthy {
      color: var(--ok);
      background: rgba(45, 106, 79, 0.12);
    }

    .status-pill.level-stale {
      color: var(--warn);
      background: rgba(165, 93, 29, 0.12);
    }

    .status-pill.level-inactive,
    .status-pill.level-error {
      color: var(--danger);
      background: rgba(159, 42, 32, 0.12);
    }

    .stats-grid,
    .runtime-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }

    .dashboard-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 16px;
      align-items: start;
    }

    .workbench {
      display: grid;
      gap: 14px;
      min-width: 0;
    }

    .side-rail {
      position: sticky;
      top: 16px;
      display: grid;
      gap: 12px;
    }

    .side-rail .stats-grid,
    .side-rail .runtime-grid {
      grid-template-columns: 1fr;
      gap: 8px;
      margin-bottom: 0;
    }

    .stat-card,
    .runtime-card {
      padding: 12px 13px;
      border-radius: 14px;
      background: var(--panel-strong);
      border: 1px solid var(--stroke);
      min-height: 78px;
    }

    .stat-label,
    .runtime-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .stat-value {
      margin-top: 8px;
      font-size: clamp(20px, 2.4vw, 26px);
      line-height: 1;
      letter-spacing: -0.06em;
    }

    .stat-meta,
    .runtime-meta {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      word-break: break-word;
    }

    .action-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      padding: 16px;
    }

    .control-card {
      display: grid;
      gap: 10px;
      padding: 14px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.68);
      border: 1px solid var(--stroke);
      min-width: 0;
    }

    .control-title {
      margin: 0;
      font-size: 15px;
      letter-spacing: -0.02em;
    }

    .control-meta {
      min-height: 1.4em;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }

    .dialog-backdrop {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: none;
      place-items: center;
      padding: 22px;
      background: rgba(38, 25, 14, 0.42);
      backdrop-filter: blur(8px);
    }

    .dialog-backdrop.open {
      display: grid;
    }

    .dialog-panel {
      width: min(920px, 100%);
      max-height: min(88vh, 920px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      overflow: hidden;
      background: var(--panel-strong);
      border: 1px solid var(--stroke);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow);
    }

    .dialog-head,
    .dialog-actions {
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      padding: 16px 18px;
      background: rgba(255, 251, 246, 0.98);
    }

    .dialog-head {
      border-bottom: 1px solid var(--stroke);
    }

    .dialog-actions {
      border-top: 1px solid var(--stroke);
    }

    .dialog-title {
      margin: 0;
      font-size: 18px;
      letter-spacing: -0.03em;
    }

    .dialog-body {
      min-height: 0;
      overflow: auto;
      padding: 18px;
      display: grid;
      gap: 16px;
    }

    .dialog-form {
      display: grid;
      gap: 14px;
    }

    .dialog-status {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }

    .artifact-section {
      display: grid;
      gap: 10px;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid var(--stroke);
      background: rgba(255, 255, 255, 0.54);
    }

    .artifact-section-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
    }

    .artifact-section-title {
      margin: 0;
      font-size: 14px;
      letter-spacing: -0.02em;
    }

    .artifact-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 10px;
    }

    .artifact-grid.audio {
      grid-template-columns: 1fr;
    }

    .artifact-card {
      min-width: 0;
      display: grid;
      gap: 8px;
      padding: 10px;
      border-radius: 12px;
      border: 1px solid var(--stroke);
      background: rgba(255, 255, 255, 0.78);
    }

    .artifact-thumb,
    .artifact-thumb-fallback {
      width: 100%;
      aspect-ratio: 1 / 1;
      border-radius: 10px;
      border: 1px solid var(--stroke);
      background: rgba(38, 25, 14, 0.06);
      object-fit: cover;
    }

    .artifact-thumb-fallback {
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 12px;
      text-align: center;
      padding: 10px;
    }

    .artifact-name {
      font-size: 13px;
      font-weight: 700;
      line-height: 1.35;
      word-break: break-word;
    }

    .artifact-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      word-break: break-word;
    }

    .artifact-audio {
      width: 100%;
    }

    .lock-panel {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(180px, 0.75fr) auto;
      gap: 10px;
      align-items: end;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid var(--stroke);
      background: rgba(38, 25, 14, 0.04);
    }

    .lock-title {
      font-size: 13px;
      font-weight: 700;
    }

    .profile-toolbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(180px, 0.8fr) auto auto;
      gap: 10px;
      align-items: end;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .form-grid.full {
      grid-template-columns: 1fr;
    }

    label.field {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }

    input[type="text"],
    input[type="number"],
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--stroke);
      border-radius: 10px;
      background: white;
      color: var(--text);
      padding: 9px 10px;
      font: inherit;
      font-size: 13px;
    }

    textarea {
      min-height: 90px;
      resize: vertical;
      line-height: 1.5;
      font-family: var(--font-ui);
    }

    textarea.tall {
      min-height: 150px;
    }

    .model-editor {
      display: grid;
      gap: 8px;
    }

    .model-row {
      display: grid;
      grid-template-columns: 0.7fr 1fr 1.3fr 0.55fr 0.55fr 0.55fr;
      gap: 8px;
      align-items: end;
    }

    input[type="file"] {
      width: 100%;
      font: inherit;
      font-size: 12px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
      padding: 18px 20px;
    }

    .toolbar-left,
    .toolbar-right {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    input[type="date"] {
      appearance: none;
      border: 1px solid var(--stroke);
      border-radius: 12px;
      background: white;
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
    }

    button {
      border: 0;
      border-radius: 12px;
      padding: 10px 14px;
      font: inherit;
      color: white;
      background: var(--accent);
      cursor: pointer;
      transition: transform 140ms ease, opacity 140ms ease, box-shadow 140ms ease;
      box-shadow: 0 10px 24px rgba(182, 73, 38, 0.18);
    }

    button.secondary {
      color: var(--text);
      background: rgba(38, 25, 14, 0.08);
      box-shadow: none;
    }

    button.danger {
      color: white;
      background: var(--danger);
      box-shadow: 0 10px 24px rgba(159, 42, 32, 0.16);
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:disabled,
    input:disabled,
    select:disabled,
    textarea:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    button:disabled:hover {
      transform: none;
    }

    .date-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 18px;
    }

    .date-chip {
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--stroke);
      background: rgba(255, 255, 255, 0.72);
      color: var(--muted);
      cursor: pointer;
      transition: all 140ms ease;
    }

    .date-chip.active {
      border-color: rgba(182, 73, 38, 0.22);
      background: var(--accent-soft);
      color: var(--accent);
    }

    .preview-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
      gap: 14px;
    }

    .preview-card {
      display: grid;
      grid-template-rows: 220px auto;
      overflow: hidden;
      animation: rise 320ms ease backwards;
    }

    .preview-card img,
    .preview-fallback {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
      background: linear-gradient(135deg, #efe2d0, #dfcfbc);
    }

    .preview-fallback {
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 13px;
      padding: 16px;
      text-align: center;
    }

    .preview-body {
      padding: 16px;
      display: grid;
      gap: 10px;
    }

    .preview-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }

    .preview-time {
      font-size: 18px;
      letter-spacing: -0.04em;
      font-weight: 600;
    }

    .preview-status {
      font-size: 12px;
      color: var(--muted);
    }

    .badge-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 9px;
      border-radius: 999px;
      background: rgba(38, 25, 14, 0.06);
      color: var(--muted);
      font-size: 12px;
    }

    .fortune {
      margin: 0;
      white-space: pre-line;
      line-height: 1.6;
      font-size: 14px;
      color: var(--text);
      min-height: 4.8em;
    }

    .preview-footer {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }

    details {
      margin-top: 16px;
      overflow: hidden;
    }

    details > summary {
      list-style: none;
      cursor: pointer;
      padding: 18px 20px;
      font-weight: 600;
      border-bottom: 1px solid transparent;
    }

    details[open] > summary {
      border-bottom-color: var(--stroke);
    }

    details > summary::-webkit-details-marker {
      display: none;
    }

    .details-body {
      padding: 18px 20px 22px;
    }

    .log-box,
    .prompt-box {
      margin: 0;
      padding: 14px;
      border-radius: 16px;
      background: #21170f;
      color: #f9f4ee;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.65;
      font-family: var(--font-mono);
    }

    .profiles-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 14px;
    }

    .profile-card {
      border: 1px solid var(--stroke);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.66);
      padding: 16px;
      display: grid;
      gap: 10px;
    }

    .profile-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }

    .profile-name {
      font-size: 20px;
      letter-spacing: -0.04em;
      margin: 0;
    }

    .keyvals {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .keyval {
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(38, 25, 14, 0.04);
    }

    .keyval-label {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .keyval-value {
      margin-top: 6px;
      font-size: 13px;
      line-height: 1.5;
      word-break: break-word;
    }

    .empty-state {
      padding: 36px 24px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed var(--stroke);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.48);
    }

    .muted {
      color: var(--muted);
    }

    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      border: 0;
    }

    @keyframes rise {
      from {
        transform: translateY(8px);
        opacity: 0;
      }
      to {
        transform: translateY(0);
        opacity: 1;
      }
    }

    @media (max-width: 980px) {
      .hero {
        grid-template-columns: 1fr;
      }

      .stats-grid,
      .runtime-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .dashboard-layout {
        grid-template-columns: 1fr;
      }

      .side-rail {
        position: static;
        grid-row: 1;
      }

      .action-grid {
        grid-template-columns: 1fr;
      }

      .model-row {
        grid-template-columns: 1fr;
      }

      .lock-panel,
      .profile-toolbar {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 640px) {
      main {
        width: min(100% - 20px, 1360px);
        padding-top: 18px;
      }

      .toolbar {
        align-items: stretch;
        flex-direction: column;
      }

      .toolbar-left,
      .toolbar-right {
        width: 100%;
      }

      .stats-grid,
      .runtime-grid,
      .keyvals,
      .form-grid {
        grid-template-columns: 1fr;
      }

      .preview-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <article class="panel hero-copy">
        <h1>CALLROO PRINTER DASHBOARD</h1>
        <div class="hero-subtitle">생성 결과, 상태, 로그를 한 화면에서</div>
        <p>출력 이력 프리뷰를 날짜로 걸러 보고, 현재 서비스 상태와 최근 로그, LLM 프롬프트 설정을 접이식 패널로 확인합니다. 기본 포트는 <strong>3001</strong>입니다.</p>
      </article>
      <aside class="panel status-rail">
        <div id="service-pill" class="status-pill level-unknown">
          <span class="status-dot" aria-hidden="true"></span>
          <span id="service-pill-text">상태 확인 중</span>
        </div>
        <div>
          <div class="stat-label">System Summary</div>
          <div id="service-summary" class="stat-meta">서비스 정보를 불러오는 중입니다.</div>
        </div>
        <div>
          <div class="stat-label">Last Refresh</div>
          <div id="last-refresh" class="stat-meta">-</div>
        </div>
      </aside>
    </section>

    <section class="dashboard-layout">
      <div class="workbench">
        <section class="panel action-grid">
          <article class="control-card">
            <h2 class="control-title">프린트 호출</h2>
            <button id="print-button" type="button">지금 출력</button>
            <div id="print-status" class="control-meta">대시보드에서 서비스 트리거를 큐에 넣습니다.</div>
          </article>
          <article class="control-card">
            <h2 class="control-title">프롬프트 설정</h2>
            <button id="open-settings-dialog" type="button">설정 열기</button>
            <div class="control-meta">프로필, 프롬프트, 모델 후보를 다이얼로그에서 수정합니다.</div>
          </article>
          <article class="control-card">
            <h2 class="control-title">아티팩트</h2>
            <button id="open-upload-dialog" type="button">아티팩트 열기</button>
            <div class="control-meta">그림과 음악 파일을 관리합니다.</div>
          </article>
        </section>

        <section class="panel toolbar">
          <div class="toolbar-left">
            <label for="date-filter">날짜 검색</label>
            <input id="date-filter" type="date">
            <button id="apply-filter" type="button">적용</button>
            <button id="reset-filter" type="button" class="secondary">초기화</button>
          </div>
          <div class="toolbar-right muted" id="preview-count">프리뷰를 불러오는 중입니다.</div>
        </section>

        <section class="date-chip-row" id="date-chips"></section>

        <section id="preview-area">
          <div class="preview-grid" id="preview-grid"></div>
        </section>
      </div>

      <aside class="side-rail">
        <section class="stats-grid" id="stats-grid">
          <article class="panel stat-card">
            <div class="stat-label">전체 작업</div>
            <div class="stat-value">-</div>
            <div class="stat-meta">집계 중</div>
          </article>
        </section>
        <section class="runtime-grid" id="runtime-grid"></section>
      </aside>
    </section>

    <details class="panel">
      <summary>시스템 로그</summary>
      <div class="details-body">
        <div class="muted" id="log-meta"></div>
        <pre id="log-body" class="log-box">로그를 불러오는 중입니다.</pre>
      </div>
    </details>

    <details class="panel">
      <summary>LLM 설정과 프롬프트</summary>
      <div class="details-body">
        <div id="llm-profiles" class="profiles-grid"></div>
      </div>
    </details>
  </main>

  <div id="settings-dialog" class="dialog-backdrop" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="settings-dialog-title">
    <section class="dialog-panel">
      <header class="dialog-head">
        <h2 id="settings-dialog-title" class="dialog-title">프롬프트 및 설정 수정</h2>
        <button type="button" class="secondary" data-close-dialog="settings-dialog">닫기</button>
      </header>
      <div class="dialog-body">
        <div id="settings-lock-panel" class="lock-panel">
          <div>
            <div id="settings-lock-title" class="lock-title">설정 잠김</div>
            <div id="settings-lock-status" class="dialog-status">수정 토큰을 입력하면 편집할 수 있습니다.</div>
          </div>
          <label class="field">수정 토큰
            <input id="settings-token" type="password" autocomplete="current-password">
          </label>
          <button id="unlock-settings" type="button">잠금 해제</button>
        </div>
        <div class="profile-toolbar">
          <label class="field">프로필
            <select id="profile-select"></select>
          </label>
          <label class="field">새 프로필 이름
            <input id="new-profile-name" type="text" placeholder="예: gigachad">
          </label>
          <button id="add-profile" type="button" class="secondary">추가</button>
          <button id="delete-profile" type="button" class="danger">삭제</button>
        </div>
        <div class="dialog-form" id="profile-editor">
          <div class="form-grid">
            <label class="field">Weight
              <input id="edit-weight" type="number" min="0" step="0.1">
            </label>
            <label class="field">Fallback Text
              <input id="edit-fallback" type="text">
            </label>
          </div>
          <div class="form-grid full">
            <label class="field">System Prompt
              <textarea id="edit-system-prompt" class="tall"></textarea>
            </label>
            <label class="field">Prompt
              <textarea id="edit-prompt" class="tall"></textarea>
            </label>
          </div>
          <div class="model-editor" id="model-editor"></div>
        </div>
      </div>
      <footer class="dialog-actions">
        <div id="profile-status" class="dialog-status">프로필을 선택하면 편집기가 채워집니다.</div>
        <button id="save-profile" type="button">설정 저장</button>
      </footer>
    </section>
  </div>

  <div id="upload-dialog" class="dialog-backdrop" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="upload-dialog-title">
    <section class="dialog-panel">
      <header class="dialog-head">
        <h2 id="upload-dialog-title" class="dialog-title">아티팩트</h2>
        <button type="button" class="secondary" data-close-dialog="upload-dialog">닫기</button>
      </header>
      <div class="dialog-body">
        <section class="artifact-section">
          <h3 class="artifact-section-title">업로드</h3>
          <label class="field">파일
            <input id="asset-file" type="file" accept="image/*,.wav,.mp3">
          </label>
          <div class="form-grid">
            <label class="field">업로드 종류
              <select id="upload-kind">
                <option value="image">그림</option>
                <option value="audio">음악/효과음</option>
              </select>
            </label>
            <label class="field">대상 프로필
              <select id="upload-profile"></select>
            </label>
            <label class="field">그림 태그
              <input id="upload-tag" type="text" placeholder="예: 행운">
            </label>
            <label class="field">오디오 대상
              <select id="upload-audio-target">
                <option value="">설정에 연결 안 함</option>
                <option value="launch_sounds">출력 중 반복 재생</option>
                <option value="printer_connected_file">프린터 연결</option>
                <option value="printer_failed_file">프린터 실패</option>
                <option value="print_completed_file">출력 완료</option>
              </select>
            </label>
          </div>
        </section>
        <section class="artifact-section">
          <div class="artifact-section-head">
            <h3 class="artifact-section-title">등록된 그림</h3>
            <div id="artifact-image-count" class="dialog-status">-</div>
          </div>
          <div id="artifact-image-list" class="artifact-grid"></div>
        </section>
        <section class="artifact-section">
          <div class="artifact-section-head">
            <h3 class="artifact-section-title">등록된 음악</h3>
            <div id="artifact-audio-count" class="dialog-status">-</div>
          </div>
          <div id="artifact-audio-list" class="artifact-grid audio"></div>
        </section>
      </div>
      <footer class="dialog-actions">
        <div id="upload-status" class="dialog-status">이미지는 태그에, 오디오는 효과음 설정에 연결할 수 있습니다.</div>
        <button id="upload-asset" type="button">업로드</button>
      </footer>
    </section>
  </div>

  <script>
    const REFRESH_MS = 15000;
    const state = {
      selectedDate: "",
      selectedProfile: "",
      snapshot: null,
      editorDirty: false,
      settingsUnlocked: false,
      settingsTokenRequired: false,
      settingsToken: "",
      timer: null,
    };

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function formatDateTime(value) {
      if (!value) {
        return "-";
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return value;
      }
      return date.toLocaleString("ko-KR", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function formatRelativeSeconds(value) {
      if (value == null) {
        return "-";
      }
      if (value < 60) {
        return `${Math.round(value)}초 전`;
      }
      if (value < 3600) {
        return `${Math.round(value / 60)}분 전`;
      }
      if (value < 86400) {
        return `${Math.round(value / 3600)}시간 전`;
      }
      return `${Math.round(value / 86400)}일 전`;
    }

    function renderStats(snapshot) {
      const latestJob = snapshot.latest_job;
      const statsGrid = document.getElementById("stats-grid");
      const statusCounts = snapshot.status_counts || {};
      const cards = [
        {
          label: "전체 작업",
          value: snapshot.total_jobs,
          meta: `필터 결과 ${snapshot.filtered_jobs}건`,
        },
        {
          label: "오늘 작업",
          value: snapshot.today_jobs,
          meta: `기준일 ${snapshot.today_date || "-"}`,
        },
        {
          label: "최근 작업",
          value: latestJob ? (latestJob.status || "-") : "-",
          meta: latestJob ? formatDateTime(latestJob.triggered_at) : "작업 없음",
        },
        {
          label: "상태 분포",
          value: Object.keys(statusCounts).length ? Object.entries(statusCounts).map(([key, count]) => `${key} ${count}`).join(" · ") : "-",
          meta: snapshot.previews_truncated ? "표시는 최근 일부만 노출" : "표시 제한 없음",
        },
      ];

      statsGrid.innerHTML = cards.map((card) => `
        <article class="panel stat-card">
          <div class="stat-label">${escapeHtml(card.label)}</div>
          <div class="stat-value">${escapeHtml(card.value)}</div>
          <div class="stat-meta">${escapeHtml(card.meta)}</div>
        </article>
      `).join("");
    }

    function renderRuntime(runtime) {
      const grid = document.getElementById("runtime-grid");
      const cards = [
        {
          label: "Bluetooth Backend",
          value: runtime.bluetooth_backend || "-",
          meta: runtime.bluetooth_target || "-",
        },
        {
          label: "Outputs",
          value: runtime.outputs_dir || "-",
          meta: `assets: ${runtime.assets_dir || "-"}`,
        },
        {
          label: "Cooldown",
          value: runtime.cooldown_seconds != null ? `${runtime.cooldown_seconds}s` : "-",
          meta: runtime.cooldown_mode || "-",
        },
        {
          label: "LLM Profiles",
          value: runtime.llm_profile_count || 0,
          meta: runtime.profile_names || "-",
        },
      ];

      grid.innerHTML = cards.map((card) => `
        <article class="panel runtime-card">
          <div class="runtime-label">${escapeHtml(card.label)}</div>
          <div class="stat-value">${escapeHtml(card.value)}</div>
          <div class="runtime-meta">${escapeHtml(card.meta)}</div>
        </article>
      `).join("");
    }

    function renderStatus(snapshot) {
      const service = snapshot.service || {};
      const pill = document.getElementById("service-pill");
      pill.className = `status-pill level-${escapeHtml(service.level || "unknown")}`;
      document.getElementById("service-pill-text").textContent = service.label || "unknown";
      document.getElementById("service-summary").textContent = service.summary || "상태 정보를 찾지 못했습니다.";
      document.getElementById("last-refresh").textContent = formatDateTime(snapshot.generated_at);
    }

    function renderDateChips(snapshot) {
      const row = document.getElementById("date-chips");
      const dates = snapshot.available_dates || [];
      row.innerHTML = dates.slice(0, 10).map((entry) => {
        const active = entry.date === state.selectedDate ? "active" : "";
        return `<button type="button" class="date-chip ${active}" data-date="${escapeHtml(entry.date)}">${escapeHtml(entry.date)} · ${escapeHtml(entry.count)}</button>`;
      }).join("");

      row.querySelectorAll(".date-chip").forEach((button) => {
        button.addEventListener("click", () => {
          state.selectedDate = button.dataset.date || "";
          document.getElementById("date-filter").value = state.selectedDate;
          fetchDashboard();
        });
      });
    }

    function renderPreviews(snapshot) {
      const previews = snapshot.previews || [];
      const grid = document.getElementById("preview-grid");
      const count = document.getElementById("preview-count");
      count.textContent = `${snapshot.filtered_jobs}건${state.selectedDate ? ` · ${state.selectedDate}` : ""}`;

      if (!previews.length) {
        grid.innerHTML = `<div class="empty-state">선택한 날짜에 표시할 프리뷰가 없습니다.</div>`;
        return;
      }

      grid.innerHTML = previews.map((preview, index) => {
        const image = preview.image_url
          ? `<a href="${escapeHtml(preview.image_url)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(preview.image_url)}" alt="${escapeHtml(preview.job_id)}"></a>`
          : `<div class="preview-fallback">미리보기 이미지가 없습니다.</div>`;

        const badges = [
          preview.llm_profile_name ? `<span class="badge">LLM ${escapeHtml(preview.llm_profile_name)}</span>` : "",
          preview.selected_tag ? `<span class="badge">태그 ${escapeHtml(preview.selected_tag)}</span>` : "",
          preview.trigger_source ? `<span class="badge">${escapeHtml(preview.trigger_source)}</span>` : "",
          preview.used_fallback ? `<span class="badge">fallback</span>` : "",
          preview.dry_run ? `<span class="badge">dry-run</span>` : "",
        ].filter(Boolean).join("");

        const footerRight = preview.asset_name ? `asset ${escapeHtml(preview.asset_name)}` : preview.error ? escapeHtml(preview.error) : "";
        return `
          <article class="panel preview-card" style="animation-delay:${index * 18}ms">
            ${image}
            <div class="preview-body">
              <div class="preview-head">
                <div class="preview-time">${escapeHtml(preview.triggered_time || "-")}</div>
                <div class="preview-status">${escapeHtml(preview.status || "-")}</div>
              </div>
              <div class="badge-row">${badges}</div>
              <p class="fortune">${escapeHtml(preview.fortune || "운세 텍스트 없음")}</p>
              <div class="preview-footer">
                <span>${escapeHtml(preview.triggered_date || "-")}</span>
                <span>${footerRight}</span>
              </div>
            </div>
          </article>
        `;
      }).join("");
    }

    function renderLogs(snapshot) {
      const logs = snapshot.logs || {};
      const meta = [];
      if (logs.path) {
        meta.push(`파일 ${logs.path}`);
      }
      if (logs.updated_at) {
        meta.push(`갱신 ${formatDateTime(logs.updated_at)}`);
      }
      if (logs.line_count != null) {
        meta.push(`표시 ${logs.line_count}줄`);
      }
      document.getElementById("log-meta").textContent = meta.join(" · ");
      document.getElementById("log-body").textContent = logs.tail_text || "로그 파일이 없습니다.";
    }

    function renderProfiles(snapshot) {
      const container = document.getElementById("llm-profiles");
      const profiles = snapshot.llm_profiles || [];
      if (!profiles.length) {
        container.innerHTML = `<div class="empty-state">표시할 LLM 프로필이 없습니다.</div>`;
        return;
      }

      container.innerHTML = profiles.map((profile) => {
        const tags = (profile.tags || []).map((tag) => `<span class="badge">${escapeHtml(tag.name)} · ${escapeHtml(tag.asset_count)}</span>`).join("");
        const models = (profile.models || []).map((model) => `<span class="badge">${escapeHtml(model.name)} · ${escapeHtml(model.model)}${model.api_key_configured ? " · key" : ""}</span>`).join("");

        return `
          <article class="profile-card">
            <div class="profile-head">
              <h3 class="profile-name">${escapeHtml(profile.name)}</h3>
              <span class="badge">weight ${escapeHtml(profile.weight)}</span>
            </div>
            <div class="keyvals">
              <div class="keyval">
                <div class="keyval-label">Endpoint</div>
                <div class="keyval-value">${escapeHtml(profile.endpoint)}</div>
              </div>
              <div class="keyval">
                <div class="keyval-label">Model</div>
                <div class="keyval-value">${escapeHtml(profile.model)}</div>
              </div>
              <div class="keyval">
                <div class="keyval-label">Response Keys</div>
                <div class="keyval-value">${escapeHtml(profile.response_json_key)} / ${escapeHtml(profile.response_tag_key)}</div>
              </div>
              <div class="keyval">
                <div class="keyval-label">API Env</div>
                <div class="keyval-value">${escapeHtml(profile.api_key_env || "-")}</div>
              </div>
              <div class="keyval">
                <div class="keyval-label">API Key</div>
                <div class="keyval-value">${profile.api_key_configured ? "config" : "-"}</div>
              </div>
            </div>
            <div class="badge-row">${models || `<span class="badge">모델 후보 없음</span>`}</div>
            <div class="badge-row">${tags || `<span class="badge">태그 풀 없음</span>`}</div>
            <div>
              <div class="keyval-label">System Prompt</div>
              <pre class="prompt-box">${escapeHtml(profile.system_prompt || "")}</pre>
            </div>
            <div>
              <div class="keyval-label">Prompt</div>
              <pre class="prompt-box">${escapeHtml(profile.prompt || "")}</pre>
            </div>
            <div>
              <div class="keyval-label">Fallback</div>
              <div class="keyval-value">${escapeHtml(profile.fallback_text || "-")}</div>
            </div>
          </article>
        `;
      }).join("");
    }

    function renderArtifacts(snapshot) {
      const artifacts = snapshot.artifacts || {};
      const images = artifacts.images || [];
      const audio = artifacts.audio || [];
      const imageList = document.getElementById("artifact-image-list");
      const audioList = document.getElementById("artifact-audio-list");

      document.getElementById("artifact-image-count").textContent = `${images.length}개`;
      document.getElementById("artifact-audio-count").textContent = `${audio.length}개`;

      if (!images.length) {
        imageList.innerHTML = `<div class="empty-state">등록된 그림이 없습니다.</div>`;
      } else {
        imageList.innerHTML = images.map((artifact) => {
          const labels = (artifact.labels || []).length
            ? artifact.labels.map((label) => `<span class="badge">${escapeHtml(label)}</span>`).join("")
            : `<span class="badge">미연결</span>`;
          const media = artifact.url
            ? `<a href="${escapeHtml(artifact.url)}" target="_blank" rel="noreferrer"><img class="artifact-thumb" src="${escapeHtml(artifact.url)}" alt="${escapeHtml(artifact.name)}"></a>`
            : `<div class="artifact-thumb-fallback">파일 없음</div>`;
          return `
            <article class="artifact-card">
              ${media}
              <div class="artifact-name">${escapeHtml(artifact.name)}</div>
              <div class="badge-row">${labels}</div>
              <div class="artifact-meta">${escapeHtml(artifact.path || "")}</div>
            </article>
          `;
        }).join("");
      }

      if (!audio.length) {
        audioList.innerHTML = `<div class="empty-state">등록된 음악이 없습니다.</div>`;
      } else {
        audioList.innerHTML = audio.map((artifact) => {
          const labels = (artifact.labels || []).length
            ? artifact.labels.map((label) => `<span class="badge">${escapeHtml(label)}</span>`).join("")
            : `<span class="badge">미연결</span>`;
          const player = artifact.url
            ? `<audio class="artifact-audio" controls preload="none" src="${escapeHtml(artifact.url)}"></audio>`
            : `<div class="artifact-meta">파일을 찾지 못했습니다.</div>`;
          return `
            <article class="artifact-card">
              <div class="artifact-name">${escapeHtml(artifact.name)}</div>
              <div class="badge-row">${labels}</div>
              ${player}
              <div class="artifact-meta">${escapeHtml(artifact.path || "")}</div>
            </article>
          `;
        }).join("");
      }
    }

    function renderProfileControls(snapshot) {
      const profiles = snapshot.llm_profiles || [];
      const profileSelect = document.getElementById("profile-select");
      const uploadProfile = document.getElementById("upload-profile");
      const profileNames = profiles.map((profile) => profile.name);
      const currentProfile = profileNames.includes(state.selectedProfile)
        ? state.selectedProfile
        : (profiles[0] && profiles[0].name) || "";
      state.selectedProfile = currentProfile;

      const options = profiles.map((profile) => {
        const selected = profile.name === currentProfile ? "selected" : "";
        return `<option value="${escapeHtml(profile.name)}" ${selected}>${escapeHtml(profile.name)}</option>`;
      }).join("");
      profileSelect.innerHTML = options;
      uploadProfile.innerHTML = options;

      const profile = profiles.find((entry) => entry.name === currentProfile);
      if (profile && !state.editorDirty) {
        fillProfileEditor(profile);
      }
      applySettingsLock();
    }

    function fillProfileEditor(profile) {
      document.getElementById("edit-weight").value = profile.weight ?? 1;
      document.getElementById("edit-fallback").value = profile.fallback_text || "";
      document.getElementById("edit-system-prompt").value = profile.system_prompt || "";
      document.getElementById("edit-prompt").value = profile.prompt || "";
      renderModelEditor(profile.models || []);
      document.getElementById("upload-tag").value = ((profile.tags || [])[0] && profile.tags[0].name) || "";
    }

    function renderModelEditor(models) {
      const container = document.getElementById("model-editor");
      if (!models.length) {
        container.innerHTML = `<div class="empty-state">모델 후보가 없습니다.</div>`;
        applySettingsLock();
        return;
      }
      container.innerHTML = models.map((model, index) => `
        <div class="model-row" data-index="${index}">
          <label class="field">Name
            <input type="text" data-field="name" value="${escapeHtml(model.name || "")}">
          </label>
          <label class="field">Endpoint
            <input type="text" data-field="endpoint" value="${escapeHtml(model.endpoint || "")}">
          </label>
          <label class="field">Model
            <input type="text" data-field="model" value="${escapeHtml(model.model || "")}">
          </label>
          <label class="field">Temp
            <input type="number" step="0.05" data-field="temperature" value="${escapeHtml(model.temperature ?? 0.9)}">
          </label>
          <label class="field">Tokens
            <input type="number" step="1" data-field="max_tokens" value="${escapeHtml(model.max_tokens ?? 120)}">
          </label>
          <label class="field">Timeout
            <input type="number" step="1" data-field="timeout_seconds" value="${escapeHtml(model.timeout_seconds ?? 25)}">
          </label>
        </div>
      `).join("");
      applySettingsLock();
    }

    function selectedProfile() {
      const profiles = (state.snapshot && state.snapshot.llm_profiles) || [];
      return profiles.find((profile) => profile.name === state.selectedProfile) || profiles[0] || null;
    }

    function collectProfilePayload() {
      const profile = selectedProfile();
      if (!profile) {
        throw new Error("선택된 프로필이 없습니다.");
      }
      const models = Array.from(document.querySelectorAll(".model-row")).map((row) => {
        const payload = {};
        row.querySelectorAll("[data-field]").forEach((input) => {
          const field = input.dataset.field;
          if (field === "temperature" || field === "timeout_seconds") {
            payload[field] = Number(input.value);
          } else if (field === "max_tokens") {
            payload[field] = Number.parseInt(input.value || "0", 10);
          } else {
            payload[field] = input.value;
          }
        });
        const original = (profile.models || [])[Number(row.dataset.index || 0)] || {};
        payload.enable_thinking = Boolean(original.enable_thinking);
        payload.api_key_env = original.api_key_env || payload.api_key_env || "";
        return payload;
      });
      return {
        name: profile.name,
        weight: Number(document.getElementById("edit-weight").value || 0),
        fallback_text: document.getElementById("edit-fallback").value,
        system_prompt: document.getElementById("edit-system-prompt").value,
        prompt: document.getElementById("edit-prompt").value,
        models,
      };
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.ok === false) {
        throw new Error(body.error || `${url} ${response.status}`);
      }
      return body;
    }

    function settingsCanEdit() {
      return !state.settingsTokenRequired || state.settingsUnlocked;
    }

    function syncSettingsAuth(snapshot) {
      const required = Boolean((snapshot.dashboard || {}).settings_token_required);
      state.settingsTokenRequired = required;
      if (!required) {
        state.settingsUnlocked = true;
        state.settingsToken = "";
      }
    }

    function settingsPayload(payload) {
      if (state.settingsTokenRequired) {
        payload.edit_token = state.settingsToken;
      }
      return payload;
    }

    function applySettingsLock() {
      const canEdit = settingsCanEdit();
      const profiles = (state.snapshot && state.snapshot.llm_profiles) || [];
      const lockPanel = document.getElementById("settings-lock-panel");
      const lockTitle = document.getElementById("settings-lock-title");
      const lockStatus = document.getElementById("settings-lock-status");
      const tokenInput = document.getElementById("settings-token");
      const unlockButton = document.getElementById("unlock-settings");

      lockPanel.style.display = state.settingsTokenRequired ? "grid" : "none";
      lockTitle.textContent = canEdit ? "권한 활성화됨" : "설정 잠김";
      lockStatus.textContent = canEdit
        ? "프롬프트와 프로필을 수정할 수 있습니다."
        : "수정 토큰을 입력하면 편집할 수 있습니다.";
      tokenInput.disabled = !state.settingsTokenRequired || canEdit;
      unlockButton.disabled = !state.settingsTokenRequired || canEdit;

      document.querySelectorAll("#profile-editor input, #profile-editor select, #profile-editor textarea").forEach((input) => {
        input.disabled = !canEdit;
      });
      document.getElementById("save-profile").disabled = !canEdit || !profiles.length;
      document.getElementById("add-profile").disabled = !canEdit;
      document.getElementById("delete-profile").disabled = !canEdit || profiles.length <= 1;
      document.getElementById("new-profile-name").disabled = !canEdit;
    }

    async function unlockSettings() {
      const status = document.getElementById("settings-lock-status");
      if (!state.settingsTokenRequired) {
        state.settingsUnlocked = true;
        applySettingsLock();
        return;
      }
      const tokenInput = document.getElementById("settings-token");
      const token = tokenInput.value.trim();
      if (!token) {
        status.textContent = "수정 토큰을 입력하세요.";
        return;
      }
      status.textContent = "토큰을 확인하는 중입니다.";
      try {
        await postJson("/api/config/unlock", {edit_token: token});
        state.settingsToken = token;
        state.settingsUnlocked = true;
        tokenInput.value = "";
        applySettingsLock();
      } catch (error) {
        state.settingsUnlocked = false;
        state.settingsToken = "";
        applySettingsLock();
        status.textContent = `잠금 해제 실패: ${error.message || error}`;
      }
    }

    function openDialog(id) {
      const dialog = document.getElementById(id);
      if (!dialog) {
        return;
      }
      dialog.classList.add("open");
      dialog.setAttribute("aria-hidden", "false");
      document.body.classList.add("dialog-open");
      const focusTarget = id === "settings-dialog" && state.settingsTokenRequired && !state.settingsUnlocked
        ? document.getElementById("settings-token")
        : dialog.querySelector("select, input, textarea, button");
      if (focusTarget) {
        window.setTimeout(() => focusTarget.focus(), 0);
      }
    }

    function closeDialog(id) {
      const dialog = document.getElementById(id);
      if (!dialog) {
        return;
      }
      dialog.classList.remove("open");
      dialog.setAttribute("aria-hidden", "true");
      if (!document.querySelector(".dialog-backdrop.open")) {
        document.body.classList.remove("dialog-open");
      }
    }

    function closeOpenDialog() {
      document.querySelectorAll(".dialog-backdrop.open").forEach((dialog) => {
        closeDialog(dialog.id);
      });
    }

    async function requestPrint() {
      const button = document.getElementById("print-button");
      const status = document.getElementById("print-status");
      button.disabled = true;
      status.textContent = "출력 요청을 보내는 중입니다.";
      try {
        const result = await postJson("/api/print", {note: "dashboard"});
        status.textContent = `큐 등록 완료 · ${result.request_id}`;
        await refreshLoop();
      } catch (error) {
        status.textContent = `실패: ${error.message || error}`;
      } finally {
        button.disabled = false;
      }
    }

    async function saveProfile() {
      const status = document.getElementById("profile-status");
      if (!settingsCanEdit()) {
        status.textContent = "수정 토큰으로 잠금 해제한 뒤 저장할 수 있습니다.";
        return;
      }
      status.textContent = "저장 중입니다.";
      try {
        const result = await postJson("/api/config/llm-profile", settingsPayload(collectProfilePayload()));
        state.editorDirty = false;
        status.textContent = result.service_restart && result.service_restart.ok
          ? "저장 완료. 프린터 서비스를 재시작했습니다."
          : `저장 완료. 재시작 확인 필요${result.service_restart && result.service_restart.error ? `: ${result.service_restart.error}` : ""}`;
        await fetchDashboard();
      } catch (error) {
        status.textContent = `저장 실패: ${error.message || error}`;
      }
    }

    async function createProfile() {
      const status = document.getElementById("profile-status");
      if (!settingsCanEdit()) {
        status.textContent = "수정 토큰으로 잠금 해제한 뒤 추가할 수 있습니다.";
        return;
      }
      const nameInput = document.getElementById("new-profile-name");
      const profileName = nameInput.value.trim();
      if (!profileName) {
        status.textContent = "새 프로필 이름을 입력하세요.";
        return;
      }
      status.textContent = "프로필을 추가하는 중입니다.";
      try {
        const result = await postJson("/api/config/llm-profile/create", settingsPayload({
          name: profileName,
          source_name: state.selectedProfile,
        }));
        state.selectedProfile = result.profile || profileName;
        state.editorDirty = false;
        nameInput.value = "";
        status.textContent = result.service_restart && result.service_restart.ok
          ? "프로필 추가 완료. 프린터 서비스를 재시작했습니다."
          : "프로필 추가 완료.";
        await fetchDashboard();
      } catch (error) {
        status.textContent = `프로필 추가 실패: ${error.message || error}`;
      }
    }

    async function deleteProfile() {
      const status = document.getElementById("profile-status");
      if (!settingsCanEdit()) {
        status.textContent = "수정 토큰으로 잠금 해제한 뒤 삭제할 수 있습니다.";
        return;
      }
      const profile = selectedProfile();
      if (!profile) {
        status.textContent = "삭제할 프로필이 없습니다.";
        return;
      }
      if (!window.confirm(`프로필 "${profile.name}"을 삭제할까요?`)) {
        return;
      }
      status.textContent = "프로필을 삭제하는 중입니다.";
      try {
        const result = await postJson("/api/config/llm-profile/delete", settingsPayload({
          name: profile.name,
        }));
        state.selectedProfile = result.selected_profile || "";
        state.editorDirty = false;
        status.textContent = result.service_restart && result.service_restart.ok
          ? "프로필 삭제 완료. 프린터 서비스를 재시작했습니다."
          : "프로필 삭제 완료.";
        await fetchDashboard();
      } catch (error) {
        status.textContent = `프로필 삭제 실패: ${error.message || error}`;
      }
    }

    function readSelectedFileAsBase64() {
      const input = document.getElementById("asset-file");
      const file = input.files && input.files[0];
      if (!file) {
        return Promise.reject(new Error("업로드할 파일을 선택하세요."));
      }
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
        reader.onload = () => {
          const value = String(reader.result || "");
          const commaIndex = value.indexOf(",");
          resolve({
            file,
            content_base64: commaIndex >= 0 ? value.slice(commaIndex + 1) : value,
          });
        };
        reader.readAsDataURL(file);
      });
    }

    async function uploadAsset() {
      const status = document.getElementById("upload-status");
      status.textContent = "업로드 중입니다.";
      try {
        const selected = await readSelectedFileAsBase64();
        const kind = document.getElementById("upload-kind").value;
        const payload = {
          kind,
          filename: selected.file.name,
          content_base64: selected.content_base64,
          profile_name: document.getElementById("upload-profile").value,
          tag: document.getElementById("upload-tag").value,
          audio_target: document.getElementById("upload-audio-target").value,
        };
        const result = await postJson("/api/assets/upload", payload);
        const restartText = result.service_restart
          ? (result.service_restart.ok ? " · 서비스 재시작 완료" : ` · 재시작 확인 필요: ${result.service_restart.error}`)
          : "";
        status.textContent = `업로드 완료 · ${result.filename}${restartText}`;
        state.editorDirty = false;
        await fetchDashboard();
      } catch (error) {
        status.textContent = `업로드 실패: ${error.message || error}`;
      }
    }

    async function fetchDashboard() {
      const query = state.selectedDate ? `?date=${encodeURIComponent(state.selectedDate)}` : "";
      const response = await fetch(`/api/dashboard${query}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`dashboard API ${response.status}`);
      }
      const snapshot = await response.json();
      state.snapshot = snapshot;
      syncSettingsAuth(snapshot);
      renderStatus(snapshot);
      renderStats(snapshot);
      renderRuntime(snapshot.runtime || {});
      renderDateChips(snapshot);
      renderPreviews(snapshot);
      renderLogs(snapshot);
      renderProfileControls(snapshot);
      renderProfiles(snapshot);
      renderArtifacts(snapshot);
    }

    async function refreshLoop() {
      try {
        await fetchDashboard();
      } catch (error) {
        document.getElementById("service-summary").textContent = `대시보드 로드 실패: ${error}`;
      }
    }

    function bindControls() {
      const input = document.getElementById("date-filter");
      document.getElementById("apply-filter").addEventListener("click", () => {
        state.selectedDate = input.value || "";
        refreshLoop();
      });
      document.getElementById("reset-filter").addEventListener("click", () => {
        state.selectedDate = "";
        input.value = "";
        refreshLoop();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          state.selectedDate = input.value || "";
          refreshLoop();
        }
      });
      document.getElementById("print-button").addEventListener("click", requestPrint);
      document.getElementById("unlock-settings").addEventListener("click", unlockSettings);
      document.getElementById("save-profile").addEventListener("click", saveProfile);
      document.getElementById("add-profile").addEventListener("click", createProfile);
      document.getElementById("delete-profile").addEventListener("click", deleteProfile);
      document.getElementById("upload-asset").addEventListener("click", uploadAsset);
      document.getElementById("settings-token").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          unlockSettings();
        }
      });
      document.getElementById("open-settings-dialog").addEventListener("click", () => {
        openDialog("settings-dialog");
      });
      document.getElementById("open-upload-dialog").addEventListener("click", () => {
        openDialog("upload-dialog");
      });
      document.querySelectorAll("[data-close-dialog]").forEach((button) => {
        button.addEventListener("click", () => closeDialog(button.dataset.closeDialog));
      });
      document.querySelectorAll(".dialog-backdrop").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
          if (event.target === dialog) {
            closeDialog(dialog.id);
          }
        });
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          closeOpenDialog();
        }
      });
      document.getElementById("profile-select").addEventListener("change", (event) => {
        state.selectedProfile = event.target.value || "";
        state.editorDirty = false;
        const profile = selectedProfile();
        if (profile) {
          fillProfileEditor(profile);
        }
      });
      document.getElementById("profile-editor").addEventListener("input", () => {
        if (settingsCanEdit()) {
          state.editorDirty = true;
        }
      });
    }

    bindControls();
    refreshLoop();
    state.timer = window.setInterval(refreshLoop, REFRESH_MS);
  </script>
</body>
</html>
"""


class DashboardSnapshotBuilder:
    def __init__(
        self,
        config: AppConfig,
        *,
        service_name: str = SERVICE_NAME,
        preview_limit: int = DEFAULT_PREVIEW_LIMIT,
        log_lines: int = DEFAULT_LOG_LINES,
        snapshot_cache_seconds: float = DEFAULT_SNAPSHOT_CACHE_SECONDS,
    ) -> None:
        self.config = config
        self.service_name = service_name
        self.preview_limit = preview_limit
        self.log_lines = log_lines
        self.snapshot_cache_seconds = max(0.0, float(snapshot_cache_seconds))
        self.jobs_dir = config.output.outputs_dir / "jobs"
        self.log_path = config.output.logs_dir / config.output.log_filename
        self._local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
        self._snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def build_snapshot(self, *, selected_date: str | None = None) -> dict[str, Any]:
        normalized_date = _normalize_date(selected_date)
        cache_key = normalized_date or ""
        if self.snapshot_cache_seconds > 0:
            cached = self._snapshot_cache.get(cache_key)
            now_monotonic = time.monotonic()
            if cached and now_monotonic - cached[0] <= self.snapshot_cache_seconds:
                return cached[1]

        snapshot = self._build_snapshot_uncached(normalized_date=normalized_date)
        if self.snapshot_cache_seconds > 0:
            self._snapshot_cache[cache_key] = (time.monotonic(), snapshot)
        return snapshot

    def clear_cache(self) -> None:
        self._snapshot_cache.clear()

    def _build_snapshot_uncached(self, *, normalized_date: str | None) -> dict[str, Any]:
        jobs = self._load_jobs()
        filtered_jobs = [
            job for job in jobs if normalized_date is None or job["triggered_date"] == normalized_date
        ]
        logs = self._read_log_snapshot()
        latest_job = jobs[0] if jobs else None
        today = datetime.now(self._local_timezone).date().isoformat()
        available_dates = [
            {"date": date, "count": count}
            for date, count in sorted(
                Counter(job["triggered_date"] for job in jobs).items(),
                reverse=True,
            )
        ]

        return {
            "generated_at": datetime.now(self._local_timezone).isoformat(),
            "selected_date": normalized_date or "",
            "today_date": today,
            "today_jobs": sum(1 for job in jobs if job["triggered_date"] == today),
            "total_jobs": len(jobs),
            "filtered_jobs": len(filtered_jobs),
            "previews_truncated": len(filtered_jobs) > self.preview_limit,
            "available_dates": available_dates,
            "status_counts": dict(Counter(job["status"] for job in jobs if job["status"])),
            "latest_job": latest_job,
            "runtime": self._build_runtime_summary(),
            "service": self._build_service_status(logs, latest_job=latest_job),
            "dashboard": {
                "settings_token_required": bool(self.config.dashboard.edit_token),
            },
            "logs": logs,
            "llm_profiles": self._serialize_llm_profiles(),
            "artifacts": self._serialize_artifacts(),
            "previews": filtered_jobs[: self.preview_limit],
        }

    def resolve_preview_image(self, job_id: str) -> Path | None:
        safe_job_id = job_id.strip()
        if not safe_job_id:
            return None
        job_dir = (self.jobs_dir / safe_job_id).resolve()
        if not _is_relative_to(job_dir, self.jobs_dir.resolve()) or not job_dir.is_dir():
            return None

        composed = job_dir / "composed-ticket.png"
        if composed.is_file():
            return composed

        for payload_name in ("selected-asset.json", "result.json"):
            payload = _read_json_file(job_dir / payload_name)
            raw_path = payload.get("asset_path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = (self.config.assets_dir / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if candidate.is_file() and (
                _is_relative_to(candidate, self.config.assets_dir.resolve())
                or _is_relative_to(candidate, job_dir)
            ):
                return candidate
        return None

    def resolve_asset_file(self, asset_ref: str) -> Path | None:
        raw_ref = unquote(asset_ref).strip().lstrip("/")
        if not raw_ref:
            return None
        relative_path = Path(raw_ref)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return None
        candidate = (self.config.assets_dir / relative_path).resolve()
        assets_root = self.config.assets_dir.resolve()
        if not _is_relative_to(candidate, assets_root) or not candidate.is_file():
            return None
        if candidate.suffix.lower() not in IMAGE_EXTENSIONS | AUDIO_EXTENSIONS:
            return None
        return candidate

    def _serialize_artifacts(self) -> dict[str, list[dict[str, Any]]]:
        image_paths: dict[str, Path] = {}
        image_labels: dict[str, set[str]] = {}
        audio_paths: dict[str, Path] = {}
        audio_labels: dict[str, set[str]] = {}

        def add_entry(
            paths: dict[str, Path],
            labels: dict[str, set[str]],
            path: Path,
            label: str | None = None,
        ) -> None:
            key = self._asset_key(path)
            paths.setdefault(key, path)
            if label:
                labels.setdefault(key, set()).add(label)

        if self.config.assets_dir.is_dir():
            for path in sorted(self.config.assets_dir.rglob("*")):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                if suffix in IMAGE_EXTENSIONS:
                    add_entry(image_paths, image_labels, path)
                elif suffix in AUDIO_EXTENSIONS:
                    add_entry(audio_paths, audio_labels, path)

        for profile in self.config.llm.profiles:
            for tag_name, asset_paths in profile.tags.items():
                for asset_path in asset_paths:
                    add_entry(
                        image_paths,
                        image_labels,
                        asset_path,
                        f"{profile.name} / {tag_name}",
                    )

        for launch_sound in self.config.audio.launch_sounds:
            add_entry(
                audio_paths,
                audio_labels,
                launch_sound.file,
                f"출력 중 반복 재생 · weight {launch_sound.weight:g}",
            )

        event_audio = (
            ("printer_connected_file", "프린터 연결", self.config.audio.printer_connected_file),
            ("printer_failed_file", "프린터 실패", self.config.audio.printer_failed_file),
            ("print_completed_file", "출력 완료", self.config.audio.print_completed_file),
        )
        for _, label, audio_path in event_audio:
            if audio_path is not None:
                add_entry(audio_paths, audio_labels, audio_path, label)

        images = [
            self._serialize_asset_entry(path, labels=image_labels.get(key, set()), kind="image")
            for key, path in image_paths.items()
        ]
        audio = [
            self._serialize_asset_entry(path, labels=audio_labels.get(key, set()), kind="audio")
            for key, path in audio_paths.items()
        ]
        images.sort(key=lambda entry: (not entry["registered"], entry["name"]))
        audio.sort(key=lambda entry: (not entry["registered"], entry["name"]))
        return {"images": images, "audio": audio}

    def _serialize_asset_entry(
        self,
        path: Path,
        *,
        labels: set[str],
        kind: str,
    ) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        exists = resolved.is_file()
        relative_path = self._asset_relative_path(resolved)
        url = f"/asset/{quote(relative_path, safe='/')}" if exists and relative_path else ""
        size_bytes = resolved.stat().st_size if exists else None
        return {
            "kind": kind,
            "name": resolved.name,
            "path": relative_path or str(resolved),
            "url": url,
            "exists": exists,
            "size_bytes": size_bytes,
            "registered": bool(labels),
            "labels": sorted(labels),
        }

    def _asset_key(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        return self._asset_relative_path(resolved) or str(resolved)

    def _asset_relative_path(self, path: Path) -> str | None:
        assets_root = self.config.assets_dir.resolve()
        if not _is_relative_to(path, assets_root):
            return None
        return path.relative_to(assets_root).as_posix()

    def _load_jobs(self) -> list[dict[str, Any]]:
        if not self.jobs_dir.is_dir():
            return []

        jobs: list[dict[str, Any]] = []
        for job_dir in sorted(
            (path for path in self.jobs_dir.iterdir() if path.is_dir()),
            reverse=True,
        ):
            summary = self._load_job_summary(job_dir)
            if summary is not None:
                jobs.append(summary)
        return jobs

    def _load_job_summary(self, job_dir: Path) -> dict[str, Any] | None:
        result_payload = _read_json_file(job_dir / "result.json")
        input_payload = _read_json_file(job_dir / "input.json")
        asset_payload = _read_json_file(job_dir / "selected-asset.json")
        llm_payload = _read_json_file(job_dir / "selected-llm-profile.json")
        llm_tag_payload = _read_json_file(job_dir / "llm-tag.json")

        triggered_at = (
            _parse_datetime(result_payload.get("triggered_at"), self._local_timezone)
            or _parse_datetime(input_payload.get("triggered_at"), self._local_timezone)
            or _parse_datetime_from_job_id(job_dir.name, self._local_timezone)
        )
        if triggered_at is None:
            return None

        selected_tag = (
            _first_text(
                result_payload.get("selected_tag"),
                _read_text_file(job_dir / "tag.txt"),
                llm_tag_payload.get("tag"),
                asset_payload.get("selected_tag"),
            )
            or ""
        )
        llm_profile_name = _first_text(
            result_payload.get("llm_profile_name"),
            llm_payload.get("profile_name"),
        )
        asset_path = _first_text(
            asset_payload.get("asset_path"),
            result_payload.get("asset_path"),
        )
        image_path = self.resolve_preview_image(job_dir.name)
        error_message = _first_text(result_payload.get("error"))

        return {
            "job_id": job_dir.name,
            "triggered_at": triggered_at.isoformat(),
            "triggered_date": triggered_at.date().isoformat(),
            "triggered_time": triggered_at.strftime("%H:%M:%S"),
            "status": _first_text(result_payload.get("status")) or "unknown",
            "fortune": _read_text_file(job_dir / "fortune.txt"),
            "llm_profile_name": llm_profile_name or "",
            "selected_tag": selected_tag,
            "trigger_source": _first_text(input_payload.get("trigger_source")) or "",
            "asset_name": Path(asset_path).name if asset_path else "",
            "used_fallback": bool(result_payload.get("used_fallback", False)),
            "dry_run": bool(result_payload.get("dry_run", False)),
            "error": error_message or "",
            "image_url": f"/preview/{quote(job_dir.name, safe='')}" if image_path else "",
        }

    def _build_runtime_summary(self) -> dict[str, Any]:
        profile_names = [profile.name for profile in self.config.llm.profiles]
        return {
            "assets_dir": str(self.config.assets_dir),
            "outputs_dir": str(self.config.output.outputs_dir),
            "bluetooth_backend": self.config.bluetooth.backend,
            "bluetooth_target": self.config.bluetooth.mac_address,
            "cooldown_seconds": self.config.cooldown_seconds,
            "cooldown_mode": (
                "trigger accepted 즉시 쿨다운"
                if self.config.cooldown_on_trigger
                else "완료 후 쿨다운"
            ),
            "llm_profile_count": len(profile_names),
            "profile_names": ", ".join(profile_names),
        }

    def _build_service_status(
        self,
        logs: dict[str, Any],
        *,
        latest_job: dict[str, Any] | None,
    ) -> dict[str, Any]:
        systemd_status = self._read_systemd_status()
        log_age_seconds = logs.get("age_seconds")
        last_log_message = logs.get("last_message") or ""
        latest_job_bits: list[str] = []
        if latest_job is not None:
            latest_job_bits.append(
                f"최근 작업 {latest_job['status']} @ {latest_job['triggered_at']}"
            )
        if last_log_message:
            latest_job_bits.append(f"최근 로그: {last_log_message}")

        if systemd_status["available"]:
            active_state = systemd_status.get("active_state", "")
            sub_state = systemd_status.get("sub_state", "")
            if active_state == "active":
                summary = f"systemd가 {active_state}/{sub_state or '-'} 상태입니다."
                if log_age_seconds is not None:
                    summary += f" 로그 갱신은 {int(log_age_seconds)}초 전입니다."
                return {
                    "level": "healthy",
                    "label": "running",
                    "summary": summary,
                    "log_age_seconds": log_age_seconds,
                    "last_log_message": last_log_message,
                    "systemd": systemd_status,
                }
            if active_state in {"failed", "inactive", "deactivating"}:
                details = " ".join(latest_job_bits).strip()
                summary = f"systemd가 {active_state}/{sub_state or '-'} 상태입니다."
                if details:
                    summary += f" {details}"
                return {
                    "level": "inactive" if active_state == "inactive" else "error",
                    "label": active_state,
                    "summary": summary,
                    "log_age_seconds": log_age_seconds,
                    "last_log_message": last_log_message,
                    "systemd": systemd_status,
                }

        if log_age_seconds is not None and log_age_seconds <= LOG_STALE_SECONDS:
            summary = f"최근 로그가 {int(log_age_seconds)}초 전에 갱신되었습니다."
            if latest_job_bits:
                summary += f" {' '.join(latest_job_bits)}"
            return {
                "level": "healthy",
                "label": "recent-log",
                "summary": summary,
                "log_age_seconds": log_age_seconds,
                "last_log_message": last_log_message,
                "systemd": systemd_status,
            }

        if log_age_seconds is not None:
            summary = f"로그 갱신이 {int(log_age_seconds)}초 이상 멈춰 있습니다."
            if latest_job_bits:
                summary += f" {' '.join(latest_job_bits)}"
            return {
                "level": "stale",
                "label": "stale",
                "summary": summary,
                "log_age_seconds": log_age_seconds,
                "last_log_message": last_log_message,
                "systemd": systemd_status,
            }

        summary = "서비스 상태를 판별할 로그나 systemd 정보를 찾지 못했습니다."
        if latest_job_bits:
            summary += f" {' '.join(latest_job_bits)}"
        return {
            "level": "unknown",
            "label": "unknown",
            "summary": summary,
            "log_age_seconds": log_age_seconds,
            "last_log_message": last_log_message,
            "systemd": systemd_status,
        }

    def _read_systemd_status(self) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    self.service_name,
                    "--property=LoadState,ActiveState,SubState,MainPID,UnitFileState",
                    "--no-page",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except FileNotFoundError:
            return {
                "available": False,
                "reason": "systemctl not found",
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": str(exc),
            }

        if result.returncode != 0:
            return {
                "available": False,
                "reason": (result.stderr or result.stdout or "").strip(),
            }

        payload: dict[str, str] = {}
        for raw_line in result.stdout.splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            payload[key.strip()] = value.strip()

        load_state = payload.get("LoadState", "")
        return {
            "available": load_state not in {"", "not-found"},
            "load_state": load_state,
            "active_state": payload.get("ActiveState", ""),
            "sub_state": payload.get("SubState", ""),
            "main_pid": payload.get("MainPID", ""),
            "unit_file_state": payload.get("UnitFileState", ""),
        }

    def _read_log_snapshot(self) -> dict[str, Any]:
        if not self.log_path.is_file():
            return {
                "path": str(self.log_path),
                "exists": False,
                "updated_at": "",
                "age_seconds": None,
                "line_count": 0,
                "tail_text": "",
                "last_message": "",
            }

        tail_lines: deque[str] = deque(maxlen=self.log_lines)
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                tail_lines.append(line.rstrip("\n"))

        updated_at = datetime.fromtimestamp(
            self.log_path.stat().st_mtime,
            tz=self._local_timezone,
        )
        last_line = tail_lines[-1] if tail_lines else ""
        return {
            "path": str(self.log_path),
            "exists": True,
            "updated_at": updated_at.isoformat(),
            "age_seconds": max(
                0.0,
                (datetime.now(self._local_timezone) - updated_at).total_seconds(),
            ),
            "line_count": len(tail_lines),
            "tail_text": "\n".join(tail_lines),
            "last_message": _extract_log_message(last_line),
        }

    def _serialize_llm_profiles(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for profile in self.config.llm.profiles:
            profiles.append(
                {
                    "name": profile.name,
                    "weight": profile.weight,
                    "endpoint": profile.endpoint,
                    "model": profile.model,
                    "response_json_key": profile.response_json_key,
                    "response_tag_key": profile.response_tag_key,
                    "api_key_configured": bool(profile.api_key),
                    "api_key_env": profile.api_key_env or "",
                    "models": [
                        {
                            "name": model.name,
                            "endpoint": model.endpoint,
                            "model": model.model,
                            "enable_thinking": model.enable_thinking,
                            "api_key_configured": bool(model.api_key),
                            "api_key_env": model.api_key_env or "",
                            "temperature": model.temperature,
                            "max_tokens": model.max_tokens,
                            "timeout_seconds": model.timeout_seconds,
                        }
                        for model in profile.models
                    ],
                    "system_prompt": profile.system_prompt,
                    "prompt": profile.prompt,
                    "fallback_text": profile.fallback_text,
                    "tags": [
                        {
                            "name": tag_name,
                            "asset_count": len(asset_paths),
                            "assets": [path.name for path in asset_paths],
                        }
                        for tag_name, asset_paths in sorted(profile.tags.items())
                    ],
                }
            )
        return profiles


class _DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True


def serve_dashboard(
    config: AppConfig,
    *,
    config_path: Path | None = None,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
) -> None:
    builder = DashboardSnapshotBuilder(config)
    runtime_config_path = config_path.expanduser().resolve() if config_path else None

    def reload_builder() -> None:
        nonlocal builder
        if runtime_config_path is None:
            return
        builder = DashboardSnapshotBuilder(load_config(runtime_config_path))

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CallrooDashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802
            self._handle_request(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle_request(send_body=False)

        def do_POST(self) -> None:  # noqa: N802
            self._handle_post()

        def log_message(self, format: str, *args: Any) -> None:
            LOGGER.debug("%s - %s", self.address_string(), format % args)

        def _handle_request(self, *, send_body: bool) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_DASHBOARD_HTML, send_body=send_body)
                return
            if parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                selected_date = query.get("date", [""])[0]
                self._send_json(
                    builder.build_snapshot(selected_date=selected_date),
                    send_body=send_body,
                )
                return
            if parsed.path.startswith("/preview/"):
                job_id = parsed.path.removeprefix("/preview/")
                image_path = builder.resolve_preview_image(job_id)
                if image_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "preview not found")
                    return
                self._send_file(image_path, send_body=send_body)
                return
            if parsed.path.startswith("/asset/"):
                asset_ref = parsed.path.removeprefix("/asset/")
                asset_path = builder.resolve_asset_file(asset_ref)
                if asset_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
                    return
                self._send_file(asset_path, send_body=send_body)
                return
            if parsed.path == "/health":
                self._send_json({"ok": True}, send_body=send_body)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def _handle_post(self) -> None:
            parsed = urlparse(self.path)
            try:
                payload = self._read_json_body()
                if parsed.path == "/api/print":
                    result = _queue_dashboard_print(builder.config, payload)
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path == "/api/config/unlock":
                    _verify_dashboard_edit_token(builder.config, payload)
                    self._send_json({"ok": True, "unlocked": True}, send_body=True)
                    return
                if parsed.path == "/api/config/llm-profile":
                    if runtime_config_path is None:
                        self._send_json(
                            {"ok": False, "error": "config path is not writable from dashboard"},
                            send_body=True,
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    _verify_dashboard_edit_token(builder.config, payload)
                    result = _update_llm_profile_config(runtime_config_path, payload)
                    result["service_restart"] = _restart_printer_service()
                    reload_builder()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path == "/api/config/llm-profile/create":
                    if runtime_config_path is None:
                        self._send_json(
                            {"ok": False, "error": "config path is not writable from dashboard"},
                            send_body=True,
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    _verify_dashboard_edit_token(builder.config, payload)
                    result = _create_llm_profile_config(runtime_config_path, payload)
                    result["service_restart"] = _restart_printer_service()
                    reload_builder()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path == "/api/config/llm-profile/delete":
                    if runtime_config_path is None:
                        self._send_json(
                            {"ok": False, "error": "config path is not writable from dashboard"},
                            send_body=True,
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    _verify_dashboard_edit_token(builder.config, payload)
                    result = _delete_llm_profile_config(runtime_config_path, payload)
                    result["service_restart"] = _restart_printer_service()
                    reload_builder()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path == "/api/assets/upload":
                    if runtime_config_path is None:
                        self._send_json(
                            {"ok": False, "error": "config path is not writable from dashboard"},
                            send_body=True,
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return
                    result = _upload_asset(runtime_config_path, builder.config, payload)
                    if result.get("config_updated"):
                        result["service_restart"] = _restart_printer_service()
                    reload_builder()
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "not found")
            except ValueError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    send_body=True,
                    status=HTTPStatus.BAD_REQUEST,
                )
            except PermissionError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    send_body=True,
                    status=HTTPStatus.FORBIDDEN,
                )
            except Exception as exc:
                LOGGER.exception("Dashboard POST failed")
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    send_body=True,
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def _send_html(self, payload: str, *, send_body: bool) -> None:
            encoded = payload.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if send_body:
                self.wfile.write(encoded)

        def _read_json_body(self) -> dict[str, Any]:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if content_length <= 0:
                return {}
            if content_length > MAX_UPLOAD_BYTES * 2:
                raise ValueError("request body too large")
            raw_body = self.rfile.read(content_length)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(
            self,
            payload: dict[str, Any],
            *,
            send_body: bool,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if send_body:
                self.wfile.write(encoded)

        def _send_file(self, path: Path, *, send_body: bool) -> None:
            payload = path.read_bytes()
            mime_type, _ = mimetypes.guess_type(path.name)
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                mime_type or "application/octet-stream",
            )
            self.send_header("Cache-Control", "public, max-age=60")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body:
                self.wfile.write(payload)

    with _DashboardServer((host, port), DashboardHandler) as server:
        LOGGER.info(
            "Dashboard listening on http://%s:%s",
            host,
            port,
        )
        server.serve_forever()


def detect_service_config_path(service_name: str = SERVICE_NAME) -> Path | None:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service_name,
                "--property=ExecStart,WorkingDirectory,LoadState",
                "--no-page",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    payload: dict[str, str] = {}
    for raw_line in result.stdout.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        payload[key.strip()] = value.strip()

    if payload.get("LoadState") in {"", "not-found"}:
        return None

    exec_start = payload.get("ExecStart", "")
    detected = _extract_config_path_from_execstart(exec_start)
    if detected is not None and detected.is_file():
        return detected

    working_dir = payload.get("WorkingDirectory", "").strip()
    if working_dir:
        candidate = Path(working_dir).expanduser().resolve() / "config.json"
        if candidate.is_file():
            return candidate
    return None


def _queue_dashboard_print(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    requested_at = datetime.now().astimezone().isoformat()
    trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_payload = {
        "request_id": request_id,
        "requested_at": requested_at,
        "raw_input": "\n",
        "note": _optional_text(payload.get("note")) or "dashboard",
    }
    with trigger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trigger_payload, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "request_id": request_id,
        "queued_at": requested_at,
        "trigger_path": str(trigger_path),
    }


def _restart_printer_service(service_name: str = SERVICE_NAME) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", service_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=20.0,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return {"ok": False, "error": detail or f"exit status {result.returncode}"}
    return {"ok": True}


def _verify_dashboard_edit_token(config: AppConfig, payload: dict[str, Any]) -> None:
    expected = config.dashboard.edit_token
    if not expected:
        return
    supplied = _optional_text(payload.get("edit_token"))
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise PermissionError("invalid dashboard edit token")


def _update_llm_profile_config(config_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    profile_name = _required_text(payload.get("name"), "name")
    config_payload = _read_config_payload(config_path)
    profiles = config_payload.get("llm")
    if not isinstance(profiles, list):
        raise ValueError("config llm section must be a list")
    profile = _find_profile_payload(profiles, profile_name)

    for key in (
        "system_prompt",
        "prompt",
        "fallback_text",
        "current_time_hint_format",
        "current_time_hint_pre",
        "current_time_hint_post",
        "cleaned_examples_pre",
        "cleaned_examples_post",
        "response_json_key",
        "response_tag_key",
    ):
        if key in payload:
            profile[key] = str(payload.get(key) or "")

    if "weight" in payload:
        profile["weight"] = max(0.0, float(payload["weight"]))
    profile.pop("variation_hints", None)
    if "models" in payload:
        profile["models"] = _merge_model_payloads(
            profile.get("models"),
            payload.get("models"),
            profile,
        )
        if profile["models"]:
            primary = profile["models"][0]
            for key in (
                "endpoint",
                "model",
                "enable_thinking",
                "api_key",
                "api_key_env",
                "temperature",
                "max_tokens",
                "timeout_seconds",
            ):
                if key in primary:
                    profile[key] = primary[key]

    _write_config_payload(config_path, config_payload)
    return {"ok": True, "profile": profile_name}


def _create_llm_profile_config(config_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    profile_name = _required_text(payload.get("name"), "name")
    source_name = _optional_text(payload.get("source_name"))
    config_payload = _read_config_payload(config_path)
    profiles = config_payload.get("llm")
    if not isinstance(profiles, list):
        raise ValueError("config llm section must be a list")
    if any(
        isinstance(profile, dict) and str(profile.get("name", "")) == profile_name
        for profile in profiles
    ):
        raise ValueError(f"LLM profile already exists: {profile_name}")

    if source_name:
        source_profile = _find_profile_payload(profiles, source_name)
    elif profiles and isinstance(profiles[0], dict):
        source_profile = profiles[0]
    else:
        source_profile = _default_llm_profile_payload()

    new_profile = copy.deepcopy(source_profile)
    new_profile["name"] = profile_name
    profiles.append(new_profile)
    _write_config_payload(config_path, config_payload)
    return {"ok": True, "profile": profile_name}


def _delete_llm_profile_config(config_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    profile_name = _required_text(payload.get("name"), "name")
    config_payload = _read_config_payload(config_path)
    profiles = config_payload.get("llm")
    if not isinstance(profiles, list):
        raise ValueError("config llm section must be a list")
    if len(profiles) <= 1:
        raise ValueError("at least one LLM profile must remain")

    delete_index: int | None = None
    for index, profile in enumerate(profiles):
        if isinstance(profile, dict) and str(profile.get("name", "")) == profile_name:
            delete_index = index
            break
    if delete_index is None:
        raise ValueError(f"LLM profile not found: {profile_name}")

    profiles.pop(delete_index)
    selected_profile = ""
    for profile in profiles:
        if isinstance(profile, dict):
            selected_profile = str(profile.get("name", ""))
            if selected_profile:
                break
    _write_config_payload(config_path, config_payload)
    return {"ok": True, "profile": profile_name, "selected_profile": selected_profile}


def _default_llm_profile_payload() -> dict[str, Any]:
    return {
        "name": "new-profile",
        "weight": 1.0,
        "endpoint": "https://your-llm-endpoint.example/v1/",
        "model": "gpt-4.1-mini",
        "system_prompt": "당신은 짧고 절제된 한국어 운세를 쓰는 편집자다.",
        "prompt": '오늘의 운세를 작성해줘. 반드시 {"fortune":"...","tag":"..."} JSON 객체 하나만 반환해.',
        "response_json_key": "fortune",
        "response_tag_key": "tag",
        "enable_thinking": False,
        "api_key": None,
        "api_key_env": "SPARK_LLM_API_KEY",
        "temperature": 0.9,
        "max_tokens": 120,
        "timeout_seconds": 25.0,
        "fallback_text": "잠시 운세를 불러오지 못했어요. 다시 한 번 눌러보세요.",
        "tags": {},
        "models": [
            {
                "name": "primary",
                "endpoint": "https://your-llm-endpoint.example/v1/",
                "model": "gpt-4.1-mini",
                "enable_thinking": False,
                "api_key": None,
                "api_key_env": "SPARK_LLM_API_KEY",
                "temperature": 0.9,
                "max_tokens": 120,
                "timeout_seconds": 25.0,
            }
        ],
    }


def _upload_asset(
    config_path: Path,
    config: AppConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    kind = _required_text(payload.get("kind"), "kind")
    filename = _required_text(payload.get("filename"), "filename")
    content_base64 = _required_text(payload.get("content_base64"), "content_base64")
    target_path = _asset_upload_path(config.assets_dir, filename, kind)

    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    if not content:
        raise ValueError("uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("uploaded file is too large")

    target_path.write_bytes(content)
    config_payload = _read_config_payload(config_path)
    changed_config = False

    if kind == "image":
        profile_name = _optional_text(payload.get("profile_name"))
        tag_name = _optional_text(payload.get("tag"))
        if profile_name and tag_name:
            profiles = config_payload.get("llm")
            if not isinstance(profiles, list):
                raise ValueError("config llm section must be a list")
            profile = _find_profile_payload(profiles, profile_name)
            tags = profile.setdefault("tags", {})
            if not isinstance(tags, dict):
                raise ValueError("profile tags must be an object")
            tag_assets = tags.setdefault(tag_name, [])
            if not isinstance(tag_assets, list):
                raise ValueError("profile tag asset list must be a list")
            if target_path.name not in tag_assets:
                tag_assets.append(target_path.name)
                changed_config = True
    elif kind == "audio":
        audio_target = _optional_text(payload.get("audio_target"))
        if audio_target:
            audio = config_payload.setdefault("audio", {})
            if not isinstance(audio, dict):
                raise ValueError("audio section must be an object")
            if audio_target == "launch_sounds":
                launch_sounds = audio.setdefault("launch_sounds", [])
                if not isinstance(launch_sounds, list):
                    raise ValueError("audio.launch_sounds must be a list")
                launch_sounds.append(
                    {
                        "file": target_path.name,
                        "weight": max(0.0, float(payload.get("weight", 1.0))),
                    }
                )
            elif audio_target in {
                "printer_connected_file",
                "printer_failed_file",
                "print_completed_file",
            }:
                audio[audio_target] = target_path.name
            else:
                raise ValueError("unsupported audio_target")
            changed_config = True
    else:
        raise ValueError("kind must be image or audio")

    if changed_config:
        _write_config_payload(config_path, config_payload)

    return {
        "ok": True,
        "filename": target_path.name,
        "path": str(target_path),
        "config_updated": changed_config,
    }


def _read_config_payload(config_path: Path) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("config root must be an object")
    return payload


def _write_config_payload(config_path: Path, payload: dict[str, Any]) -> None:
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _find_profile_payload(
    profiles: list[Any],
    profile_name: str,
) -> dict[str, Any]:
    for profile in profiles:
        if isinstance(profile, dict) and str(profile.get("name", "")) == profile_name:
            return profile
    raise ValueError(f"LLM profile not found: {profile_name}")


def _merge_model_payloads(
    current_models: Any,
    incoming_models: Any,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(incoming_models, list):
        raise ValueError("models must be a list")
    current = current_models if isinstance(current_models, list) else []
    merged: list[dict[str, Any]] = []
    for index, incoming in enumerate(incoming_models):
        if not isinstance(incoming, dict):
            raise ValueError("model entries must be objects")
        existing = current[index] if index < len(current) and isinstance(current[index], dict) else {}
        model: dict[str, Any] = dict(existing)
        for key in (
            "name",
            "endpoint",
            "model",
            "api_key_env",
        ):
            if key in incoming:
                model[key] = str(incoming.get(key) or "")
        if "enable_thinking" in incoming:
            model["enable_thinking"] = bool(incoming["enable_thinking"])
        for key in ("temperature", "timeout_seconds"):
            if key in incoming:
                model[key] = float(incoming[key])
        if "max_tokens" in incoming:
            model["max_tokens"] = int(incoming["max_tokens"])
        model.setdefault("name", str(profile.get("model", f"model-{index + 1}")))
        model.setdefault("endpoint", str(profile.get("endpoint", "")))
        model.setdefault("model", str(profile.get("model", "")))
        model.setdefault("enable_thinking", bool(profile.get("enable_thinking", False)))
        model.setdefault("api_key", existing.get("api_key", profile.get("api_key")))
        model.setdefault("api_key_env", str(profile.get("api_key_env", "SPARK_LLM_API_KEY")))
        model.setdefault("temperature", float(profile.get("temperature", 0.9)))
        model.setdefault("max_tokens", int(profile.get("max_tokens", 120)))
        model.setdefault("timeout_seconds", float(profile.get("timeout_seconds", 25.0)))
        merged.append(model)
    if not merged:
        raise ValueError("models must not be empty")
    return merged


def _asset_upload_path(assets_dir: Path, filename: str, kind: str) -> Path:
    clean_name = Path(filename).name.strip()
    if not clean_name or clean_name in {".", ".."}:
        raise ValueError("invalid filename")
    suffix = Path(clean_name).suffix.lower()
    if kind == "image":
        allowed = IMAGE_EXTENSIONS
    elif kind == "audio":
        allowed = AUDIO_EXTENSIONS
    else:
        raise ValueError("kind must be image or audio")
    if suffix not in allowed:
        raise ValueError(f"unsupported {kind} extension: {suffix}")
    assets_dir.mkdir(parents=True, exist_ok=True)
    target_path = (assets_dir / clean_name).resolve()
    if not _is_relative_to(target_path, assets_dir.resolve()):
        raise ValueError("asset path escapes assets directory")
    return target_path


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            return text
    return None


def _read_text_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _parse_datetime(value: Any, local_timezone: timezone) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(local_timezone)


def _parse_datetime_from_job_id(job_id: str, local_timezone: timezone) -> datetime | None:
    pieces = job_id.split("-")
    if len(pieces) < 2:
        return None
    try:
        parsed = datetime.strptime(f"{pieces[0]}-{pieces[1]}", "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=local_timezone)


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _extract_log_message(line: str) -> str:
    if not line:
        return ""
    parts = line.split(": ", 1)
    if len(parts) == 2:
        return parts[1]
    return line


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _extract_config_path_from_execstart(exec_start: str) -> Path | None:
    if not exec_start:
        return None

    argv_marker = "argv[]="
    argv_index = exec_start.find(argv_marker)
    if argv_index == -1:
        return None

    argv_blob = exec_start[argv_index + len(argv_marker):]
    argv_blob = argv_blob.split(" ;", 1)[0].strip()
    try:
        argv = shlex.split(argv_blob)
    except ValueError:
        return None

    for index, token in enumerate(argv):
        if token == "--config" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser().resolve()
        if token.startswith("--config="):
            return Path(token.split("=", 1)[1]).expanduser().resolve()
    return None
