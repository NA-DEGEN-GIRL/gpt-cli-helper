# GPT-CLI Helper — 터미널 최적화 AI 개발 동반자

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

GPT-CLI Helper는 개발자의 터미널(CLI) 워크플로우에 자연스럽게 스며드는 대화형 AI 클라이언트입니다. OpenRouter의 범용 API 위에 구축되어 Claude, GPT, Gemini, Llama 등 최신 모델을 자유롭게 전환하며 사용할 수 있습니다. 코드 분석/리뷰, 디버깅, 학습, Diff 비교, 컨텍스트/토큰 관리까지 개발 프로세스 전반의 생산성을 극대화하도록 설계되었습니다.

- 기본 모델: `google/gemini-2.5-pro`
- 기본 컨텍스트 길이: `1,048,576` tokens
- 전역 설정 디렉터리:
  ```
  ~/codes/gpt_cli
  ```
- 세션/출력 디렉터리(프로젝트 루트 하위 자동 생성):
  ```
  ./.gpt_sessions, ./gpt_codes, ./gpt_markdowns
  ```

참고: 마크다운 코드 펜스(```), 언어 태그, 라인 번호 등 기본 문법은 GitHub 문법을 따릅니다.  

## Quick Demo (GIF)
![GPT-CLI Demo](assets/gptcli-demo.gif)

---

## ✨ 핵심 기능

- 실시간 스트리밍 출력(Rich 기반)
  - Reasoning Live: 추론 패널이 최근 n줄을 실시간 노출 후 완전히 접어 화면을 당깁니다.
  - Code Live: 코드 블록 스트리밍을 별도 패널로 표시. 길면 “...N줄 생략...” 안내.
- 견고한 코드블록 파서
  - 들여쓰기/리스트 내부의 펜스, 백틱(```)과 틸드(~~~) 모두 지원.
  - 인라인 트리플 백틱(문장 속 ```python) 오인식 방지.
  - 코드블록 중첩 깊이 추적.
- 강력한 파일 첨부 및 관리
  - `.gptignore`(전역+프로젝트) 규칙을 준수하는 TUI 파일 선택기(`/all_files`).
  - 텍스트/이미지(.png/.jpg/.jpeg/.webp/.gif/.bmp)/PDF 첨부 지원.
  - 이미지 20MB 초과 시 자동 차단, 1MB 초과 시 자동 최적화(품질 유지·크기 축소) 후 전송.
- 모델 검색/선택 TUI
  - `/search_models <키워드...>`: OpenRouter 모델 검색 → 선택 저장(`~/codes/gpt_cli/ai_models.txt`).
  - `/select_model`: 현재 프로젝트에서 모델 전환(모델별 컨텍스트 길이 함께 관리).
- Diff 뷰어(`/diff_code`)
  - 응답으로 저장된 코드블록 또는 로컬 첨부 파일을 선택해 2-way diff.
  - 문맥 줄수 +/-, 전체 보기(f), 좌우 스크롤(←/→, Shift+←/→, Home/End), PgUp/Dn·휠 스크롤 지원.
  - Pygments 기반 정밀 하이라이팅(멀티라인 문자열·docstring 포함).
- 효율적 컨텍스트/토큰 관리
  - Compact 모드(`/compact_mode`): 과거 메시지 첨부를 `[첨부: ...]`로 자동 압축.
  - 컨텍스트 리포트(`/show_context`): 시스템 프롬프트, 벤더 오프셋, 예약 토큰, 프롬프트 예산/사용률, 항목별(텍스트/이미지/PDF) 토큰 breakdown, Top-N 무거운 메시지까지 상세 분석. 옵션: `-v/--verbose`, `--top N`.
- 안전한 클립보드 복사(`/copy`)
  - `/copy <번호>`로 마지막 응답의 N번째 코드 블록을 즉시 복사.
  - 원격/제한 환경에서 실패 시 raw 코드 재출력(수동 복사) 폴백.
- 세션 스냅샷 & 복원 흐름
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

- 전역 설정:
  ```
  ~/codes/gpt_cli/
  ```
  - `ai_models.txt`         ← 모델 목록/컨텍스트 길이
  - `.gptignore_default`    ← 전역 무시 규칙(수정 가능)
- 프로젝트 루트(현재 작업 디렉터리 기준):
  ```
  .gpt_sessions/                 # 세션 JSON 저장소
    backups/session_<slug>.json  # 단일 스냅샷
  gpt_codes/                     # 코드 블록 파일 저장
    backup/<slug>/               # 코드 스냅샷
  gpt_markdowns/                 # 어시스턴트 응답 전문(Markdown) 저장
  .gptignore                     # 프로젝트 전용 무시 규칙(선택)
  .gpt_prompt_history.txt
  .gpt_favorites.json
  .gpt_session                   # 현재 세션 포인터
  ```

---

## 🚀 설치 및 설정

### 1) 저장소 클론
```bash
git clone https://github.com/your-username/gpt-cli-helper.git
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
APP_URL="https://github.com/your-username/gpt-cli-helper"
APP_TITLE="GPT-CLI"
# (선택) 컨텍스트 트리밍 비율(기본: constants.CONTEXT_TRIM_RATIO, 또는 0.75)
GPTCLI_TRIM_RATIO="0.75"
```

### 4) 전역 설정 디렉터리 자동 생성
최초 실행 시 아래 파일이 준비됩니다.
- `~/codes/gpt_cli/ai_models.txt`:
  ```
  <model_id> <context_length>
  예) openai/gpt-4o 128000
  ```
- `~/codes/gpt_cli/.gptignore_default`: 전역 무시 규칙(프로젝트 `.gptignore`와 병합 적용)

---

## ⚙️ 전역 명령어로 실행하기

### Linux/macOS (권장: 심볼릭 링크)
```bash
chmod +x gptcli.py
sudo ln -s /absolute/path/to/gptcli.py /usr/local/bin/gptcli
gptcli --help
```

### Windows (Path 등록)
- 시스템/사용자 Path에 `gptcli.py`가 있는 폴더를 추가 후:
```powershell
gptcli.py --help
```
- 확장자 없이 `gptcli`로 실행하려면 파일명을 `gptcli`로 바꾸고, `PATHEXT`에 `.PY` 포함 필요.

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

- `/commands`                       전체 명령어 도움말
- `/compact_mode`                   첨부파일 압축 모드 토글
- `/pretty_print`                   고급(Rich) 출력 토글
- `/last_response`                  마지막 응답을 Rich Markdown으로 재출력
- `/raw`                            마지막 응답 raw 출력
- `/select_model`                   모델 선택 TUI(현재 프로젝트에만 적용)
- `/search_models <키워드...>`      OpenRouter 모델 검색 → `ai_models.txt` 업데이트(TUI)
- `/theme <이름>`                   코드 하이라이트 테마 변경
- `/all_files`                      파일 선택기(TUI) 실행
- `/files <경로...>`                수동 파일/폴더 첨부(재귀, `.gptignore` 준수)
- `/clearfiles`                     첨부파일 초기화
- `/mode <dev|general|teacher>`     시스템 프롬프트 모드 변경
- `/session [이름]`                 세션 전환(TUI/직접 지정, 스냅샷 포함)
- `/backup [reason...]`             현재 세션 단일 스냅샷 강제 저장
- `/savefav <이름>`                 마지막 사용자 프롬프트 즐겨찾기 저장
- `/usefav <이름>`                  즐겨찾기 불러와 프롬프트에 채우기
- `/favs`                           즐겨찾기 목록 표시
- `/edit`                           외부 편집기($EDITOR)로 긴 프롬프트 작성 후 즉시 전송
- `/diff_code`                      코드 블록/첨부 파일 Diff TUI
- `/show_context [옵션]`            컨텍스트 상세 리포트(-v/--verbose, --top N)
- `/reset [--no-snapshot|--hard]`   세션 초기화(soft/hard)
- `/copy <N>`                       마지막 응답의 N번째 코드 블록 클립보드 복사
- `/exit`                           종료

주의: `/restore` 명령은 별도 제공하지 않으며, 세션 전환(`/session`)과 리셋(`/reset`) 플로우에서 스냅샷을 자동으로 관리합니다.

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
- 모델 목록 파일
  ```
  ~/codes/gpt_cli/ai_models.txt
  ```
  - 한 줄 형식: `<model_id> <context_length>`
  - `/search_models`, `/select_model` TUI로 관리 가능.

---

## 🧱 아키텍처 개요

- `GPTCLI`: 메인 앱 루프, 메시지/세션 상태 관리, 스트림 파이프라인 호출
- `CommandHandler`: `/...` 명령 전담, 세션/파일/테마/모델/리포트 관리
- `AIStreamParser`: OpenRouter 스트림 수신 → 마크다운/코드 펜스 상태 머신 렌더링(Reasoning/Code Live 포함)
- `ThemeManager`: Urwid/Rich 팔레트, Pygments 토큰 맵핑, Truecolor→256색 폴백
- `ConfigManager`: 디렉터리/세션/코드블록/무시 규칙/즐겨찾기/저장소 I/O
- `FileSelector`: `.gptignore` 존중 TUI 파일 선택
- `CodeDiffer`: 응답 코드/로컬 파일 diff TUI(프리뷰/가로스크롤/문맥제어)
- `ModelSearcher`: OpenRouter 모델 조회+선택 TUI
- `TokenEstimator`: 텍스트/이미지/PDF 토큰 추정(휴리스틱 포함)

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

### 1) 기존 코드 분석/리팩터링
```bash
gptcli
# /all_files 로 파일 선택 또는 /files src/app.py src/utils/
# /mode teacher 로 아키텍트 모드 전환
# 분석 요청 → /copy 1 로 제안 코드 즉시 복사
```

### 2) 오류 디버깅
- 터미널 스택 트레이스와 관련 소스 첨부(`/files ...`) → 원인/패치 제안
- `/diff_code`로 기존/수정안 시각 비교, 문맥 줄수/가로 스크롤로 정밀 검토

### 3) 학습/비교
- `/mode general` 또는 `/mode teacher`로 설명 스타일 조정
- 예: “asyncio vs threading 차이와 예제 코드” → `/savefav asyncio_vs_thread`로 프롬프트 저장

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

# GPT-CLI Helper — The Developer’s AI CLI (English)

GPT-CLI Helper is a conversational AI client engineered for terminal-first workflows. It runs on OpenRouter’s universal API so you can switch among cutting-edge models (Claude, GPT, Gemini, Llama). Beyond Q&A, it boosts productivity across code analysis/review, debugging, learning, diffing, and context/token management.

- Default model: `google/gemini-2.5-pro`
- Default context length: `1,048,576` tokens
- Global config dir:
  ```
  ~/codes/gpt_cli
  ```
- Project-local session/output dirs (auto-created in working dir):
  ```
  ./.gpt_sessions, ./gpt_codes, ./gpt_markdowns
  ```

## Highlights
- Streaming UI (Rich): Reasoning Live panel (auto-collapses cleanly), Code Live panel with dynamic height and “…N lines omitted…”
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

- Global config:
  ```
  ~/codes/gpt_cli/
  ```
  - `ai_models.txt`, `.gptignore_default`
- Project root:
  ```
  .gpt_sessions/ (with backups/)
  gpt_codes/     (with backup/<slug>/)
  gpt_markdowns/
  .gptignore, .gpt_prompt_history.txt, .gpt_favorites.json, .gpt_session
  ```

---

## Install & Setup
```bash
git clone https://github.com/your-username/gpt-cli-helper.git
cd gpt-cli-helper
pip install -r requirements.txt
```

`.env`:
```env
OPENROUTER_API_KEY="sk-or-..."
APP_URL="https://github.com/your-username/gpt-cli-helper"
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
- `/commands`, `/compact_mode`, `/pretty_print`, `/last_response`, `/raw`
- `/select_model`, `/search_models <kw...>`, `/theme <name>`
- `/all_files`, `/files <path...>`, `/clearfiles`
- `/mode <dev|general|teacher>`
- `/session [name]`, `/backup [reason...]`
- `/savefav <name>`, `/usefav <name>`, `/favs`, `/edit`
- `/diff_code`, `/show_context [opts]`, `/reset [--no-snapshot|--hard]`, `/copy <N>`, `/exit`

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
- Model list file:
  ```
  ~/codes/gpt_cli/ai_models.txt  # "<model_id> <context_length>"
  ```

---

## Architecture
- `GPTCLI`, `CommandHandler`, `AIStreamParser`, `ThemeManager`, `ConfigManager`, `FileSelector`, `CodeDiffer`, `ModelSearcher`, `TokenEstimator`

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