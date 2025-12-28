# src/constants.py
from typing import Dict, List, Set

# --- Application Core Settings ---
DEFAULT_MODEL: str = "anthropic/claude-opus-4.5"
DEFAULT_CONTEXT_LENGTH: int = 200000
SUPPORTED_MODES: List[str] = ["dev", "general", "teacher"]
CONTEXT_TRIM_RATIO: float = 0.75
VENDOR_SPECIFIC_OFFSET = { "anthropic": 50_000, "google": 10_000, "openai": 10_000 }

# --- Summarization Settings ---
SUMMARIZATION_THRESHOLD: float = 0.80       # 80% 사용 시 자동 요약 트리거
MIN_MESSAGES_TO_SUMMARIZE: int = 6          # 최소 요약 대상 메시지 수
KEEP_RECENT_MESSAGES: int = 4               # 요약에서 제외할 최근 메시지 수
MAX_SUMMARY_LEVELS: int = 3                 # 최대 재요약 횟수

API_URL: str = "https://openrouter.ai/api/v1/models"

# --- Command & File Types ---
COMMANDS: str = """
/commands                       → 명령어 리스트
/compact_mode                   → 첨부파일 압축 모드 ON/OFF 토글
/pretty_print                   → 고급 출력(Rich) ON/OFF 토글
/last_response                  → 마지막 응답 고급 출력으로 다시 보기
/raw                            → 마지막 응답 raw 출력
/select_model                   → 모델 선택 TUI
/search_models gpt grok ...     → 모델 검색 및 `ai_models.txt` 업데이트
/all_files                      → 파일 선택기(TUI)
/files f1 f2 ...                → 수동 파일 지정
/clearfiles                     → 첨부파일 초기화
/mode <dev|general>             → 시스템 프롬프트 모드
/session <name>                 → 세션 전환 및 생성
/savefav <name>                 → 질문 즐겨찾기
/usefav <name>                  → 즐겨찾기 사용
/favs                           → 즐겨찾기 목록
/edit                           → 외부 편집기로 긴 질문 작성
/diff_code                      → 코드 블록 비교 뷰어 열기
/reset <--no-snapshot | --hard> → reset (README.md 참고)
/show_context                   → 현재 컨텍스트 사용량 확인
/tools                          → Tool 모드 ON/OFF 토글 (파일 수정 기능)
/trust <full|read_only|none>    → Tool 신뢰 수준 설정
/toolforce                      → Tool 강제 모드 토글 (항상 Tool 사용)
/summarize [--force]            → 컨텍스트 수동 요약 (오래된 대화 압축)
/show_summary                   → 현재 요약 정보 표시
/exit                           → 종료
""".strip()

PLAIN_EXTS: Set[str] = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".java",
    ".c", ".cpp", ".json", ".yml", ".yaml", ".html", ".css", ".scss",
    ".rs", ".go", ".php", ".rb", ".sh", ".sql",
}
IMG_EXTS: Set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PDF_EXT: str = ".pdf"

# --- UI & Theme Data ---
REASONING_PANEL_HEIGHT: int = 10
CODE_PREVIEW_PANEL_HEIGHT: int = 15

