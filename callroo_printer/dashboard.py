from __future__ import annotations

import base64
import binascii
import copy
import hmac
import json
import logging
import mimetypes
import os
import shutil
import shlex
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as EMAIL_POLICY
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image

from callroo_printer.config import AppConfig, load_config
from callroo_printer.layout import compose_manual_print

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
MANUAL_PRINT_MAX_TEXT_CHARS = 1200
MANUAL_PRINT_IMAGE_MAX_BYTES = 8 * 1024 * 1024
MANUAL_UPLOADS_DIRNAME = "manual-uploads"
MANUAL_HISTORY_DIRNAME = "manual-history"
MANUAL_BORDER_STYLES = {"none", "thin", "thick", "double"}
MANUAL_TEXT_ALIGNS = {"left", "center", "right"}
MANUAL_TEXT_VERTICAL_ALIGNS = {"top", "center", "bottom"}
MANUAL_FONT_SIZE_MIN = 16
MANUAL_FONT_SIZE_MAX = 56
MANUAL_LABEL_WIDTH_MIN = 80
MANUAL_LABEL_HEIGHT_MIN = 56
MANUAL_LABEL_HEIGHT_MAX = 1200
MANUAL_CONTENT_MARGIN_DEFAULT = 16
MANUAL_CONTENT_MARGIN_MIN = 0
MANUAL_CONTENT_MARGIN_MAX = 96
MANUAL_IMAGE_SCALE_MIN = 25
MANUAL_IMAGE_SCALE_MAX = 300
MANUAL_IMAGE_ROTATION_MIN = -180
MANUAL_IMAGE_ROTATION_MAX = 180
REST_TEXT_PRINT_ENDPOINTS = {"/api/print/text", "/api/print-text"}
REST_IMAGE_PRINT_ENDPOINTS = {"/api/print/image", "/api/print-image"}

