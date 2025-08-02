# GPT-CLI Pro: 개발자를 위한 궁극의 AI 터미널

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**GPT-CLI Pro**는 터미널 환경에서 AI의 능력을 최대한으로 활용하고자 하는 개발자와 파워 유저를 
위한 차세대 명령줄 인터페이스입니다. 단순한 질의응답을 넘어, **파일 시스템 연동, 멀티모달 지원,
실시간 코드 렌더링, TUI 기반의 직관적인 인터페이스** 등 강력한 기능들을 통해 개발 생산성을 
극대화합니다.

<!-- (데모 GIF/스크린샷 위치) -->
<!-- 이 자리에 프로그램의 핵심 기능을 보여주는 GIF나 스크린샷을 넣으면 좋습니다. -->
<!-- 예: TUI 파일 선택기, 스트리밍 응답, diff 기능 등 -->

## ✨ 주요 기능 (Key Features)

*   **⚡ 실시간 스트리밍 응답**: AI의 답변이 생성되는 과정을 실시간으로 확인하며, `rich` 
라이브러리를 통해 아름답게 렌더링된 마크다운 및 코드 블록을 제공합니다.
*   **📋 출력 모드 토글**: `/pretty_print` 명령어로 **고급 출력(Rich)**과 **순수 텍스트(Raw)** 
모드를 전환할 수 있습니다. 순수 텍스트 모드는 서식 없이 AI의 답변을 그대로 출력하여 손쉬운 복사-붙여넣기를 지원합니다.
*   **🌳 TUI 파일 선택기**: `urwid` 기반의 텍스트 UI를 통해 현재 디렉토리의 파일을 직관적으로 
선택하고 AI에게 컨텍스트로 전달할 수 있습니다. (`.gptignore` 지원)
*   **🖼️ 멀티모달 지원**: 텍스트뿐만 아니라 **이미지(.png, .jpg), PDF 문서** 파일을 첨부하여 
시각적 컨텍스트를 포함한 질문이 가능합니다.
*   **↔️ 코드 비교 (Diff)**: 로컬 파일과 AI가 생성한 코드를 즉시 비교하거나, 이전 응답과 현재 응답 간의 코드 
변경점을 추적할 수 있습니다.
*   **🧠 세션 관리**: 모든 대화는 세션별로 안전하게 저장되어 언제든지 이전 대화를 이어갈 수 
있습니다.
*   **🔧 높은 사용자 정의**:
    *   `ai_models.txt`: 사용하고자 하는 모델 목록을 직접 관리하고 TUI로 선택합니다.
    *   `.gptignore`: `.gitignore`와 동일한 문법으로 민감하거나 불필요한 파일/디렉토리를 AI 
컨텍스트에서 제외합니다.
    *   `dev` / `general` 모드: 질문의 목적에 따라 AI의 전문성과 페르소나를 전환합니다.
*   **🚀 생산성 도구**:
    *   멀티라인 입력 지원 (`prompt_toolkit`)
    *   명령어 및 파일 경로 자동 완성
    *   대화 내역 기반 자동 제안
    *   즐겨찾기(Favorites) 기능으로 자주 사용하는 프롬프트 저장/호출
    *   AI 응답 자동 클립보드 복사

## 🚀 시작하기 (Getting Started)

### 1. 사전 요구사항

*   Python 3.9 이상
*   `git` (저장소 복제 시)

### 2. 설치

1.  **프로젝트 복제:**
    ```bash
    git clone <저장소_URL>
    cd <프로젝트_디렉토리>
    ```

