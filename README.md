# callroo-printer

라즈베리파이에서 버튼 한 번으로 짧은 LLM 생성 문구와 이미지를 합성해 미니 프린터로 출력하는 헤드리스 앱입니다. 터미널 `Enter`, Linux 입력 장치, 웹 대시보드의 출력 버튼으로 트리거할 수 있습니다.

트리거가 들어오면 앱은 LLM 프로필을 가중치 기반으로 고르고, 프로필 안의 모델 후보를 순서대로 호출합니다. 모델이 JSON으로 본문과 태그를 반환하면 태그에 연결된 이미지 풀에서 그림을 하나 골라 티켓 이미지를 만들고, 오디오 효과와 함께 Bluetooth 프린터로 전송합니다. 기본 출력물은 가운데 정렬된 헤드라인, 이미지 아래 태그, 테두리 있는 본문 영역으로 합성됩니다.

## 주요 기능

- OpenAI 호환 Chat Completions endpoint 지원
- Brave Search API와 Tavily 기반 LLM `web_search` tool calling 지원
- 여러 LLM 프로필의 가중치 기반 랜덤 선택
- 프로필별 모델 fallback 지원: 예를 들어 primary Gemma를 먼저 쓰고 실패하면 Qwen으로 자동 전환
- 태그별 이미지 풀, 시작음/완료음/실패음, 반복 재생 오디오 지원
- MXW01 계열을 위한 `TiMini-Print` direct backend와 keep-alive
- 출력 입력, LLM 요청/응답, 합성 이미지, 프린터 바이너리 저장
- 실제 프린터 없이 결과물을 확인하는 `--dry-run`
- 웹 대시보드: 출력 호출, 날짜별 프리뷰, 서비스 상태, 로그, 프롬프트/프로필 편집, 아티팩트 관리

## 준비

라즈베리파이에서 아래 패키지를 먼저 준비합니다.

```bash
sudo apt update
sudo apt install -y python3-pip python3-pil python3-venv bluez bluez-tools fonts-nanum ffmpeg
git submodule update --init --recursive
bash scripts/apply_timiniprint_patches.sh
python3 -m pip install -r requirements.txt
```

`fonts-nanum` 대신 `fonts-noto-cjk`를 써도 됩니다. 한글이 깨지면 `config.json`의 `layout.font_path`를 직접 지정하세요.

## 설정

예시 설정을 복사한 뒤 로컬 값만 채웁니다. `config.json`은 `.gitignore`에 포함되어 있으므로 API key, 프린터 MAC, 대시보드 편집 토큰 같은 비밀 값은 여기에 둡니다.

```bash
cp config.example.json config.json
```

LLM 설정은 배열입니다. 각 항목이 하나의 프롬프트 프로필이고, `weight`가 클수록 더 자주 선택됩니다. 프로필 안의 `models` 배열은 순서대로 시도되는 fallback 후보입니다.

```json
{
  "dashboard": {
    "edit_token": "REPLACE_WITH_DASHBOARD_EDIT_TOKEN"
  },
  "llm": [
    {
      "name": "fortune-haiku",
      "weight": 1.0,
      "prompt": "출력은 {\"fortune\":\"...\",\"tag\":\"...\"} JSON 객체 하나뿐이다...",
      "models": [
        {
          "name": "gemma-primary",
          "endpoint": "https://your-primary-llm.example/v1/",
          "model": "gemma4",
          "api_key": null,
          "api_key_env": "PRIMARY_LLM_API_KEY"
        },
        {
          "name": "qwen-fallback",
          "endpoint": "https://your-fallback-llm.example/v1/",
          "model": "REPLACE_WITH_QWEN_FALLBACK_MODEL",
          "api_key_env": "SPARK_LLM_API_KEY",
          "enable_thinking": false
        }
      ],
      "tags": {
        "번뜩": ["vending-01.png"],
        "행운": ["mug-02.png"]
      }
    }
  ]
}
```

`llm[].api_key`가 있으면 그 값을 우선 사용하고, 비어 있으면 `api_key_env` 환경 변수에서 읽습니다. `models`를 비워두면 프로필의 `endpoint`, `model`, `api_key*` 값을 단일 모델 설정으로 사용합니다.

