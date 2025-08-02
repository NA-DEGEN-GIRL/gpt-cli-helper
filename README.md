# GPT-CLI Pro: 개발자를 위한 궁극의 AI 터미널

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**GPT-CLI Pro**는 터미널 환경에서 AI의 능력을 최대한으로 활용하고자 하는 개발자와 파워 유저를 위한 차세대 명령줄 인터페이스입니다. 단순한 질의응답을 넘어, **파일 시스템 연동, 멀티모달 지원, 실시간 코드 렌더링, 전역 설정 관리** 등 강력한 기능들을 통해 개발 생산성을 극대화합니다.

<!-- (데모 GIF/스크린샷 위치) -->
<!-- 이 자리에 프로그램의 핵심 기능을 보여주는 GIF나 스크린샷을 넣으면 좋습니다. -->
<!-- 예: TUI 파일 선택기, 스트리밍 응답, diff 기능 등 -->

## ✨ 주요 기능 (Key Features)

*   **💻 전역 명령어 지원**: 시스템 어디서든 `gptcli` 같은 명령어로 즉시 실행할 수 있습니다.
*   **⚙️ 중앙 설정 관리**: `ai_models.txt` 파일은 홈 디렉토리의 중앙 설정 폴더에서 관리되어, 모든 프로젝트에서 동일한 모델 목록을 공유합니다.
*   **⚡ 실시간 스트리밍 응답**: AI의 답변이 생성되는 과정을 실시간으로 확인하며, `rich` 라이브러리를 통해 아름답게 렌더링된 마크다운 및 코드 블록을 제공합니다.
*   **📄 출력 제어**:
    *   `/pretty_print`: **고급 출력(Rich)**과 **순수 텍스트(Raw)** 모드를 실시간으로 전환합니다.
    *   `/raw`: 고급 모드로 답변을 받은 후에도, 마지막 답변의 원본(Raw) 텍스트를 즉시 다시 볼 수 있습니다.
*   **🧠 지능형 파서**: **중첩된 코드 블록**(` ``` ` 안에 ` ``` `가 있는 경우)을 완벽하게 인식하고 처리합니다.
*   **🌳 TUI 파일 선택기**: `urwid` 기반의 텍스트 UI를 통해 현재 디렉토리의 파일을 직관적으로 선택하고 AI에게 컨텍스트로 전달할 수 있습니다. (`.gptignore` 지원)
*   **🖼️ 멀티모달 지원**: 텍스트뿐만 아니라 **이미지(.png, .jpg), PDF 문서** 파일을 첨부하여 시각적 컨텍스트를 포함한 질문이 가능합니다.
*   **↔️ 코드 비교 (Diff)**: 로컬 파일과 AI가 생성한 코드를 즉시 비교하거나, 이전 응답과 현재 응답 간의 코드 변경점을 추적할 수 있습니다.

## 🚀 전역 설치 및 설정 (권장)

이 스크립트를 시스템 전역 명령어로 등록하여 어떤 디렉토리에서든 `gptcli`와 같이 사용할 수 있습니다.

### 1단계: 스크립트 파일 저장

먼저, `gptcli_o3.py` 스크립트 파일을 컴퓨터의 영구적인 위치에 저장합니다. 터미널에서 아래 명령어를 사용하여 적절한 위치에 디렉토리를 만들고 스크립트를 그곳으로 옮기거나 저장하세요.

```bash
# 예시: 홈 디렉토리 아래에 'scripts' 폴더를 만들고 그 안에 저장
mkdir -p ~/scripts
# gptcli_o3.py 파일을 ~/scripts/gptcli_o3.py 로 이동 또는 저장했다고 가정
```

### 2단계: 전역 모델 설정 파일 생성 (최초 1회)

이 스크립트는 모든 프로젝트에서 공유할 모델 목록을 사용자의 중앙 설정 폴더에서 읽어옵니다. 아래 명령어를 실행하여 설정 폴더와 `ai_models.txt` 파일을 생성하세요.

**주의:** 스크립트 코드에 정의된 `CONFIG_DIR` 경로(`~/codes/gpt_cli`)에 맞춰 파일을 생성합니다.

```bash
# 1. 전역 설정 디렉토리 생성
mkdir -p ~/codes/gpt_cli

# 2. 전역 ai_models.txt 파일 생성 및 기본 내용 채우기
cat > ~/codes/gpt_cli/ai_models.txt << EOF
# --- 추천 모델 (한 줄에 하나씩) ---
openai/gpt-4o
google/gemini-flash-1.5
anthropic/claude-3.5-sonnet
# --- 무료 모델 ---
meta-llama/llama-3-8b-instruct:free
mistralai/mistral-7b-instruct:free
EOF
```
이제 이 파일 하나만 수정하면 어디서든 동일한 모델 목록을 사용할 수 있습니다.

### 3단계: 전역 명령어 등록 (macOS / Linux)

스크립트를 `gptcli` 라는 명령어로 실행할 수 있도록 시스템 경로에 심볼릭 링크(바로가기)를 생성합니다.

1.  **스크립트에 실행 권한 부여:**
    ```bash
    # 1단계에서 저장한 경로를 사용
    chmod +x ~/scripts/gptcli_o3.py
    ```

2.  **심볼릭 링크 생성 (명령어 등록):**
    `/usr/local/bin`은 시스템 `PATH`에 포함된 표준 디렉토리입니다.
    ```bash
    # 'gptcli' 라는 이름의 명령어를 생성합니다. 원하는 다른 이름으로 변경 가능합니다.
    # sudo는 시스템 폴더에 쓰는 권한을 얻기 위함입니다.
    sudo ln -s ~/scripts/gptcli_o3.py /usr/local/bin/gptcli
    ```