2.  **가상 환경 생성 및 활성화:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # macOS/Linux
    # venv\Scripts\activate   # Windows
    ```

3.  **의존성 설치:**
    아래 내용을 `requirements.txt` 파일로 저장한 후, 명령어를 실행하세요.
    ```text
    # requirements.txt
    openai
    python-dotenv
    rich
    prompt-toolkit
    pathspec
    urwid
    pyperclip
    ```
    ```bash
    pip install -r requirements.txt
    ```

### 3. 환경 설정

프로그램을 실행하기 전, 현재 디렉토리(`gptcli_o3.py`가 있는 곳)에 아래 파일들을 설정해야 
합니다.

1.  **.env 파일 생성**
    OpenRouter API 키와 같은 민감한 정보를 저장합니다.
    ```env
    # .env
    OPENROUTER_API_KEY="sk-or-..." # 필수: 여러분의 OpenRouter API 키

    # 선택: API 요청에 포함될 커스텀 헤더
    APP_URL="https://github.com/your-repo/gpt-cli-pro"
    APP_TITLE="My Custom GPT-CLI"
    ```
    > **⚠️ 경고:** `.env` 파일은 민감 정보를 포함하므로 `.gitignore`에 추가하여 Git 저장소에 
포함되지 않도록 하세요.

2.  **ai_models.txt 파일 생성**
    사용하고 싶은 AI 모델 목록을 관리합니다. 한 줄에 하나의 모델 '슬러그(slug)'를 입력하세요. `#`로 시작하는 
줄은 주석 처리됩니다.
    ```text
    # ai_models.txt

    # --- 추천 모델 ---
    openai/gpt-4o
    google/gemini-flash-1.5
    anthropic/claude-3-haiku:beta

    # --- 기타 모델 ---
    meta-llama/llama-3-8b-instruct:free
    mistralai/mistral-7b-instruct:free
    ```

3.  **.gptignore 파일 생성 (선택 사항)**
    AI 컨텍스트에 포함시키지 않을 파일이나 디렉토리 패턴을 지정합니다. `.gitignore`와 문법이 
동일합니다.
    ```text
    # .gptignore

    # 시스템 및 가상환경
    .git
    .idea
    .vscode
    venv/
    __pycache__/

    # 민감 정보
    *.env
    secrets/
    ```

## 💡 사용법 (Usage)

### 기본 채팅 모드 실행

터미널에서 아래 명령어를 입력하여 대화형 채팅 세션을 시작합니다.

```bash
python gptcli_o3.py
```

`Question>` 프롬프트가 나타나면 자유롭게 질문하거나 `/`로 시작하는 명령어를 사용할 수 있습니다.

### 단일 프롬프트 모드

간단한 질문을 빠르게 던지고 답변만 받고 싶을 때 사용합니다.

```bash
python gptcli_o3.py "파이썬에서 리스트의 중복 요소를 제거하는 가장 효율적인 방법은 뭐야?"
```

### 명령어 목록 (Commands)

채팅 세션 중 `Question>` 프롬프트에서 아래 명령어를 사용할 수 있습니다.

```
명령어              설명                                                       예시
─────────────────── ────────────────────────────────────────────────────────── ─────────────────────────
/commands           사용 가능한 모든 명령어 목록을 표시합니다.                 /commands
/pretty_print       고급 출력(Rich)과 순수 텍스트(Raw) 모드를 전환합니다.      /pretty_print
/select_model       `ai_models.txt` 기반의 TUI 모델 선택기를 엽니다.           /select_model
/model <slug>       모델을 직접 변경합니다.                                    /model openai/gpt-4o
/all_files          TUI 파일 선택기를 열어 컨텍스트에 추가할 파일을 선택합니다. /all_files
/files <f1> <f2>    공백으로 구분하여 파일 경로를 직접 지정합니다.             /files main.py utils.py
/clearfiles         현재 첨부된 모든 파일을 초기화합니다.                      /clearfiles
/mode <mode>        AI의 페르소나를 `dev` 또는 `general`로 변경합니다.         /mode dev
/savefav <name>     마지막 질문을 즐겨찾기에 저장합니다.                       /savefav python-dedup
/usefav <name>      저장된 즐겨찾기 질문을 불러와 입력합니다.                  /usefav python-dedup
/favs               저장된 모든 즐겨찾기 목록을 출력합니다.                    /favs
/diffme             첨부된 파일과 AI의 응답 코드 간의 차이를 비교합니다.       /diffme
/diffcode           이전 AI 응답과 현재 응답의 코드 블록 간 차이를 비교합니다. /diffcode
/reset              현재 세션의 모든 대화 내역을 삭제합니다.                   /reset
/exit               프로그램을 종료합니다.                                     /exit
```

