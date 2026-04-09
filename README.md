# callroo-printer

라즈베리파이 3에서 헤드리스로 실행하는 터미널 앱입니다. 터미널에서 `Enter` 키를 누르거나 설정한 Linux 입력 장치에서 트리거 키가 들어오면:

1. 가중치에 따라 LLM 프로필 하나를 고르고
2. OpenAI-Compatible LLM에 오늘의 운세 하이쿠와 태그를 요청한 뒤
3. 선택된 태그에 연결된 그림 풀에서 이미지를 고르고
4. 현재 시각과 선택된 태그를 함께 합성해서
5. Bluetooth 프린터로 출력합니다.

앱은 `Ctrl+C`를 누를 때까지 계속 실행되며, 한 번 출력하면 기본 1분 쿨타임이 적용됩니다.

## 특징

- Bluetooth 연결 유지용 keep-alive 백그라운드 스레드 포함
- 트리거 수락 직후 설정한 오디오 파일을 작업 완료 시점까지 반복 재생
- LLM 프로필 여러 개를 두고 가중치 기반으로 랜덤 선택 가능
- LLM이 고른 태그별로 `asset pool`을 나눠 그림 선택 가능
- 한글 운세 출력을 위한 폰트 경로 설정 지원
- 입력, LLM 호출, 합성 이미지, 출력 바이너리를 파일로 저장
- 실제 프린트 없이 결과물만 확인하는 `dry-run` 모드 지원

## 준비

라즈베리파이에서 아래 패키지를 먼저 준비하세요.

```bash
sudo apt update
sudo apt install -y python3-pip python3-pil python3-venv bluez bluez-tools fonts-nanum
git submodule update --init --recursive
bash scripts/apply_timiniprint_patches.sh
python3 -m pip install -r requirements.txt
```

`fonts-nanum` 대신 `fonts-noto-cjk`를 써도 됩니다. 한글이 깨지면 설정 파일의 `layout.font_path`를 직접 지정하세요.

## 블루투스 페어링

앱은 RFCOMM 소켓으로 직접 붙습니다. 실행 전에 한 번만 페어링/신뢰 등록해 두는 편이 안정적입니다.

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

프린터 RFCOMM 채널을 확인하고 싶다면:

```bash
sdptool browse <printer-mac>
```

자동 감지가 실패하면 `config.json`의 `bluetooth.channel`에 직접 넣으면 됩니다.

MXW01 같이 BLE 기반으로 보이는 기기는 RFCOMM 대신 `TiMini-Print`의 direct 경로를 쓰는 편이 맞습니다.
이 경우 `config.json`에서 `bluetooth.backend`를 `timiniprint_cli_direct`로 두고,
`bluetooth.timiniprint_repo`를 `vendor/TiMini-Print`로 두면 이 프로젝트가 관리하는 `TiMini-Print` submodule을 사용합니다.
이 submodule은 upstream 원본 커밋을 그대로 가리키고, MXW01 direct 지원 수정은 `patches/timiniprint/*.patch`를 `scripts/apply_timiniprint_patches.sh`로 적용하는 구조입니다.
이 백엔드는 서비스 실행 중 MXW01 direct 세션을 유지하고, `bluetooth.keepalive_interval_seconds` 주기로 상태 조회를 보내 sleep 진입을 막도록 동작합니다. 연속 실패가 누적되면 `bluetooth.adapter_reset_after_failures` 기준으로 호스트 Bluetooth 어댑터를 리셋해 복구를 시도합니다.

## 실행

예시 설정을 복사해서 수정하세요. `config.json`은 로컬 전용 설정 파일이고 `.gitignore`에 포함되어 있으므로, LLM endpoint, 프린터 MAC, 입력 장치 경로 같은 값은 여기에만 넣으면 됩니다.

```bash
cp config.example.json config.json
export SPARK_LLM_API_KEY="..."
python3 -m callroo_printer --config config.json
```

실행 후 터미널 `Enter` 또는 설정한 Linux 입력 장치의 트리거 키가 들어올 때마다 출력이 시작됩니다. 쿨타임 중이면 남은 시간을 로그로 보여줍니다.