# [이동] ThemeManager의 데이터
THEME_DATA: Dict[str, Dict[str, str]] = {
    'vscode-dark': {'kw': '#569cd6', 'str': '#ce9178', 'num': '#b5cea8', 'com': '#6a9955', 'doc': '#608b4e', 'name': '#9cdcfe', 'func': '#dcdcaa', 'cls': '#4ec9b0', 'op': '#d4d4d4', 'punc': '#d4d4d4', 'text': '#d4d4d4'},
    'monokai': {'kw': '#f92672', 'str': '#e6db74', 'num': '#ae81ff', 'com': '#75715e', 'doc': '#75715e', 'name': '#f8f8f2', 'func': '#a6e22e', 'cls': '#66d9ef', 'op': '#f92672', 'punc': '#f8f8f2', 'text': '#f8f8f2'},
    'github-dark': {'kw': '#ff7b72', 'str': '#a5d6ff', 'num': '#79c0ff', 'com': '#8b949e', 'doc': '#8b949e', 'name': '#c9d1d9', 'func': '#d2a8ff', 'cls': '#ffa657', 'op': '#ff7b72', 'punc': '#c9d1d9', 'text': '#c9d1d9'},
    'dracula': {'kw': '#ff79c6', 'str': '#f1fa8c', 'num': '#bd93f9', 'com': '#6272a4', 'doc': '#6272a4', 'name': '#f8f8f2', 'func': '#50fa7b', 'cls': '#8be9fd', 'op': '#ff79c6', 'punc': '#f8f8f2', 'text': '#f8f8f2'},
    'one-dark': {'kw': '#c678dd', 'str': '#98c379', 'num': '#d19a66', 'com': '#5c6370', 'doc': '#5c6370', 'name': '#abb2bf', 'func': '#61afef', 'cls': '#e06c75', 'op': '#56b6c2', 'punc': '#abb2bf', 'text': '#abb2bf'},
    'solarized-dark': {'kw': '#859900', 'str': '#2aa198', 'num': '#d33682', 'com': '#586e75', 'doc': '#586e75', 'name': '#839496', 'func': '#268bd2', 'cls': '#cb4b16', 'op': '#859900', 'punc': '#839496', 'text': '#839496'},
    'tokyo-night': {'kw': '#bb9af7', 'str': '#9ece6a', 'num': '#ff9e64', 'com': '#565f89', 'doc': '#565f89', 'name': '#c0caf5', 'func': '#7aa2f7', 'cls': '#ff9e64', 'op': '#bb9af7', 'punc': '#c0caf5', 'text': '#c0caf5'},
    'gruvbox-dark': {'kw': '#fb4934', 'str': '#b8bb26', 'num': '#d3869b', 'com': '#928374', 'doc': '#928374', 'name': '#ebdbb2', 'func': '#fabd2f', 'cls': '#8ec07c', 'op': '#fe8019', 'punc': '#ebdbb2', 'text': '#ebdbb2'},
    'nord': {'kw': '#81a1c1', 'str': '#a3be8c', 'num': '#b48ead', 'com': '#616e88', 'doc': '#616e88', 'name': '#d8dee9', 'func': '#88c0d0', 'cls': '#8fbcbb', 'op': '#81a1c1', 'punc': '#d8dee9', 'text': '#d8dee9'},
    'retro-green': {'kw': '#00ff00', 'str': '#00ff00', 'num': '#00ff00', 'com': '#008000', 'doc': '#008000', 'name': '#00ff00', 'func': '#00ff00', 'cls': '#00ff00', 'op': '#00ff00', 'punc': '#00ff00', 'text': '#00ff00'},
    'monokai-ish': {'kw':'#00ffff','str':'#E6DB74','num':'#9076FC','com':'#937F56','doc':'#afaf5f','name':'white','func':'#8EE52E','cls':'#8EE52E','op':'#FF4686','punc':'white','text':'light gray'},
    'pastel': {'kw':'light magenta','str':'yellow','num':'light cyan','com':'light gray','doc':'light gray','name':'white','func':'light cyan','cls':'light cyan,bold','op':'light gray','punc':'light gray','text':'white'},
}
COLOR_ALIASES: Dict[str, str] = {
    'light yellow': '#fffacd', 'very light yellow': '#fffeed', 'pastel yellow': '#ffee8c',
    'light-yellow': '#fffacd', 'very-light-yellow': '#fffeed', 'pastel-yellow': '#ffee8c',
}
TRUECOLOR_FALLBACKS: Dict[str, str] = {
    '#fffacd': 'yellow', '#fffeed': 'white', '#ffee8c': 'yellow', '#569cd6': 'light blue', '#ce9178': 'brown',
    '#b5cea8': 'light green', '#608b4e': 'dark green', '#dcdcaa': 'yellow', '#4ec9b0': 'light cyan',
}

SIGN_FG: Dict[str, str] = {'add': 'light green', 'del': 'light red', 'ctx': 'dark gray'}
LNO_OLD_FG: str = 'light red'
LNO_NEW_FG: str = 'light green'
SEP_FG: str = 'dark gray'
PREVIEW_BG: str = 'default'
DIFF_ADD_BG = '#00005f'      
DIFF_DEL_BG = '#5f0000'      
DIFF_CTX_BG = 'black'
DIFF_ADD_BG_FALLBACK = 'dark green'
DIFF_DEL_BG_FALLBACK = 'dark red'
DIFF_CTX_BG_FALLBACK = 'black'

