# GPT CLI Chat Assistant

이 프로젝트는 OpenAI GPT API를 기반으로 한 터미널 기반 챗봇 도구입니다.  
개발 보조에 특화되어 있으며, 파일 첨부, 코드 비교, 세션 유지, 프롬프트 즐겨찾기 등 다양한 기능을 제공합니다.

---

## ✨ 주요 기능

- GPT-4o 기반 CLI 대화: 터미널에서 GPT 모델과 직접 대화
- 코드 파일 첨부 및 자동 diff 분석 (`/diffme`)
- GPT 역할 전환 지원 (`/mode dev` / `general`): 개발 모드와 일반 모드 간 전환
- 모델 전환 가능 (`/model gpt-4` 등)
- 세션별 기록 및 자동 저장: 대화를 세션 단위로 저장하여 나중에 이어서 가능
- 응답 Markdown + 코드 자동 저장: 응답과 코드를 Markdown 포맷으로 자동 저장
- 프롬프트 즐겨찾기 기능 (`/savefav`, `/usefav`)
- 명령어 자동완성 기능 (tab 지원)
- 클립보드 복사 기능 (`--copy`, 기본 비활성화)
- 로딩 애니메이션: 응답 대기 시 로딩 중임을 알려주는 애니메이션 제공

---

## 💻 설치 방법

1. 파이썬 가상환경 생성 (선택)

```bash
python3 -m venv ~/.venvs/gptcli
source ~/.venvs/gptcli/bin/activate
```

2. 의존 패키지 설치

```bash
pip install -r requirements.txt
```

3. .env 파일 생성

```plaintext
OPENAI_API_KEY=[REDACTED]
```

4. 전역 명령 설정 (gptcli)

```bash
chmod +x gpt_cli_helper.py
sudo ln -s /full/path/to/gpt_cli_helper.py /usr/local/bin/gptcli
```

5. 실행

터미널에서 `gptcli --chat` 명령어를 실행하여 어시스턴트와 상호작용 시작

---

# GPT CLI Chat Assistant

This project is a terminal-based chatbot tool built on the OpenAI GPT API.  
It is tailored for development assistance and offers a variety of features, including file attachment, code comparison, session maintenance, prompt bookmarking, and more.

---

## ✨ Key Features

- GPT-4o-based CLI conversations: Interact directly with GPT models in your terminal
- Code file attachment and automatic diff analysis (`/diffme`)
- Role switching support for GPT (`/mode dev` / `general`): Switch between developer mode and general assistant mode
- Model switching capabilities (`/model gpt-4`, etc.)
- Session tracking and auto-save: Save conversations in sessions to continue later
- Markdown and code auto-save: Automatically save responses and code in Markdown format
- Prompt bookmarking (`/savefav`, `/usefav`)
- Command autocompletion (tab support)
- Clipboard copy support (`--copy`, disabled by default)
- Loading animation: Provides an animation while waiting for a response

---

## 💻 Installation Instructions

1. Create a Python virtual environment (Optional)

```bash
python3 -m venv ~/.venvs/gptcli
source ~/.venvs/gptcli/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Create a .env file

```plaintext
OPENAI_API_KEY=[REDACTED]
```

4. Set up global command (gptcli)

```bash
chmod +x gpt_cli_helper.py
sudo ln -s /full/path/to/gpt_cli_helper.py /usr/local/bin/gptcli
```

5. Run the Application

Invoke `gptcli --chat` in your terminal to start interacting with the assistant