헤드리스 버튼을 시스템 입력으로 직접 받으려면 `config.json`의 `input` 섹션을 켜세요.

```json
{
  "input": {
    "stdin_enabled": true,
    "linux_event_enabled": true,
    "linux_event_paths": [
      "/dev/input/by-id/your-button-event-kbd"
    ],
    "linux_event_keycodes": [28, 96]
  }
}
```

`linux_event_paths`에는 `/dev/input/by-id/...` 같은 안정적인 경로를 권장합니다. 실행 계정은 해당 장치를 읽을 수 있어야 하므로 보통 `input` 그룹 권한이 필요합니다.

fresh clone 뒤에는 한 번 아래를 실행해서 `TiMini-Print` submodule과 patch를 준비해야 합니다.

```bash
git submodule update --init --recursive
bash scripts/apply_timiniprint_patches.sh
```

트리거가 수락되면 바로 `audio.clip_file` 재생을 시작하고, 출력 작업이 끝날 때까지 반복 재생합니다. 기본은 `assets/clip.wav`이고, `clip.mp3` 같은 파일명으로 바꾸면 `assets/` 아래 파일을 선택합니다. `wav`는 `aplay`로 바로 재생하고, `mp3` 같은 비-WAV 파일은 `ffmpeg`로 임시 `wav`로 변환한 뒤 같은 ALSA 경로로 재생합니다. `audio.clip_volume`으로 0.0~1.0 범위의 반복 재생 볼륨을 조절할 수 있고, `audio.event_volume`은 연결 성공/실패/출력 완료 같은 단발 효과음 볼륨입니다. `audio.printer_connected_file`, `audio.printer_failed_file`, `audio.print_completed_file`에 파일명을 넣으면 각각 프린터 연결 성공, 연결 실패, 출력 완료 시 재생합니다. 완료음은 실제 출력이 끝난 뒤 1초 후에 재생됩니다. `audio.aplay_device`를 비워두면 앱이 `aplay -l` 결과에서 ALSA 출력 후보를 골라 자동으로 붙고, 실패하면 다음 후보로 한 번 더 시도합니다. 수동 고정이 필요하면 `plughw:CARD=Headphones,DEV=0` 같은 값을 넣으면 됩니다. 후보는 `aplay -L`로 확인할 수 있습니다.

실제 출력 없이 그림 파일만 확인하고 싶다면:

```bash
python3 -m callroo_printer --config config.json --dry-run
```

## systemd 서비스

헤드리스 장치에서 계속 띄워둘 거라면 `systemd` 서비스로 올리는 편이 맞습니다.

서비스로 돌릴 때는 보통 터미널 입력이 없으므로 `config.json`에서 아래처럼 두는 것을 권장합니다.

```json
{
  "input": {
    "stdin_enabled": false,
    "linux_event_enabled": true
  }
}
```

현재 저장소에는 서비스 설치 스크립트와 예시 유닛이 포함되어 있습니다.

```bash
sudo bash deploy/systemd/install.sh
sudoedit /etc/default/callroo-printer
sudo bash deploy/systemd/install.sh
```

상태와 로그 확인:

```bash
sudo systemctl status callroo-printer.service
journalctl -u callroo-printer.service -f
```

`install.sh`는 먼저 `vendor/TiMini-Print` submodule을 초기화하고 `patches/timiniprint/*.patch`를 적용한 뒤, 현재 작업 트리를 `/opt/callroo-printer` 같은 설치 디렉터리로 동기화합니다. direct backend를 쓰는 경우 설치된 `config.json`도 submodule 경로를 보도록 맞춥니다. 설치 디렉터리 안에는 서비스 전용 `.venv`도 만들고, 거기에 이 프로젝트 requirements를 설치한 뒤 그 Python으로 서비스를 띄웁니다. 그래서 구현 변경, `assets` 변경, `config.json` 변경, `TiMini-Print` patch 변경 뒤에는 같은 스크립트를 다시 실행하면 설치 복사본과 런타임 의존성이 함께 갱신되고 서비스도 restart됩니다.
예시 유닛은 [callroo-printer.service](/home/ancom/workspace/call-roo/deploy/systemd/callroo-printer.service)에 남겨두었습니다.
`systemd` 정지 시에는 `SIGINT`로 종료해서 프린터 세션과 입력 감시를 정상 정리하도록 되어 있습니다.