LLM이 최신 웹 정보를 직접 요청하게 하려면 프로필에 `web_search`를 추가합니다. `tool_calling_enabled`가 켜져 있으면 Chat Completions 요청에 `web_search` tool 스키마가 포함되고, 모델이 `tool_calls`를 반환할 때 검색 API 결과를 `role=tool` 메시지로 되돌린 뒤 최종 JSON 응답을 다시 요청합니다.

Tavily를 쓰려면 `TAVILY_API_KEY`를 환경 변수로 두거나 `api_key`에 직접 넣을 수 있습니다. 직접값이 있으면 `api_key_env`보다 우선합니다. 비밀값을 저장소에 남기지 않으려면 환경 변수 방식을 권장합니다.

```json
{
  "llm": [
    {
      "name": "fortune-haiku",
      "web_search": {
        "enabled": true,
        "provider": "tavily",
        "api_key": null,
        "api_key_env": "TAVILY_API_KEY",
        "count": 3,
        "country": "KR",
        "search_depth": "basic",
        "include_answer": false,
        "include_raw_content": false,
        "tool_calling_enabled": true,
        "tool_name": "web_search",
        "tool_max_rounds": 2,
        "daily_prefetch_enabled": true,
        "daily_prefetch_time": "09:00"
      }
    }
  ]
}
```

Brave를 쓰려면 `BRAVE_API_KEY`를 환경 변수로 두거나 `api_key`에 직접 넣습니다.

```json
{
  "llm": [
    {
      "name": "fortune-haiku",
      "web_search": {
        "enabled": true,
        "provider": "brave",
        "endpoint": "https://api.search.brave.com/res/v1/web/search",
        "api_key": null,
        "api_key_env": "BRAVE_API_KEY",
        "count": 3,
        "search_lang": "ko",
        "country": "KR",
        "tool_calling_enabled": true,
        "tool_name": "web_search",
        "tool_max_rounds": 2
      }
    }
  ]
}
```

띠별 운세처럼 한 페이지에서 12개 항목을 미리 가져오려면 `daysaju` provider를 쓸 수 있습니다. 이 provider는 API 키가 필요 없고, `signs` 전체를 매일 `daily_prefetch_time`에 캐시에 채워 둡니다.

```json
{
  "llm": [
    {
      "name": "fortune-zodiac",
      "web_search": {
        "enabled": true,
        "provider": "daysaju",
        "endpoint": "https://daysaju.com/fortune/zodiac",
        "api_key": null,
        "api_key_env": "",
        "count": 12,
        "tool_calling_enabled": true,
        "tool_name": "web_search",
        "tool_max_rounds": 2,
        "daily_prefetch_enabled": true,
        "daily_prefetch_time": "09:00",
        "query_template": "오늘 {sign}띠 운세",
        "signs": ["쥐", "소", "호랑이", "토끼", "용", "뱀", "말", "양", "원숭이", "닭", "개", "돼지"]
      }
    }
  ]
}
```

별자리처럼 정해진 후보를 먼저 검색해 프롬프트에 붙이는 기존 방식도 같이 쓸 수 있습니다. 이 경우 `signs`에 후보를 넣고 `query_template`에 `{sign}`을 포함합니다.

`daily_prefetch_enabled`를 켜면 프린터 서비스가 매일 `daily_prefetch_time`에 `signs` 전체를 미리 검색해 메모리 캐시에 채웁니다. 예를 들어 별자리 12개나 띠 12개를 오전 9시에 갱신해 두면 실제 출력 시점에는 그날 캐시된 자료를 사용합니다.

## 실행

프린터 서비스 모드:

```bash
python3 -m callroo_printer --config config.json
```

실제 출력 없이 산출물만 만들기:

```bash
python3 -m callroo_printer --config config.json --dry-run
```

웹 대시보드만 띄우기:

```bash
python3 -m callroo_printer --config config.json --dashboard --dashboard-host 127.0.0.1 --dashboard-port 3001
```

기본 바인드는 `127.0.0.1:3001`입니다. 같은 네트워크에서 직접 접속해야 한다면 `--dashboard-host 0.0.0.0`으로 띄울 수 있지만, 이 경우 trusted LAN, Tailscale, VPN, reverse proxy 인증 같은 보호 장치 뒤에서만 쓰는 것을 권장합니다.

## 대시보드

대시보드는 출력물과 운영 상태를 한 화면에서 보는 용도입니다. 저장소 루트의 기본 `config.json`으로 실행하면, 설치된 `callroo-printer.service`가 사용하는 config 경로를 따라가서 `/opt/callroo-printer` 쪽 로그와 산출물을 보여줍니다.

