# GPT-CLI Pro (gptcli.py) — 터미널 최적화 AI 동반자

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

GPT-CLI Pro는 OpenRouter(=OpenAI 호환 Chat Completions) 위에서 동작하는 고급 터미널 AI 클라이언트입니다. 실시간 스트리밍(추론·코드 프리뷰), 중첩 코드블록 파서, 파일/모델 선택 TUI, 코드 Diff 뷰, 토큰·컨텍스트 리포트, 세션/히스토리/즐겨찾기까지 “개발자 관점”에서 필요한 기능을 한 데 통합했습니다.

- OpenRouter를 통해 Claude 3.x, GPT-4o, Llama 3 등 다양한 모델을 한 API 키로 전환/호출
- 실시간 스트리밍: 추론(Reasoning) Live, 코드 Live(동적 높이→최대 높이 캡)
- 중첩 코드블록·들여쓰기·인라인 오탐 방지까지 고려한 라인 기반 펜스 파서(```/~~~)
- 파일 선택/모델 선택/모델 검색 등 urwid 기반 TUI
- 전/후 응답 코드 Diff(문맥 줄수 ±, 전체 보기 토글, 가로 스크롤)
- .gptignore_default + .gptignore 병합 규칙, Compact 모드로 토큰 절감
- /copy: 클립보드 복사(pyclipboard) + SSH 환경 안전 대안(원시 코드 재출력)

---

## ✨ 핵심 기능

### 1) 스트리밍 출력(Pretty/Rich, Raw)
- Pretty 모드: Rich 기반 실시간 렌더. 
  - 추론 Live(높이 REASONING_PANEL_HEIGHT=10)
  - 코드 Live(동적→최대 CODE_PREVIEW_PANEL_HEIGHT=15, 초과 시 “N줄 생략” 안내)
  - Markdown 인라인 강조를 안전 처리(simple_markdown_to_rich)
- Raw 모드: 응답 청크를 그대로 출력. reasoning 채널이 오면 함께 출력됨.

참고: Chat Completions API 인터페이스는 OpenAI 호환이 널리 쓰이는 표준입니다. 유사한 페이로드/스트리밍 패턴은 다른 공급자 문서에서도 확인할 수 있습니다([forefront.ai](https://docs.forefront.ai/api-reference/chat), [cohere.com](https://docs.cohere.com/v2/reference/chat-v1)).

### 2) 견고한 코드블록 파서
- 줄-시작(왼쪽 공백 허용) + 3개 이상 ``` 또는 ~~~ 만 “시작”으로 인정
- info 토큰(언어)은 0~1개만 허용, 언어 뒤 설명 텍스트가 오면 “시작”으로 보지 않음
- 인라인 ```…``` 오탐 방지
- 개행 없는 조각(fragment) 보호(시작/닫힘 의심 시 대기)
- 닫힘 펜스 tail(개행 없이 끝) 처리
- 중첩 fence 깊이 관리(nesting_depth), fence 문자 동일·길이 조건 준수

메시지/컨텐츠 블록 개념은 여러 프레임워크에서 유사하게 정의됩니다([python.langchain.com](https://python.langchain.com/api_reference/core/messages.html)).

### 3) 첨부/멀티모달
- /all_files TUI(urwid) + .gptignore_default + .gptignore 병합 규칙
- 이미지(.png/.jpg 등): data URL로 전송, 토큰 추정(자동 리사이즈/타일링 근사)
- PDF: 파일 파트로 전송(모델 지원 한정). 토큰 대략 근사(PDF 텍스트 추출 시 정확도 향상)

### 4) 컨텍스트 관리/절감(Compact)
- Trim: 모델 컨텍스트·시스템 프롬프트·벤더 오프셋·예약분 고려
- Compact 모드(기본 on): 과거 유저 메시지의 첨부를 플레이스홀더로 축약
- /show_context: 예산/사용/항목별(텍스트·이미지·PDF) 분해, 상위 N 무거운 메시지 표시

스트리밍/요청 모델 패턴은 범용적이며, 다른 SDK/프레임워크의 스트림 패턴과도 유사합니다([ai.pydantic.dev](https://ai.pydantic.dev/api/models/base/)).

### 5) Diff 뷰(urwid)
- unified_diff 기반, 문맥 줄수 n(±/F 키로 확장/축소·전체 보기 토글)
- 수평 스크롤(←→/Shift로 가속), 줄 번호/표식/구분자
- Pygments 사전 렉싱으로 줄 단위 정확 하이라이트(멀티라인 docstring 처리)
- 색상 팔레트: 16/256 환경 고려(HEX → 안전 색 강등/팔레트 표준화 옵션 제공)

### 6) 모델 선택/검색(TUI)
- /select_model: ai_models.txt 기반 리스트에서 선택
- /search_models: OpenRouter 모델 API 검색 후 선택 항목 저장

### 7) 세션/저장/복원
- .gpt_sessions/session_<name>.json: 메시지/모델/컨텍스트/usage 누적
- 마지막 응답 .md 저장(gpt_markdowns/), 코드블록 자동 파일 저장(gpt_codes/)
- /reset: 세션 백업+초기화, /restore: 백업에서 복원(코드 파일 포함)

### 8) /copy (클립보드) + 안전 대안
- `/copy <번호>`: 해당 코드블록을 pyperclip.copy()
- SSH/헤드리스 환경에서 실패 시(예: X11 미설정) → **원시 코드 그대로 재출력**(패딩/패널 없이), 사용자가 드래그·복사 가능
- Git Bash는 X 서버 내장 X, WSL2(WSLg)는 내장 X 서버가 있어 성공 확률 높음

---

## 🚀 설치

### 1) Python/패키지
```text
# requirements.txt
openai
python-dotenv
rich
urwid
prompt-toolkit
pyperclip
pathspec
tiktoken
PyPDF2
Pillow
requests
pygments
```
```bash
pip install -r requirements.txt
```

### 2) API 키(.env)
```env
OPENROUTER_API_KEY="sk-or-..."
```

### 3) 전역 설정/디렉터리
- 기본 전역 디렉터리: `~/codes/gpt_cli`
- 최초 실행 시 자동 생성:
  - `ai_models.txt` (모델 목록)
  - `.gptignore_default` (전역 무시 규칙)

---

## 💡 사용법

### 대화형 모드
```bash
gptcli
```

### 단일 프롬프트
```bash
gptcli "파이썬에서 set으로 중복 제거 예시" --model openai/gpt-4o
```

### 주요 명령어(대화 중)
| 명령 | 설명 |
|---|---|
| /commands | 명령어 목록 |
| /pretty_print | Pretty/Rich ↔ Raw 토글 |
| /raw | 마지막 응답 원문 다시 출력 |
| /select_model | 모델 선택 TUI |
| /search_models gpt o3 | 모델 검색 후 ai_models.txt 갱신 |
| /all_files | 파일 선택기(TUI) |
| /files f1 f2 | 파일 직접 지정(재귀·무시규칙 적용) |
| /clearfiles | 첨부 초기화 |
| /mode <dev|general|teacher> [-s <session>] | 페르소나/세션 전환 |
| /savefav <name> /usefav <name> /favs | 즐겨찾기 관리 |
| /diff_code | 코드블록 간 Diff 뷰 |
| /show_context [--top N] [-v] | 컨텍스트 리포트 표시 |
| /reset /restore | 세션 초기화/복원 |
| /copy <번호> | 해당 코드블록을 클립보드 복사(실패 시 원문 재출력) |
| /exit | 종료 |

### 첨부/멀티모달
- /all_files 또는 /files로 추가
- .gptignore_default + .gptignore 병합 규칙 준수
- 이미지: data URL + 토큰 추정
- PDF: 파일 파트(모델 호환성 필요)

---

## 🧠 내부 구조(요약)

- ask_stream: 스트리밍 엔진(Pretty/Raw), reasoning/코드 Live, fence 파서, 중첩·fragment·tail 처리, 동적/최대 높이
- TokenEstimator: tiktoken 기반 텍스트/이미지/PDF 토큰(근사)
- FileSelector(urwid): 디렉터리 재귀/부분선택/전체선택, 무시규칙 병합
- ModelSearcher(urwid): OpenRouter 모델 API 검색/선택
- CodeDiffer(urwid): unified_diff 렌더, 문맥 n 조정, 전체 보기 토글, 수평 스크롤, 사전 렉싱 하이라이트
- Compact 모드: 과거 첨부 축약(플레이스홀더)
- 세션: 메시지/모델/컨텍스트/usage 백업·복원·마크다운 저장·코드블록 저장

---

## 🧪 팁 & 문제 해결

### 1) /copy 실패(SSH)
- 원인: 헤드리스(X 클립보드 없음). Git Bash는 X 서버 없음 → **VcXsrv/Xming** 필요. WSL2(WSLg)는 내장 X 서버로 성공 가능.
- 이미 README 반영: 실패 시 원시 코드 재출력(패널/패딩 없음) → 바로 드래그/복사.

### 2) 색상(16/256/TrueColor)
- urwid는 초기화 시 `$TERM` 기반으로 색상 모드 결정. 256색 강제:
  - 실행 전 `export TERM=xterm-256color`
  - 코드에서 `screen = urwid.raw_display.Screen(); screen.set_terminal_properties(colors=256)` → MainLoop에 전달
- HEX 색 사용 시에는 256 안전값으로 강등하는 유틸/팔레트 사용 권장.

### 3) 스트리밍 중 취소
- Ctrl+C 시, Live 안전 종료 및 “응답 중단” 출력 → 다음 프롬프트로 복귀

---

## 🔐 보안
- .gptignore_default + .gptignore로 비밀·대용량·불필요 경로 제외
- SENSITIVE_KEYS 마스킹 훅(추가 확장 가능)

---

## 📚 참고(Interfaces/Streaming)
- Chat Completions 형식(타 공급자도 유사): [forefront.ai](https://docs.forefront.ai/api-reference/chat), [cohere.com](https://docs.cohere.com/v2/reference/chat-v1)
- 메시지/컨텐츠 블록 개념(프레임워크 관점): [python.langchain.com](https://python.langchain.com/api_reference/core/messages.html)
- 스트리밍 요청 패턴/설계: [ai.pydantic.dev](https://ai.pydantic.dev/api/models/base/)

---

# GPT-CLI Pro (gptcli.py) — The Developer’s AI CLI (English)

GPT-CLI Pro is a power-user AI CLI built on OpenRouter (OpenAI-compatible Chat Completions). It ships real-time streaming (reasoning/code lives), a robust nested code fence parser, urwid TUIs (file/model pickers), a diff viewer, token/context reporting, sessions/history/favorites—everything opinionated for developers.

- One API key to flip among Claude/GPT/Llama families
- Streaming: Reasoning Live (fixed height), Code Live (dynamic→capped)
- Line-anchored, indentation-tolerant, nested code fence parser for ```/~~~
- urwid TUIs: file/model pickers and model search
- Code diff (context ±, full-view toggle, horizontal scroll)
- .gptignore_default + .gptignore merge, Compact mode to cut tokens
- /copy with pyperclip and SSH-safe fallback (raw reprint)

## Features
- Pretty vs Raw streaming. Pretty uses Rich:
  - Reasoning Live (height=REASONING_PANEL_HEIGHT)
  - Code Live (auto-sizes up to CODE_PREVIEW_PANEL_HEIGHT with “… N lines omitted”)
- Robust fence parsing:
  - only line-start fences (with left whitespace) count
  - one info token allowed; in-line ```…``` never triggers code mode
  - fragment wait and tail-close handling; nesting depth for inner fences
- Multimodal attachments:
  - File picker TUI respecting ignore rules
  - Images as data-URL + token estimation
  - PDF as file part (model-dependent)
- Context budgeting:
  - trim by context/system/reserves/vendor offsets
  - Compact mode: reduce old user messages’ attachments into placeholders
  - /show_context prints budgets, categories, top heavy messages
- Diff viewer (urwid):
  - unified_diff with context lines, full toggle, horizontal scroll
  - tokenized highlighting via Pygments (pre-lexed per line, docstrings)
- Model selection/search TUIs
- Sessions / backups / code-block autosave

Interfaces and streaming patterns resemble common Chat APIs ([forefront.ai](https://docs.forefront.ai/api-reference/chat), [cohere.com](https://docs.cohere.com/v2/reference/chat-v1)); content/message blocks are aligned with typical frameworks ([python.langchain.com](https://python.langchain.com/api_reference/core/messages.html)); streamed request design mirrors general patterns ([ai.pydantic.dev](https://ai.pydantic.dev/api/models/base/)).

## Install
See the Korean section (requirements.txt). Add `.env`:
```env
OPENROUTER_API_KEY="sk-or-..."
```

## Usage
- Interactive: `gptcli`
- One-off: `gptcli "question" --model openai/gpt-4o`
- Commands: `/all_files`, `/files`, `/diff_code`, `/copy <n>`, `/select_model`, `/search_models`, `/pretty_print`, `/raw`, `/mode`, `/reset`, `/restore`, `/show_context`, `/exit`

## Troubleshooting
- /copy fails on SSH:
  - Git Bash lacks X server; install VcXsrv/Xming, or use WSL2 (WSLg). The CLI already reprints raw code fallback for drag-copy.
- 16/256/TrueColor:
  - set `TERM=xterm-256color` before launch; pass a pre-configured Screen to MainLoop; down-convert hex to safe 256 palette entries.

## Security
- Ignore rules to avoid leaking secrets or noise.

## References
- Chat completions: [forefront.ai](https://docs.forefront.ai/api-reference/chat), [cohere.com](https://docs.cohere.com/v2/reference/chat-v1)
- Message/content blocks: [python.langchain.com](https://python.langchain.com/api_reference/core/messages.html)
- Streaming models/patterns: [ai.pydantic.dev](https://ai.pydantic.dev/api/models/base/)