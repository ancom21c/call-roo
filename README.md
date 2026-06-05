# callroo-printer

라즈베리파이에서 버튼 한 번으로 짧은 LLM 생성 문구와 이미지를 합성해 미니 프린터로 출력하는 헤드리스 앱입니다. 터미널 `Enter`, Linux 입력 장치, 웹 대시보드의 출력 버튼으로 트리거할 수 있습니다.

트리거가 들어오면 앱은 LLM 프로필을 가중치 기반으로 고르고, 프로필 안의 모델 후보를 순서대로 호출합니다. 모델이 JSON으로 본문과 태그를 반환하면 태그에 연결된 이미지 풀에서 그림을 하나 골라 티켓 이미지를 만들고, 오디오 효과와 함께 Bluetooth 프린터로 전송합니다. 기본 출력물은 가운데 정렬된 헤드라인, 이미지 아래 태그, 테두리 있는 본문 영역으로 합성됩니다.

## 주요 기능

- OpenAI 호환 Chat Completions endpoint 지원
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
- `프롬프트 설정` 다이얼로그에서 프로필, 프롬프트, 모델 후보 수정
- 권한 해제 뒤 프롬프트 프로필 추가/삭제
- `아티팩트` 다이얼로그에서 그림/음악 업로드와 현재 등록된 파일 목록 확인
- 음악 아티팩트는 브라우저 미디어 컨트롤로 미리 재생

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

상태와 로그 확인:

```bash
sudo systemctl status callroo-printer.service
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

`install.sh`는 submodule 초기화, `TiMini-Print` patch 적용, `/opt/callroo-printer` 동기화, `.venv` 생성, requirements 설치, systemd restart까지 처리합니다. 구현 변경, `assets` 변경, `config.json` 변경 뒤에는 다시 실행하거나 `scripts/deploy.sh`를 사용하세요.

## 로그와 산출물

- 앱 로그: `logs/callroo-printer.log`
- 트리거 큐: `outputs/dashboard-triggers.jsonl`
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
- `layout.title_icon_file`: 타이틀 오른쪽에 붙일 아이콘 파일. 상대 경로면 `assets_dir` 기준
- `layout.font_path`: 한글 폰트 경로
- `layout.paper_width_px`, `layout.side_margin_px`: 출력 폭과 좌우 여백
- `output.logs_dir`, `output.outputs_dir`: 로그와 산출물 저장 경로

상대 경로는 `config.json` 파일 위치 기준으로 해석됩니다.