## 🔧 고급 기능 상세

### Pretty Print 토글 (`/pretty_print`)

기본적으로 AI의 답변은 가독성을 높이기 위해 마크다운, 코드 하이라이팅 등 서식이 적용된 상태로 
출력됩니다. 하지만 답변 내용을 그대로 복사하여 다른 곳에 붙여넣고 싶을 때는 이러한 서식이 방해가 될 수 있습니다.

`/pretty_print` 명령어를 사용하여 **고급 출력 모드(기본값)와 순수 텍스트 모드를 전환**할 수 
있습니다.

*   **고급 출력 (ON)**: `rich` 라이브러리를 통한 실시간 패널, 문법 하이라이팅 등이 적용됩니다.
*   **순수 텍스트 (OFF)**: AI가 생성한 텍스트가 아무런 서식 없이 그대로 출력됩니다. 코드 블록을
포함한 전체 답변을 깨끗하게 복사할 때 유용합니다.

### TUI 파일 선택기 (`/all_files`)

`/all_files` 명령을 실행하면, 터미널 전체를 사용하는 강력한 파일 선택기가 나타납니다.

*   **탐색**: 화살표 키 (`↑`, `↓`)로 파일을 이동합니다.
*   **선택/해제**: `Space` 키로 파일 또는 디렉토리 전체를 선택/해제합니다.
*   **폴더 펼치기/접기**: `Enter` 키로 디렉토리를 확장하거나 접습니다.
*   **전체 선택/해제**: `A` 키로 보이는 모든 파일을 선택하고, `N` 키로 전체 선택을 해제합니다.
*   **완료 및 취소**: `S` 키를 눌러 선택을 완료하고 채팅으로 돌아가거나, `Q` 키를 눌러 
취소합니다.

### 코드 Diff 기능

`dev` 모드에서 코드를 다룰 때 매우 유용한 기능입니다.

1.  **`/diffme`**:
    *   먼저 `/files` 또는 `/all_files`로 로컬 파일을 첨부합니다.
    *   AI에게 "이 코드를 리팩토링해줘" 와 같이 요청하여 코드 응답을 받습니다.
    *   `/diffme`를 실행하면, 처음에 첨부했던 원본 파일과 AI가 생성한 코드 블록 간의 `diff` 
결과가 터미널에 출력됩니다.

2.  **`/diffcode`**:
    *   AI와 코드를 개선하는 대화를 여러 번 주고받은 상황에서 사용합니다.
    *   `/diffcode`를 실행하면, 바로 직전 AI의 코드 응답과 가장 최근의 코드 응답을 비교하여 
변경 사항을 명확하게 보여줍니다.

## ⚙️ 기술 스택 (Tech Stack)

*   **API 클라이언트**: [openai-python](https://github.com/openai/openai-python)
*   **UI / 렌더링**:
    *   [Rich](https://github.com/Textualize/rich): 아름다운 터미널 UI 및 마크다운/코드 렌더링
    *   [Urwid](https://github.com/urwid/urwid): TUI 파일/모델 선택기 구현
    *   [Prompt Toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit): 강력한 
대화형 프롬프트 세션
*   **설정 및 파일 처리**:
    *   [python-dotenv](https://github.com/theskumar/python-dotenv): `.env` 파일 관리
    *   [pathspec](https://github.com/cpburnz/python-path-spec): `.gitignore` 스타일 패턴 매칭

## 🤝 기여 (Contributing)

버그 리포트, 기능 제안, 코드 기여 등 모든 종류의 기여를 환영합니다. 이슈를 제기하거나 Pull Request를 보내주세요.

## 📜 라이선스 (License)

이 프로젝트는 MIT 라이선스 하에 배포됩니다. 자세한 내용은 `LICENSE` 파일을 참고하세요.