서비스 모드에서 읽는 에셋은 설치 복사본의 `assets/`입니다. 즉 소스 저장소의 `assets/`를 바꾼 뒤에는 `install.sh`를 다시 실행해서 설치 디렉터리로 동기화해야 합니다. 설치 복사본 내부의 이미지 목록은 트리거마다 다시 스캔하므로, 설치 디렉터리 쪽 `assets/`를 직접 바꾼 경우에는 다음 출력부터 바로 반영됩니다. `TiMini-Print`도 같은 방식으로 설치 복사본을 사용하며, 필요한 patch는 설치 전에 자동 적용됩니다.

## 로그와 산출물

- 앱 로그: `logs/callroo-printer.log`
- 트리거별 산출물: `outputs/<timestamp>-<id>/`
- 각 작업 폴더에는 `input.json`, `llm-request.json`, `llm-response.json` 또는 `llm-error.json`, `fortune.txt`, `composed-ticket.png`, `print-job.bin`, `result.json`이 저장됩니다.
- `dry-run` 모드에서는 `print-job.bin`까지 생성하지만 Bluetooth 전송은 하지 않습니다.

## 설정 포인트

- `bluetooth.keepalive_hex`
  - 기본값 `1f1111`
  - 이 계열 미니 프린터에서 자주 쓰이는 paper-state 조회 명령 기반입니다.
- `bluetooth.keepalive_interval_seconds`
  - 기본값 `5.0`
  - keep-alive 송신 주기입니다.
- `bluetooth.keepalive_timeout_seconds`
  - 기본값 `3.0`
  - direct backend에서 status notification을 기다리는 최대 시간입니다.
- `bluetooth.connect_timeout_seconds`
  - 기본값 `5.0`
  - RFCOMM 연결 타임아웃이자 direct backend의 connect 타임아웃으로도 사용합니다.
- `bluetooth.reconnect_delay_seconds`
  - 기본값 `2.0`
  - keep-alive 또는 연결 실패 뒤 다시 붙기 전 대기 시간입니다.
- `bluetooth.adapter_name`
  - 기본값 `hci0`
  - 연속 실패 시 리셋할 호스트 Bluetooth 어댑터 이름입니다.
- `bluetooth.adapter_reset_after_failures`
  - 기본값 `3`
  - 이 횟수만큼 연속 실패하면 `hciconfig` 기반 어댑터 리셋을 시도합니다.
- `bluetooth.adapter_reset_cooldown_seconds`
  - 기본값 `30.0`
  - 어댑터 리셋을 너무 자주 반복하지 않기 위한 최소 간격입니다.
- `input.stdin_enabled`
  - 기본값 `true`
  - 현재 터미널 세션에서 `Enter` 입력을 트리거로 받습니다.
- `input.linux_event_enabled`
  - 기본값 `false`
  - `/dev/input/event*` 장치를 직접 읽어 포커스와 무관한 시스템 입력을 트리거로 받습니다.
- `input.linux_event_paths`
  - 예: `/dev/input/by-id/your-button-event-kbd`
  - 여러 장치를 동시에 감시할 수 있습니다.
- `input.linux_event_keycodes`
  - 기본값 `[28, 96]`
  - 각각 `KEY_ENTER`, `KEY_KPENTER`입니다.
- `audio.clip_volume`
  - 기본값 `1.0`
  - `audio.clip_file` 반복 재생 볼륨입니다. `0.0`이면 재생하지 않고, `1.0`이면 원본 볼륨입니다.
- `audio.event_volume`
  - 기본값 `1.0`
  - 프린터 연결/실패/출력 완료 단발 효과음 볼륨입니다.
- `audio.clip_file`
  - 기본값 `clip.wav`
  - 상대 경로면 `assets_dir` 기준으로 해석합니다. 예: `clip.mp3`
- `audio.printer_connected_file`, `audio.printer_failed_file`, `audio.print_completed_file`
  - 기본값 `null`
  - 상대 경로면 `assets_dir` 기준으로 해석합니다. 예: `connected.mp3`
