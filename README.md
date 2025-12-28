# GPT-CLI Helper — 터미널 최적화 AI 개발 동반자

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

GPT-CLI Helper는 개발자의 터미널(CLI) 워크플로우에 자연스럽게 스며드는 대화형 AI 클라이언트입니다. OpenRouter의 범용 API 위에 구축되어 Claude, GPT, Gemini, Llama 등 최신 모델을 자유롭게 전환하며 사용할 수 있습니다. **Claude Code 스타일의 Tool 모드**와 **자동 요약 기반 무한 컨텍스트**를 지원하여, 단순 Q&A를 넘어 실제 파일 수정/검색/실행까지 AI가 직접 수행합니다.

- 기본 모델: `anthropic/claude-opus-4.5`
- 기본 컨텍스트 길이: `200,000` tokens
- 모든 설정/출력은 **프로젝트 루트(현재 작업 디렉터리)** 기준으로 저장:
  ```
  ./ai_models.txt        # 모델 목록
  ./.gptignore           # 무시 규칙
  ./.gpt_sessions/       # 세션 저장소
  ./gpt_codes/           # 코드 블록 저장
  ./gpt_markdowns/       # 응답 마크다운 저장
  ```

참고: 마크다운 코드 펜스(```), 언어 태그, 라인 번호 등 기본 문법은 GitHub 문법을 따릅니다.  

## Quick Demo (GIF)
![GPT-CLI Demo](assets/gptcli-demo.gif)

---

## ✨ 핵심 기능

### 🔧 Tool 모드 — AI가 직접 코드를 수정합니다
- **6가지 도구 지원**: Read, Write, Edit, Bash, Grep, Glob
- 사용자 요청 시 AI가 **직접 파일을 읽고, 수정하고, 명령을 실행**합니다.
- **Trust Level 시스템**으로 안전하게 제어:
  | 레벨 | 설명 |
  |------|------|
  | `full` | 🟢 모든 Tool 자동 실행 (기본값) |
  | `read_only` | 🟡 읽기(Read/Grep/Glob)만 자동, 쓰기는 확인 |
  | `none` | 🔴 모든 Tool 실행 전 사용자 확인 |
- **위험 명령 자동 차단**: `rm -rf /`, `sudo rm`, `mkfs` 등은 신뢰 수준과 관계없이 항상 확인
- `/tools`: Tool 모드 ON/OFF 토글
- `/trust <full|read_only|none>`: 신뢰 수준 변경
- `/toolforce`: Tool 강제 모드 (항상 Tool 사용 유도)

### 🧠 자동 요약 기반 무한 컨텍스트
> *"The conversation has unlimited context through automatic summarization."*

- 컨텍스트 사용률 **80% 초과 시 자동 요약** 트리거
- 오래된 대화를 요약으로 대체하여 **핵심 정보 보존**
- 최대 3단계 재요약으로 **실질적 무한 대화** 가능
- **청크 분할 요약**: 대용량 대화도 안전하게 처리 (Gemini 등 API 제한 대응)
- `/summarize [--force]`: 수동 요약 실행
- `/show_summary`: 현재 요약 정보 표시 (압축률, 토큰 절감량 등)

### 📡 실시간 스트리밍 출력(Rich 기반)
- Reasoning Live: 추론 패널이 최근 n줄을 실시간 노출 후 완전히 접어 화면을 당깁니다.
- Code Live: 코드 블록 스트리밍을 별도 패널로 표시. 길면 "...N줄 생략..." 안내.

### 📝 견고한 코드블록 파서
- 들여쓰기/리스트 내부의 펜스, 백틱(```)과 틸드(~~~) 모두 지원.
- 인라인 트리플 백틱(문장 속 ```python) 오인식 방지.
- 코드블록 중첩 깊이 추적.

### 📎 강력한 파일 첨부 및 관리
- `.gptignore`(전역+프로젝트) 규칙을 준수하는 TUI 파일 선택기(`/all_files`).
- 텍스트/이미지(.png/.jpg/.jpeg/.webp/.gif/.bmp)/PDF 첨부 지원.
- 이미지 20MB 초과 시 자동 차단, 1MB 초과 시 자동 최적화(품질 유지·크기 축소) 후 전송.

### 🤖 모델 검색/선택 TUI
- `/search_models <키워드...>`: OpenRouter 모델 검색 → 선택 저장(`ai_models.txt`).
- `/select_model`: 현재 프로젝트에서 모델 전환(모델별 컨텍스트 길이 함께 관리).

### 🔍 Diff 뷰어(`/diff_code`)
- 응답으로 저장된 코드블록 또는 로컬 첨부 파일을 선택해 2-way diff.
- 문맥 줄수 +/-, 전체 보기(f), 좌우 스크롤(←/→, Shift+←/→, Home/End), PgUp/Dn·휠 스크롤 지원.
- Pygments 기반 정밀 하이라이팅(멀티라인 문자열·docstring 포함).

### 📊 효율적 컨텍스트/토큰 관리
- Compact 모드(`/compact_mode`): 과거 메시지 첨부를 `[첨부: ...]`로 자동 압축.
- 컨텍스트 리포트(`/show_context`): 시스템 프롬프트, 벤더 오프셋, 예약 토큰, 프롬프트 예산/사용률, 항목별(텍스트/이미지/PDF) 토큰 breakdown, Top-N 무거운 메시지까지 상세 분석. 옵션: `-v/--verbose`, `--top N`.

### 📋 안전한 클립보드 복사(`/copy`)
- `/copy <번호>`로 마지막 응답의 N번째 코드 블록을 즉시 복사.
- 원격/제한 환경에서 실패 시 raw 코드 재출력(수동 복사) 폴백.

### 💾 세션 스냅샷 & 복원 흐름
- `/session`: 세션 전환 시 현재 세션 스냅샷 자동 저장 → 대상 세션 스냅샷 복원.
- `/reset`: soft(스냅샷 생성), `--no-snapshot`, `--hard`(스냅샷까지 삭제) 지원.
- `/backup [reason...]`: 현재 세션 단일 스냅샷 강제 저장.

---

## 📦 요구사항

- Python
  ```
  3.9+
  ```
- OS
  - Linux/macOS 권장. Windows도 동작하나 일부 TUI/컬러 처리 차이가 있을 수 있습니다(Windows Terminal 권장).
- 필수 Python 패키지(예시)
  ```
  rich, urwid, prompt_toolkit, requests, pyperclip, python-dotenv, openai, pathspec, tiktoken, Pillow, PyPDF2, pygments
  ```
- 선택/환경별 의존성
  - Linux에서 클립보드 복사 기능(pyprclip) 사용 시:
    ```
    xclip 또는 xsel (X11), wl-clipboard (Wayland)
    ```
  - Truecolor 미지원 터미널에서는 256색으로 강등되어 표시될 수 있습니다.

---

## 🧭 디렉터리 구조(실행 시 자동 생성)

모든 설정과 출력은 **프로젝트 루트(현재 작업 디렉터리)** 기준으로 저장됩니다:

```
프로젝트_루트/
├── ai_models.txt              # 모델 목록 (model_id context_length)
├── .gptignore                 # 파일 선택기에서 무시할 패턴
├── .gpt_session               # 현재 세션 포인터
├── .gpt_prompt_history.txt    # 프롬프트 히스토리
├── .gpt_favorites.json        # 즐겨찾기 저장
│
├── .gpt_sessions/             # 세션 JSON 저장소
│   ├── session_default.json
│   └── backups/               # 세션 스냅샷
│       └── session_<slug>.json
│
├── gpt_codes/                 # 코드 블록 파일 저장
│   └── backup/<slug>/         # 코드 스냅샷
│
└── gpt_markdowns/             # 어시스턴트 응답 전문(Markdown) 저장
```

---

## 🚀 설치 및 설정

### 1) 저장소 클론
```bash
git clone https://github.com/NA-DEGEN-GIRL/gpt-cli-helper.git
cd gpt-cli-helper
```

### 2) 의존성 설치
```bash
pip install -r requirements.txt
```

### 3) API 키 설정 (.env)
```bash
# 프로젝트 루트의 .env
OPENROUTER_API_KEY="sk-or-..."
# (선택) 앱 메타
APP_URL="https://github.com/NA-DEGEN-GIRL/gpt-cli-helper"
APP_TITLE="GPT-CLI"
# (선택) 컨텍스트 트리밍 비율(기본: 0.75)
GPTCLI_TRIM_RATIO="0.75"
```

### 4) 자동 생성 파일
최초 실행 시 프로젝트 루트에 아래 파일이 자동 생성됩니다:
- `ai_models.txt`: 모델 목록 (`<model_id> <context_length>` 형식)
- `.gptignore`: 파일 선택기에서 무시할 패턴

---

## ⚙️ 전역 명령어로 실행하기 (어디서든 `gptcli` 한 방)

### Linux/macOS (권장: 래퍼 스크립트)

가상환경의 Python을 직접 지정하는 래퍼 스크립트 방식이 가장 안정적입니다.

**1) 래퍼 스크립트 생성**

```bash
sudo nano /usr/local/bin/gptcli
```

아래 내용을 붙여넣기 (경로는 본인 환경에 맞게 수정):

```bash
#!/bin/bash

# --- 사용자 설정 ---
# gptcli.py와 .venv가 있는 프로젝트 디렉터리의 절대 경로 pwd를 사용하여 해당 폴더로 수정
PROJECT_DIR="/home/ubuntu/codes/gpt_cli"
# ------------------

VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python3"
SCRIPT_PATH="${PROJECT_DIR}/gptcli.py"

# 가상환경 Python으로 실행 (모든 인자 전달)
"$VENV_PYTHON" "$SCRIPT_PATH" "$@"
```

**2) 실행 권한 부여**

```bash
sudo chmod +x /usr/local/bin/gptcli
```

**3) 완료! 이제 어디서든:**

```bash
gptcli
```

> **왜 이 방식인가?**
> - 시스템 Python과 충돌 없음 (가상환경 격리)
> - 의존성 버전 고정 보장
> - 심볼릭 링크보다 유연함 (경로/환경변수 커스텀 가능)

### 대안: 심볼릭 링크 (간단하지만 제한적)

```bash
chmod +x gptcli.py
sudo ln -s /absolute/path/to/gptcli.py /usr/local/bin/gptcli
```

> 주의: 이 방식은 시스템 Python을 사용하므로, 가상환경 의존성이 시스템에도 설치되어 있어야 합니다.

### Windows (Path 등록)

1. 시스템/사용자 Path에 `gptcli.py`가 있는 폴더 추가
2. PowerShell에서:
```powershell
python C:\path\to\gptcli.py
```
- 또는 `.bat` 래퍼 스크립트 생성:
```batch
@echo off
C:\path\to\.venv\Scripts\python.exe C:\path\to\gptcli.py %*
```

---

## ⌨️ 프롬프트/자동완성/키바인딩

- 프롬프트 헤더 예:
  ```
  [ gemini-2.5-pro | session: default | mode: dev | 2 files | compact mode ]
  ```
- Enter 동작:
  - 자동완성 중: 현재(또는 첫 번째) 후보 적용
  - 슬래시 명령어 입력 중: 실행
  - 일반 텍스트: 줄바꿈(멀티라인), Alt+Enter(=Esc+Enter): 강제 실행
- Esc: 버퍼 리셋, Ctrl+A: 전체 선택
- `_`로 시작하는 첫 토큰은 힌트 모드(자동완성 유도)
- 경로 자동완성은 `.gptignore` 규칙을 실시간 반영

---

## 🛠️ 명령어 레퍼런스(요약)

### 기본 명령어
| 명령어 | 설명 |
|--------|------|
| `/commands` | 전체 명령어 도움말 |
| `/compact_mode` | 첨부파일 압축 모드 토글 |
| `/pretty_print` | 고급(Rich) 출력 토글 |
| `/last_response` | 마지막 응답을 Rich Markdown으로 재출력 |
| `/raw` | 마지막 응답 raw 출력 |
| `/exit` | 종료 |

### 🔧 Tool 관련 명령어 (NEW!)
| 명령어 | 설명 |
|--------|------|
| `/tools` | Tool 모드 ON/OFF 토글 (파일 수정 기능) |
| `/trust <full\|read_only\|none>` | Tool 신뢰 수준 설정 |
| `/toolforce` | Tool 강제 모드 토글 (항상 Tool 사용) |

### 🧠 요약 관련 명령어 (NEW!)
| 명령어 | 설명 |
|--------|------|
| `/summarize [--force]` | 컨텍스트 수동 요약 (오래된 대화 압축) |
| `/show_summary` | 현재 요약 정보 표시 (압축률, 절감량 등) |

### 모델/테마 명령어
| 명령어 | 설명 |
|--------|------|
| `/select_model` | 모델 선택 TUI(현재 프로젝트에만 적용) |
| `/search_models <키워드...>` | OpenRouter 모델 검색 → `ai_models.txt` 업데이트(TUI) |
| `/theme <이름>` | 코드 하이라이트 테마 변경 |
| `/mode <dev\|general\|teacher>` | 시스템 프롬프트 모드 변경 |

### 파일 명령어
| 명령어 | 설명 |
|--------|------|
| `/all_files` | 파일 선택기(TUI) 실행 |
| `/files <경로...>` | 수동 파일/폴더 첨부(재귀, `.gptignore` 준수) |
| `/clearfiles` | 첨부파일 초기화 |

### 세션 명령어
| 명령어 | 설명 |
|--------|------|
| `/session [이름]` | 세션 전환(TUI/직접 지정, 스냅샷 포함) |
| `/backup [reason...]` | 현재 세션 단일 스냅샷 강제 저장 |
| `/reset [--no-snapshot\|--hard]` | 세션 초기화(soft/hard) |

### 즐겨찾기/편집기
| 명령어 | 설명 |
|--------|------|
| `/savefav <이름>` | 마지막 사용자 프롬프트 즐겨찾기 저장 |
| `/usefav <이름>` | 즐겨찾기 불러와 프롬프트에 채우기 |
| `/favs` | 즐겨찾기 목록 표시 |
| `/edit` | 외부 편집기($EDITOR)로 긴 프롬프트 작성 후 즉시 전송 |

### 분석/유틸리티
| 명령어 | 설명 |
|--------|------|
| `/diff_code` | 코드 블록/첨부 파일 Diff TUI |
| `/show_context [옵션]` | 컨텍스트 상세 리포트(-v/--verbose, --top N) |
| `/copy <N>` | 마지막 응답의 N번째 코드 블록 클립보드 복사 |

> **참고**: `/restore` 명령은 별도 제공하지 않습니다. 세션 전환(`/session`)과 리셋(`/reset`) 플로우에서 스냅샷을 자동으로 관리합니다.

---

## 🖼️ 파일 첨부 규칙

- 텍스트(예: `.py/.ts/.json/.md/...`)는 내용이 코드 펜스와 함께 전송됩니다.
- 이미지: 20MB 초과 시 거부. 1MB 초과는 자동 최적화(JPEG, 품질/리사이즈) 후 data: URL로 전송.
- PDF: data: URL로 전송(일부 모델만 직접 처리 가능). 토큰은 대략 KB*3으로 추정.
- 전송 전 첨부 토큰 분석 표를 출력하며, Compact 모드에서는 과거 메시지 첨부가 파일명 플레이스홀더로 압축됩니다.

---

## 🧪 Diff 뷰어 키 가이드(`/diff_code`)

- 리스트: ↑/↓ 이동, Enter(섹션 펼침/파일 프리뷰), Space(선택), D(diff 실행), Q(종료)
- 프리뷰: PgUp/Dn·휠 스크롤, ←/→ 가로 스크롤(Shift 가속), Home/End 시작/끝
- Diff 실행 화면:
  - `+`/`-`: 문맥 줄 수 증/감
  - `f`: 전체 보기 토글
  - `←/→`, `Shift+←/→`, `Home/End`: 가로 스크롤
  - `Q`: 닫기

---

## 🧰 테마

- 하이라이팅 테마:
  ```
  monokai-ish(기본), vscode-dark, github-dark, dracula, one-dark, solarized-dark, tokyo-night, gruvbox-dark, nord, retro-green, pastel
  ```
- 적용:
```bash
/theme <이름>
```

---

## 🔒 보안/프라이버시 및 저장 위치

- 전송 대상: 입력 텍스트, 선택된 첨부(텍스트/이미지/PDF), 시스템 프롬프트는 OpenRouter API로 전송됩니다.
- 로컬 저장:
  - 세션: `./.gpt_sessions/session_<name>.json`
  - 응답 Markdown: `./gpt_markdowns/*.md`
  - 코드 블록: `./gpt_codes/codeblock_<session>_*`
  - 스냅샷: `./.gpt_sessions/backups/session_<slug>.json`, `./gpt_codes/backup/<slug>/`
- 민감정보가 포함된 파일을 첨부하지 않도록 주의하세요. `.gptignore`를 통해 기본적으로 민감/불필요 파일들을 배제합니다.

---

## 🧩 고급 설정

- 컨텍스트 트리밍 비율(환경 변수)
  ```
  GPTCLI_TRIM_RATIO="0.75"
  ```
  값이 클수록 과거 문맥을 더 많이 유지합니다(응답 토큰 예약 고려).
- 모델 컨텍스트 예약(휴리스틱)
  - 200k 이상: 32k, 128k 이상: 16k, 그 외: 4k(내부 휴리스틱)
- 모델 목록 파일: `./ai_models.txt` (프로젝트 루트)
  - 한 줄 형식: `<model_id> <context_length>`
  - `/search_models`, `/select_model` TUI로 관리 가능.

---

## 🧱 아키텍처 개요

### 핵심 모듈
| 모듈 | 역할 |
|------|------|
| `GPTCLI` | 메인 앱 루프, 메시지/세션 상태 관리, 스트림 파이프라인 호출 |
| `CommandHandler` | `/...` 명령 전담, 세션/파일/테마/모델/리포트 관리 |
| `AIStreamParser` | OpenRouter 스트림 수신 → 마크다운/코드 펜스 상태 머신 렌더링(Reasoning/Code Live 포함) |

### 🔧 Tool 시스템 (NEW!)
| 모듈 | 역할 |
|------|------|
| `ToolRegistry` | 스키마/실행기/권한 통합 관리, Tool 호출 전체 흐름 오케스트레이션 |
| `ToolExecutor` | 실제 Tool 실행 (Read/Write/Edit/Bash/Grep/Glob) |
| `PermissionManager` | Trust Level 관리, 위험 명령 패턴 검사, 사용자 확인 프롬프트 |
| `ToolLoopService` | Tool 호출 루프 관리 (AI 응답 → Tool 실행 → 결과 피드백 반복) |

### 🧠 요약 시스템 (NEW!)
| 모듈 | 역할 |
|------|------|
| `SummarizationService` | 자동/수동 요약, 청크 분할 요약, 재요약 레벨 관리 |

### 지원 모듈
| 모듈 | 역할 |
|------|------|
| `ThemeManager` | Urwid/Rich 팔레트, Pygments 토큰 맵핑, Truecolor→256색 폴백 |
| `ConfigManager` | 디렉터리/세션/코드블록/무시 규칙/즐겨찾기/저장소 I/O |
| `FileSelector` | `.gptignore` 존중 TUI 파일 선택 |
| `CodeDiffer` | 응답 코드/로컬 파일 diff TUI(프리뷰/가로스크롤/문맥제어) |
| `ModelSearcher` | OpenRouter 모델 조회+선택 TUI |
| `TokenEstimator` | 텍스트/이미지/PDF 토큰 추정(휴리스틱 포함) |

### Tool 실행 흐름
```
사용자 요청: "버그 좀 고쳐줘"
        ↓
    [AI 분석]
        ↓
    Tool 호출 결정 (Read → Edit)
        ↓
    ┌─────────────────────────────┐
    │  PermissionManager 확인     │
    │  (Trust Level 체크)         │
    └─────────────────────────────┘
        ↓
    ┌─────────────────────────────┐
    │  ToolExecutor 실행          │
    │  📖 Read → 파일 읽기        │
    │  ✏️ Edit → 코드 수정         │
    └─────────────────────────────┘
        ↓
    결과 피드백 → AI 응답 완성
```

---

## 🛠️ 문제 해결(Troubleshooting)

- OpenRouter API 오류:
  - `.env`의 `OPENROUTER_API_KEY` 확인, 네트워크/프록시 환경 점검
- 클립보드 복사 실패(PyperclipException):
  - Linux: `xclip`/`xsel`(X11) 또는 `wl-clipboard`(Wayland) 설치 후 재시도
  - 원격/권한 제한 환경에서는 자동으로 raw 코드가 출력됩니다.
- 터미널 색상/깜빡임/왜곡:
  - Truecolor 미지원 터미널에서 색상 차이가 있을 수 있습니다. 256색 폴백 사용.
- Windows TUI 문제:
  - Windows Terminal 사용 권장. 기본 콘솔에서 키바인딩/컬러가 제한될 수 있음.
- PDF/이미지 토큰 과다:
  - 이미지 해상도/품질을 낮추거나 PDF 내용을 텍스트로 추출해 첨부

---

## 💡 워크플로우 예시

### 1) 🔧 Tool 모드로 직접 버그 수정 (NEW!)
```bash
gptcli
# Tool 모드 활성화 확인 (기본: ON)
> /tools
# Tool 모드: ON

# AI에게 직접 수정 요청
> src/utils/parser.py 에서 발생하는 IndexError 버그 좀 고쳐줘

# AI가 자동으로:
# 📖 Read: src/utils/parser.py 읽기
# ✏️ Edit: 버그 수정 적용
# 결과 설명 출력
```

### 2) 🛡️ 안전한 코드 리뷰 (read_only 모드)
```bash
gptcli
> /trust read_only  # 읽기만 자동, 수정은 확인 필요

> 이 프로젝트의 보안 취약점을 찾아줘

# AI가 자동으로:
# 📂 Glob: *.py 파일 목록
# 📖 Read: 주요 파일들 읽기
# 🔍 Grep: 위험 패턴 검색
# (Write/Edit 시도 시 사용자 확인 요청)
```

### 3) 📊 기존 코드 분석/리팩터링 (파일 첨부 방식)
```bash
gptcli
# /all_files 로 파일 선택 또는 /files src/app.py src/utils/
# /mode teacher 로 아키텍트 모드 전환
# 분석 요청 → /copy 1 로 제안 코드 즉시 복사
```

### 4) 🐛 오류 디버깅
- 터미널 스택 트레이스와 관련 소스 첨부(`/files ...`) → 원인/패치 제안
- `/diff_code`로 기존/수정안 시각 비교, 문맥 줄수/가로 스크롤로 정밀 검토
- 또는 Tool 모드에서: "이 에러 메시지 분석하고 고쳐줘" → AI가 직접 수정

### 5) 📚 학습/비교
- `/mode general` 또는 `/mode teacher`로 설명 스타일 조정
- 예: "asyncio vs threading 차이와 예제 코드" → `/savefav asyncio_vs_thread`로 프롬프트 저장

### 6) 🧠 긴 대화 세션 (자동 요약 활용)
```bash
gptcli
# 긴 대화 진행...
# (컨텍스트 80% 초과 시 자동 요약 발생)
# [🔄 자동 요약] 15,000 토큰 → 3,000 토큰 (압축률: 80%)

# 수동 요약 상태 확인
> /show_summary
# 요약 정보: 레벨 1, 압축률 80%, 절감 12,000 토큰

# 필요시 강제 요약
> /summarize --force
```

---

## 🔧 개발 팁

- 가상환경 권장:
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
- 로깅/디버깅: TUI 종료 후 스크롤이 위로 튀면 한 줄 개행이 바닥 스냅을 유발합니다(내부에서 처리).

---

## 📄 라이선스

- MIT License

---

# GPT-CLI Helper — The Developer's AI CLI (English)

GPT-CLI Helper is a conversational AI client engineered for terminal-first workflows. It runs on OpenRouter's universal API so you can switch among cutting-edge models (Claude, GPT, Gemini, Llama). It features **Claude Code-style Tool mode** and **automatic summarization for unlimited context**, enabling AI to directly modify files, search code, and execute commands beyond simple Q&A.

- Default model: `anthropic/claude-opus-4.5`
- Default context length: `200,000` tokens
- All settings/outputs stored in **project root (current working directory)**:
  ```
  ./ai_models.txt, ./.gptignore, ./.gpt_sessions/, ./gpt_codes/, ./gpt_markdowns/
  ```

## 🔧 Tool Mode (NEW!)
AI can directly read, write, edit files and execute commands:
- **6 Tools**: Read, Write, Edit, Bash, Grep, Glob
- **Trust Levels** for safety control:
  | Level | Description |
  |-------|-------------|
  | `full` | 🟢 Auto-execute all tools (default) |
  | `read_only` | 🟡 Auto for read-only, confirm for writes |
  | `none` | 🔴 Confirm every tool execution |
- **Dangerous command blocking**: `rm -rf /`, `sudo rm`, `mkfs` always require confirmation
- Commands: `/tools`, `/trust <level>`, `/toolforce`

## 🧠 Automatic Summarization (NEW!)
> *"The conversation has unlimited context through automatic summarization."*

- Auto-triggers at **80% context usage**
- Replaces old messages with summaries, **preserving key information**
- Up to 3 levels of re-summarization for **virtually unlimited conversations**
- **Chunked summarization** for large conversations (handles Gemini API limits)
- Commands: `/summarize [--force]`, `/show_summary`

## Other Highlights
- Streaming UI (Rich): Reasoning Live panel (auto-collapses cleanly), Code Live panel with dynamic height and "…N lines omitted…"
- Robust code fence parser: handles lists/indentation, backticks and tildes, avoids inline triple-backtick false positives, tracks nesting
- Powerful attachments: `.gptignore`-aware TUI file picker; text, image(optimizes >1MB), PDF
- Model TUI: `/search_models` (OpenRouter), `/select_model` (switch locally with per-model context length)
- Diff TUI (`/diff_code`): +/- context, full-file toggle, horizontal scroll, PgUp/Dn, wheel, precise syntax highlight
- Context management: Compact mode, rich `/show_context` report with vendor offsets, budget/usage, per-item breakdown, Top-N heavy messages
- Clipboard-safe `/copy`: raw fallback if clipboard access fails
- Session snapshots: `/session` (switch with auto-snapshot), `/reset` (soft/no-snapshot/hard), `/backup [reason...]`

---

## Requirements

- Python
  ```
  3.9+
  ```
- OS
  - Linux/macOS recommended. Windows works with minor TUI/color differences (Windows Terminal recommended).
- Python deps (examples)
  ```
  rich, urwid, prompt_toolkit, requests, pyperclip, python-dotenv, openai, pathspec, tiktoken, Pillow, PyPDF2, pygments
  ```
- Clipboard on Linux:
  ```
  xclip or xsel (X11), wl-clipboard (Wayland)
  ```

---

## Directory layout

All files are stored in the **project root (current working directory)**:
```
./ai_models.txt           # Model list (model_id context_length)
./.gptignore              # Ignore patterns for file picker
./.gpt_sessions/          # Session storage (with backups/)
./gpt_codes/              # Code block files (with backup/<slug>/)
./gpt_markdowns/          # Assistant response markdowns
./.gpt_session            # Current session pointer
```

> **Note**: Global config directory (`~/codes/gpt_cli`) is no longer used. All settings are project-local.

---

## Install & Setup
```bash
git clone https://github.com/NA-DEGEN-GIRL/gpt-cli-helper.git
cd gpt-cli-helper
pip install -r requirements.txt
```

`.env`:
```env
OPENROUTER_API_KEY="sk-or-..."
APP_URL="https://github.com/NA-DEGEN-GIRL/gpt-cli-helper"
APP_TITLE="GPT-CLI"
GPTCLI_TRIM_RATIO="0.75"
```

Global command:
```bash
chmod +x gptcli.py
sudo ln -s /absolute/path/to/gptcli.py /usr/local/bin/gptcli
gptcli --help
```
Windows: add folder to Path, then `gptcli.py --help`.

---

## Commands (short)

### Tool & Summarization (NEW!)
- `/tools` - Toggle Tool mode (file modification)
- `/trust <full|read_only|none>` - Set trust level
- `/toolforce` - Toggle force-tool mode
- `/summarize [--force]` - Manual context summarization
- `/show_summary` - Show current summary info

### General
- `/commands`, `/compact_mode`, `/pretty_print`, `/last_response`, `/raw`, `/exit`
- `/select_model`, `/search_models <kw...>`, `/theme <name>`
- `/all_files`, `/files <path...>`, `/clearfiles`
- `/mode <dev|general|teacher>`
- `/session [name]`, `/backup [reason...]`, `/reset [--no-snapshot|--hard]`
- `/savefav <name>`, `/usefav <name>`, `/favs`, `/edit`
- `/diff_code`, `/show_context [opts]`, `/copy <N>`

---

## Attachments
- Text is wrapped in fenced code blocks.
- Images: >20MB rejected; >1MB are optimized (JPEG, size/quality) and sent as data URLs.
- PDFs: sent as data URLs (some models may not parse PDF directly). Token cost ~ KB*3.

---

## Advanced
- Context trim ratio via env:
  ```
  GPTCLI_TRIM_RATIO="0.75"
  ```
- Model list file: `./ai_models.txt` (project root)
  - Format: `<model_id> <context_length>`
  - Managed via `/search_models`, `/select_model` TUI

---

## Architecture

### Core
- `GPTCLI` - Main loop, message/session management
- `CommandHandler` - Slash command processing
- `AIStreamParser` - Stream rendering (Reasoning/Code Live)

### Tool System (NEW!)
- `ToolRegistry` - Schema/executor/permission orchestration
- `ToolExecutor` - Actual tool execution (Read/Write/Edit/Bash/Grep/Glob)
- `PermissionManager` - Trust levels, dangerous command detection
- `ToolLoopService` - Tool call loop management

### Summarization (NEW!)
- `SummarizationService` - Auto/manual summarization, chunked processing

### Support
- `ThemeManager`, `ConfigManager`, `FileSelector`, `CodeDiffer`, `ModelSearcher`, `TokenEstimator`

---

## Troubleshooting
- OpenRouter errors: check `OPENROUTER_API_KEY` and network/proxy
- Clipboard on Linux: install `xclip`/`xsel` or `wl-clipboard`
- Terminal colors: truecolor vs 256-color fallback
- Windows TUI quirks: prefer Windows Terminal
- Large PDFs/Images: downscale/convert to text when possible

---

## License
MIT