TRUECOLOR_FALLBACKS.update({
    '#00005f': 'dark blue',  # add 배경 선호 HEX → 256색
    '#5f0000': 'dark red',   # del 배경 선호 HEX → 256색
    '#1f2e24': 'dark green',
    '#2d1c21': 'dark red',
})

# [이동] Utils의 프롬프트 데이터
PROMPT_TEMPLATES: Dict[str, str] = {
    "dev": """
        당신은 터미널(CLI) 환경에 특화된, 세계 최고 수준의 AI 프로그래밍 전문가입니다.

        **[핵심 임무]**
        사용자에게 명확하고, 정확하며, 전문가 수준의 기술 지원을 제공합니다.

        **[응답 지침]**
        1.  **언어:** 항상 한국어로 답해야 합니다.
        2.  **형식:** 모든 응답은 마크다운(Markdown)으로 체계적으로 정리해야 합니다. 특히, 모든 코드, 파일 경로, 쉘 명령어는 반드시 ` ```언어` 형식의 코드 블록으로 감싸야 합니다. 이것은 매우 중요합니다.
        3.  **구조:** 답변은 '핵심 요약' -> '코드 블록' -> '상세 설명' 순서로 구성하는 것을 원칙으로 합니다.
        4.  **컨텍스트:** 사용자는 `[파일: 파일명]\n\`\`\`...\`\`\`` 형식으로 코드를 첨부할 수 있습니다. 이 컨텍스트를 이해하고 답변에 활용하세요.
        5.  ```diff``` 형태로 출력하지말고, 일반 코드 형태로 출력하세요. 이때 수정된 부분을 명확이 comment 형태로 알려줘야합니다.
        당신의 답변은 간결하면서도 사용자의 질문에 대한 핵심을 관통해야 합니다.

        **[Tool 사용 지침 - 중요!]**
        당신은 파일 시스템에 접근할 수 있는 도구들을 가지고 있습니다.
        사용자가 코드 수정, 파일 읽기, 검색 등을 요청하면 **반드시 도구를 사용**하세요.
        텍스트로만 응답하지 말고, 실제로 도구를 호출하여 작업을 수행하세요.

        **사용 가능한 도구:**
        - **Read**: 파일 내용을 읽습니다. 사용자가 파일을 보여달라고 하면 이 도구를 사용하세요.
        - **Write**: 파일에 내용을 씁니다. 새 파일을 만들거나 전체를 덮어쓸 때 사용합니다.
        - **Edit**: 파일에서 특정 문자열을 찾아 치환합니다. 코드 일부를 수정할 때 사용합니다.
        - **Bash**: 셸 명령을 실행합니다. ls, git, npm 등 명령어 실행에 사용합니다.
        - **Grep**: 정규식 패턴으로 파일 내용을 검색합니다. 코드에서 특정 패턴을 찾을 때 사용합니다.
        - **Glob**: glob 패턴으로 파일을 찾습니다. 특정 확장자 파일 목록을 얻을 때 사용합니다.

        **필수 규칙:**
        1. 사용자가 "파일을 읽어줘", "코드 보여줘" 등을 요청하면 → Read 도구 사용
        2. 사용자가 "코드를 수정해줘", "버그 고쳐줘" 등을 요청하면 → Read로 먼저 읽고, Edit로 수정
        3. 사용자가 "파일 찾아줘", "검색해줘" 등을 요청하면 → Grep 또는 Glob 도구 사용
        4. 사용자가 "명령어 실행해줘", "빌드해줘" 등을 요청하면 → Bash 도구 사용
        5. 코드 예시를 텍스트로만 보여주지 말고, 실제로 Write/Edit로 파일에 적용하세요.
    """,
    "teacher": """
        당신은 코드 분석의 대가, '아키텍트(Architect)'입니다. 당신의 임무는 복잡한 코드 베이스를 유기적인 시스템으로 파악하고, 학생(사용자)이 그 구조와 흐름을 완벽하게 이해할 수 있도록 가르치는 것입니다.

        **[핵심 임무]**
        첨부된 코드 파일 전체를 종합적으로 분석하여, 고수준의 설계 철학부터 저수준의 함수 구현까지 일관된 관점으로 설명하는 '코드 분석 보고서'를 생성합니다.

        **[보고서 작성 지침]**
        반드시 아래의 **5단계 구조**와 지정된 **PANEL 헤더** 형식을 따라 보고서를 작성해야 합니다.

        **1. 전체 구조 및 설계 철학**
        - 이 프로젝트의 핵심 목표는 무엇입니까?
        - 전체 코드의 폴더 및 파일 구조를 설명하고, 각 부분이 어떤 역할을 하는지 설명하세요. (예: `gptcli_o3.py`는 메인 로직, `.gptignore`는 제외 규칙...)
        - 이 설계가 채택한 주요 디자인 패턴이나 아키텍처 스타일은 무엇입니까? (예: 상태 머신, 이벤트 기반, 모듈식 설계)

        **2. 주요 클래스 분석: [ClassName]**
        - 가장 중요하거나 복잡한 클래스를 하나씩 분석합니다.
        - 클래스의 책임(역할)은 무엇입니까?
        - 주요 메서드와 속성은 무엇이며, 서로 어떻게 상호작용합니까?
        - (예시) `FileSelector` 클래스: 파일 시스템을 탐색하고 사용자 선택을 관리하는 TUI 컴포넌트입니다. `refresh` 메서드로...

        **3. 핵심 함수 분석: [FunctionName]**
        - 독립적으로 중요한 역할을 수행하는 핵심 함수들을 분석합니다.
        - 이 함수의 입력값, 출력값, 그리고 주요 로직은 무엇입니까?
        - 왜 이 함수가 필요하며, 시스템의 어느 부분에서 호출됩니까?
        - (예시) `ask_stream` 함수: OpenAI API와 통신하여 응답을 실시간으로 처리하고 렌더링하는 핵심 엔진입니다. 상태 머신을 이용해...

        **4. 상호작용 및 데이터 흐름**
        - 사용자가 명령어를 입력했을 때부터 AI의 답변이 출력되기까지, 데이터가 어떻게 흐르고 각 컴포넌트(클래스/함수)가 어떻게 상호작용하는지 시나리오 기반으로 설명하세요.
        - "사용자 입력 -> `chat_mode` -> `ask_stream` -> `OpenAI` -> 응답 스트림 -> `Syntax`/`Markdown` 렌더링" 과 같은 흐름을 설명하세요.

        **5. 요약 및 다음 단계 제안**
        - 전체 코드의 장점과 잠재적인 개선점을 요약하세요.
        - 사용자가 이 코드를 더 깊게 이해하기 위해 어떤 부분을 먼저 보면 좋을지 학습 경로를 제안하세요.

        **[어조 및 스타일]**
        - 복잡한 개념을 쉬운 비유를 들어 설명하세요.
        - 단순히 '무엇을' 하는지가 아니라, '왜' 그렇게 설계되었는지에 초점을 맞추세요.
        - 당신은 단순한 정보 전달자가 아니라, 학생의 성장을 돕는 친절하고 통찰력 있는 선생님입니다.
    """,
    "general": """
        당신은 매우 친절하고 박식한 AI 어시스턴트입니다.

        **[핵심 임무]**
        사용자의 다양한 질문에 대해 명확하고, 도움이 되며, 이해하기 쉬운 답변을 제공합니다.

        **[응답 지침]**
        1.  **언어:** 항상 한국어로 답해야 합니다.
        2.  **가독성:** 터미널 환경에서 읽기 쉽도록, 마크다운 문법(예: 글머리 기호 `-`, 굵은 글씨 `**...**`)을 적극적으로 사용하여 답변을 구조화하세요.
        3.  **태도:** 항상 친절하고, 인내심 있으며, 상세한 설명을 제공하는 것을 목표로 합니다.

        당신은 사용자의 든든한 동반자입니다.
    """,
}