- `audio.aplay_device`
  - 예: `plughw:CARD=Headphones,DEV=0`
  - `aplay`에 그대로 넘길 ALSA 출력 장치입니다. 비워두면 앱이 자동 감지한 ALSA 재생 장치를 사용합니다.
- `bluetooth.backend`
  - `rfcomm` 또는 `timiniprint_cli_direct`
  - `MXW01`처럼 RFCOMM/SPP가 아니라 BLE로 보이는 기기는 `timiniprint_cli_direct`를 권장합니다.
  - `timiniprint_cli_direct`는 서비스가 살아 있는 동안 direct 세션을 유지하고 keep-alive를 보냅니다.
- `bluetooth.timiniprint_repo`
  - `timiniprint_cli_direct` 사용 시 `TiMini-Print` 저장소 경로입니다.
- `bluetooth.timiniprint_direct_y_scale`
  - `TiMini-Print --mxw01-direct-y-scale`로 그대로 전달됩니다.
- `llm.endpoint`
  - `https://your-llm-endpoint.example/v1/`처럼 base URL을 넣어도 되고
  - `/chat/completions`까지 포함한 전체 URL을 넣어도 됩니다.
- `llm.prompt`
  - 현재 기본값은 `{"fortune":"...","tag":"..."}` 형태의 JSON을 반환하도록 유도합니다.
- `llm.response_tag_key`
  - 기본값 `tag`
  - 모델이 선택한 태그를 읽을 JSON 필드 이름입니다.
- `llm`
  - 이제 객체가 아니라 배열입니다. 각 항목이 하나의 완전한 LLM 설정이며 `endpoint`, `model`, `prompt`, `weight`, `tags` 등을 직접 가집니다.
- `llm[].weight`
  - 기본값 `1.0`
  - 트리거마다 이 가중치 비율로 사용할 LLM 프로필을 고릅니다.
- `llm[].tags`
  - 태그명 -> 그림 파일 배열 맵입니다. 예: `"emotion": ["assetfile1.png", "assetfile2.png"]`
  - 프롬프트에는 이 맵의 키들만 넘기고, 모델은 그중 하나를 `response_tag_key` 필드로 반환합니다.
  - 반환된 키에 연결된 그림 목록 중 하나가 랜덤으로 인쇄됩니다.
- `llm.current_time_hint_format`
  - 기본값 `%Y-%m-%d %H:%M:%S %Z`
  - 프롬프트에 같이 넣는 현재 시각 문자열 포맷입니다.
- `llm.current_time_hint_pre`, `llm.current_time_hint_post`
  - 현재 시각 힌트 블록의 앞/뒤 문구입니다.
- `llm.cleaned_examples_pre`, `llm.cleaned_examples_post`
  - 최근 출력 예시 블록의 앞/뒤 문구입니다.
- `llm.response_json_key`
  - 기본값 `fortune`
  - 모델 응답 JSON에서 실제로 인쇄할 문자열을 꺼낼 키입니다.
- `llm.enable_thinking`
  - 기본값 `false`
  - Qwen3.5 계열이 reasoning을 길게 붙이는 경우를 막기 위해 `chat_template_kwargs.enable_thinking=false`로 요청합니다.
- `layout.font_path`
  - 예: `/usr/share/fonts/truetype/nanum/NanumGothic.ttf`
- `output.logs_dir`
  - 앱 로그 파일이 쌓이는 디렉토리입니다.
- `output.outputs_dir`
  - 트리거별 작업 산출물이 쌓이는 디렉토리입니다.
- 상대 경로
  - `assets_dir`, `output.logs_dir`, `output.outputs_dir`, `layout.font_path` 같은 상대 경로는 `config.json` 파일 위치 기준으로 해석됩니다.

## 참고

- 이 구현은 Bluetooth RFCOMM + ESC/POS 래스터 이미지 출력 방식 기준입니다.
- 프린터 실물의 서비스 채널이 다르면 `bluetooth.channel` 또는 `bluetooth.channel_candidates`를 조정해야 할 수 있습니다.
- 쿨타임 기본 동작은 "트리거가 수락된 시점부터 60초"입니다.