_DASHBOARD_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>callroo dashboard</title>
  <link rel="icon" type="image/png" href="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png">
  <link rel="apple-touch-icon" href="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png">
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

    .hero-title-row {
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 0 0 8px;
      min-width: 0;
    }

    .hero-mark {
      width: clamp(38px, 5vw, 58px);
      height: clamp(38px, 5vw, 58px);
      flex: 0 0 auto;
      border-radius: 999px;
      object-fit: cover;
      border: 2px solid rgba(38, 25, 14, 0.12);
      box-shadow: 0 10px 24px rgba(38, 25, 14, 0.18);
    }

    h1 {
      margin: 0;
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

    .status-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
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
      grid-template-columns: repeat(4, minmax(0, 1fr));
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

    button,
    .button-link {
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

    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 39px;
      text-align: center;
      text-decoration: none;
    }

    button.secondary,
    .button-link.secondary {
      color: var(--text);
      background: rgba(38, 25, 14, 0.08);
      box-shadow: none;
    }

    button.danger {
      color: white;
      background: var(--danger);
      box-shadow: 0 10px 24px rgba(159, 42, 32, 0.16);
    }

    button.danger {
      color: white;
      background: var(--danger);
      box-shadow: 0 10px 24px rgba(159, 42, 32, 0.16);
    }

    button:hover,
    .button-link:hover {
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
        <div class="hero-title-row">
          <img class="hero-mark" src="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png" alt="" aria-hidden="true" onerror="this.remove()">
          <h1>CALLROO PRINTER DASHBOARD</h1>
        </div>
        <div class="hero-subtitle">생성 결과, 상태, 로그를 한 화면에서</div>
        <p>출력 이력 프리뷰를 날짜로 걸러 보고, 현재 서비스 상태와 최근 로그, LLM 프롬프트 설정을 접이식 패널로 확인합니다. 기본 포트는 <strong>3001</strong>입니다.</p>
      </article>
      <aside class="panel status-rail">
        <div class="status-pills">
          <div id="service-pill" class="status-pill level-unknown">
            <span class="status-dot" aria-hidden="true"></span>
            <span id="service-pill-text">상태 확인 중</span>
          </div>
          <div id="bluetooth-pill" class="status-pill level-unknown">
            <span class="status-dot" aria-hidden="true"></span>
            <span id="bluetooth-pill-text">Bluetooth 확인 중</span>
          </div>
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
          <article class="control-card">
            <h2 class="control-title">수동 출력</h2>
            <a class="button-link secondary" href="/print">프린터 페이지</a>
            <div class="control-meta">문구나 그림 파일을 바로 합성해 출력합니다.</div>
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

    function bluetoothStatusLabel(status) {
      const labels = {
        connected: "연결됨",
        connecting: "연결 대기",
        retrying: "재시도 중",
        starting: "시작 중",
        disabled: "비활성",
        stopped: "중지됨",
        unknown: "확인 안 됨",
      };
      return labels[status] || status || "확인 안 됨";
    }

    function bluetoothStatusLevel(status) {
      if (status === "connected") {
        return "healthy";
      }
      if (status === "retrying" || status === "connecting" || status === "starting") {
        return "stale";
      }
      if (status === "disabled" || status === "stopped") {
        return "inactive";
      }
      return "unknown";
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

    function renderRuntime(runtime, bluetooth) {
      const grid = document.getElementById("runtime-grid");
      const bluetoothMeta = [
        bluetooth && bluetooth.message ? bluetooth.message : "",
        bluetooth && bluetooth.last_error ? `오류: ${bluetooth.last_error}` : "",
        bluetooth && Number(bluetooth.failure_count) > 0 ? `실패 ${bluetooth.failure_count}회` : "",
        bluetooth && bluetooth.updated_at ? `갱신 ${formatDateTime(bluetooth.updated_at)}` : "",
      ].filter(Boolean).join(" · ");
      const cards = [
        {
          label: "Bluetooth Status",
          value: bluetoothStatusLabel(bluetooth && bluetooth.status),
          meta: bluetoothMeta || "상태 보고 없음",
        },
        {
          label: "Bluetooth Backend",
          value: (bluetooth && bluetooth.backend) || runtime.bluetooth_backend || "-",
          meta: (bluetooth && bluetooth.mac_address) || runtime.bluetooth_target || "-",
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
      const bluetooth = snapshot.bluetooth || {};
      const pill = document.getElementById("service-pill");
      pill.className = `status-pill level-${escapeHtml(service.level || "unknown")}`;
      document.getElementById("service-pill-text").textContent = service.label || "unknown";
      const bluetoothPill = document.getElementById("bluetooth-pill");
      bluetoothPill.className = `status-pill level-${bluetoothStatusLevel(bluetooth.status)}`;
      document.getElementById("bluetooth-pill-text").textContent = `Bluetooth ${bluetoothStatusLabel(bluetooth.status)}`;
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
          ? `<a href="${escapeHtml(preview.image_url)}" target="_blank" rel="noreferrer"><img src="${escapeHtml(preview.image_url)}" alt="${escapeHtml(preview.job_id)}" loading="lazy" decoding="async"></a>`
          : `<div class="preview-fallback">미리보기 이미지가 없습니다.</div>`;

        const badges = [
          preview.llm_profile_name ? `<span class="badge">LLM ${escapeHtml(preview.llm_profile_name)}</span>` : "",
          preview.selected_tag ? `<span class="badge">태그 ${escapeHtml(preview.selected_tag)}</span>` : "",
          preview.trigger_source ? `<span class="badge">${escapeHtml(preview.trigger_source)}</span>` : "",
          preview.used_fallback ? `<span class="badge">fallback</span>` : "",
          preview.dry_run ? `<span class="badge">dry-run</span>` : "",
          preview.manual_print ? `<span class="badge">수동 출력</span>` : "",
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
      renderRuntime(snapshot.runtime || {}, snapshot.bluetooth || {});
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

    function removeBrokenHeroMarks() {
      document.querySelectorAll(".hero-mark").forEach((image) => {
        const removeIfBroken = () => {
          if (image.complete && image.naturalWidth === 0) {
            image.remove();
          }
        };
        image.addEventListener("error", () => image.remove());
        removeIfBroken();
        window.setTimeout(removeIfBroken, 100);
        window.setTimeout(removeIfBroken, 1000);
      });
    }

    removeBrokenHeroMarks();
    bindControls();
    refreshLoop();
    state.timer = window.setInterval(refreshLoop, REFRESH_MS);
  </script>
</body>
</html>
"""

_PRINTER_DASHBOARD_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>callroo manual printer</title>
  <link rel="icon" type="image/png" href="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png">
  <link rel="apple-touch-icon" href="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png">
  <style>
    :root {
      --bg: #f4efe8;
      --bg-strong: #e6ddd0;
      --panel: rgba(255, 251, 246, 0.88);
      --panel-strong: rgba(255, 251, 246, 0.97);
      --stroke: rgba(70, 52, 34, 0.14);
      --text: #26190e;
      --muted: #6d5a4c;
      --accent: #b64926;
      --accent-soft: rgba(182, 73, 38, 0.12);
      --danger: #9f2a20;
      --shadow: 0 18px 40px rgba(65, 45, 28, 0.12);
      --radius: 22px;
      --radius-sm: 14px;
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
        radial-gradient(circle at 12% 0%, rgba(182, 73, 38, 0.16), transparent 30%),
        radial-gradient(circle at 92% 10%, rgba(66, 105, 82, 0.16), transparent 28%),
        linear-gradient(180deg, #f8f3ec 0%, var(--bg) 46%, #efe6db 100%);
    }

    main {
      width: min(1280px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 56px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .topbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
      padding: 22px;
      margin-bottom: 16px;
    }

    .title-row {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }

    .hero-mark {
      width: 46px;
      height: 46px;
      flex: 0 0 auto;
      border-radius: 999px;
      object-fit: cover;
      border: 2px solid rgba(38, 25, 14, 0.12);
      box-shadow: 0 10px 24px rgba(38, 25, 14, 0.18);
    }

    h1 {
      margin: 0;
      font-size: clamp(26px, 4vw, 42px);
      line-height: 1;
      letter-spacing: 0;
    }

    .subtitle {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }

    .button-link,
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

    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 39px;
      text-align: center;
      text-decoration: none;
      white-space: nowrap;
    }

    button.secondary,
    .button-link.secondary {
      color: var(--text);
      background: rgba(38, 25, 14, 0.08);
      box-shadow: none;
    }

    button:hover,
    .button-link:hover {
      transform: translateY(-1px);
    }

    button:disabled,
    input:disabled,
    select:disabled,
    textarea:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    .printer-layout {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.85fr);
      gap: 16px;
      align-items: start;
    }

    .editor {
      padding: 18px;
      display: grid;
      gap: 14px;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    .form-grid.two {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    label.field {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }

    textarea,
    input[type="file"],
    input[type="range"],
    input[type="number"],
    select {
      width: 100%;
      font: inherit;
      font-size: 13px;
    }

    textarea,
    input[type="number"],
    select {
      border: 1px solid var(--stroke);
      border-radius: 10px;
      background: white;
      color: var(--text);
      padding: 9px 10px;
    }

    textarea {
      min-height: 210px;
      resize: vertical;
      line-height: 1.55;
      font-family: var(--font-ui);
    }

    .toggle-field {
      display: flex;
      min-height: 38px;
      align-items: center;
      gap: 8px;
      padding: 9px 10px;
      border: 1px solid var(--stroke);
      border-radius: 10px;
      background: white;
      color: var(--text);
    }

    .file-drop {
      display: grid;
      gap: 8px;
      padding: 12px;
      border: 1px dashed rgba(70, 52, 34, 0.24);
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.54);
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
    }

    .status {
      min-height: 1.5em;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .status.error {
      color: var(--danger);
    }

    .preview-panel {
      position: sticky;
      top: 16px;
      padding: 18px;
      display: grid;
      gap: 14px;
    }

    .preview-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }

    .preview-title {
      margin: 0;
      font-size: 18px;
      letter-spacing: -0.02em;
    }

    .preview-meta {
      color: var(--muted);
      font-size: 12px;
    }

    .paper-shell {
      display: grid;
      justify-items: center;
      padding: 18px;
      border-radius: var(--radius-sm);
      background: rgba(38, 25, 14, 0.06);
      overflow: auto;
    }

    .paper-preview {
      position: relative;
      width: 344px;
      height: 220px;
      min-height: 220px;
      color: #111;
      background: #fffdf8;
      box-shadow: 0 16px 32px rgba(38, 25, 14, 0.18);
      overflow: hidden;
      touch-action: none;
      user-select: none;
    }

    .paper-preview.border-none {
      border: 0;
      border-radius: 0;
    }

    .paper-preview.border-thin {
      border: 2px solid #111;
      border-radius: 12px;
    }

    .paper-preview.border-thick {
      border: 4px solid #111;
      border-radius: 12px;
    }

    .paper-preview.border-double {
      border: 6px double #111;
      border-radius: 12px;
    }

    .canvas-image-layer {
      position: absolute;
      inset: var(--content-margin, 16px);
      overflow: hidden;
      z-index: 1;
    }

    .canvas-text-layer {
      position: absolute;
      inset: var(--content-margin, 16px);
      overflow: hidden;
      z-index: 2;
    }

    .canvas-image-item {
      position: absolute;
      border: 1px solid transparent;
      transform-origin: center;
      cursor: grab;
      touch-action: none;
    }

    .canvas-image-item.selected {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(182, 73, 38, 0.14);
      z-index: 3;
    }

    .canvas-image-item:active {
      cursor: grabbing;
    }

    .canvas-image-item img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      filter: grayscale(1);
      pointer-events: none;
      user-select: none;
      -webkit-user-drag: none;
    }

    .canvas-image-item.crop img {
      object-fit: cover;
    }

    .canvas-image-handle,
    .label-resize-handle {
      position: absolute;
      display: none;
      width: 16px;
      height: 16px;
      border: 2px solid #fffdf8;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 4px 12px rgba(38, 25, 14, 0.22);
      touch-action: none;
    }

    .canvas-image-item.selected .canvas-image-handle {
      display: block;
    }

    .canvas-text-item {
      position: absolute;
      display: flex;
      flex-direction: column;
      padding: 0;
      border: 1px solid transparent;
      color: #111;
      font-weight: 700;
      line-height: 1.35;
      white-space: pre-line;
      word-break: keep-all;
      overflow-wrap: anywhere;
      overflow: hidden;
      cursor: grab;
      touch-action: none;
      user-select: none;
    }

    .canvas-text-item.selected {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(182, 73, 38, 0.14);
      z-index: 4;
    }

    .canvas-text-item:active {
      cursor: grabbing;
    }

    .canvas-text-content {
      width: 100%;
    }

    .canvas-text-item.align-left {
      text-align: left;
      align-items: flex-start;
    }

    .canvas-text-item.align-center {
      text-align: center;
      align-items: center;
    }

    .canvas-text-item.align-right {
      text-align: right;
      align-items: flex-end;
    }

    .canvas-text-item.valign-top {
      justify-content: flex-start;
    }

    .canvas-text-item.valign-center {
      justify-content: center;
    }

    .canvas-text-item.valign-bottom {
      justify-content: flex-end;
    }

    .canvas-text-handle {
      position: absolute;
      right: -9px;
      bottom: -9px;
      display: none;
      width: 16px;
      height: 16px;
      border: 2px solid #fffdf8;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 4px 12px rgba(38, 25, 14, 0.22);
      cursor: nwse-resize;
      touch-action: none;
    }

    .canvas-text-item.selected .canvas-text-handle {
      display: block;
    }

    .image-resize-handle {
      right: -9px;
      bottom: -9px;
      cursor: nwse-resize;
    }

    .image-rotate-handle {
      right: -9px;
      top: -9px;
      cursor: grab;
    }

    .label-resize-handle {
      display: block;
      right: 5px;
      bottom: 5px;
      z-index: 5;
      cursor: nwse-resize;
    }

    .manual-preview-text {
      position: absolute;
      inset: 16px;
      z-index: 2;
      display: none;
      align-items: center;
      justify-content: center;
      white-space: pre-line;
      word-break: keep-all;
      overflow-wrap: anywhere;
      line-height: 1.45;
      font-weight: 700;
      pointer-events: none;
    }

    .manual-preview-text.visible {
      display: flex;
    }

    .manual-preview-text.align-left {
      justify-content: flex-start;
      text-align: left;
    }

    .manual-preview-text.align-center {
      justify-content: center;
      text-align: center;
    }

    .manual-preview-text.align-right {
      justify-content: flex-end;
      text-align: right;
    }

    .image-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      min-height: 32px;
      align-items: center;
    }

    .image-chip {
      min-width: 0;
      max-width: 100%;
      padding: 7px 10px;
      border: 1px solid var(--stroke);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.72);
      box-shadow: none;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .image-chip.selected {
      color: var(--accent);
      border-color: rgba(182, 73, 38, 0.42);
      background: var(--accent-soft);
    }

    .image-tools {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
    }

    .image-tools .status {
      min-width: min(240px, 100%);
    }

    .image-geometry-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 10px;
      border: 1px solid rgba(70, 52, 34, 0.12);
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.5);
    }

    .image-tool-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .range-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .range-value {
      width: 44px;
      color: var(--muted);
      font-size: 12px;
      text-align: right;
      font-family: var(--font-mono);
    }

    .history-panel {
      margin-top: 16px;
      padding: 18px;
      display: grid;
      gap: 14px;
    }

    .history-head {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }

    .history-title {
      margin: 0;
      font-size: 18px;
      letter-spacing: -0.02em;
    }

    .history-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 12px;
    }

    .history-card {
      min-width: 0;
      display: grid;
      gap: 9px;
      padding: 10px;
      border: 1px solid var(--stroke);
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.64);
    }

    .history-thumb {
      width: 100%;
      aspect-ratio: 1 / 0.76;
      border-radius: 10px;
      border: 1px solid var(--stroke);
      background: #fffdf8;
      object-fit: contain;
    }

    .history-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      word-break: break-word;
    }

    .history-actions {
      display: flex;
      gap: 8px;
    }

    .history-actions a,
    .history-actions button {
      flex: 1;
      min-width: 0;
      padding: 8px 10px;
      font-size: 12px;
    }

    @media (max-width: 900px) {
      .topbar,
      .printer-layout {
        grid-template-columns: 1fr;
      }

      .preview-panel {
        position: static;
      }

      .form-grid {
        grid-template-columns: 1fr;
      }

      .image-geometry-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 640px) {
      main {
        width: min(100% - 20px, 1280px);
        padding-top: 16px;
      }

      .actions {
        align-items: stretch;
        flex-direction: column;
      }

      .actions button,
      .actions .button-link {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <main>
    <header class="panel topbar">
      <div>
        <div class="title-row">
          <img class="hero-mark" src="/asset/Gemini_Generated_Image_trohomtrohomtroh_top_left.png" alt="" aria-hidden="true" onerror="this.remove()">
          <h1>MANUAL PRINTER DASHBOARD</h1>
        </div>
        <div class="subtitle">문구와 그림 파일을 한 장의 프린터 출력물로 바로 큐에 등록합니다.</div>
      </div>
      <a class="button-link secondary" href="/">운영 대시보드</a>
    </header>

    <section class="printer-layout">
      <form id="manual-form" class="panel editor">
        <label class="field">문구
          <textarea id="manual-text" maxlength="1200" placeholder="출력할 문구를 입력하세요."></textarea>
        </label>
        <div class="image-tools">
          <div id="selected-text-status" class="status">문구를 입력하거나 텍스트 박스를 추가하세요.</div>
          <div class="image-tool-actions">
            <button id="add-text-box" type="button" class="secondary">텍스트 박스 추가</button>
            <button id="remove-selected-text" type="button" class="secondary">선택 텍스트 삭제</button>
          </div>
        </div>
        <div id="text-list" class="image-list"></div>
        <div class="file-drop">
          <label class="field">그림 파일
            <input id="manual-image" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" multiple>
          </label>
          <div class="status">PDF, DOCX, TXT 같은 문서 파일은 업로드하지 않습니다.</div>
          <div id="image-list" class="image-list"></div>
        </div>
        <div class="form-grid two">
          <label class="field">라벨 폭 (dot)
            <input id="label-width" type="number" min="80" max="344" step="4" value="344">
          </label>
          <label class="field">라벨 높이 (dot)
            <input id="label-height" type="number" min="56" max="1200" step="4" value="220">
          </label>
          <label class="field">내부 여백 (dot)
            <input id="content-margin" type="number" min="0" max="96" step="4" value="16">
          </label>
        </div>
        <div class="form-grid">
          <label class="field">테두리
            <select id="border-style">
              <option value="thin">얇은 테두리</option>
              <option value="thick">두꺼운 테두리</option>
              <option value="double">이중 테두리</option>
              <option value="none">테두리 없음</option>
            </select>
          </label>
          <label class="field">가로 정렬
            <select id="text-align">
              <option value="center">가운데</option>
              <option value="left">왼쪽</option>
              <option value="right">오른쪽</option>
            </select>
          </label>
          <label class="field">세로 정렬
            <select id="text-vertical-align">
              <option value="center">가운데</option>
              <option value="top">위</option>
              <option value="bottom">아래</option>
            </select>
          </label>
          <label class="field">글자 크기
            <div class="range-row">
              <input id="font-size" type="range" min="16" max="56" value="28">
              <span id="font-size-value" class="range-value">28px</span>
            </div>
          </label>
        </div>
        <div id="text-geometry-controls" class="image-geometry-grid">
          <label class="field">텍스트 X (dot)
            <input id="text-x" type="number" step="1" value="0">
          </label>
          <label class="field">텍스트 Y (dot)
            <input id="text-y" type="number" step="1" value="0">
          </label>
          <label class="field">텍스트 폭 (dot)
            <input id="text-width" type="number" min="8" step="1" value="0">
          </label>
          <label class="field">텍스트 높이 (dot)
            <input id="text-height" type="number" min="8" step="1" value="0">
          </label>
        </div>
        <div class="form-grid">
          <label class="field">선택 그림 확대
            <div class="range-row">
              <input id="image-scale" type="range" min="25" max="300" value="100">
              <span id="image-scale-value" class="range-value">100%</span>
            </div>
          </label>
          <label class="field">선택 그림 회전
            <div class="range-row">
              <input id="image-rotation" type="range" min="-180" max="180" step="15" value="0">
              <span id="image-rotation-value" class="range-value">0°</span>
            </div>
          </label>
          <label class="field">크롭
            <span class="toggle-field">
              <input id="image-crop" type="checkbox">
              <span>영역 채우기</span>
            </span>
          </label>
        </div>
        <div id="image-geometry-controls" class="image-geometry-grid">
          <label class="field">그림 X (dot)
            <input id="image-x" type="number" step="1" value="0">
          </label>
          <label class="field">그림 Y (dot)
            <input id="image-y" type="number" step="1" value="0">
          </label>
          <label class="field">그림 폭 (dot)
            <input id="image-width" type="number" min="8" step="1" value="0">
          </label>
          <label class="field">그림 높이 (dot)
            <input id="image-height" type="number" min="8" step="1" value="0">
          </label>
        </div>
        <div class="image-tools">
          <div id="selected-image-status" class="status">그림을 올리면 캔버스에서 이동·크기조절·회전할 수 있습니다.</div>
          <div class="image-tool-actions">
            <button id="fit-selected-image" type="button" class="secondary">라벨에 맞춤</button>
            <button id="remove-selected-image" type="button" class="secondary">선택 그림 삭제</button>
          </div>
        </div>
        <div class="actions">
          <div id="manual-status" class="status">문구나 그림 중 하나를 넣으면 출력할 수 있습니다.</div>
          <div>
            <button id="clear-manual" type="button" class="secondary">초기화</button>
            <button id="submit-manual" type="submit">출력 큐 등록</button>
          </div>
        </div>
      </form>

      <aside class="panel preview-panel">
        <div class="preview-head">
          <h2 class="preview-title">미리보기</h2>
          <div id="preview-meta" class="preview-meta">384dot paper</div>
        </div>
        <div class="paper-shell">
          <div id="paper-preview" class="paper-preview border-thin">
            <div id="canvas-image-layer" class="canvas-image-layer"></div>
            <div id="canvas-text-layer" class="canvas-text-layer"></div>
            <div id="label-resize-handle" class="label-resize-handle" title="라벨 크기 조절" aria-label="라벨 크기 조절"></div>
          </div>
        </div>
      </aside>
    </section>

    <section class="panel history-panel">
      <div class="history-head">
        <h2 class="history-title">수동 출력 이력</h2>
        <button id="refresh-history" type="button" class="secondary">새로고침</button>
      </div>
      <div id="manual-history-list" class="history-grid"></div>
      <div id="history-status" class="status">이력을 불러오는 중입니다.</div>
    </section>
  </main>

  <script>
    const allowedImageExtensions = new Set(["png", "jpg", "jpeg", "bmp", "gif", "webp"]);
    const maxImageBytes = 8 * 1024 * 1024;
    const defaultContentMargin = 16;
    const minImageSize = 8;
    const minVisibleImageDots = 8;
    const minTextSize = 8;
    const minVisibleTextDots = 8;
    const state = {
      images: [],
      selectedImageId: "",
      texts: [],
      selectedTextId: "",
      history: [],
      drag: null,
      nextImageIndex: 1,
      nextTextIndex: 1,
    };

    function clampNumber(value, min, max, fallback) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      return Math.max(min, Math.min(max, Math.round(parsed)));
    }

    function snapNumber(value, step = 4) {
      return Math.round(Number(value) / step) * step;
    }

    function clampSteppedNumber(value, min, max, fallback, step = 4) {
      return clampNumber(snapNumber(value, step), min, max, fallback);
    }

    function readManualOptions() {
      return {
        labelWidth: clampNumber(document.getElementById("label-width").value, 80, 344, 344),
        labelHeight: clampNumber(document.getElementById("label-height").value, 56, 1200, 220),
        contentMargin: clampSteppedNumber(document.getElementById("content-margin").value, 0, 96, defaultContentMargin),
        borderStyle: document.getElementById("border-style").value,
        textAlign: document.getElementById("text-align").value,
        textVerticalAlign: document.getElementById("text-vertical-align").value,
        fontSize: clampNumber(document.getElementById("font-size").value, 16, 56, 28),
        imageScale: clampNumber(document.getElementById("image-scale").value, 25, 300, 100),
        imageRotation: clampNumber(document.getElementById("image-rotation").value, -180, 180, 0),
        imageCrop: document.getElementById("image-crop").checked,
      };
    }

    function contentBox(options = readManualOptions()) {
      return {
        width: Math.max(1, options.labelWidth - (options.contentMargin * 2)),
        height: Math.max(1, options.labelHeight - (options.contentMargin * 2)),
      };
    }

    function selectedImage() {
      return state.images.find((item) => item.id === state.selectedImageId) || null;
    }

    function selectImage(imageId) {
      state.selectedImageId = state.images.some((item) => item.id === imageId) ? imageId : "";
      if (state.selectedImageId) {
        state.selectedTextId = "";
      }
      syncManualTextEditor();
      renderPreview();
    }

    function selectedText() {
      return state.texts.find((item) => item.id === state.selectedTextId) || null;
    }

    function selectText(textId) {
      state.selectedTextId = state.texts.some((item) => item.id === textId) ? textId : "";
      if (state.selectedTextId) {
        state.selectedImageId = "";
      }
      syncManualTextEditor();
      renderPreview();
    }

    function setTextStatus(message) {
      document.getElementById("selected-text-status").textContent = message;
    }

    function textBounds(item, options = readManualOptions()) {
      const box = contentBox(options);
      const width = clampNumber(item.width, minTextSize, Math.max(minTextSize, box.width * 2), box.width);
      const height = clampNumber(item.height, minTextSize, Math.max(minTextSize, box.height * 2), Math.min(96, box.height));
      return {
        minWidth: minTextSize,
        maxWidth: Math.max(minTextSize, box.width * 2),
        minHeight: minTextSize,
        maxHeight: Math.max(minTextSize, box.height * 2),
        minX: Math.min(0, -width + minVisibleTextDots),
        maxX: Math.max(0, box.width - minVisibleTextDots),
        minY: Math.min(0, -height + minVisibleTextDots),
        maxY: Math.max(0, box.height - minVisibleTextDots),
      };
    }

    function clampTextItem(item, options = readManualOptions()) {
      const before = {
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
        fontSize: item.font_size,
      };
      const boundsBefore = textBounds(item, options);
      item.width = clampNumber(item.width, boundsBefore.minWidth, boundsBefore.maxWidth, Math.max(1, contentBox(options).width));
      item.height = clampNumber(item.height, boundsBefore.minHeight, boundsBefore.maxHeight, Math.min(96, contentBox(options).height));
      const boundsAfter = textBounds(item, options);
      item.x = clampNumber(item.x, boundsAfter.minX, boundsAfter.maxX, 0);
      item.y = clampNumber(item.y, boundsAfter.minY, boundsAfter.maxY, 0);
      item.font_size = clampNumber(item.font_size, 16, 56, readManualOptions().fontSize);
      if (!["left", "center", "right"].includes(item.text_align)) {
        item.text_align = readManualOptions().textAlign;
      }
      if (!["top", "center", "bottom"].includes(item.vertical_align)) {
        item.vertical_align = readManualOptions().textVerticalAlign;
      }
      return (
        before.x !== item.x ||
        before.y !== item.y ||
        before.width !== item.width ||
        before.height !== item.height ||
        before.fontSize !== item.font_size
      );
    }

    function textGeometryInputs() {
      return {
        x: document.getElementById("text-x"),
        y: document.getElementById("text-y"),
        width: document.getElementById("text-width"),
        height: document.getElementById("text-height"),
      };
    }

    function syncTextGeometryControls(item, options = readManualOptions()) {
      const inputs = textGeometryInputs();
      Object.values(inputs).forEach((input) => {
        input.disabled = !item;
      });
      if (!item) {
        inputs.x.value = 0;
        inputs.y.value = 0;
        inputs.width.value = 0;
        inputs.height.value = 0;
        return;
      }
      const bounds = textBounds(item, options);
      inputs.x.min = bounds.minX;
      inputs.x.max = bounds.maxX;
      inputs.y.min = bounds.minY;
      inputs.y.max = bounds.maxY;
      inputs.width.min = bounds.minWidth;
      inputs.width.max = bounds.maxWidth;
      inputs.height.min = bounds.minHeight;
      inputs.height.max = bounds.maxHeight;
      inputs.x.value = item.x;
      inputs.y.value = item.y;
      inputs.width.value = item.width;
      inputs.height.value = item.height;
    }

    function createTextItem(text = "") {
      const options = readManualOptions();
      const box = contentBox(options);
      const width = Math.max(80, Math.min(box.width, 180));
      const height = Math.max(48, Math.min(box.height, 88));
      const stagger = (state.texts.length % 5) * 14;
      const item = {
        id: `text-${Date.now().toString(36)}-${state.nextTextIndex++}`,
        text: text || "",
        x: Math.max(0, Math.min(Math.max(0, box.width - width), 12 + stagger)),
        y: Math.max(0, Math.min(Math.max(0, box.height - height), 12 + stagger)),
        width,
        height,
        font_size: options.fontSize,
        text_align: options.textAlign,
        vertical_align: options.textVerticalAlign,
      };
      clampTextItem(item, options);
      return item;
    }

    function ensureSelectedTextItem() {
      let item = selectedText();
      if (item) {
        return item;
      }
      item = createTextItem("");
      state.texts.push(item);
      state.selectedTextId = item.id;
      state.selectedImageId = "";
      return item;
    }

    function syncManualTextEditor() {
      const textarea = document.getElementById("manual-text");
      const selected = selectedText();
      if (document.activeElement !== textarea) {
        textarea.value = selected ? selected.text : "";
      }
    }

    function validateImageFile(file) {
      if (!file) {
        return;
      }
      const extension = (file.name.split(".").pop() || "").toLowerCase();
      if (!allowedImageExtensions.has(extension) || (file.type && !file.type.startsWith("image/"))) {
        throw new Error("그림 파일만 업로드할 수 있습니다. PDF, DOCX, TXT 문서는 제외됩니다.");
      }
      if (file.size > maxImageBytes) {
        throw new Error("그림 파일은 8MB 이하만 업로드할 수 있습니다.");
      }
    }

    function readFileAsDataUrl(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
        reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsDataURL(file);
      });
    }

    function readImageDimensions(dataUrl) {
      return new Promise((resolve) => {
        const image = new Image();
        image.onload = () => resolve({
          width: image.naturalWidth || 120,
          height: image.naturalHeight || 90,
        });
        image.onerror = () => resolve({ width: 120, height: 90 });
        image.src = dataUrl;
      });
    }

    function splitDataUrl(dataUrl) {
      const commaIndex = dataUrl.indexOf(",");
      return commaIndex >= 0 ? dataUrl.slice(commaIndex + 1) : dataUrl;
    }

    function setStatus(message, isError = false) {
      const status = document.getElementById("manual-status");
      status.textContent = message;
      status.classList.toggle("error", isError);
    }

    function setImageStatus(message) {
      document.getElementById("selected-image-status").textContent = message;
    }

    function imageBounds(item, options = readManualOptions()) {
      const box = contentBox(options);
      const width = clampNumber(item.width, minImageSize, Math.max(minImageSize, box.width * 2), Math.min(180, box.width));
      const height = clampNumber(item.height, minImageSize, Math.max(minImageSize, box.height * 2), Math.min(120, box.height));
      return {
        minWidth: minImageSize,
        maxWidth: Math.max(minImageSize, box.width * 2),
        minHeight: minImageSize,
        maxHeight: Math.max(minImageSize, box.height * 2),
        minX: Math.min(0, -width + minVisibleImageDots),
        maxX: Math.max(0, box.width - minVisibleImageDots),
        minY: Math.min(0, -height + minVisibleImageDots),
        maxY: Math.max(0, box.height - minVisibleImageDots),
      };
    }

    function clampImageItem(item, options = readManualOptions()) {
      const boundsBefore = imageBounds(item, options);
      const before = {
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
        scale: item.scale,
        rotation: item.rotation_degrees,
      };
      item.width = clampNumber(item.width, boundsBefore.minWidth, boundsBefore.maxWidth, Math.min(180, contentBox(options).width));
      item.height = clampNumber(item.height, boundsBefore.minHeight, boundsBefore.maxHeight, Math.min(120, contentBox(options).height));
      const boundsAfter = imageBounds(item, options);
      item.x = clampNumber(item.x, boundsAfter.minX, boundsAfter.maxX, 0);
      item.y = clampNumber(item.y, boundsAfter.minY, boundsAfter.maxY, 0);
      item.rotation_degrees = clampNumber(item.rotation_degrees, -180, 180, 0);
      item.scale = clampNumber(item.scale, 25, 300, 100);
      item.baseWidth = clampNumber(item.baseWidth, minImageSize, boundsAfter.maxWidth, item.width);
      item.baseHeight = clampNumber(item.baseHeight, minImageSize, boundsAfter.maxHeight, item.height);
      return (
        before.x !== item.x ||
        before.y !== item.y ||
        before.width !== item.width ||
        before.height !== item.height ||
        before.scale !== item.scale ||
        before.rotation !== item.rotation_degrees
      );
    }

    function imageGeometryInputs() {
      return {
        x: document.getElementById("image-x"),
        y: document.getElementById("image-y"),
        width: document.getElementById("image-width"),
        height: document.getElementById("image-height"),
      };
    }

    function syncImageGeometryControls(item, options = readManualOptions()) {
      const inputs = imageGeometryInputs();
      const controls = Object.values(inputs);
      controls.forEach((input) => {
        input.disabled = !item;
      });
      if (!item) {
        inputs.x.value = 0;
        inputs.y.value = 0;
        inputs.width.value = 0;
        inputs.height.value = 0;
        return;
      }
      const bounds = imageBounds(item, options);
      inputs.x.min = bounds.minX;
      inputs.x.max = bounds.maxX;
      inputs.y.min = bounds.minY;
      inputs.y.max = bounds.maxY;
      inputs.width.min = bounds.minWidth;
      inputs.width.max = bounds.maxWidth;
      inputs.height.min = bounds.minHeight;
      inputs.height.max = bounds.maxHeight;
      inputs.x.value = item.x;
      inputs.y.value = item.y;
      inputs.width.value = item.width;
      inputs.height.value = item.height;
    }

    async function createImageItem(file) {
      const previewUrl = URL.createObjectURL(file);
      const dimensions = await readImageDimensions(previewUrl);
      const box = contentBox();
      const maxWidth = Math.max(24, Math.min(box.width, 180));
      const aspect = dimensions.width > 0 ? dimensions.height / dimensions.width : 0.75;
      let width = maxWidth;
      let height = Math.max(24, Math.round(width * aspect));
      if (height > box.height) {
        const ratio = box.height / height;
        width = Math.max(24, Math.round(width * ratio));
        height = Math.max(24, Math.round(height * ratio));
      }
      const stagger = (state.images.length % 5) * 14;
      const item = {
        id: `image-${Date.now().toString(36)}-${state.nextImageIndex++}`,
        filename: file.name,
        file,
        previewUrl,
        x: Math.max(0, Math.min(Math.max(0, box.width - width), 8 + stagger)),
        y: Math.max(0, Math.min(Math.max(0, box.height - height), 8 + stagger)),
        width,
        height,
        baseWidth: width,
        baseHeight: height,
        scale: 100,
        rotation_degrees: 0,
        crop: false,
      };
      clampImageItem(item);
      return item;
    }

    function revokeImagePreviewUrl(item) {
      if (item?.previewUrl) {
        URL.revokeObjectURL(item.previewUrl);
        item.previewUrl = "";
      }
    }

    function revokeAllImagePreviewUrls() {
      state.images.forEach(revokeImagePreviewUrl);
    }

    function createCanvasImageElement(item) {
      const element = document.createElement("div");
      element.className = "canvas-image-item";
      element.dataset.imageId = item.id;
      const image = document.createElement("img");
      image.draggable = false;
      const resizeHandle = document.createElement("span");
      resizeHandle.className = "canvas-image-handle image-resize-handle";
      resizeHandle.dataset.imageHandle = "resize";
      resizeHandle.title = "그림 크기 조절";
      const rotateHandle = document.createElement("span");
      rotateHandle.className = "canvas-image-handle image-rotate-handle";
      rotateHandle.dataset.imageHandle = "rotate";
      rotateHandle.title = "그림 회전";
      element.append(image, resizeHandle, rotateHandle);
      return element;
    }

    function renderCanvasImages(imageLayer, options) {
      const elementsById = new Map(
        Array.from(imageLayer.querySelectorAll(".canvas-image-item")).map((element) => [
          element.dataset.imageId,
          element,
        ])
      );
      const activeIds = new Set();
      state.images.forEach((item) => {
        clampImageItem(item, options);
        activeIds.add(item.id);
        const element = elementsById.get(item.id) || createCanvasImageElement(item);
        if (!element.parentElement) {
          imageLayer.append(element);
        }
        element.className = `canvas-image-item ${item.id === state.selectedImageId ? "selected" : ""} ${item.crop ? "crop" : ""}`;
        element.style.left = `${item.x}px`;
        element.style.top = `${item.y}px`;
        element.style.width = `${item.width}px`;
        element.style.height = `${item.height}px`;
        element.style.transform = `rotate(${item.rotation_degrees}deg)`;
        const image = element.querySelector("img");
        if (image.src !== item.previewUrl) {
          image.src = item.previewUrl;
        }
        image.alt = item.filename;
      });
      elementsById.forEach((element, imageId) => {
        if (!activeIds.has(imageId)) {
          element.remove();
        }
      });
    }

    function createCanvasTextElement(item) {
      const element = document.createElement("div");
      element.className = "canvas-text-item";
      element.dataset.textId = item.id;
      const content = document.createElement("div");
      content.className = "canvas-text-content";
      const resizeHandle = document.createElement("span");
      resizeHandle.className = "canvas-text-handle";
      resizeHandle.dataset.textHandle = "resize";
      resizeHandle.title = "텍스트 박스 크기 조절";
      element.append(content, resizeHandle);
      return element;
    }

    function renderCanvasTexts(textLayer, options) {
      const elementsById = new Map(
        Array.from(textLayer.querySelectorAll(".canvas-text-item")).map((element) => [
          element.dataset.textId,
          element,
        ])
      );
      const activeIds = new Set();
      state.texts.forEach((item) => {
        clampTextItem(item, options);
        if (!item.text.trim()) {
          return;
        }
        activeIds.add(item.id);
        const element = elementsById.get(item.id) || createCanvasTextElement(item);
        if (!element.parentElement) {
          textLayer.append(element);
        }
        element.className = `canvas-text-item ${item.id === state.selectedTextId ? "selected" : ""} align-${item.text_align} valign-${item.vertical_align}`;
        element.style.left = `${item.x}px`;
        element.style.top = `${item.y}px`;
        element.style.width = `${item.width}px`;
        element.style.height = `${item.height}px`;
        element.style.fontSize = `${item.font_size}px`;
        element.querySelector(".canvas-text-content").textContent = item.text;
      });
      elementsById.forEach((element, textId) => {
        if (!activeIds.has(textId)) {
          element.remove();
        }
      });
    }

    function renderImageList() {
      const list = document.getElementById("image-list");
      if (!state.images.length) {
        list.innerHTML = '<span class="status">선택된 그림 없음</span>';
        return;
      }
      list.innerHTML = state.images.map((item, index) => `
        <button type="button" class="image-chip ${item.id === state.selectedImageId ? "selected" : ""}" data-select-image="${escapeHtml(item.id)}">
          ${escapeHtml(index + 1)}. ${escapeHtml(item.filename)}
        </button>
      `).join("");
    }

    function renderTextList() {
      const list = document.getElementById("text-list");
      const visibleTexts = state.texts.filter((item) => item.text.trim());
      if (!visibleTexts.length) {
        list.innerHTML = '<span class="status">텍스트 박스 없음</span>';
        return;
      }
      list.innerHTML = visibleTexts.map((item, index) => `
        <button type="button" class="image-chip ${item.id === state.selectedTextId ? "selected" : ""}" data-select-text="${escapeHtml(item.id)}">
          ${escapeHtml(index + 1)}. ${escapeHtml(item.text.slice(0, 24))}
        </button>
      `).join("");
    }

    function renderPreview() {
      const options = readManualOptions();
      const paper = document.getElementById("paper-preview");
      const imageLayer = document.getElementById("canvas-image-layer");
      const textLayer = document.getElementById("canvas-text-layer");
      const fontLabel = document.getElementById("font-size-value");
      const imageScale = document.getElementById("image-scale");
      const imageScaleLabel = document.getElementById("image-scale-value");
      const imageRotation = document.getElementById("image-rotation");
      const imageRotationLabel = document.getElementById("image-rotation-value");
      const imageCrop = document.getElementById("image-crop");
      const fitButton = document.getElementById("fit-selected-image");
      const removeButton = document.getElementById("remove-selected-image");
      const selected = selectedImage();
      const selectedTextItem = selectedText();
      const textAlign = document.getElementById("text-align");
      const textVerticalAlign = document.getElementById("text-vertical-align");
      const fontSize = document.getElementById("font-size");
      const removeTextButton = document.getElementById("remove-selected-text");

      paper.className = `paper-preview border-${options.borderStyle}`;
      paper.style.width = `${options.labelWidth}px`;
      paper.style.height = `${options.labelHeight}px`;
      paper.style.minHeight = `${options.labelHeight}px`;
      paper.style.setProperty("--content-margin", `${options.contentMargin}px`);
      renderCanvasImages(imageLayer, options);
      renderCanvasTexts(textLayer, options);

      textAlign.disabled = !selectedTextItem;
      textVerticalAlign.disabled = !selectedTextItem;
      fontSize.disabled = !selectedTextItem;
      removeTextButton.disabled = !selectedTextItem;
      if (selectedTextItem) {
        textAlign.value = selectedTextItem.text_align;
        textVerticalAlign.value = selectedTextItem.vertical_align;
        fontSize.value = selectedTextItem.font_size;
        fontLabel.textContent = `${selectedTextItem.font_size}px`;
        setTextStatus(`${selectedTextItem.width}×${selectedTextItem.height}dot · x${selectedTextItem.x}, y${selectedTextItem.y}`);
      } else {
        textAlign.value = options.textAlign;
        textVerticalAlign.value = options.textVerticalAlign;
        fontSize.value = options.fontSize;
        fontLabel.textContent = "—";
        setTextStatus("문구를 입력하거나 텍스트 박스를 추가하세요.");
      }

      imageScale.disabled = !selected;
      imageRotation.disabled = !selected;
      imageCrop.disabled = !selected;
      fitButton.disabled = !selected;
      removeButton.disabled = !selected;
      if (selected) {
        imageScale.value = selected.scale;
        imageRotation.value = selected.rotation_degrees;
        imageCrop.checked = selected.crop;
        imageScaleLabel.textContent = `${selected.scale}%`;
        imageRotationLabel.textContent = `${selected.rotation_degrees}°`;
        setImageStatus(`${selected.filename} · ${selected.width}×${selected.height}dot · x${selected.x}, y${selected.y}`);
      } else {
        imageScale.value = 100;
        imageRotation.value = 0;
        imageCrop.checked = false;
        imageScaleLabel.textContent = "—";
        imageRotationLabel.textContent = "—";
        setImageStatus("그림을 올리면 캔버스에서 이동·크기조절·회전할 수 있습니다.");
      }
      syncImageGeometryControls(selected, options);
      syncTextGeometryControls(selectedTextItem, options);

      document.getElementById("label-width").value = options.labelWidth;
      document.getElementById("label-height").value = options.labelHeight;
      document.getElementById("content-margin").value = options.contentMargin;
      renderImageList();
      renderTextList();

      const pieces = [];
      pieces.push(`후보 폭 384dot 고정`);
      pieces.push(`${options.labelWidth}×${options.labelHeight}dot 라벨`);
      pieces.push(`${options.contentMargin}dot 여백`);
      const textLength = state.texts.reduce((sum, item) => sum + item.text.trim().length, 0);
      if (textLength) {
        pieces.push(`${textLength}자`);
      }
      if (state.images.length) {
        pieces.push(`${state.images.length}개 그림`);
      }
      if (state.texts.some((item) => item.text.trim())) {
        pieces.push(`${state.texts.filter((item) => item.text.trim()).length}개 텍스트`);
      }
      document.getElementById("preview-meta").textContent = pieces.join(" · ");
    }

    async function handleImageChange() {
      const input = document.getElementById("manual-image");
      const files = Array.from(input.files || []);
      if (!files.length) {
        return;
      }
      try {
        files.forEach(validateImageFile);
        for (const file of files) {
          const item = await createImageItem(file);
          state.images.push(item);
          state.selectedImageId = item.id;
        }
        setStatus(`${files.length}개 그림 추가됨 · 캔버스에서 위치와 크기를 조절하세요.`);
      } catch (error) {
        setStatus(error.message || String(error), true);
      } finally {
        input.value = "";
      }
      renderPreview();
    }

    function applySelectedImageScale() {
      const item = selectedImage();
      if (!item) {
        renderPreview();
        return;
      }
      const nextScale = clampNumber(document.getElementById("image-scale").value, 25, 300, 100);
      const centerX = item.x + (item.width / 2);
      const centerY = item.y + (item.height / 2);
      item.scale = nextScale;
      item.width = Math.max(8, Math.round(item.baseWidth * (nextScale / 100)));
      item.height = Math.max(8, Math.round(item.baseHeight * (nextScale / 100)));
      item.x = Math.round(centerX - (item.width / 2));
      item.y = Math.round(centerY - (item.height / 2));
      renderPreview();
    }

    function applySelectedImageRotation() {
      const item = selectedImage();
      if (!item) {
        renderPreview();
        return;
      }
      item.rotation_degrees = clampNumber(document.getElementById("image-rotation").value, -180, 180, 0);
      renderPreview();
    }

    function applySelectedImageCrop() {
      const item = selectedImage();
      if (!item) {
        renderPreview();
        return;
      }
      item.crop = document.getElementById("image-crop").checked;
      renderPreview();
    }

    function applySelectedImageGeometry(event) {
      const item = selectedImage();
      if (!item) {
        renderPreview();
        return;
      }
      const inputs = imageGeometryInputs();
      const previous = {
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
      };
      item.x = Number(inputs.x.value);
      item.y = Number(inputs.y.value);
      item.width = Number(inputs.width.value);
      item.height = Number(inputs.height.value);
      if (event?.target === inputs.width || event?.target === inputs.height) {
        item.baseWidth = item.width;
        item.baseHeight = item.height;
        item.scale = 100;
      }
      const corrected = clampImageItem(item);
      if (corrected) {
        setStatus("그림 위치/크기를 유효한 dot 범위로 자동 보정했습니다.");
      } else if (
        previous.x !== item.x ||
        previous.y !== item.y ||
        previous.width !== item.width ||
        previous.height !== item.height
      ) {
        setStatus("그림 위치/크기를 적용했습니다.");
      }
      renderPreview();
    }

    function handleManualTextInput() {
      const textarea = document.getElementById("manual-text");
      const value = textarea.value;
      let item = selectedText();
      if (!item && value.trim()) {
        item = ensureSelectedTextItem();
      }
      if (item) {
        item.text = value;
      }
      renderPreview();
    }

    function addTextBox() {
      const textarea = document.getElementById("manual-text");
      const item = createTextItem(textarea.value.trim() || "텍스트");
      state.texts.push(item);
      state.selectedTextId = item.id;
      state.selectedImageId = "";
      syncManualTextEditor();
      setTextStatus("텍스트 박스를 추가했습니다.");
      renderPreview();
    }

    function removeSelectedText() {
      const item = selectedText();
      if (!item) {
        return;
      }
      state.texts = state.texts.filter((candidate) => candidate.id !== item.id);
      state.selectedTextId = state.texts.length ? state.texts[state.texts.length - 1].id : "";
      syncManualTextEditor();
      setTextStatus("선택한 텍스트 박스를 제거했습니다.");
      renderPreview();
    }

    function applySelectedTextStyle() {
      const item = selectedText();
      if (!item) {
        renderPreview();
        return;
      }
      item.text_align = document.getElementById("text-align").value;
      item.vertical_align = document.getElementById("text-vertical-align").value;
      item.font_size = clampNumber(document.getElementById("font-size").value, 16, 56, item.font_size);
      renderPreview();
    }

    function applySelectedTextGeometry(event) {
      const item = selectedText();
      if (!item) {
        renderPreview();
        return;
      }
      const inputs = textGeometryInputs();
      const previous = {
        x: item.x,
        y: item.y,
        width: item.width,
        height: item.height,
      };
      item.x = Number(inputs.x.value);
      item.y = Number(inputs.y.value);
      item.width = Number(inputs.width.value);
      item.height = Number(inputs.height.value);
      if (event?.target === inputs.width || event?.target === inputs.height) {
        item.width = clampSteppedNumber(item.width, minTextSize, textBounds(item).maxWidth, previous.width);
        item.height = clampSteppedNumber(item.height, minTextSize, textBounds(item).maxHeight, previous.height);
      }
      const corrected = clampTextItem(item);
      if (corrected) {
        setStatus("텍스트 위치/크기를 유효한 dot 범위로 자동 보정했습니다.");
      } else if (
        previous.x !== item.x ||
        previous.y !== item.y ||
        previous.width !== item.width ||
        previous.height !== item.height
      ) {
        setStatus("텍스트 위치/크기를 적용했습니다.");
      }
      renderPreview();
    }

    function fitSelectedImageToLabel() {
      const item = selectedImage();
      if (!item) {
        return;
      }
      const box = contentBox();
      item.x = 0;
      item.y = 0;
      item.width = box.width;
      item.height = box.height;
      item.baseWidth = item.width;
      item.baseHeight = item.height;
      item.scale = 100;
      clampImageItem(item);
      setStatus("선택한 그림을 라벨 안쪽 영역에 맞췄습니다.");
      renderPreview();
    }

    function removeSelectedImage() {
      const item = selectedImage();
      if (!item) {
        return;
      }
      state.images = state.images.filter((candidate) => candidate.id !== item.id);
      revokeImagePreviewUrl(item);
      state.selectedImageId = state.images.length ? state.images[state.images.length - 1].id : "";
      setStatus("선택한 그림을 제거했습니다.");
      renderPreview();
    }

    function pointerAngle(clientX, clientY, centerX, centerY) {
      return Math.atan2(clientY - centerY, clientX - centerX) * (180 / Math.PI);
    }

    function beginLabelResize(event) {
      const options = readManualOptions();
      state.drag = {
        mode: "label-resize",
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startWidth: options.labelWidth,
        startHeight: options.labelHeight,
      };
      event.preventDefault();
    }

    function handleCanvasPointerDown(event) {
      const textElement = event.target.closest(".canvas-text-item");
      if (textElement) {
        const textId = textElement.dataset.textId || "";
        state.selectedTextId = textId;
        state.selectedImageId = "";
        syncManualTextEditor();
        const item = selectedText();
        if (!item) {
          return;
        }
        const handle = event.target.closest("[data-text-handle]");
        const mode = handle?.dataset.textHandle === "resize" ? "text-resize" : "text-move";
        state.drag = {
          mode,
          id: textId,
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startX: item.x,
          startY: item.y,
          startWidth: item.width,
          startHeight: item.height,
        };
        event.preventDefault();
        renderPreview();
        return;
      }

      const imageElement = event.target.closest(".canvas-image-item");
      if (!imageElement) {
        if (event.target.id === "paper-preview" || event.target.id === "canvas-image-layer" || event.target.id === "canvas-text-layer") {
          state.selectedImageId = "";
          state.selectedTextId = "";
          syncManualTextEditor();
          renderPreview();
        }
        return;
      }
      const imageId = imageElement.dataset.imageId || "";
      state.selectedImageId = imageId;
      state.selectedTextId = "";
      syncManualTextEditor();
      const item = selectedImage();
      if (!item) {
        return;
      }
      const handle = event.target.closest("[data-image-handle]");
      const mode = handle?.dataset.imageHandle === "resize"
        ? "image-resize"
        : handle?.dataset.imageHandle === "rotate"
          ? "image-rotate"
          : "image-move";
      const rect = imageElement.getBoundingClientRect();
      const centerX = rect.left + (rect.width / 2);
      const centerY = rect.top + (rect.height / 2);
      state.drag = {
        mode,
        id: imageId,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX: item.x,
        startY: item.y,
        startWidth: item.width,
        startHeight: item.height,
        startBaseWidth: item.baseWidth,
        startBaseHeight: item.baseHeight,
        startRotation: item.rotation_degrees,
        startAngle: pointerAngle(event.clientX, event.clientY, centerX, centerY),
        centerX,
        centerY,
      };
      event.preventDefault();
      renderPreview();
    }

    function handlePointerMove(event) {
      if (!state.drag) {
        return;
      }
      const drag = state.drag;
      event.preventDefault();
      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      if (drag.mode === "label-resize") {
        document.getElementById("label-width").value = clampSteppedNumber(drag.startWidth + dx, 80, 344, drag.startWidth);
        document.getElementById("label-height").value = clampSteppedNumber(drag.startHeight + dy, 56, 1200, drag.startHeight);
        renderPreview();
        return;
      }

      if (drag.mode === "text-move" || drag.mode === "text-resize") {
        const item = state.texts.find((candidate) => candidate.id === drag.id);
        if (!item) {
          state.drag = null;
          return;
        }
        if (drag.mode === "text-move") {
          item.x = Math.round(drag.startX + dx);
          item.y = Math.round(drag.startY + dy);
        } else {
          item.width = Math.max(minTextSize, clampSteppedNumber(drag.startWidth + dx, minTextSize, textBounds(item).maxWidth, drag.startWidth));
          item.height = Math.max(minTextSize, clampSteppedNumber(drag.startHeight + dy, minTextSize, textBounds(item).maxHeight, drag.startHeight));
        }
        renderPreview();
        return;
      }

      const item = state.images.find((candidate) => candidate.id === drag.id);
      if (!item) {
        state.drag = null;
        return;
      }
      if (drag.mode === "image-move") {
        item.x = Math.round(drag.startX + dx);
        item.y = Math.round(drag.startY + dy);
      } else if (drag.mode === "image-resize") {
        const widthScale = Math.max(0.1, (drag.startWidth + dx) / Math.max(1, drag.startWidth));
        const heightScale = Math.max(0.1, (drag.startHeight + dy) / Math.max(1, drag.startHeight));
        const scale = Math.max(widthScale, heightScale);
        item.width = Math.max(8, snapNumber(drag.startWidth * scale, 4));
        item.height = Math.max(8, snapNumber(drag.startHeight * scale, 4));
        item.baseWidth = item.width;
        item.baseHeight = item.height;
        item.scale = 100;
      } else if (drag.mode === "image-rotate") {
        const angle = pointerAngle(event.clientX, event.clientY, drag.centerX, drag.centerY);
        item.rotation_degrees = clampNumber(drag.startRotation + angle - drag.startAngle, -180, 180, drag.startRotation);
      }
      renderPreview();
    }

    function handlePointerUp() {
      state.drag = null;
    }

    async function submitManualPrint(event) {
      event.preventDefault();
      const textItems = state.texts
        .map((item) => ({ ...item, text: item.text.trim() }))
        .filter((item) => item.text);
      const text = textItems.map((item) => item.text).join("\\n").trim();
      if (!textItems.length && !state.images.length) {
        setStatus("문구나 그림 중 하나를 넣어주세요.", true);
        return;
      }

      const button = document.getElementById("submit-manual");
      button.disabled = true;
      setStatus("출력 큐에 등록하는 중입니다.");
      try {
        const options = readManualOptions();
        const images = [];
        for (const item of state.images) {
          clampImageItem(item, options);
          const dataUrl = await readFileAsDataUrl(item.file);
          images.push({
            id: item.id,
            filename: item.filename,
            content_base64: splitDataUrl(dataUrl),
            x: item.x,
            y: item.y,
            width: item.width,
            height: item.height,
            rotation_degrees: item.rotation_degrees,
            crop: item.crop,
          });
        }
        const payload = {
          text,
          border_style: options.borderStyle,
          text_align: options.textAlign,
          text_vertical_align: options.textVerticalAlign,
          font_size: options.fontSize,
          label_width_px: options.labelWidth,
          label_height_px: options.labelHeight,
          content_margin_px: options.contentMargin,
          image_scale_percent: options.imageScale,
          image_crop: options.imageCrop,
          image_rotation_degrees: options.imageRotation,
          images,
          text_items: textItems.map((item) => ({
            id: item.id,
            text: item.text,
            x: item.x,
            y: item.y,
            width: item.width,
            height: item.height,
            font_size: item.font_size,
            text_align: item.text_align,
            vertical_align: item.vertical_align,
          })),
        };

        const response = await fetch("/api/manual-print", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.ok === false) {
          throw new Error(body.error || `manual print ${response.status}`);
        }
        setStatus(`큐 등록 완료 · ${body.request_id}`);
        await fetchManualHistory();
      } catch (error) {
        setStatus(`등록 실패: ${error.message || error}`, true);
      } finally {
        button.disabled = false;
      }
    }

    function clearManualForm() {
      document.getElementById("manual-form").reset();
      revokeAllImagePreviewUrls();
      state.images = [];
      state.selectedImageId = "";
      state.texts = [];
      state.selectedTextId = "";
      state.drag = null;
      state.nextImageIndex = 1;
      state.nextTextIndex = 1;
      setStatus("문구나 그림 중 하나를 넣으면 출력할 수 있습니다.");
      renderPreview();
    }

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
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function renderManualHistory() {
      const list = document.getElementById("manual-history-list");
      const status = document.getElementById("history-status");
      const history = state.history || [];
      if (!history.length) {
        list.innerHTML = "";
        status.textContent = "아직 수동 출력 이력이 없습니다.";
        return;
      }
      status.textContent = `${history.length}개 이력`;
      list.innerHTML = history.map((entry) => {
        const labelSize = entry.label_width_px && entry.label_height_px
          ? `${entry.label_width_px}×${entry.label_height_px}dot`
          : "라벨 크기 없음";
        const marginSize = Number.isFinite(Number(entry.content_margin_px))
          ? `${entry.content_margin_px}dot 여백`
          : "16dot 여백";
        const outputSize = entry.output_width_px && entry.output_height_px
          ? `출력 ${entry.output_width_px}×${entry.output_height_px}dot`
          : "출력 크기 없음";
        const imageCount = Number(entry.image_count || 0);
        const text = entry.text_preview || (entry.image_name ? `그림 ${entry.image_name}` : "수동 출력");
        const detail = imageCount > 0
          ? `${outputSize} · 라벨 ${labelSize} · ${marginSize} · 그림 ${imageCount}개`
          : `${outputSize} · 라벨 ${labelSize} · ${marginSize}`;
        const image = entry.exists
          ? `<img class="history-thumb" src="${escapeHtml(entry.image_url)}" alt="${escapeHtml(text)}">`
          : `<div class="history-thumb"></div>`;
        return `
          <article class="history-card" data-history-id="${escapeHtml(entry.id)}">
            ${image}
            <div class="history-meta">${escapeHtml(formatDateTime(entry.created_at))} · ${escapeHtml(detail)}</div>
            <div class="history-meta">${escapeHtml(text)}</div>
            <div class="history-actions">
              <button class="secondary" type="button" data-reprint-history="${escapeHtml(entry.id)}">재출력</button>
              <a class="button-link secondary" href="${escapeHtml(entry.download_url)}" download>다운로드</a>
              <button class="danger" type="button" data-delete-history="${escapeHtml(entry.id)}">삭제</button>
            </div>
          </article>
        `;
      }).join("");
      list.querySelectorAll("[data-reprint-history]").forEach((button) => {
        button.addEventListener("click", () => reprintManualHistory(button.dataset.reprintHistory || "", button));
      });
      list.querySelectorAll("[data-delete-history]").forEach((button) => {
        button.addEventListener("click", () => deleteManualHistory(button.dataset.deleteHistory || ""));
      });
    }

    async function fetchManualHistory() {
      const status = document.getElementById("history-status");
      status.textContent = "이력을 불러오는 중입니다.";
      status.classList.remove("error");
      try {
        const response = await fetch("/api/manual-history", { cache: "no-store" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.ok === false) {
          throw new Error(body.error || `history ${response.status}`);
        }
        state.history = body.history || [];
        renderManualHistory();
      } catch (error) {
        state.history = [];
        renderManualHistory();
        status.textContent = `이력 로드 실패: ${error.message || error}`;
        status.classList.add("error");
      }
    }

    async function reprintManualHistory(historyId, button = null) {
      if (!historyId) {
        return;
      }
      const status = document.getElementById("history-status");
      status.classList.remove("error");
      status.textContent = "재출력을 큐에 등록하는 중입니다.";
      if (button) {
        button.disabled = true;
      }
      try {
        const response = await fetch(`/api/manual-history/${encodeURIComponent(historyId)}/reprint`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: "{}",
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.ok === false) {
          throw new Error(body.error || `reprint ${response.status}`);
        }
        status.textContent = `재출력 큐 등록 완료 · ${body.request_id}`;
        await fetchManualHistory();
      } catch (error) {
        status.textContent = `재출력 실패: ${error.message || error}`;
        status.classList.add("error");
      } finally {
        if (button) {
          button.disabled = false;
        }
      }
    }

    async function deleteManualHistory(historyId) {
      if (!historyId) {
        return;
      }
      const status = document.getElementById("history-status");
      status.classList.remove("error");
      status.textContent = "이력을 삭제하는 중입니다.";
      try {
        const response = await fetch(`/api/manual-history/${encodeURIComponent(historyId)}`, {
          method: "DELETE",
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.ok === false) {
          throw new Error(body.error || `delete ${response.status}`);
        }
        state.history = state.history.filter((entry) => entry.id !== historyId);
        renderManualHistory();
      } catch (error) {
        status.textContent = `삭제 실패: ${error.message || error}`;
        status.classList.add("error");
      }
    }

    function removeBrokenHeroMarks() {
      document.querySelectorAll(".hero-mark").forEach((image) => {
        const removeIfBroken = () => {
          if (image.complete && image.naturalWidth === 0) {
            image.remove();
          }
        };
        image.addEventListener("error", () => image.remove());
        removeIfBroken();
        window.setTimeout(removeIfBroken, 100);
        window.setTimeout(removeIfBroken, 1000);
      });
    }

    document.getElementById("manual-form").addEventListener("submit", submitManualPrint);
    document.getElementById("clear-manual").addEventListener("click", clearManualForm);
    document.getElementById("manual-text").addEventListener("input", handleManualTextInput);
    document.getElementById("label-width").addEventListener("input", renderPreview);
    document.getElementById("label-height").addEventListener("input", renderPreview);
    document.getElementById("content-margin").addEventListener("input", renderPreview);
    document.getElementById("border-style").addEventListener("change", renderPreview);
    document.getElementById("text-align").addEventListener("change", applySelectedTextStyle);
    document.getElementById("text-vertical-align").addEventListener("change", applySelectedTextStyle);
    document.getElementById("font-size").addEventListener("input", applySelectedTextStyle);
    document.getElementById("image-scale").addEventListener("input", applySelectedImageScale);
    document.getElementById("image-rotation").addEventListener("input", applySelectedImageRotation);
    document.getElementById("image-crop").addEventListener("change", applySelectedImageCrop);
    Object.values(imageGeometryInputs()).forEach((input) => {
      input.addEventListener("change", applySelectedImageGeometry);
    });
    Object.values(textGeometryInputs()).forEach((input) => {
      input.addEventListener("change", applySelectedTextGeometry);
    });
    document.getElementById("manual-image").addEventListener("change", handleImageChange);
    document.getElementById("add-text-box").addEventListener("click", addTextBox);
    document.getElementById("remove-selected-text").addEventListener("click", removeSelectedText);
    document.getElementById("text-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-select-text]");
      if (button) {
        selectText(button.dataset.selectText || "");
      }
    });
    document.getElementById("image-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-select-image]");
      if (button) {
        selectImage(button.dataset.selectImage || "");
      }
    });
    document.getElementById("fit-selected-image").addEventListener("click", fitSelectedImageToLabel);
    document.getElementById("remove-selected-image").addEventListener("click", removeSelectedImage);
    document.getElementById("paper-preview").addEventListener("pointerdown", handleCanvasPointerDown);
    document.getElementById("label-resize-handle").addEventListener("pointerdown", beginLabelResize);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    window.addEventListener("beforeunload", revokeAllImagePreviewUrls);
    document.getElementById("refresh-history").addEventListener("click", fetchManualHistory);
    removeBrokenHeroMarks();
    renderPreview();
    fetchManualHistory();
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
        self.jobs_root = self.jobs_dir.resolve()
        self.assets_root = config.assets_dir.resolve()
        self.bluetooth_status_path = config.output.outputs_dir / "bluetooth-status.json"
        self.log_path = config.output.logs_dir / config.output.log_filename
        self._local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
        self._snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._artifacts_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None

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
        self._artifacts_cache = None

    def _build_snapshot_uncached(self, *, normalized_date: str | None) -> dict[str, Any]:
        job_entries = self._load_job_index()
        filtered_entries = [
            job
            for job in job_entries
            if normalized_date is None or job["triggered_date"] == normalized_date
        ]
        logs = self._read_log_snapshot()
        today = datetime.now(self._local_timezone).date().isoformat()
        available_dates = [
            {"date": date, "count": count}
            for date, count in sorted(
                Counter(job["triggered_date"] for job in job_entries).items(),
                reverse=True,
            )
        ]
        previews = self._load_preview_summaries(filtered_entries[: self.preview_limit])
        latest_job = None
        if job_entries:
            latest_job = next(
                (
                    preview
                    for preview in previews
                    if preview["job_id"] == job_entries[0]["job_id"]
                ),
                None,
            )
            if latest_job is None:
                latest_job = self._load_job_summary(
                    job_entries[0]["job_dir"],
                    index_entry=job_entries[0],
                )

        return {
            "generated_at": datetime.now(self._local_timezone).isoformat(),
            "selected_date": normalized_date or "",
            "today_date": today,
            "today_jobs": sum(1 for job in job_entries if job["triggered_date"] == today),
            "total_jobs": len(job_entries),
            "filtered_jobs": len(filtered_entries),
            "previews_truncated": len(filtered_entries) > self.preview_limit,
            "available_dates": available_dates,
            "status_counts": dict(
                Counter(job["status"] for job in job_entries if job["status"])
            ),
            "latest_job": latest_job,
            "runtime": self._build_runtime_summary(),
            "bluetooth": self._read_bluetooth_status(),
            "service": self._build_service_status(logs, latest_job=latest_job),
            "dashboard": {
                "settings_token_required": bool(self.config.dashboard.edit_token),
            },
            "logs": logs,
            "llm_profiles": self._serialize_llm_profiles(),
            "artifacts": self._serialize_artifacts_cached(),
            "previews": previews,
        }

    def resolve_preview_image(self, job_id: str) -> Path | None:
        safe_job_id = job_id.strip()
        if not safe_job_id:
            return None
        job_dir = (self.jobs_dir / safe_job_id).resolve()
        if not _is_relative_to_path(job_dir, self.jobs_root) or not job_dir.is_dir():
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
                _is_relative_to_path(candidate, self.assets_root)
                or _is_relative_to_path(candidate, job_dir)
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
        if not _is_relative_to_path(candidate, self.assets_root) or not candidate.is_file():
            return None
        if candidate.suffix.lower() not in IMAGE_EXTENSIONS | AUDIO_EXTENSIONS:
            return None
        return candidate

    def resolve_manual_history_image(self, history_id: str) -> Path | None:
        safe_id = _safe_history_id(history_id)
        if safe_id is None:
            return None
        history_dir = self.config.output.outputs_dir / MANUAL_HISTORY_DIRNAME
        image_path = (history_dir / safe_id / "composed-ticket.png").resolve()
        history_root = history_dir.resolve()
        if not _is_relative_to(image_path, history_root) or not image_path.is_file():
            return None
        return image_path

    def list_manual_history(self, *, limit: int = 60) -> list[dict[str, Any]]:
        history_dir = self.config.output.outputs_dir / MANUAL_HISTORY_DIRNAME
        if not history_dir.is_dir():
            return []

        entries: list[tuple[datetime, dict[str, Any]]] = []
        for entry_dir in (path for path in history_dir.iterdir() if path.is_dir()):
            history_id = entry_dir.name
            if _safe_history_id(history_id) is None:
                continue
            metadata = _read_json_file(entry_dir / "manual-print.json")
            image_path = entry_dir / "composed-ticket.png"
            output_size = _image_dimensions(image_path)
            created_at = _first_text(metadata.get("queued_at"), metadata.get("created_at"))
            sort_time = _parse_datetime(created_at, self._local_timezone)
            if not created_at and image_path.is_file():
                sort_time = datetime.fromtimestamp(
                    image_path.stat().st_mtime,
                    tz=self._local_timezone,
                )
                created_at = sort_time.isoformat()
            if sort_time is None:
                timestamp = (
                    image_path.stat().st_mtime
                    if image_path.is_file()
                    else entry_dir.stat().st_mtime
                )
                sort_time = datetime.fromtimestamp(
                    timestamp,
                    tz=self._local_timezone,
                )
            text = _first_text(metadata.get("text")) or ""
            text_items = metadata.get("text_items")
            text_count = (
                len([item for item in text_items if isinstance(item, dict) and item.get("text")])
                if isinstance(text_items, list)
                else (1 if text else 0)
            )
            images = metadata.get("images")
            image_count = (
                len(images)
                if isinstance(images, list)
                else (1 if metadata.get("image_path") else 0)
            )
            entries.append(
                (
                    sort_time,
                    {
                        "id": history_id,
                        "created_at": created_at or "",
                        "text": text,
                        "text_preview": text[:80],
                        "text_count": text_count,
                        "image_name": _first_text(metadata.get("image_name")) or "",
                        "image_count": image_count,
                        "border_style": _first_text(metadata.get("border_style")) or "thin",
                        "text_vertical_align": (
                            _first_text(metadata.get("text_vertical_align"))
                            or _first_text(metadata.get("vertical_align"))
                            or "center"
                        ),
                        "label_width_px": _optional_number(metadata.get("label_width_px")),
                        "label_height_px": _optional_number(metadata.get("label_height_px")),
                        "content_margin_px": _optional_number(
                            metadata.get("content_margin_px")
                        ),
                        "image_scale_percent": _optional_number(
                            metadata.get("image_scale_percent")
                        ),
                        "image_crop": bool(metadata.get("image_crop", False)),
                        "image_rotation_degrees": _optional_number(
                            metadata.get("image_rotation_degrees")
                        ),
                        "image_url": f"/manual-history/{quote(history_id, safe='')}/image",
                        "download_url": (
                            f"/manual-history/{quote(history_id, safe='')}/image?download=1"
                        ),
                        "exists": image_path.is_file(),
                        "output_width_px": output_size[0] if output_size else None,
                        "output_height_px": output_size[1] if output_size else None,
                        "size_bytes": (
                            image_path.stat().st_size if image_path.is_file() else None
                        ),
                    },
                )
            )
        entries.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in entries[:limit]]

    def _serialize_artifacts_cached(self) -> dict[str, list[dict[str, Any]]]:
        if self.snapshot_cache_seconds <= 0:
            return self._serialize_artifacts()
        now_monotonic = time.monotonic()
        if (
            self._artifacts_cache is not None
            and now_monotonic - self._artifacts_cache[0] <= self.snapshot_cache_seconds
        ):
            return self._artifacts_cache[1]
        artifacts = self._serialize_artifacts()
        self._artifacts_cache = (now_monotonic, artifacts)
        return artifacts

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
        resolved = _absolute_path(path, base_dir=self.config.assets_dir)
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
        resolved = _absolute_path(path, base_dir=self.config.assets_dir)
        return self._asset_relative_path(resolved) or str(resolved)

    def _asset_relative_path(self, path: Path) -> str | None:
        if not _is_relative_to_path(path, self.assets_root):
            return None
        return path.relative_to(self.assets_root).as_posix()

    def _load_job_index(self) -> list[dict[str, Any]]:
        if not self.jobs_dir.is_dir():
            return []

        entries: list[dict[str, Any]] = []
        for job_dir in sorted(
            (path for path in self.jobs_dir.iterdir() if path.is_dir()),
            reverse=True,
        ):
            entry = self._load_job_index_entry(job_dir)
            if entry is not None:
                entries.append(entry)
        return entries

    def _load_job_index_entry(self, job_dir: Path) -> dict[str, Any] | None:
        result_payload = _read_json_file(job_dir / "result.json")
        triggered_at = (
            _parse_datetime(result_payload.get("triggered_at"), self._local_timezone)
            or _parse_datetime_from_job_id(job_dir.name, self._local_timezone)
        )
        if triggered_at is None:
            input_payload = _read_json_file(job_dir / "input.json")
            triggered_at = _parse_datetime(
                input_payload.get("triggered_at"),
                self._local_timezone,
            )
        if triggered_at is None:
            return None
        return {
            "job_id": job_dir.name,
            "job_dir": job_dir,
            "triggered_at": triggered_at,
            "triggered_date": triggered_at.date().isoformat(),
            "status": _first_text(result_payload.get("status")) or "unknown",
            "result_payload": result_payload,
        }

    def _load_preview_summaries(
        self,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for entry in entries:
            summary = self._load_job_summary(
                entry["job_dir"],
                index_entry=entry,
            )
            if summary is not None:
                summaries.append(summary)
        return summaries

    def _load_jobs(self) -> list[dict[str, Any]]:
        return self._load_preview_summaries(self._load_job_index())

    def _load_job_summary(
        self,
        job_dir: Path,
        *,
        index_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        indexed_result_payload = (index_entry or {}).get("result_payload")
        result_payload = (
            indexed_result_payload
            if isinstance(indexed_result_payload, dict)
            else _read_json_file(job_dir / "result.json")
        )
        input_payload = _read_json_file(job_dir / "input.json")

        indexed_triggered_at = (index_entry or {}).get("triggered_at")
        triggered_at = (
            indexed_triggered_at
            if isinstance(indexed_triggered_at, datetime)
            else None
        )
        triggered_at = (
            triggered_at
            or _parse_datetime(result_payload.get("triggered_at"), self._local_timezone)
            or _parse_datetime(input_payload.get("triggered_at"), self._local_timezone)
            or _parse_datetime_from_job_id(job_dir.name, self._local_timezone)
        )
        if triggered_at is None:
            return None

        asset_payload: dict[str, Any] | None = None
        selected_tag = _first_text(result_payload.get("selected_tag"))
        if selected_tag is None:
            llm_tag_payload = _read_json_file(job_dir / "llm-tag.json")
            asset_payload = _read_json_file(job_dir / "selected-asset.json")
            selected_tag = _first_text(
                _read_text_file(job_dir / "tag.txt"),
                llm_tag_payload.get("tag"),
                asset_payload.get("selected_tag"),
            )
        selected_tag = selected_tag or ""

        llm_profile_name = _first_text(result_payload.get("llm_profile_name"))
        if llm_profile_name is None:
            llm_payload = _read_json_file(job_dir / "selected-llm-profile.json")
            llm_profile_name = _first_text(llm_payload.get("profile_name"))

        asset_path = _first_text(result_payload.get("asset_path"))
        if asset_path is None:
            if asset_payload is None:
                asset_payload = _read_json_file(job_dir / "selected-asset.json")
            asset_path = _first_text(asset_payload.get("asset_path"))
        has_preview_image = (job_dir / "composed-ticket.png").is_file()
        if not has_preview_image:
            has_preview_image = self.resolve_preview_image(job_dir.name) is not None
        error_message = _first_text(result_payload.get("error"))

        return {
            "job_id": job_dir.name,
            "triggered_at": triggered_at.isoformat(),
            "triggered_date": triggered_at.date().isoformat(),
            "triggered_time": triggered_at.strftime("%H:%M:%S"),
            "status": (index_entry or {}).get("status")
            or _first_text(result_payload.get("status"))
            or "unknown",
            "fortune": _read_text_file(job_dir / "fortune.txt"),
            "llm_profile_name": llm_profile_name or "",
            "selected_tag": selected_tag,
            "trigger_source": _first_text(input_payload.get("trigger_source")) or "",
            "asset_name": Path(asset_path).name if asset_path else "",
            "used_fallback": bool(result_payload.get("used_fallback", False)),
            "manual_print": bool(result_payload.get("manual_print", False)),
            "dry_run": bool(result_payload.get("dry_run", False)),
            "error": error_message or "",
            "image_url": f"/preview/{quote(job_dir.name, safe='')}" if has_preview_image else "",
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

        stat = self.log_path.stat()
        tail_lines = _tail_text_lines(self.log_path, max_lines=self.log_lines)
        updated_at = datetime.fromtimestamp(
            stat.st_mtime,
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

    def _read_bluetooth_status(self) -> dict[str, Any]:
        if not self.bluetooth_status_path.is_file():
            return {
                "path": str(self.bluetooth_status_path),
                "exists": False,
                "status": "unknown",
                "message": "Bluetooth status has not been reported yet.",
                "updated_at": "",
                "age_seconds": None,
                "last_error": "",
                "failure_count": None,
                "backend": self.config.bluetooth.backend,
                "mac_address": self.config.bluetooth.mac_address,
            }

        payload = _read_json_file(self.bluetooth_status_path)
        updated_at = _parse_datetime(payload.get("updated_at"), self._local_timezone)
        age_seconds = (
            max(0.0, (datetime.now(self._local_timezone) - updated_at).total_seconds())
            if updated_at is not None
            else None
        )
        status = _first_text(payload.get("status")) or "unknown"
        return {
            "path": str(self.bluetooth_status_path),
            "exists": True,
            "status": status,
            "message": _first_text(payload.get("message")) or "",
            "updated_at": updated_at.isoformat() if updated_at is not None else "",
            "age_seconds": age_seconds,
            "last_error": _first_text(payload.get("last_error")) or "",
            "failure_count": _optional_number(payload.get("failure_count")),
            "backend": _first_text(payload.get("backend")) or self.config.bluetooth.backend,
            "mac_address": _first_text(payload.get("mac_address")) or self.config.bluetooth.mac_address,
            "adapter_name": _first_text(payload.get("adapter_name")) or "",
            "last_success_at": _first_text(payload.get("last_success_at")) or "",
            "keepalive_supported": bool(payload.get("keepalive_supported", False)),
            "dry_run": bool(payload.get("dry_run", False)),
            "stale": bool(age_seconds is not None and age_seconds > LOG_STALE_SECONDS),
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


def _build_health_response(snapshot: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
    service = snapshot.get("service")
    bluetooth = snapshot.get("bluetooth")
    service_payload = service if isinstance(service, dict) else {}
    bluetooth_payload = bluetooth if isinstance(bluetooth, dict) else {}
    service_running = (
        service_payload.get("level") == "healthy"
        and service_payload.get("label") == "running"
    )
    bluetooth_running = bluetooth_payload.get("status") == "connected"
    healthy = service_running and bluetooth_running
    payload = {
        "ok": healthy,
        "status": "healthy" if healthy else "unhealthy",
        "service": {
            "running": service_running,
            "level": service_payload.get("level") or "unknown",
            "label": service_payload.get("label") or "unknown",
        },
        "bluetooth": {
            "running": bluetooth_running,
            "status": bluetooth_payload.get("status") or "unknown",
            "updated_at": bluetooth_payload.get("updated_at") or "",
            "last_error": bluetooth_payload.get("last_error") or "",
        },
    }
    return payload, HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE


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

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle_delete()

        def log_message(self, format: str, *args: Any) -> None:
            LOGGER.debug("%s - %s", self.address_string(), format % args)

        def _handle_request(self, *, send_body: bool) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_DASHBOARD_HTML, send_body=send_body)
                return
            if parsed.path in {"/print", "/printer"}:
                self._send_html(_PRINTER_DASHBOARD_HTML, send_body=send_body)
                return
            if parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                selected_date = query.get("date", [""])[0]
                self._send_json(
                    builder.build_snapshot(selected_date=selected_date),
                    send_body=send_body,
                )
                return
            if parsed.path == "/api/manual-history":
                self._send_json(
                    {"ok": True, "history": builder.list_manual_history()},
                    send_body=send_body,
                )
                return
            if parsed.path.startswith("/manual-history/") and parsed.path.endswith("/image"):
                history_id = parsed.path.removeprefix("/manual-history/").removesuffix("/image")
                image_path = builder.resolve_manual_history_image(history_id)
                if image_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "manual history image not found")
                    return
                query = parse_qs(parsed.query)
                self._send_file(
                    image_path,
                    send_body=send_body,
                    download_name=(
                        f"callroo-manual-{_safe_history_id(history_id)}.png"
                        if query.get("download", [""])[0]
                        else None
                    ),
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
                payload, status = _build_health_response(builder.build_snapshot())
                self._send_json(payload, send_body=send_body, status=status)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def _handle_post(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in REST_TEXT_PRINT_ENDPOINTS:
                    payload = self._read_rest_body()
                    result = _queue_rest_text_print(builder.config, payload)
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path in REST_IMAGE_PRINT_ENDPOINTS:
                    payload = self._read_rest_body()
                    result = _queue_rest_image_print(builder.config, payload)
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return

                payload = self._read_json_body()
                if parsed.path == "/api/print":
                    result = _queue_dashboard_print(builder.config, payload)
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path == "/api/manual-print":
                    result = _queue_manual_print(builder.config, payload)
                    builder.clear_cache()
                    self._send_json(result, send_body=True)
                    return
                if parsed.path.startswith("/api/manual-history/") and parsed.path.endswith("/reprint"):
                    history_id = (
                        parsed.path.removeprefix("/api/manual-history/").removesuffix("/reprint")
                    )
                    result = _queue_manual_history_reprint(builder.config, history_id)
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

        def _handle_delete(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path.startswith("/api/manual-history/"):
                    history_id = parsed.path.removeprefix("/api/manual-history/")
                    result = _delete_manual_history(builder.config, history_id)
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
            except Exception as exc:
                LOGGER.exception("Dashboard DELETE failed")
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
            raw_body = self._read_body_bytes(max_bytes=MAX_UPLOAD_BYTES * 2)
            if not raw_body:
                return {}
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _read_rest_body(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type == "multipart/form-data":
                raw_body = self._read_body_bytes(max_bytes=MAX_UPLOAD_BYTES * 2)
                return _parse_multipart_form(content_type, raw_body)
            if media_type == "application/x-www-form-urlencoded":
                raw_body = self._read_body_bytes(max_bytes=MAX_UPLOAD_BYTES * 2)
                fields = parse_qs(
                    raw_body.decode("utf-8"),
                    keep_blank_values=True,
                )
                return {key: values[-1] if values else "" for key, values in fields.items()}
            return self._read_json_body()

        def _read_body_bytes(self, *, max_bytes: int) -> bytes:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if content_length <= 0:
                return b""
            if content_length > max_bytes:
                raise ValueError("request body too large")
            return self.rfile.read(content_length)

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

        def _send_file(
            self,
            path: Path,
            *,
            send_body: bool,
            download_name: str | None = None,
        ) -> None:
            payload = path.read_bytes()
            mime_type, _ = mimetypes.guess_type(path.name)
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                mime_type or "application/octet-stream",
            )
            self.send_header("Cache-Control", "public, max-age=60")
            if download_name:
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename={download_name}",
                )
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
    _append_jsonl_record(trigger_path, trigger_payload)
    return {
        "ok": True,
        "request_id": request_id,
        "queued_at": requested_at,
        "trigger_path": str(trigger_path),
    }


def _queue_manual_print(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    requested_at = datetime.now().astimezone().isoformat()
    text = _manual_print_text(payload.get("text"))
    border_style = _manual_choice(
        payload.get("border_style"),
        allowed=MANUAL_BORDER_STYLES,
        default="thin",
    )
    text_align = _manual_choice(
        payload.get("text_align"),
        allowed=MANUAL_TEXT_ALIGNS,
        default="center",
    )
    text_vertical_align = _manual_choice(
        payload.get("text_vertical_align") or payload.get("vertical_align"),
        allowed=MANUAL_TEXT_VERTICAL_ALIGNS,
        default="center",
    )
    font_size = _manual_font_size(payload.get("font_size"), config.layout.body_font_size)
    label_width_px = _manual_label_width(payload.get("label_width_px"), config)
    label_height_px = _manual_label_height(payload.get("label_height_px"))
    content_margin_px = _manual_content_margin(
        _first_present(
            payload,
            "content_margin_px",
            "content_margin",
            "margin_px",
            "margin",
            "padding_px",
            "padding",
        )
    )
    image_scale_percent = _manual_number_range(
        payload.get("image_scale_percent"),
        default=100,
        minimum=MANUAL_IMAGE_SCALE_MIN,
        maximum=MANUAL_IMAGE_SCALE_MAX,
    )
    image_crop = bool(payload.get("image_crop", False))
    image_rotation_degrees = _manual_number_range(
        payload.get("image_rotation_degrees"),
        default=0,
        minimum=MANUAL_IMAGE_ROTATION_MIN,
        maximum=MANUAL_IMAGE_ROTATION_MAX,
    )
    image_items = _save_manual_image_items(
        config.output.outputs_dir,
        request_id=request_id,
        payload=payload,
        label_width_px=label_width_px,
        label_height_px=label_height_px,
        content_margin_px=content_margin_px,
    )
    text_items = _save_manual_text_items(
        payload=payload,
        label_width_px=label_width_px,
        label_height_px=label_height_px,
        content_margin_px=content_margin_px,
        font_size=font_size,
        text_align=text_align,
        text_vertical_align=text_vertical_align,
    )
    image_path = Path(str(image_items[0]["path"])) if image_items else None
    image_name = ", ".join(str(item.get("filename", "")) for item in image_items if item.get("filename"))

    if not text and not image_items and not text_items:
        raise ValueError("manual print requires text or image")

    history_metadata = {
        "request_id": request_id,
        "created_at": requested_at,
        "queued_at": requested_at,
        "text": text,
        "border_style": border_style,
        "text_align": text_align,
        "text_vertical_align": text_vertical_align,
        "font_size": font_size,
        "label_width_px": label_width_px,
        "label_height_px": label_height_px,
        "content_margin_px": content_margin_px,
        "image_scale_percent": image_scale_percent,
        "image_crop": image_crop,
        "image_rotation_degrees": image_rotation_degrees,
        "image_path": str(image_path or ""),
        "image_name": image_name,
        "images": _serializable_image_items(image_items),
        "text_items": _serializable_text_items(text_items),
    }
    try:
        _save_manual_history(config, request_id, history_metadata, image_path=image_path)
    except Exception:
        _cleanup_manual_upload_items(image_items)
        raise

    trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_payload = {
        "request_id": request_id,
        "requested_at": requested_at,
        "raw_input": text or "\n",
        "note": "manual-print",
        "manual_print": {
            "text": text,
            "border_style": border_style,
            "text_align": text_align,
            "text_vertical_align": text_vertical_align,
            "font_size": font_size,
            "label_width_px": label_width_px,
            "label_height_px": label_height_px,
            "content_margin_px": content_margin_px,
            "image_scale_percent": image_scale_percent,
            "image_crop": image_crop,
            "image_rotation_degrees": image_rotation_degrees,
            "image_path": str(image_path or ""),
            "image_name": image_name,
            "images": _serializable_image_items(image_items),
            "text_items": _serializable_text_items(text_items),
        },
    }
    try:
        _append_jsonl_record(trigger_path, trigger_payload)
    except Exception:
        _cleanup_manual_upload_items(image_items)
        _delete_manual_history(config, request_id, missing_ok=True)
        raise

    return {
        "ok": True,
        "request_id": request_id,
        "queued_at": requested_at,
        "trigger_path": str(trigger_path),
        "has_image": bool(image_items),
        "image_count": len(image_items),
        "text_chars": len(text) or sum(len(str(item.get("text", ""))) for item in text_items),
        "text_count": len(text_items) if text_items else (1 if text else 0),
        "border_style": border_style,
        "content_margin_px": content_margin_px,
        "history_url": f"/manual-history/{quote(request_id, safe='')}/image",
        "download_url": f"/manual-history/{quote(request_id, safe='')}/image?download=1",
    }


def _queue_manual_history_reprint(config: AppConfig, history_id: str) -> dict[str, Any]:
    safe_id = _safe_history_id(history_id)
    if safe_id is None:
        raise ValueError("invalid manual history id")
    history_dir = config.output.outputs_dir / MANUAL_HISTORY_DIRNAME / safe_id
    metadata = _read_json_file(history_dir / "manual-print.json")
    if not metadata:
        raise ValueError("manual history entry not found")

    request_id = uuid.uuid4().hex[:12]
    requested_at = datetime.now().astimezone().isoformat()
    text = _manual_print_text(metadata.get("text"))
    border_style = _manual_choice(
        metadata.get("border_style"),
        allowed=MANUAL_BORDER_STYLES,
        default="thin",
    )
    text_align = _manual_choice(
        metadata.get("text_align"),
        allowed=MANUAL_TEXT_ALIGNS,
        default="center",
    )
    text_vertical_align = _manual_choice(
        metadata.get("text_vertical_align") or metadata.get("vertical_align"),
        allowed=MANUAL_TEXT_VERTICAL_ALIGNS,
        default="center",
    )
    font_size = _manual_font_size(metadata.get("font_size"), config.layout.body_font_size)
    label_width_px = _manual_label_width(metadata.get("label_width_px"), config)
    label_height_px = _manual_label_height(metadata.get("label_height_px"))
    content_margin_px = _manual_content_margin(
        _first_present(
            metadata,
            "content_margin_px",
            "content_margin",
            "margin_px",
            "margin",
            "padding_px",
            "padding",
        )
    )
    image_scale_percent = _manual_number_range(
        metadata.get("image_scale_percent"),
        default=100,
        minimum=MANUAL_IMAGE_SCALE_MIN,
        maximum=MANUAL_IMAGE_SCALE_MAX,
    )
    image_crop = bool(metadata.get("image_crop", False))
    image_rotation_degrees = _manual_number_range(
        metadata.get("image_rotation_degrees"),
        default=0,
        minimum=MANUAL_IMAGE_ROTATION_MIN,
        maximum=MANUAL_IMAGE_ROTATION_MAX,
    )
    image_items, legacy_image_path = _copy_manual_history_images(
        config.output.outputs_dir,
        request_id=request_id,
        metadata=metadata,
    )
    text_items = _copy_manual_history_text_items(
        metadata=metadata,
        label_width_px=label_width_px,
        label_height_px=label_height_px,
        content_margin_px=content_margin_px,
        font_size=font_size,
        text_align=text_align,
        text_vertical_align=text_vertical_align,
    )
    image_path = Path(str(image_items[0]["path"])) if image_items else legacy_image_path
    image_name = (
        ", ".join(
            str(item.get("filename", ""))
            for item in image_items
            if item.get("filename")
        )
        or _first_text(metadata.get("image_name"))
        or ""
    )

    if not text and not image_path and not image_items and not text_items:
        raise ValueError("manual history entry has no printable content")

    history_metadata = {
        "request_id": request_id,
        "created_at": requested_at,
        "queued_at": requested_at,
        "reprinted_from": safe_id,
        "text": text,
        "border_style": border_style,
        "text_align": text_align,
        "text_vertical_align": text_vertical_align,
        "font_size": font_size,
        "label_width_px": label_width_px,
        "label_height_px": label_height_px,
        "content_margin_px": content_margin_px,
        "image_scale_percent": image_scale_percent,
        "image_crop": image_crop,
        "image_rotation_degrees": image_rotation_degrees,
        "image_path": str(image_path or ""),
        "image_name": image_name,
        "images": _serializable_image_items(image_items),
        "text_items": _serializable_text_items(text_items),
    }
    try:
        _save_manual_history(config, request_id, history_metadata, image_path=image_path)
    except Exception:
        _cleanup_manual_upload_items(image_items)
        if legacy_image_path is not None:
            _unlink_if_exists(legacy_image_path)
        raise

    trigger_path = config.output.outputs_dir / "dashboard-triggers.jsonl"
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_payload = {
        "request_id": request_id,
        "requested_at": requested_at,
        "raw_input": text or "\n",
        "note": "manual-reprint",
        "manual_print": {
            "text": text,
            "border_style": border_style,
            "text_align": text_align,
            "text_vertical_align": text_vertical_align,
            "font_size": font_size,
            "label_width_px": label_width_px,
            "label_height_px": label_height_px,
            "content_margin_px": content_margin_px,
            "image_scale_percent": image_scale_percent,
            "image_crop": image_crop,
            "image_rotation_degrees": image_rotation_degrees,
            "image_path": str(image_path or ""),
            "image_name": image_name,
            "images": _serializable_image_items(image_items),
            "text_items": _serializable_text_items(text_items),
        },
    }
    try:
        _append_jsonl_record(trigger_path, trigger_payload)
    except Exception:
        _cleanup_manual_upload_items(image_items)
        if legacy_image_path is not None:
            _unlink_if_exists(legacy_image_path)
        _delete_manual_history(config, request_id, missing_ok=True)
        raise

    return {
        "ok": True,
        "request_id": request_id,
        "queued_at": requested_at,
        "trigger_path": str(trigger_path),
        "reprinted_from": safe_id,
        "has_image": bool(image_path or image_items),
        "image_count": len(image_items) if image_items else (1 if image_path else 0),
        "text_chars": len(text) or sum(len(str(item.get("text", ""))) for item in text_items),
        "text_count": len(text_items) if text_items else (1 if text else 0),
        "border_style": border_style,
        "content_margin_px": content_margin_px,
        "history_url": f"/manual-history/{quote(request_id, safe='')}/image",
        "download_url": f"/manual-history/{quote(request_id, safe='')}/image?download=1",
    }


def _queue_rest_text_print(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    manual_payload = _rest_manual_options(payload)
    manual_payload["text"] = _required_text(payload.get("text"), "text")
    return _queue_manual_print(config, manual_payload)


def _queue_rest_image_print(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    manual_payload = _rest_manual_options(payload)
    label_width_px = _manual_label_width(manual_payload.get("label_width_px"), config)
    label_height_px = _manual_label_height(manual_payload.get("label_height_px"))
    content_margin_px = _manual_content_margin(manual_payload.get("content_margin_px"))
    manual_payload["label_width_px"] = label_width_px
    manual_payload["label_height_px"] = label_height_px
    manual_payload["content_margin_px"] = content_margin_px
    image_item = _rest_image_source(payload)
    image_item.update(
        _rest_image_item_options(
            payload,
            label_width_px,
            label_height_px,
            content_margin_px,
        )
    )
    manual_payload["text"] = _optional_text(payload.get("text")) or ""
    manual_payload["images"] = [image_item]
    return _queue_manual_print(config, manual_payload)


def _rest_manual_options(payload: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    _copy_first_present(options, payload, "label_width_px", "label_width_px", "label_width")
    _copy_first_present(options, payload, "label_height_px", "label_height_px", "label_height")
    _copy_first_present(
        options,
        payload,
        "content_margin_px",
        "content_margin_px",
        "content_margin",
        "margin_px",
        "margin",
        "padding_px",
        "padding",
    )
    _copy_first_present(options, payload, "border_style", "border_style", "border")
    _copy_first_present(options, payload, "text_align", "text_align", "align")
    _copy_first_present(
        options,
        payload,
        "text_vertical_align",
        "text_vertical_align",
        "vertical_align",
        "valign",
    )
    _copy_first_present(options, payload, "font_size", "font_size")
    return options


def _rest_image_item_options(
    payload: dict[str, Any],
    label_width_px: int,
    label_height_px: int,
    content_margin_px: int,
) -> dict[str, Any]:
    content_width = max(1, label_width_px - (content_margin_px * 2))
    content_height = max(1, label_height_px - (content_margin_px * 2))
    item: dict[str, Any] = {
        "x": 0,
        "y": 0,
        "width": content_width,
        "height": content_height,
    }
    _copy_first_present(item, payload, "x", "x", "image_x")
    _copy_first_present(item, payload, "y", "y", "image_y")
    _copy_first_present(item, payload, "width", "image_width_px", "image_width", "width")
    _copy_first_present(item, payload, "height", "image_height_px", "image_height", "height")
    _copy_first_present(
        item,
        payload,
        "rotation_degrees",
        "rotation_degrees",
        "image_rotation_degrees",
        "rotation",
    )
    crop_value = _first_present(payload, "crop", "image_crop")
    if crop_value is not None:
        item["crop"] = _coerce_bool(crop_value)
    return item


def _rest_image_source(payload: dict[str, Any]) -> dict[str, Any]:
    image = payload.get("image")
    if image is None:
        image = payload.get("file")
    if isinstance(image, dict):
        filename = _required_text(image.get("filename"), "image.filename")
        content_base64 = _required_text(image.get("content_base64"), "image.content_base64")
        return {"filename": filename, "content_base64": content_base64}

    filename = _required_text(payload.get("filename"), "filename")
    content_base64 = _required_text(payload.get("content_base64"), "content_base64")
    return {"filename": filename, "content_base64": content_base64}


def _parse_multipart_form(content_type: str, raw_body: bytes) -> dict[str, Any]:
    if not raw_body:
        return {}
    message = BytesParser(policy=EMAIL_POLICY).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n"
        ).encode("utf-8")
        + raw_body
    )
    if not message.is_multipart():
        raise ValueError("multipart request is invalid")

    payload: dict[str, Any] = {}
    first_file: dict[str, Any] | None = None
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        raw_part = part.get_payload(decode=True) or b""
        if filename:
            file_payload = {
                "filename": Path(filename).name,
                "content_base64": base64.b64encode(raw_part).decode("ascii"),
            }
            if first_file is None:
                first_file = file_payload
            payload[str(name)] = file_payload
            continue
        payload[str(name)] = raw_part.decode("utf-8", errors="replace")

    if first_file is not None and not isinstance(payload.get("image"), dict):
        payload["image"] = first_file
    return payload


def _copy_first_present(
    target: dict[str, Any],
    source: dict[str, Any],
    target_key: str,
    *source_keys: str,
) -> None:
    value = _first_present(source, *source_keys)
    if value is not None:
        target[target_key] = value


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None and source[key] != "":
            return source[key]
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _copy_manual_history_images(
    outputs_dir: Path,
    *,
    request_id: str,
    metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], Path | None]:
    copied_paths: list[Path] = []
    try:
        raw_items = metadata.get("images")
        copied_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list) and raw_items:
            for index, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                source_path = _resolve_manual_upload_path(item.get("path"), outputs_dir)
                filename = _first_text(item.get("filename")) or source_path.name
                target_path = _copy_manual_upload_for_request(
                    outputs_dir,
                    request_id=request_id,
                    source_path=source_path,
                    filename=filename,
                    index=index,
                )
                copied_paths.append(target_path)
                copied_items.append(
                    {
                        "id": _optional_text(item.get("id")) or f"image-{index + 1}",
                        "filename": filename,
                        "path": str(target_path),
                        "x": _manual_number_range(
                            item.get("x"),
                            default=0,
                            minimum=-MANUAL_LABEL_HEIGHT_MAX,
                            maximum=MANUAL_LABEL_HEIGHT_MAX,
                        ),
                        "y": _manual_number_range(
                            item.get("y"),
                            default=0,
                            minimum=-MANUAL_LABEL_HEIGHT_MAX,
                            maximum=MANUAL_LABEL_HEIGHT_MAX,
                        ),
                        "width": _manual_number_range(
                            item.get("width"),
                            default=1,
                            minimum=1,
                            maximum=MANUAL_LABEL_HEIGHT_MAX,
                        ),
                        "height": _manual_number_range(
                            item.get("height"),
                            default=1,
                            minimum=1,
                            maximum=MANUAL_LABEL_HEIGHT_MAX,
                        ),
                        "rotation_degrees": _manual_number_range(
                            item.get("rotation_degrees"),
                            default=0,
                            minimum=MANUAL_IMAGE_ROTATION_MIN,
                            maximum=MANUAL_IMAGE_ROTATION_MAX,
                        ),
                        "crop": bool(item.get("crop", False)),
                    }
                )
            return copied_items, None

        legacy_path = _first_text(metadata.get("image_path"))
        if legacy_path is None:
            return [], None
        source_path = _resolve_manual_upload_path(legacy_path, outputs_dir)
        filename = _first_text(metadata.get("image_name")) or source_path.name
        target_path = _copy_manual_upload_for_request(
            outputs_dir,
            request_id=request_id,
            source_path=source_path,
            filename=filename,
            index=0,
        )
        copied_paths.append(target_path)
        return [], target_path
    except Exception:
        for path in copied_paths:
            _unlink_if_exists(path)
        raise


def _copy_manual_history_text_items(
    *,
    metadata: dict[str, Any],
    label_width_px: int,
    label_height_px: int,
    content_margin_px: int,
    font_size: int,
    text_align: str,
    text_vertical_align: str,
) -> list[dict[str, Any]]:
    raw_items = metadata.get("text_items")
    if not isinstance(raw_items, list) or not raw_items:
        return []

    text_items: list[dict[str, Any]] = []
    content_width = max(1, label_width_px - (content_margin_px * 2))
    content_height = max(1, label_height_px - (content_margin_px * 2))
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        item_text = _manual_print_text(item.get("text"))
        if not item_text:
            continue
        text_items.append(
            {
                "id": _optional_text(item.get("id")) or f"text-{index + 1}",
                "text": item_text,
                "x": _manual_number_range(
                    item.get("x"),
                    default=0,
                    minimum=-content_width,
                    maximum=content_width,
                ),
                "y": _manual_number_range(
                    item.get("y"),
                    default=0,
                    minimum=-content_height,
                    maximum=content_height,
                ),
                "width": _manual_number_range(
                    item.get("width"),
                    default=content_width,
                    minimum=8,
                    maximum=max(8, content_width * 2),
                ),
                "height": _manual_number_range(
                    item.get("height"),
                    default=content_height,
                    minimum=8,
                    maximum=max(8, content_height * 2),
                ),
                "font_size": _manual_number_range(
                    item.get("font_size"),
                    default=font_size,
                    minimum=MANUAL_FONT_SIZE_MIN,
                    maximum=MANUAL_FONT_SIZE_MAX,
                ),
                "text_align": _manual_choice(
                    item.get("text_align"),
                    allowed=MANUAL_TEXT_ALIGNS,
                    default=text_align,
                ),
                "vertical_align": _manual_choice(
                    item.get("vertical_align"),
                    allowed=MANUAL_TEXT_VERTICAL_ALIGNS,
                    default=text_vertical_align,
                ),
            }
        )
    return text_items


def _resolve_manual_upload_path(value: Any, outputs_dir: Path) -> Path:
    raw_path = _required_text(value, "manual image path")
    candidate = Path(raw_path).expanduser().resolve()
    uploads_dir = (outputs_dir / MANUAL_UPLOADS_DIRNAME).resolve()
    if not _is_relative_to(candidate, uploads_dir):
        raise ValueError("manual history image must be inside manual uploads directory")
    if not candidate.is_file():
        raise ValueError("manual history image file not found")
    if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("manual history image file is not supported")
    return candidate


def _copy_manual_upload_for_request(
    outputs_dir: Path,
    *,
    request_id: str,
    source_path: Path,
    filename: str,
    index: int,
) -> Path:
    target_path = _manual_upload_path(
        outputs_dir,
        request_id=request_id,
        filename=f"{index + 1:02d}-{Path(filename).name}",
    )
    _atomic_write_bytes(target_path, source_path.read_bytes())
    return target_path


def _append_jsonl_record(path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    with path.open("ab+") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() > 0:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write(line)


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

    previous_content = target_path.read_bytes() if target_path.is_file() else None
    _atomic_write_bytes(target_path, content)
    try:
        if changed_config:
            _write_config_payload(config_path, config_payload)
    except Exception:
        if previous_content is None:
            _unlink_if_exists(target_path)
        else:
            _atomic_write_bytes(target_path, previous_content)
        raise

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
    _atomic_write_text(
        config_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        _unlink_if_exists(tmp_path)
        raise


def _unlink_if_exists(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


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


def _save_manual_upload(
    outputs_dir: Path,
    *,
    request_id: str,
    filename: str,
    content_base64: str,
) -> Path:
    target_path = _manual_upload_path(outputs_dir, request_id=request_id, filename=filename)
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image.content_base64 is not valid base64") from exc
    if not content:
        raise ValueError("manual image file is empty")
    if len(content) > MANUAL_PRINT_IMAGE_MAX_BYTES:
        raise ValueError("manual image file is too large")
    _validate_image_content(content)
    _atomic_write_bytes(target_path, content)
    return target_path


def _save_manual_image_items(
    outputs_dir: Path,
    *,
    request_id: str,
    payload: dict[str, Any],
    label_width_px: int,
    label_height_px: int,
    content_margin_px: int,
) -> list[dict[str, Any]]:
    raw_items = payload.get("images")
    if raw_items is None:
        legacy = payload.get("image")
        raw_items = [legacy] if legacy is not None else []
    if not isinstance(raw_items, list):
        raise ValueError("images must be a list")

    image_items: list[dict[str, Any]] = []
    content_width = max(1, label_width_px - (content_margin_px * 2))
    content_height = max(1, label_height_px - (content_margin_px * 2))
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError("image entries must be objects")
        filename = _required_text(item.get("filename"), f"images[{index}].filename")
        content_base64 = _required_text(
            item.get("content_base64"),
            f"images[{index}].content_base64",
        )
        clean_name = f"{index + 1:02d}-{Path(filename).name}"
        target_path = _save_manual_upload(
            outputs_dir,
            request_id=request_id,
            filename=clean_name,
            content_base64=content_base64,
        )
        item_width = _manual_number_range(
            item.get("width"),
            default=min(content_width, 180),
            minimum=8,
            maximum=max(8, content_width * 2),
        )
        item_height = _manual_number_range(
            item.get("height"),
            default=min(content_height, 120),
            minimum=8,
            maximum=max(8, content_height * 2),
        )
        image_items.append(
            {
                "id": _optional_text(item.get("id")) or f"image-{index + 1}",
                "filename": filename,
                "path": str(target_path),
                "x": _manual_number_range(
                    item.get("x"),
                    default=0,
                    minimum=-content_width,
                    maximum=content_width,
                ),
                "y": _manual_number_range(
                    item.get("y"),
                    default=0,
                    minimum=-content_height,
                    maximum=content_height,
                ),
                "width": item_width,
                "height": item_height,
                "rotation_degrees": _manual_number_range(
                    item.get("rotation_degrees"),
                    default=0,
                    minimum=MANUAL_IMAGE_ROTATION_MIN,
                    maximum=MANUAL_IMAGE_ROTATION_MAX,
                ),
                "crop": bool(item.get("crop", False)),
            }
        )
    return image_items


def _save_manual_text_items(
    *,
    payload: dict[str, Any],
    label_width_px: int,
    label_height_px: int,
    content_margin_px: int,
    font_size: int,
    text_align: str,
    text_vertical_align: str,
) -> list[dict[str, Any]]:
    raw_items = payload.get("text_items")
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise ValueError("text_items must be a list")

    text_items: list[dict[str, Any]] = []
    content_width = max(1, label_width_px - (content_margin_px * 2))
    content_height = max(1, label_height_px - (content_margin_px * 2))
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError("text item entries must be objects")
        item_text = _manual_print_text(item.get("text"))
        if not item_text:
            continue
        item_width = _manual_number_range(
            item.get("width"),
            default=content_width,
            minimum=8,
            maximum=max(8, content_width * 2),
        )
        item_height = _manual_number_range(
            item.get("height"),
            default=content_height,
            minimum=8,
            maximum=max(8, content_height * 2),
        )
        text_items.append(
            {
                "id": _optional_text(item.get("id")) or f"text-{index + 1}",
                "text": item_text,
                "x": _manual_number_range(
                    item.get("x"),
                    default=0,
                    minimum=-content_width,
                    maximum=content_width,
                ),
                "y": _manual_number_range(
                    item.get("y"),
                    default=0,
                    minimum=-content_height,
                    maximum=content_height,
                ),
                "width": item_width,
                "height": item_height,
                "font_size": _manual_number_range(
                    item.get("font_size"),
                    default=font_size,
                    minimum=MANUAL_FONT_SIZE_MIN,
                    maximum=MANUAL_FONT_SIZE_MAX,
                ),
                "text_align": _manual_choice(
                    item.get("text_align"),
                    allowed=MANUAL_TEXT_ALIGNS,
                    default=text_align,
                ),
                "vertical_align": _manual_choice(
                    item.get("vertical_align"),
                    allowed=MANUAL_TEXT_VERTICAL_ALIGNS,
                    default=text_vertical_align,
                ),
            }
        )
    return text_items


def _serializable_image_items(image_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.get("id", "")),
            "filename": str(item.get("filename", "")),
            "path": str(item.get("path", "")),
            "x": int(item.get("x", 0)),
            "y": int(item.get("y", 0)),
            "width": int(item.get("width", 1)),
            "height": int(item.get("height", 1)),
            "rotation_degrees": int(item.get("rotation_degrees", 0)),
            "crop": bool(item.get("crop", False)),
        }
        for item in image_items
    ]


def _serializable_text_items(text_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.get("id", "")),
            "text": str(item.get("text", "")),
            "x": int(item.get("x", 0)),
            "y": int(item.get("y", 0)),
            "width": int(item.get("width", 1)),
            "height": int(item.get("height", 1)),
            "font_size": int(item.get("font_size", 28)),
            "text_align": str(item.get("text_align", "center")),
            "vertical_align": str(item.get("vertical_align", "center")),
        }
        for item in text_items
    ]


def _cleanup_manual_upload_items(image_items: list[dict[str, Any]]) -> None:
    for item in image_items:
        raw_path = str(item.get("path", "")).strip()
        if raw_path:
            _unlink_if_exists(Path(raw_path))


def _save_manual_history(
    config: AppConfig,
    request_id: str,
    metadata: dict[str, Any],
    *,
    image_path: Path | None,
) -> Path:
    history_id = _safe_history_id(request_id)
    if history_id is None:
        raise ValueError("invalid manual history id")
    history_dir = (config.output.outputs_dir / MANUAL_HISTORY_DIRNAME / history_id).resolve()
    history_root = (config.output.outputs_dir / MANUAL_HISTORY_DIRNAME).resolve()
    if not _is_relative_to(history_dir, history_root):
        raise ValueError("manual history path escapes history directory")

    rendered = compose_manual_print(
        text=str(metadata.get("text") or ""),
        image_path=image_path,
        image_items=[
            {
                **item,
                "path": Path(str(item.get("path", ""))),
            }
            for item in metadata.get("images", [])
            if isinstance(item, dict) and str(item.get("path", "")).strip()
        ],
        text_items=[
            dict(item)
            for item in metadata.get("text_items", [])
            if isinstance(item, dict)
        ],
        printed_at=datetime.now().astimezone(),
        config=config.layout,
        border_style=str(metadata.get("border_style") or "thin"),
        text_align=str(metadata.get("text_align") or "center"),
        text_vertical_align=str(
            metadata.get("text_vertical_align") or metadata.get("vertical_align") or "center"
        ),
        font_size=int(metadata.get("font_size") or config.layout.body_font_size),
        label_width_px=int(metadata.get("label_width_px") or 0),
        label_height_px=int(metadata.get("label_height_px") or 0),
        content_margin_px=_manual_content_margin(metadata.get("content_margin_px")),
        image_scale_percent=int(metadata.get("image_scale_percent") or 100),
        image_crop=bool(metadata.get("image_crop", False)),
        image_rotation_degrees=int(metadata.get("image_rotation_degrees") or 0),
    )
    history_dir.mkdir(parents=True, exist_ok=True)
    image_buffer = BytesIO()
    rendered.save(image_buffer, format="PNG")
    _atomic_write_bytes(history_dir / "composed-ticket.png", image_buffer.getvalue())
    _atomic_write_text(
        history_dir / "manual-print.json",
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
    )
    return history_dir


def _delete_manual_history(
    config: AppConfig,
    history_id: str,
    *,
    missing_ok: bool = False,
) -> dict[str, Any]:
    safe_id = _safe_history_id(history_id)
    if safe_id is None:
        raise ValueError("invalid manual history id")
    history_root = (config.output.outputs_dir / MANUAL_HISTORY_DIRNAME).resolve()
    target_dir = (history_root / safe_id).resolve()
    if not _is_relative_to(target_dir, history_root):
        raise ValueError("manual history path escapes history directory")
    if not target_dir.exists():
        if missing_ok:
            return {"ok": True, "deleted": False, "id": safe_id}
        raise ValueError("manual history entry not found")
    if not target_dir.is_dir():
        raise ValueError("manual history entry is not a directory")
    shutil.rmtree(target_dir)
    return {"ok": True, "deleted": True, "id": safe_id}


def _manual_upload_path(outputs_dir: Path, *, request_id: str, filename: str) -> Path:
    clean_name = Path(filename).name.strip()
    if not clean_name or clean_name in {".", ".."}:
        raise ValueError("invalid image filename")
    suffix = Path(clean_name).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError("manual print only accepts image files")
    uploads_dir = (outputs_dir / MANUAL_UPLOADS_DIRNAME).resolve()
    target_path = (uploads_dir / request_id / clean_name).resolve()
    if not _is_relative_to(target_path, uploads_dir):
        raise ValueError("manual image path escapes uploads directory")
    return target_path


def _safe_history_id(value: str) -> str | None:
    text = unquote(str(value or "")).strip().strip("/")
    if not text or "/" in text or "\\" in text or ".." in Path(text).parts:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(char not in allowed for char in text):
        return None
    return text


def _validate_image_content(content: bytes) -> None:
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("manual print only accepts valid image files") from exc


def _manual_print_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MANUAL_PRINT_MAX_TEXT_CHARS:
        raise ValueError(f"text must be {MANUAL_PRINT_MAX_TEXT_CHARS} characters or fewer")
    return text


def _manual_choice(value: Any, *, allowed: set[str], default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


def _manual_font_size(value: Any, default: int) -> int:
    return _manual_number_range(
        value,
        default=default,
        minimum=MANUAL_FONT_SIZE_MIN,
        maximum=MANUAL_FONT_SIZE_MAX,
    )


def _manual_label_width(value: Any, config: AppConfig) -> int:
    max_width = config.layout.paper_width_px - (config.layout.side_margin_px * 2)
    return _manual_number_range(
        value,
        default=max_width,
        minimum=min(MANUAL_LABEL_WIDTH_MIN, max_width),
        maximum=max_width,
    )


def _manual_label_height(value: Any) -> int:
    return _manual_number_range(
        value,
        default=220,
        minimum=MANUAL_LABEL_HEIGHT_MIN,
        maximum=MANUAL_LABEL_HEIGHT_MAX,
    )


def _manual_content_margin(value: Any) -> int:
    return _manual_number_range(
        value,
        default=MANUAL_CONTENT_MARGIN_DEFAULT,
        minimum=MANUAL_CONTENT_MARGIN_MIN,
        maximum=MANUAL_CONTENT_MARGIN_MAX,
    )


def _manual_number_range(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


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


def _optional_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


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


def _tail_text_lines(
    path: Path,
    *,
    max_lines: int,
    block_size: int = 8192,
) -> list[str]:
    if max_lines <= 0:
        return []

    chunks: list[bytes] = []
    newline_count = 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        while position > 0 and newline_count <= max_lines:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


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


def _is_relative_to_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _absolute_path(path: Path, *, base_dir: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return base_dir / expanded


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