3.  **(업데이트 시) 기존 명령어 덮어쓰기:**
    이미 `gptcli` 명령어를 등록해 둔 상태에서 새 버전의 스크립트로 업데이트하려면, `-f` (force) 옵션을 추가하여 기존 링크를 안전하게 덮어쓸 수 있습니다.
    ```bash
    sudo ln -sf ~/scripts/gptcli_o3.py /usr/local/bin/gptcli
    ```

### 4단계: 확인

터미널을 새로 열거나, `source ~/.zshrc` (또는 `~/.bashrc`)를 실행한 후, 아무 디렉토리에서나 아래 명령어를 실행해 보세요.
```bash
gptcli
```
프로그램이 정상적으로 실행되면 설정이 완료된 것입니다.

## 📝 로컬 프로젝트 환경 설정

전역 설치 후, 각 프로젝트 폴더에서 처음 `gptcli`를 실행할 때 아래 파일들을 설정할 수 있습니다. 이 파일들은 해당 프로젝트에만 적용됩니다.

1.  **.env 파일 (프로젝트별 API 키 등)**
    프로젝트 루트에 `.env` 파일을 만들면 `OPENROUTER_API_KEY` 등을 관리할 수 있습니다.
    ```env
    # .env
    OPENROUTER_API_KEY="sk-or-..." 
    ```

2.  **.gptignore 파일 (프로젝트별 무시 목록)**
    `venv`, `.git`, `node_modules` 등 AI 컨텍스트에 포함하고 싶지 않은 파일/폴더를 지정합니다. 문법은 `.gitignore`와 동일합니다.

## 💡 사용법

### 대화형 모드

`gptcli` (또는 설정한 명령어)를 인자 없이 실행합니다. 이전 대화 내역을 이어서 사용할 수 있는 세션 기반의 채팅이 시작됩니다.

```bash
gptcli
```

### 단일 프롬프트 모드

간단한 질문을 빠르게 던지고 답변만 받고 싶을 때 사용합니다. 질문을 따옴표로 감싸서 인자로 전달하면, AI의 답변이 출력된 후 프로그램이 바로 종료됩니다.

```bash
# 기본 모델로 질문
gptcli "파이썬에서 리스트의 중복 요소를 제거하는 가장 효율적인 방법은?"

# 특정 모델을 지정하여 질문
gptcli "React에서 상태 관리를 위한 최고의 라이브러리는?" --model anthropic/claude-3.5-sonnet
```

### 명령어 목록 (대화형 모드 전용)

채팅 세션 중 `Question>` 프롬프트에서 아래 명령어를 사용할 수 있습니다.

| 명령어 | 설명 | 예시 |
| :--- | :--- | :--- |
| `/commands` | 사용 가능한 모든 명령어 목록을 표시합니다. | `/commands` |
| `/pretty_print` | 고급 출력(Rich)과 순수 텍스트(Raw) 모드를 전환합니다. | `/pretty_print` |
| `/raw` | 마지막 답변을 서식 없는 순수 텍스트로 다시 출력합니다. | `/raw` |
| `/select_model` | `ai_models.txt` 기반의 TUI 모델 선택기를 엽니다. | `/select_model` |
| `/all_files` | TUI 파일 선택기를 열어 컨텍스트에 추가할 파일을 선택합니다. | `/all_files` |
| `/files <paths>` | 공백으로 구분하여 파일 경로를 직접 지정합니다. | `/files main.py` |
| `/clearfiles` | 현재 첨부된 모든 파일을 초기화합니다. | `/clearfiles` |
| `/mode <mode>` | AI의 페르소나를 `dev` 또는 `general`로 변경합니다. | `/mode dev` |
| `/savefav <name>` | 마지막 질문을 즐겨찾기에 저장합니다. | `/savefav python-dedup` |
| `/usefav <name>` | 저장된 즐겨찾기 질문을 불러와 입력합니다. | `/usefav python-dedup` |
| `/favs` | 저장된 모든 즐겨찾기 목록을 출력합니다. | `/favs` |
| `/diffme` | 첨부된 파일과 AI의 응답 코드 간의 차이를 비교합니다. | `/diffme` |
| `/diffcode` | 이전 AI 응답과 현재 응답의 코드 블록 간 차이를 비교합니다. | `/diffcode` |
| `/reset` | 현재 세션의 모든 대화 내역을 삭제합니다. | `/reset` |
| `/exit` | 프로그램을 종료합니다. | `/exit` |

## ⚙️ 기술 스택 (Tech Stack)

*   **API 클라이언트**: [openai-python](https://github.com/openai/openai-python)
*   **UI / 렌더링**:
    *   [Rich](https://github.com/Textualize/rich): 아름다운 터미널 UI 및 마크다운/코드 렌더링
    *   [Urwid](https://github.com/urwid/urwid): TUI 파일/모델 선택기 구현
    *   [Prompt Toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit): 강력한 대화형 프롬프트 세션
*   **설정 및 파일 처리**:
    *   [python-dotenv](https://github.com/theskumar/python-dotenv): `.env` 파일 관리
    *   [pathspec](https://github.com/cpburnz/python-path-spec): `.gitignore` 스타일 패턴 매칭

## 📜 라이선스 (License)

이 프로젝트는 MIT 라이선스 하에 배포됩니다.