대시보드에서 할 수 있는 일:

- 날짜별 출력 프리뷰 검색
- 현재 서비스 상태, 최근 작업 상태, 최근 로그 확인
- `지금 출력` 버튼으로 서비스에 출력 트리거 추가
- `/print` 수동 프린터 페이지에서 원하는 문구나 그림 파일을 바로 출력 큐에 등록
- `프롬프트 설정` 다이얼로그에서 프로필, 프롬프트, 모델 후보 수정
- 권한 해제 뒤 프롬프트 프로필 추가/삭제
- `아티팩트` 다이얼로그에서 그림/음악 업로드와 현재 등록된 파일 목록 확인
- 음악 아티팩트는 브라우저 미디어 컨트롤로 미리 재생

수동 프린터 페이지는 텍스트 입력과 그림 파일 업로드만 받습니다. 문서 파일(PDF, DOCX, TXT 등)은 업로드 대상이 아니며, 서버에서도 이미지 확장자와 실제 이미지 디코딩을 검증합니다. 출력 후보 PNG는 `layout.paper_width_px` 프린터 도트 폭(기본 384dot)에 맞춰 합성되고, 라벨 폭/높이와 그림 좌표도 같은 dot 기준으로 편집합니다. `/print` 캔버스에서 라벨 크기를 드래그로 조절하고 여러 그림을 올려 위치, 크기, 확대/축소, 크롭, 회전을 조정한 뒤 출력 큐에 넣을 수 있습니다. 선택한 그림의 x/y/폭/높이는 직접 입력할 수 있고, 범위를 벗어난 값은 유효한 dot 범위로 자동 보정됩니다. 제출 시 수동 출력 이력에도 PNG가 저장되어 프린터 서비스가 꺼져 있어도 브라우저에서 확인, 다운로드, 재출력, 삭제할 수 있습니다.

간단한 REST 출력 API도 제공합니다. 모든 크기와 좌표 값은 최종 출력 후보 안의 프린터 dot 기준이며, 범위를 벗어난 값은 서버에서 유효 범위로 보정됩니다. 응답에는 공통으로 `ok`, `request_id`, `history_url`, `download_url`이 포함됩니다.

문구 출력은 `POST /api/print/text`에 JSON으로 요청합니다.

```bash
curl -X POST http://127.0.0.1:3001/api/print/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"바로 출력할 문구","label_width":220,"label_height":96,"border":"double","align":"center","font_size":30}'
```

문구 API 옵션:

| 필드 | 설명 |
| --- | --- |
| `text` | 출력할 문구. 필수 |
| `label_width` 또는 `label_width_px` | 라벨 폭 dot |
| `label_height` 또는 `label_height_px` | 라벨 높이 dot |
| `border` 또는 `border_style` | `none`, `thin`, `thick`, `double` |
| `align` 또는 `text_align` | `left`, `center`, `right` |
| `font_size` | 글자 크기. 16-56 |

그림 출력은 `POST /api/print/image`에 `multipart/form-data`로 파일과 옵션을 보냅니다.

```bash
curl -X POST http://127.0.0.1:3001/api/print/image \
  -F image=@./label.png \
  -F label_width=284 \
  -F label_height=180 \
  -F border=thin \
  -F image_width=240 \
  -F image_height=140 \
  -F image_x=0 \
  -F image_y=0 \
  -F rotation=0 \
  -F crop=false
```

그림 API는 JSON base64 방식도 받습니다.

```bash
curl -X POST http://127.0.0.1:3001/api/print/image \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "label.png",
    "content_base64": "BASE64_ENCODED_IMAGE",
    "label_width": 284,
    "label_height": 180,
    "image_width": 240,
    "image_height": 140,
    "crop": false
  }'
```

그림 API 옵션:

| 필드 | 설명 |
| --- | --- |
| `image` | multipart 파일 필드. JSON에서는 `image.filename`, `image.content_base64` 객체도 가능 |
| `filename`, `content_base64` | JSON base64 업로드용 이미지 이름과 내용 |
| `text` | 그림 위에 함께 올릴 문구. 선택 |
| `label_width` 또는 `label_width_px` | 라벨 폭 dot |
| `label_height` 또는 `label_height_px` | 라벨 높이 dot |
| `border` 또는 `border_style` | `none`, `thin`, `thick`, `double` |
| `align` 또는 `text_align` | 함께 올린 문구 정렬. `left`, `center`, `right` |
| `font_size` | 함께 올린 문구 글자 크기. 16-56 |
| `image_x` 또는 `x` | 라벨 안쪽 기준 그림 X 좌표 |
| `image_y` 또는 `y` | 라벨 안쪽 기준 그림 Y 좌표 |
| `image_width`, `image_width_px`, 또는 `width` | 그림 폭 dot |
| `image_height`, `image_height_px`, 또는 `height` | 그림 높이 dot |
| `rotation`, `image_rotation_degrees`, 또는 `rotation_degrees` | 그림 회전 각도. -180-180 |
| `crop` 또는 `image_crop` | `true`이면 지정한 그림 영역을 채우도록 크롭 |

수동 출력 이력을 다시 출력하려면 `POST /api/manual-history/<request-id>/reprint`를 호출합니다. 기존 이력의 문구, 라벨, 이미지 위치/크기를 복제해 새 출력 요청으로 큐에 넣습니다.

프롬프트/프로필 설정은 기본적으로 잠겨 있습니다. `config.json`의 `dashboard.edit_token`에 토큰을 넣고, 대시보드에서 같은 토큰을 입력해야 편집할 수 있습니다. 비워두면 호환성을 위해 저장 API가 토큰을 요구하지 않습니다.

대시보드는 전체 인증 서버가 아닙니다. 외부 네트워크에 직접 공개하지 말고, 기본 localhost 바인드를 유지하거나 별도 접근 제어 뒤에서 노출하세요.

대시보드 snapshot API는 상태/프리뷰/로그 로딩을 줄이기 위해 짧게 메모리 캐싱됩니다. 기본값은 8초입니다.

## Bluetooth와 MXW01

RFCOMM 프린터는 실행 전에 한 번 페어링/신뢰 등록해 두는 편이 안정적입니다.

```bash
bluetoothctl
power on
agent on
default-agent
scan on
pair <printer-mac>
trust <printer-mac>
connect <printer-mac>
```

프린터 RFCOMM 채널 확인:

```bash
sdptool browse <printer-mac>
```

자동 감지가 실패하면 `config.json`의 `bluetooth.channel`에 직접 넣습니다.

MXW01처럼 BLE direct 경로가 필요한 기기는 `bluetooth.backend`를 `timiniprint_cli_direct`로 두고 `bluetooth.timiniprint_repo`를 `vendor/TiMini-Print`로 둡니다. 이 저장소는 upstream `TiMini-Print` submodule에 `patches/timiniprint/*.patch`를 적용해 사용합니다.

## 입력과 오디오

헤드리스 버튼을 시스템 입력으로 직접 받으려면 `input` 섹션을 켭니다.

```json
{
  "input": {
    "stdin_enabled": false,
    "linux_event_enabled": true,
    "linux_event_paths": [
      "/dev/input/by-id/your-button-event-kbd"
    ],
    "linux_event_keycodes": [28, 96]
  }
}
```

`linux_event_paths`에는 `/dev/input/by-id/...` 같은 안정적인 경로를 권장합니다. 실행 계정은 해당 장치를 읽을 수 있어야 하므로 보통 `input` 그룹 권한이 필요합니다.

트리거가 수락되면 `audio.launch_sounds` 중 하나를 가중치 기반으로 골라 작업 완료까지 반복 재생합니다. `wav`는 `aplay`로 재생하고, `mp3` 같은 비-WAV 파일은 `ffmpeg`로 임시 `wav` 변환 뒤 재생합니다. `audio.printer_connected_file`, `audio.printer_failed_file`, `audio.print_completed_file`은 단발 효과음입니다.

## 배포

처음 설치:

```bash
sudo bash deploy/systemd/install.sh
sudoedit /etc/default/callroo-printer
sudo bash deploy/systemd/install.sh
```

현재 작업 트리와 로컬 `config.json`을 설치 복사본에 반영하고 서비스를 재시작:

```bash
bash scripts/deploy.sh
```

`callroo-dashboard.service`가 설치되어 있으면 `scripts/deploy.sh`가 프린터 서비스 배포 뒤 대시보드 서비스도 함께 재시작합니다. 대시보드가 다른 이름으로 설치되어 있으면 `--dashboard-service-name`을 넘기고, 별도로 관리하려면 `--skip-dashboard-restart`를 사용합니다.

상태와 로그 확인:

```bash
sudo systemctl status callroo-printer.service
sudo systemctl status callroo-dashboard.service
journalctl -u callroo-printer.service -f
```

대시보드 서비스 유닛도 [deploy/systemd/callroo-dashboard.service](deploy/systemd/callroo-dashboard.service)에 있습니다. 기본 예시는 `callroo` 사용자/그룹을 가정합니다. 유닛의 `User`, `Group`, `WorkingDirectory`, `ExecStart`가 장비의 설치 경로와 맞는지 확인한 뒤 설치합니다.

```bash
sudo install -m 644 deploy/systemd/callroo-dashboard.service /etc/systemd/system/callroo-dashboard.service
sudoedit /etc/systemd/system/callroo-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now callroo-dashboard.service
```

설치 뒤에는 `callroo-printer.service`와 `callroo-dashboard.service`를 함께 띄우면 됩니다.

`install.sh`는 submodule 초기화, `TiMini-Print` patch 적용, `/opt/callroo-printer` 동기화, `.venv` 생성, requirements 설치, systemd restart까지 처리합니다. 구현 변경, `assets` 변경, `config.json` 변경 뒤에는 다시 실행하거나 `scripts/deploy.sh`를 사용하세요. 대시보드가 별도 서비스로 떠 있다면 새 HTML/REST 핸들러를 읽도록 대시보드 서비스도 재시작되어야 합니다.

## 로그와 산출물

- 앱 로그: `logs/callroo-printer.log`
- 트리거 큐: `outputs/dashboard-triggers.jsonl`
- 수동 출력 업로드 임시 파일: `outputs/manual-uploads/<request-id>/`
- 수동 출력 이력 PNG: `outputs/manual-history/<request-id>/composed-ticket.png`
- 트리거별 산출물: `outputs/jobs/<timestamp>-<id>/`
- 작업 폴더 주요 파일: `input.json`, `llm-request.json`, `llm-response.json`, `llm-error.json`, `llm-model.json`, `llm-attempts.json`, `fortune.txt`, `composed-ticket.png`, `print-job.bin`, `result.json`
- `--dry-run`에서는 `print-job.bin`까지 만들지만 Bluetooth 전송은 하지 않습니다.

## 설정 포인트

- `assets_dir`: 그림과 음악 아티팩트 기준 디렉터리
- `cooldown_seconds`: 트리거 수락 후 다음 출력까지 대기 시간
- `dashboard.edit_token`: 대시보드 설정 편집 토큰
- `bluetooth.backend`: `rfcomm` 또는 `timiniprint_cli_direct`
- `bluetooth.mac_address`: 프린터 MAC 주소
- `bluetooth.keepalive_interval_seconds`: direct backend keep-alive 주기
- `bluetooth.adapter_reset_after_failures`: 연속 실패 뒤 Bluetooth 어댑터 리셋 기준
- `audio.launch_sounds`: 출력 중 반복 재생할 시작음 후보
- `audio.launch_sound_volume`, `audio.event_volume`: 반복음/효과음 볼륨
- `llm[].name`: 대시보드와 산출물에 표시되는 프로필 이름
- `llm[].weight`: 프로필 선택 가중치
- `llm[].system_prompt`, `llm[].prompt`: 모델에 전달할 지시문
- `llm[].models`: 순서대로 시도할 모델 후보
- `llm[].tags`: 태그명에서 이미지 파일 목록으로 가는 맵
- `llm[].web_search`: Brave/Tavily 검색 또는 Ohaasa/Daysaju 직접 파밍 설정. `enabled`와 `tool_calling_enabled`를 켜면 LLM `web_search` tool calling 사용
- `llm[].web_search.daily_prefetch_enabled`: 지정 시각에 `signs` 전체 검색 결과를 미리 캐시
- `layout.title_icon_file`: 타이틀 오른쪽에 붙일 아이콘 파일. 상대 경로면 `assets_dir` 기준
- `layout.font_path`: 한글 폰트 경로
- `layout.paper_width_px`, `layout.side_margin_px`: 출력 폭과 좌우 여백
- `output.logs_dir`, `output.outputs_dir`: 로그와 산출물 저장 경로

상대 경로는 `config.json` 파일 위치 기준으로 해석됩니다.
