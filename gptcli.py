from __future__ import annotations

# ── stdlib
import argparse
import base64
import difflib
import itertools
import json
import mimetypes
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from typing import Union  # FileSelector 타입 힌트용

# ── 3rd-party
import shutil
import pyperclip
import urwid
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pathspec import PathSpec
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter, FuzzyCompleter, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import Condition
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.markdown import Markdown
from rich.theme import Theme

# 우리 앱만의 커스텀 테마 정의
rich_theme = Theme({
    "markdown.h1": "bold bright_white",
    "markdown.h2": "bold bright_white",
    "markdown.h3": "bold bright_white",
    "markdown.list": "cyan",
    "markdown.block_quote": "italic #8b949e",  # 옅은 회색
    "markdown.code": "bold white on #484f58",  # 회색 배경
    "markdown.hr": "yellow",
    "markdown.link": "underline bright_white"
})

# ────────────────────────────────
# 환경 초기화 / ENV INIT
# ────────────────────────────────
CONFIG_DIR = Path.home() / "codes" / "gpt_cli"
BASE_DIR = Path.cwd()

#_GPCLI_SCREEN = urwid.raw_display.Screen()
#_GPCLI_SCREEN.set_mouse_keys(True) # 마우스 키 이벤트 활성화
#_GPCLI_SCREEN.set_mode('mouse', True) # 마우스 모드 활성화 (클릭, 드래그 등)

SESSION_DIR = BASE_DIR / ".gpt_sessions"
SESSION_DIR.mkdir(exist_ok=True)

SESSION_FILE = lambda n: SESSION_DIR / f"session_{n}.json"
PROMPT_HISTORY_FILE = BASE_DIR / ".gpt_prompt_history.txt"
FAVORITES_FILE = BASE_DIR / ".gpt_favorites.json"
IGNORE_FILE = BASE_DIR / ".gptignore"
OUTPUT_DIR = BASE_DIR / "gpt_outputs"
MD_OUTPUT_DIR = BASE_DIR / "gpt_markdowns"
MODELS_FILE = CONFIG_DIR / "ai_models.txt"

OUTPUT_DIR.mkdir(exist_ok=True)
MD_OUTPUT_DIR.mkdir(exist_ok=True)

TRIMMED_HISTORY = 20
console = Console(theme=rich_theme)
stop_loading = threading.Event()

# .env 로드
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    console.print("[bold red]OPENROUTER_API_KEY 가 .env 에 없습니다.[/bold red]")
    sys.exit(1)

# 기본 헤더(앱 URL/타이틀) – 미설정 시 예시 사용
DEFAULT_HEADERS = {
    "HTTP-Referer": os.getenv("APP_URL", "https://github.com/user/gpt-cli"),
    "X-Title": os.getenv("APP_TITLE", "GPT-CLI"),
}

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    default_headers=DEFAULT_HEADERS,
)

def get_session_names() -> List[str]:
    """ .gpt_sessions 디렉터리에서 'session_*.json' 파일들을 찾아 세션 이름을 반환합니다. """
    names = []
    if not SESSION_DIR.exists():
        return []
    for f in SESSION_DIR.glob("session_*.json"):
        # "session_default.json" -> "default"
        name_part = f.name[len("session_"):-len(".json")]
        names.append(name_part)
    return sorted(names)

class ConditionalCompleter(Completer):
    """
    모든 문제를 해결한, 최종 버전의 '지능형' 자동 완성기.
    /mode <mode> [-s <session>] 문법까지 지원합니다.
    """
    def __init__(self, command_completer: Completer, file_completer: Completer):
        self.command_completer = command_completer
        self.file_completer = file_completer
        self.attached_completer: Optional[Completer] = None

        self.modes_with_meta = [
            Completion("dev", display_meta="개발/기술 지원 전문가"),
            Completion("general", display_meta="친절하고 박식한 어시스턴트"),
            Completion("teacher", display_meta="코드 구조 분석 아키텍트"),
        ]
        self.mode_completer = WordCompleter(
            words=[c.text for c in self.modes_with_meta], 
            ignore_case=True,
            meta_dict={c.text: c.display_meta for c in self.modes_with_meta}
        )
        self.session_option_completer = WordCompleter(["-s", "--session"], ignore_case=True)
    
    def update_attached_file_completer(self, attached_filenames: List[str]):
        if attached_filenames:
            self.attached_completer =  FuzzyCompleter(WordCompleter(attached_filenames, ignore_case=True))
        else:
            self.attached_completer = None

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped_text = text.lstrip()
        

        # mode 선택
        if stripped_text.startswith('/mode'):
            words = stripped_text.split()

            # "/mode"만 있거나, "/mode d" 처럼 두 번째 단어 입력 중일 때
            if len(words) < 2 or (len(words) == 2 and words[1] == document.get_word_before_cursor(WORD=True)):
                yield from self.mode_completer.get_completions(document, complete_event)
                return

            # "/mode dev"가 입력되었고, 세 번째 단어("-s")를 입력할 차례일 때
            # IndexError 방지: len(words) >= 2 인 것이 확실한 상황
            if len(words) == 2 and words[1] in ["dev", "general", "teacher"] and text.endswith(" "):
                yield from self.session_option_completer.get_completions(document, complete_event)
                return

            # "/mode dev -s"가 입력되었고, 네 번째 단어(세션 이름)를 입력할 차례일 때
            # IndexError 방지: len(words) >= 3 인 것이 확실한 상황
            if len(words) >= 3 and words[2] in ["-s", "--session"]:
                session_names = get_session_names()
                session_completer = FuzzyCompleter(WordCompleter(session_names, ignore_case=True))
                yield from session_completer.get_completions(document, complete_event)
                return
            
            # 위의 어떤 경우에도 해당하지 않으면, 기본적으로 모드 완성기를 보여줌
            yield from self.mode_completer.get_completions(document, complete_event)
            return

        # 경우 1: 경로 완성이 필요한 경우
        if stripped_text.startswith('/files '):
            yield from self.file_completer.get_completions(document, complete_event)

        # 경우 2: 명령어 완성이 필요한 경우
        elif stripped_text.startswith('/') and ' ' not in stripped_text:
            yield from self.command_completer.get_completions(document, complete_event)

        # 경우 3: 그 외 (일반 질문 시 '첨부 파일 이름' 완성 시도)
        else:
            word = document.get_word_before_cursor(WORD=True)
            if word and self.attached_completer:
                yield from self.attached_completer.get_completions(document, complete_event)
            else:
                yield from []

def select_model(current: str) -> str:
    if not MODELS_FILE.exists():
        console.print(f"[yellow]{MODELS_FILE} 가 없습니다.[/yellow]")
        return current
    models = [
        line.strip() for line in MODELS_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not models:
        return current

    # ▼▼▼ 개선점 1: TUI 상단에 현재 모델 정보 표시 ▼▼▼
    # 'info' 팔레트 스타일을 사용하여 눈에 잘 띄게 합니다.
    header_text = urwid.Text([
        "모델 선택 (Enter로 선택, Q로 취소)\n",
        ("info", f"현재 모델: {current.split('/')[-1]}")
    ])
    items = [header_text, urwid.Divider()]
    
    body: List[urwid.Widget] = []
    result: List[Optional[str]] = [None]
    
    def raise_exit(val: Optional[str]) -> None:
        result[0] = val
        raise urwid.ExitMainLoop()

    for m in models:
        disp = m.split("/")[-1]
        
        # ▼▼▼ 개선점 2: 현재 모델에 시각적 표시 추가 ▼▼▼
        if m == current:
            # 현재 선택된 모델은 앞에 화살표를 붙이고 (현재) 텍스트를 추가합니다.
            label = f"-> {disp} (현재)"
            # AttrMap을 사용해 다른 색상(예: 'key')으로 강조할 수도 있습니다.
            # 예: body.append(urwid.AttrMap(btn, 'key', focus_map='myfocus'))
        else:
            label = f"   {disp}" # 정렬을 위한 공백 추가

        btn = urwid.Button(label)
        urwid.connect_signal(btn, "click", lambda button, model=m: raise_exit(model))
        body.append(urwid.AttrMap(btn, None, focus_map="myfocus"))

    listbox = urwid.ListBox(urwid.SimpleFocusListWalker(items + body))
    
    def unhandled(key: str) -> None:
        if key in ("q", "Q"):
            raise_exit(None)
            
    urwid.MainLoop(listbox, palette=PALETTE, unhandled_input=unhandled).run()
    
    return result[0] or current

# ────────────────────────────────
# 유틸 함수
# ────────────────────────────────
def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# 세션/즐겨찾기
def load_session(name: str) -> Dict[str, Any]:
    data = load_json(SESSION_FILE(name), {"messages": [], "model": "openai/gpt-4o"})
    if isinstance(data, list):  # legacy
        data = {"messages": data, "model": "openai/gpt-4o"}
    return data


def save_session(name: str, msgs: List[Dict[str, Any]], model: str) -> None:
    save_json(SESSION_FILE(name), {"messages": msgs, "model": model})


def load_favorites() -> Dict[str, str]:
    return load_json(FAVORITES_FILE, {})


def save_favorite(name: str, prompt: str) -> None:
    favs = load_favorites()
    favs[name] = prompt
    save_json(FAVORITES_FILE, favs)


# .gptignore
def ignore_spec() -> Optional[PathSpec]:
    return (
        PathSpec.from_lines("gitwildmatch", IGNORE_FILE.read_text().splitlines())
        if IGNORE_FILE.exists()
        else None
    )


def is_ignored(p: Path, spec: Optional[PathSpec]) -> bool:
    return spec.match_file(p.relative_to(BASE_DIR).as_posix()) if spec else False


# 파일 처리
PLAIN_EXTS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".cpp",
    ".json",
    ".yml",
    ".yaml",
    ".html",
    ".css",
    ".scss",
    ".rs",
    ".go",
    ".php",
    ".rb",
    ".sh",
    ".sql",
}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PDF_EXT = ".pdf"


def read_plain_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"[파일 읽기 실패: {e}]"


def encode_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")

def prepare_content_part(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() in IMG_EXTS:
        data_url = f"data:{mimetypes.guess_type(path)[0]};base64,{encode_base64(path)}"
        return {"type": "image_url", "image_url": {"url": data_url}}
    if path.suffix.lower() == PDF_EXT:
        data_url = f"data:application/pdf;base64,{encode_base64(path)}"
        return {
            "type": "file",
            "file": {"filename": path.name, "file_data": data_url},
        }
    # plain text
    text = read_plain_file(path)
    safe_text = text # mask_sensitive(text)
    return {
        "type": "text",
        "text": f"\n\n[파일: {path}]\n```\n{safe_text}\n```",
    }

SENSITIVE_KEYS = ["secret", "private", "key", "api"]
PALETTE = [                               
            ('key', 'yellow', 'black'),
            ('info', 'dark gray', 'black'),                                                       
            ('myfocus', 'black', 'light gray'), # 커스텀 포커스 색                                       
        ]

def mask_sensitive(text: str) -> str:
    for key in SENSITIVE_KEYS:
        pattern = rf"({re.escape(key)}\s*=\s*)(['\"]?).*?\2"
        text = re.sub(pattern, r"\1[REDACTED]", text, flags=re.I)
    return text

def _parse_backticks(line: str) -> Optional[tuple[int, str]]:
    """
    주어진 라인이 코드 블록 구분자인지 확인하고, 백틱 개수와 언어 태그를 반환합니다.
    """
    stripped_line = line.strip()
    if not stripped_line.startswith('`'):
        return None

    count = 0
    for char in stripped_line:
        if char == '`':
            count += 1
        else:
            break

    # 최소 3개 이상이어야 유효한 구분자로 간주
    if count < 3:
        return None

    # 구분자 뒤에 다른 문자가 있다면 백틱이 아니므로 유효하지 않음
    if len(stripped_line) > count and stripped_line[count] == '`':
        return None

    language = stripped_line[count:].strip()
    return count, language



# ──────────────────────────────────────────────────────
# 5. 코드 블록 추출 / 저장
# ──────────────────────────────────────────────────────
def extract_code_blocks(markdown: str) -> List[Tuple[str, str]]:
    """
    State-machine 기반으로 마크다운에서 코드 블록을 추출합니다.
    ask_stream의 실시간 파싱 로직과 동일한 원리로, 정규식보다 안정적입니다.
    """
    blocks = []
    lines = markdown.split('\n')
    
    in_code_block = False
    outer_delimiter_len = 0
    nesting_depth = 0
    code_buffer: List[str] = []
    language = ""
    
    for line in lines:
        delimiter_info = _parse_backticks(line)

        # 코드 블록 시작 
        if not in_code_block:
            if delimiter_info:
                in_code_block = True
                outer_delimiter_len, language = delimiter_info
                nesting_depth = 0
                code_buffer = []
            
        # 코드 블록 종료 
        else:
            is_matching_delimiter = delimiter_info and delimiter_info[0] == outer_delimiter_len

            if is_matching_delimiter:
                # 같은 길이의 백틱 구분자. 중첩 여부 판단.
                if delimiter_info[1]: # 언어 태그가 있으면 중첩 시작
                    nesting_depth += 1
                else: # 언어 태그가 없으면 중첩 종료
                    nesting_depth -= 1

            if nesting_depth < 0:
                # 최종 블록 종료
                blocks.append((language, "\n".join(code_buffer)))
                in_code_block = False
            else:
                code_buffer.append(line)

    # 파일 끝까지 코드 블록이 닫히지 않은 엣지 케이스 처리
    if in_code_block and code_buffer:
        blocks.append((language, "\n".join(code_buffer)))
        
    return blocks

def save_code_blocks(blocks: Sequence[Tuple[str, str]]) -> List[Path]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    saved: List[Path] = []
    
    # 다양한 언어 확장자를 지원하도록 대폭 확장된 매핑
    ext_map = {
        # 스크립팅 & 프로그래밍 언어
        "python": "py", "py": "py",
        "javascript": "js", "js": "js",
        "typescript": "ts", "ts": "ts",
        "bash": "sh", "sh": "sh", "shell": "sh",
        "java": "java",
        "c": "c",
        "cpp": "cpp", "c++": "cpp",
        "go": "go",
        "rust": "rs", "rs": "rs",
        "ruby": "rb", "rb": "rb",
        "php": "php",
        "sql": "sql",
        
        # 마크업 & 데이터 형식
        "html": "html",
        "css": "css",
        "scss": "scss",
        "json": "json",
        "xml": "xml",
        "yaml": "yml", "yml": "yml",
        "markdown": "md", "md": "md",
        
        # 기타
        "text": "txt", "plaintext": "txt",
        "diff": "diff",
    }

    for i, (lang, code) in enumerate(blocks, 1):
        # 언어 태그를 소문자로 변환하여 확장자 찾기 (없으면 'txt'가 기본값)
        lang_key = lang.lower() if lang else "text"
        ext = ext_map.get(lang_key, "txt")
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        p = OUTPUT_DIR / f"gpt_output_{timestamp}_{i}.{ext}"
        cnt = 1
        while p.exists():
            p = OUTPUT_DIR / f"gpt_output_{timestamp}_{i}_{cnt}.{ext}"
            cnt += 1
        
        p.write_text(code, encoding="utf-8")
        saved.append(p)
    return saved


# ──────────────────────────────────────────────────────
# 6. UI 보조 (로딩 / diff)
# ──────────────────────────────────────────────────────
def spinner() -> None:
    for ch in itertools.cycle("|/-\\"):
        if stop_loading.is_set():
            break
        console.print(f"[cyan]Thinking {ch}", end="\r", highlight=False)
        time.sleep(0.1)


def render_diff(a: str, b: str, lang: str = "text") -> None:
    diff = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm=""))
    if not diff:
        console.print("[green]차이 없음[/green]")
        return
    for line in diff:
        if line.startswith(("-", "+")):
            color = "#330000" if line.startswith("-") else "#003300"
            console.print(Syntax(line, lang, background_color=color))
        elif line.startswith("@@"):
            console.print(line, style="cyan")
        else:
            console.print(line)


# ──────────────────────────────────────────────────────

class FileSelector:
    def __init__(self) -> None:
        self.spec = ignore_spec()
        self.items: List[Tuple[Path, bool]] = []  # (path, is_dir)
        self.selected: set[Path] = set()
        self.expanded: set[Path] = set()

    def refresh(self) -> None:
        self.items.clear()
        def visit_dir(path: Path, depth: int):
            # path는 항상 절대경로
            path = path.resolve()
            if is_ignored(path, self.spec):
                return
            self.items.append((path, True))
            # expanded 집합도 절대경로 기준
            if path in self.expanded:
                try:
                    # 하위 디렉터리 우선
                    for d in sorted([p for p in path.iterdir() if p.is_dir()]):
                        visit_dir(d, depth+1)
                    for f in sorted([p for p in path.iterdir() if p.is_file()]):
                        if is_ignored(f, self.spec): continue
                        if f.suffix.lower() in (*PLAIN_EXTS, *IMG_EXTS, PDF_EXT):
                            self.items.append((f.resolve(), False))
                except Exception:
                    pass
        visit_dir(BASE_DIR.resolve(), 0)
    
    def get_all_files_in_dir(self, folder: Path) -> set[Path]:                                       
        # 실제 폴더 구조에서 무시규칙(is_ignored)까지 적용해서 모든 하위 파일을 반환                 
        result = set()                                                                               
        try:                                                                                         
            for entry in folder.iterdir():                                                           
                if entry.is_dir():                                                                   
                    result |= self.get_all_files_in_dir(entry)                                       
                elif entry.is_file():                                                                
                    if is_ignored(entry, self.spec):                                                 
                        continue                                                                     
                    if entry.suffix.lower() in (*PLAIN_EXTS, *IMG_EXTS, PDF_EXT):                    
                        result.add(entry.resolve())                                                  
        except Exception:                                                                            
            pass                                                                                     
        return result
    
    def folder_all_selected(self, folder: Path) -> bool:                                             
        # 해당 폴더 하위 모든 허용파일이 self.selected에 다 들어있는지                               
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files) and all_files.issubset(self.selected)                                 
                                                                                                    
    def folder_partial_selected(self, folder: Path) -> bool:                                         
        # 일부만 선택된 경우 체크(부분선택)                                                          
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files & self.selected) and not all_files.issubset(self.selected)             
                                                                                                        
    # TUI
    def start(self) -> List[str]:
        self.refresh()

        def mkwidget(data: Tuple[Path, bool]) -> urwid.Widget:                                           
            path, is_dir = data                                                                          
            depth = len(path.relative_to(BASE_DIR).parts) - (0 if is_dir else 1)                         
            indent = "  " * depth                                                                        
                                                                                                        
            # 선택 상태 결정: 부분선택(폴더) 고려                                                        
            if is_dir:                                                                                   
                if self.folder_all_selected(path):                                                       
                    checked = "✔"                                                                        
                elif self.folder_partial_selected(path):                                                 
                    checked = "−"  # 또는 "*" 등                                                         
                else:                                                                                    
                    checked = " "                                                                        
                arrow = "▼" if path in self.expanded else "▶"                                            
                label = f"{indent}{arrow} [{checked}] {path.name}/"                                      
            else:                                                                                        
                checked = "✔" if path in self.selected else " "                                          
                label = f"{indent}  [{checked}] {path.name}"                                             
            return urwid.AttrMap(urwid.SelectableIcon(label, 0), None, focus_map='myfocus') 

        walker = urwid.SimpleFocusListWalker([mkwidget(i) for i in self.items])
        
        def refresh_list() -> None:
            walker[:] = [mkwidget(i) for i in self.items]

        def keypress(key: str) -> None:
            if isinstance(key, tuple) and len(key) >= 4:
                event_type, button, col, row = key[:4]
                if event_type == 'mouse press':
                    if button == 4:  # 마우스 휠 업
                        # 위로 스크롤 (ListBox focus 이동)
                        if listbox.focus_position > 0:
                            listbox.focus_position -= 1
                        return
                    elif button == 5:  # 마우스 휠 다운
                        # 아래로 스크롤
                        if listbox.focus_position < len(self.items) - 1:
                            listbox.focus_position += 1
                        return
                return
            
            idx = listbox.focus_position
            if key == " ":
                tgt, is_dir = self.items[idx]
                tgt = tgt.resolve()
                if is_dir:
                    files_in_dir = self.get_all_files_in_dir(tgt)
                    if files_in_dir.issubset(self.selected):                                                 
                        # 이미 전체 선택되어 있었으니 전체 해제                                              
                        self.selected -= files_in_dir                                                        
                        self.selected.discard(tgt)                                                           
                    else:                                                                                    
                        # 전체 선택 아님, 모두 추가                                                          
                        self.selected |= files_in_dir                                                        
                        self.selected.add(tgt)
                else:
                    self.selected.symmetric_difference_update({tgt})
                refresh_list()
            elif key == "enter":
                tgt, is_dir = self.items[idx]
                tgt = tgt.resolve()
                if is_dir:
                    if tgt in self.expanded:
                        self.expanded.remove(tgt)
                    else:
                        self.expanded.add(tgt)
                    self.refresh()
                    refresh_list()
            elif key.lower() == "a":
                # 전체 트리에서 모든 파일(노출 여부와 관계 없이!)을 재귀 선택
                all_files = set()
                def walk_folder(folder):
                    for entry in folder.iterdir():
                        if entry.is_dir():
                            walk_folder(entry)
                        elif entry.is_file() and not is_ignored(entry, self.spec):
                            if entry.suffix.lower() in (*PLAIN_EXTS, *IMG_EXTS, PDF_EXT):
                                all_files.add(entry.resolve())
                walk_folder(BASE_DIR)
                self.selected = all_files
                refresh_list()
            elif key.lower() == "n":
                self.selected.clear()
                refresh_list()
            elif key.lower() == "s":
                raise urwid.ExitMainLoop()
            elif key.lower() == "q":
                self.selected.clear()
                raise urwid.ExitMainLoop()

        listbox = urwid.ListBox(walker)
        help_text = urwid.Text([
            "명령어: ",
            ("key", "Space"), ":선택  ",
            ("key", "Enter"), ":펼침  ", 
            ("key", "A"), ":전체선택  ",
            ("key", "N"), ":해제  ",
            ("key", "S"), ":완료  ",
            ("key", "Q"), ":취소\n",
            ("info", f"현재 위치: {BASE_DIR}")
        ])
        
        header = urwid.Pile([
            help_text,
            urwid.Divider(),
        ])

        frame = urwid.Frame(listbox, header=header)
        
        urwid.MainLoop(                                                                                  
            frame,                                                                                       
            palette=PALETTE, # PALETTE는 전역으로 정의되었거나, 해당 함수 내에서 정의된 팔레트           
            unhandled_input=keypress,                                                                    
            #event_loop=urwid.SelectEventLoop(), # 이 줄 추가
        ).run() 
        return [str(p) for p in sorted(self.selected) if p.is_file()]

    
# ──────────────────────────────────────────────────────
# 8. OpenRouter 호출 (스트리밍)
# ──────────────────────────────────────────────────────
def ask_stream(
    messages: List[Dict[str, Any]],
    model: str,
    mode: str,
    pretty_print: bool = True
) -> Optional[str]:
    console.print(Syntax(" ", "python", theme="monokai", background_color="#008C45"))
    console.print(Syntax(" ", "python", theme="monokai", background_color="#F4F5F0"))
    console.print(Syntax(" ", "python", theme="monokai", background_color="#CD212A"))

    # ... ask_stream 함수 내부 ...

    # 시스템 프롬프트(페르소나)를 더욱 구체적이고 명확하게 수정
    if mode == "dev":
        prompt_content = """
            당신은 터미널(CLI) 환경에 특화된, 세계 최고 수준의 AI 프로그래밍 전문가입니다.

            **[핵심 임무]**
            사용자에게 명확하고, 정확하며, 전문가 수준의 기술 지원을 제공합니다.

            **[응답 지침]**
            1.  **언어:** 항상 한국어로 답해야 합니다.
            2.  **형식:** 모든 응답은 마크다운(Markdown)으로 체계적으로 정리해야 합니다. 특히, 모든 코드, 파일 경로, 쉘 명령어는 반드시 ` ```언어` 형식의 코드 블록으로 감싸야 합니다. 이것은 매우 중요합니다.
            3.  **구조:** 답변은 '핵심 요약' -> '코드 블록' -> '상세 설명' 순서로 구성하는 것을 원칙으로 합니다.
            4.  **컨텍스트:** 사용자는 `[파일: 파일명]\n\`\`\`...\`\`\`` 형식으로 코드를 첨부할 수 있습니다. 이 컨텍스트를 이해하고 답변에 활용하세요.

            당신의 답변은 간결하면서도 사용자의 질문에 대한 핵심을 관통해야 합니다.
        """
    elif mode == "teacher": # "teacher" 모드를 위한 새로운 분기
        prompt_content = """
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
        """
    elif mode == "general":  # general 모드
        prompt_content = """
            당신은 매우 친절하고 박식한 AI 어시스턴트입니다.

            **[핵심 임무]**
            사용자의 다양한 질문에 대해 명확하고, 도움이 되며, 이해하기 쉬운 답변을 제공합니다.

            **[응답 지침]**
            1.  **언어:** 항상 한국어로 답해야 합니다.
            2.  **가독성:** 터미널 환경에서 읽기 쉽도록, 마크다운 문법(예: 글머리 기호 `-`, 굵은 글씨 `**...**`)을 적극적으로 사용하여 답변을 구조화하세요.
            3.  **태도:** 항상 친절하고, 인내심 있으며, 상세한 설명을 제공하는 것을 목표로 합니다.

            당신은 사용자의 든든한 동반자입니다.
        """

    system_prompt = {
        "role": "system",
        "content": prompt_content.strip(),
    }
    def simple_markdown_to_rich(text: str) -> str:
        """
        Placeholder 기법을 '올바른 순서'로 사용하여 모든 충돌을 해결한,
        극도로 안정적인 최종 마크다운 렌더러.
        """
        placeholders: Dict[str, str] = {}
        placeholder_id_counter = 0

        def generate_placeholder(rich_tag_content: str) -> str:
            nonlocal placeholder_id_counter
            key = f"__GPCLI_PLACEHOLDER_{placeholder_id_counter}__"
            placeholders[key] = rich_tag_content
            placeholder_id_counter += 1
            return key

        # --- 1단계: 모든 마크업을 Placeholder로 변환 ---
        # 우선순위가 가장 높은 것부터 처리합니다. 인라인 코드가 가장 강력합니다.
        
        # 1-1. 인라인 코드(`...`) -> Placeholder
        def inline_code_replacer(match: re.Match) -> str:
            content = match.group(1)
            if not content.strip():
                return f"`{content}`"  # 빈 내용은 그대로 둠
            stripped_content = content.strip() 
            escaped_content = stripped_content.replace('[', r'\[')
            #rich_tag = f"[#F8F8F2 on #3C3C3C] {escaped_content} [/]"
            rich_tag = f"[bold white on #484f58] {escaped_content} [/]"
            return generate_placeholder(rich_tag)

        processed_text = re.sub(r"`([^`]+)`", inline_code_replacer, text)

        # 1-2. 굵은 글씨(**...**) -> Placeholder
        def bold_replacer(match: re.Match) -> str:
            content = match.group(1)
            return generate_placeholder(f"[bold]{content}[/bold]")

        processed_text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", bold_replacer, processed_text, flags=re.DOTALL)
        
        # --- 2단계: 안전하게 텍스트-레벨 마크업 처리 ---
        # 이제 모든 rich 태그가 숨겨졌으므로, 남아있는 텍스트를 안전하게 처리합니다.
        
        # 2-1. [ 문자 이스케이프: 이제 간단한 replace로 안전하게 처리 가능
        processed_text = processed_text.replace('[', r'\[')
        
        # 2-2. 리스트 마커 변환
        processed_text = re.sub(r"^(\s*)(\d+)\. ", r"\1[yellow]\2.[/yellow] ", processed_text, flags=re.MULTILINE)
        processed_text = re.sub(r"^(\s*)[\-\*] ", r"\1[bold blue]•[/bold blue] ", processed_text, flags=re.MULTILINE)

        # --- 3단계: Placeholder를 **역순으로** 복원 ---
        # 마지막에 생성된 placeholder(가장 바깥쪽)부터 복원해야 중첩이 올바르게 풀립니다. 이것이 핵심입니다.
        for key in reversed(list(placeholders.keys())):
            processed_text = processed_text.replace(key, placeholders[key])
            
        return processed_text

    model_online = model if model.endswith(":online") else f"{model}:online"
    
    # reasoning 지원 모델 감지 및 extra_body 설정
    use_reasoning = True #any(x in model.lower() for x in ['o1-', 'reasoning'])
    extra_body = {'reasoning': {}} if use_reasoning else {}

    with console.status("[cyan]Loading...", spinner="dots"):
        try:
            stream = client.chat.completions.create(
                model=model_online,
                messages=[system_prompt] + messages[-TRIMMED_HISTORY:],
                stream=True,
                extra_body=extra_body,
            )
        except OpenAIError as e:
            console.print(f"[red]API 오류: {e}[/red]")
            return None

    if not pretty_print:
        full_reply = ""
        console.print(f"[bold]{model}:[/bold]")
        try:
            for chunk in stream:
                if chunk.choices[0].delta and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    # 서식 없이 그대로 출력
                    console.print(content, end="", markup=False)
        except StopIteration:
            pass
        finally:
            console.print()  # 마지막 줄바꿈
        return full_reply

    # 상태 머신 변수 초기화
    full_reply = ""
    in_code_block = False
    buffer = ""
    code_buffer, language = "", "text"
    normal_buffer, last_flush_time = "", time.time()
    reasoning_buffer = ""
    
    outer_delimiter_len = 0
    nesting_depth = 0

    console.print(f"[bold]{model}:[/bold]")
    stream_iter = iter(stream)

    try:
        while True:
            chunk = next(stream_iter)
            delta = chunk.choices[0].delta

            if hasattr(delta, 'reasoning') and delta.reasoning:
                if normal_buffer: console.print(normal_buffer, end="", markup=False); normal_buffer = ""
                
                with Live(console=console, auto_refresh=True, refresh_per_second=4, transient=True) as live:
                    reasoning_buffer = delta.reasoning
                    while True:
                        try:
                            lines, total_lines = reasoning_buffer.splitlines(), len(reasoning_buffer.splitlines())
                            display_text = "\n".join(f"[italic]{l}[/italic]" for l in lines[-8:])
                            if total_lines > 8:
                                display_text = f"[dim]... ({total_lines - 8}줄 생략) ...[/dim]\n{display_text}"
                            
                            panel = Panel(display_text, height=10, title=f"[magenta]🤔 추론 과정 ({total_lines}줄)[/magenta]", border_style="magenta")
                            live.update(panel)

                            chunk = next(stream_iter)
                            delta = chunk.choices[0].delta
                            if hasattr(delta, 'reasoning') and delta.reasoning:
                                reasoning_buffer += delta.reasoning
                            elif delta.content:
                                buffer += delta.content; break
                        except StopIteration:
                            break
                continue

            if not (delta and delta.content): 
                continue
            
            full_reply += delta.content
            buffer += delta.content
            #full_reply = simple_markdown_to_rich(full_reply)

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                delimiter_info = _parse_backticks(line)

                if not in_code_block:
                    if delimiter_info:
                        if normal_buffer: 
                            console.print(simple_markdown_to_rich(normal_buffer), end="", markup=True, highlight = False)
                            normal_buffer = ""
                        
                        in_code_block = True
                        outer_delimiter_len, language = delimiter_info
                        nesting_depth = 0
                        code_buffer = ""
                        
                        live = Live(console=console, auto_refresh=True, refresh_per_second=5)
                        with live:
                            while in_code_block:
                                lines, total_lines = code_buffer.splitlines(), len(code_buffer.splitlines())
                                panel_height, display_height = 12, 10
                                
                                display_text = "\n".join(f"[cyan]{l}[/cyan]" for l in lines[-display_height:])
                                if total_lines > display_height:
                                    display_text = f"[dim]... ({total_lines - display_height}줄 생략) ...[/dim]\n{display_text}"
                                
                                temp_panel = Panel(display_text, height=panel_height, title=f"[yellow]코드 입력중 ({language}) {total_lines}줄[/yellow]", border_style="dim", highlight=False)
                                live.update(temp_panel)
                                
                                try:
                                    chunk = next(stream_iter)
                                    if chunk.choices[0].delta and chunk.choices[0].delta.content:
                                        full_reply += chunk.choices[0].delta.content
                                        buffer += chunk.choices[0].delta.content
                                        
                                        while "\n" in buffer:
                                            sub_line, buffer = buffer.split("\n", 1)
                                            sub_delimiter_info = _parse_backticks(sub_line)
                                            is_matching = sub_delimiter_info and sub_delimiter_info[0] == outer_delimiter_len

                                            if is_matching:
                                                if sub_delimiter_info[1]:
                                                    nesting_depth += 1
                                                else:
                                                    nesting_depth -= 1

                                            if nesting_depth < 0:
                                                in_code_block = False
                                                break
                                            else:
                                                code_buffer += sub_line +"\n"

                                        
                                        if not in_code_block: 
                                            break

                                except StopIteration:
                                    in_code_block = False
                                    break
                            
                            if code_buffer.rstrip():
                                if language == 'markdown':
                                    syntax_block = Markdown(code_buffer.rstrip())
                                else:
                                    syntax_block = Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True, word_wrap=True)
                                final_panel = Panel.fit(syntax_block, title=f"[green]코드 ({language})[/green]", border_style="green")
                                live.update(final_panel)
                            else:
                                live.update("")
                            live.stop()
                            
                    else:
                        normal_buffer += line + "\n"

            # 백틱 3개이상 코드 구분을 캐치 못할것을 대비하여 백틱 하나로 끝나면 일단 대기
            if not in_code_block and buffer:
                if buffer.endswith('`'):
                    pass # 아무것도 안하고 다음 청크를 기다림
                else:
                    normal_buffer += buffer
                    buffer = ""

            current_time = time.time()
            if normal_buffer and (len(normal_buffer) > 20 or (current_time - last_flush_time > 0.25)):
                if '\n' in normal_buffer:
                    parts = normal_buffer.rsplit('\n',1)
                    text_to_flush = parts[0] + '\n'
                    normal_buffer = parts[1]
                    try:
                        display_text = simple_markdown_to_rich(text_to_flush)
                        rich_text = Text.from_markup(display_text, end="")
                        rich_text.no_wrap = True
                        console.print(rich_text, highlight=False)
                        #console.print(display_text, end="", markup=True, highlight=False)
                    except Exception as e:
                        # 그냥 있는 그대로 출력하면 문제없이 진행됨
                        # ▼▼▼ [최종 수정 1] ▼▼▼
                        # 1. RAW 텍스트로 오류 메시지를 출력합니다.
                        #console.print(f"\n--- 렌더링 오류 발생 ---", style="bold red")
                        #console.print(f"오류: {e}", markup=False, highlight=False)
                        
                        # 2. Panel을 제거하고, 오류 원본 텍스트를 markup/highlight 없이 순수하게 출력합니다.
                        # 이것이 재귀적 렌더링 오류를 막는 가장 안전한 방법입니다.
                        #console.print("--- 오류 원본 텍스트 ---", style="bold cyan")
                        console.print(text_to_flush, markup=False, highlight=False)
                        #console.print("--- 오류 원본 끝 ---", style="bold cyan")
                        # ▲▲▲ 최종 수정 완료 ▲▲▲

                    last_flush_time = current_time
                #display_text = simple_markdown_to_rich(normal_buffer)
                #console.print(display_text, end="", markup=True, highlight=False)
                #console.print(display_text, end="", markup=False, highlight=False)
                #normal_buffer = ""; last_flush_time = current_time
        
    except StopIteration:
        if normal_buffer:
            try:
                display_text = simple_markdown_to_rich(normal_buffer)
                rich_text = Text.from_markup(display_text, end="")
                rich_text.no_wrap = True
                console.print(rich_text, highlight=False)
                #console.print(display_text, end="", markup=True, highlight=False)
            except Exception as e:
                # 그냥 있는 그대로 출력해버려서 bypass
                # ▼▼▼ [최종 수정 2] ▼▼▼
                #console.print(f"\n--- 최종 렌더링 오류 발생 ---", style="bold red")
                #console.print(f"오류: {e}", markup=False, highlight=False)
                #console.print("--- 오류 원본 텍스트 ---", style="bold cyan")
                console.print(normal_buffer, markup=False, highlight=False)
                #console.print("--- 오류 원본 끝 ---", style="bold cyan")
                # ▲▲▲ 최종 수정 완료 ▲▲▲

    if in_code_block and code_buffer:
        console.print("\n[yellow]경고: 코드 블록이 제대로 닫히지 않았습니다.[/yellow]")
        console.print(Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True))

    console.print()
    return full_reply


# ──────────────────────────────────────────────────────
# 9. 멀티라인 Prompt 세션
# ──────────────────────────────────────────────────────
prompt_session = PromptSession(
    history=FileHistory(PROMPT_HISTORY_FILE),
    auto_suggest=AutoSuggestFromHistory(),
    multiline=True,
    prompt_continuation="          ",
)


# ──────────────────────────────────────────────────────
# 10. 메인 대화 루프
# ──────────────────────────────────────────────────────
COMMANDS = """
/commands            → 명령어 리스트
/pretty_print        → 고급 출력(Rich) ON/OFF 토글
/raw                 → 마지막 응답 raw 출력
/select_model        → 모델 선택 TUI
/all_files           → 파일 선택기(TUI)
/files f1 f2 ...     → 수동 파일 지정
/clearfiles          → 첨부파일 초기화
/mode <dev|general>  → 시스템 프롬프트 모드
/savefav <name>      → 질문 즐겨찾기
/usefav <name>       → 즐겨찾기 사용
/favs                → 즐겨찾기 목록
/diffme              → 선택파일 vs GPT 코드 비교
/diffcode            → 이전↔현재 GPT 코드 비교
/reset               → 세션 리셋
/exit                → 종료
""".strip()


def chat_mode(name: str, copy_clip: bool) -> None:
    # 1. 초기 모드는 항상 'dev'로 고정
    mode = "dev"
    current_session_name = name
    
    data = load_session(current_session_name)
    messages: List[Dict[str, Any]] = data["messages"]
    model = data["model"]
    
    attached: List[str] = []
    last_resp = ""
    pretty_print_enabled = True 

    # 1. 기본 명령어 자동 완성기 생성
    command_list = [cmd.split()[0] for cmd in COMMANDS.strip().split('\n')]
    command_completer = FuzzyCompleter(WordCompleter(command_list, ignore_case=True))

    # 1-2. .gptignore를 존중하는 파일 목록 생성 -> 파일 완성기
    spec = ignore_spec()
    try:
        file_list = [p.name for p in BASE_DIR.iterdir() if not is_ignored(p, spec)]
    except Exception:
        file_list = []
    # pathcompleter는 동작안해서 Fuzzycompleter를 쓰지만, 하위 폴더내용물을 접근못함
    file_completer = FuzzyCompleter(WordCompleter(file_list, ignore_case=True))

    # ConditionalCompleter 생성 (초기에는 첨부 파일 완성기가 비어있음)
    conditional_completer = ConditionalCompleter(
        command_completer=command_completer,
        file_completer=file_completer
    )

    # 키 바인딩 준비
    key_bindings = KeyBindings()
    session = PromptSession() # session 객체를 먼저 생성해야 filter에서 참조 가능

    @key_bindings.add("enter", filter=Condition(lambda: session.default_buffer.complete_state is not None))
    def _(event):
        complete_state = event.current_buffer.complete_state
        if complete_state:
            if complete_state.current_completion:
                event.current_buffer.apply_completion(complete_state.current_completion)
            elif complete_state.completions:
                event.current_buffer.apply_completion(complete_state.completions[0])

    # 최종 PromptSession 설정
    session.history = FileHistory(PROMPT_HISTORY_FILE)
    session.auto_suggest = AutoSuggestFromHistory()
    session.multiline = True
    session.prompt_continuation = "          "
    session.completer = conditional_completer
    session.key_bindings = key_bindings
    session.complete_while_typing = True

    console.print(Panel.fit(COMMANDS, title="[yellow]/명령어[/yellow]"))
    console.print(f"[cyan]세션('{current_session_name}') 시작 – 모델: {model}[/cyan]", highlight=False)

    while True:
        try:
            # ✅ 루프 시작 시, 최신 'attached' 목록으로 completer를 업데이트!
            attached_filenames = [Path(p).name for p in attached]
            conditional_completer.update_attached_file_completer(attached_filenames)
            prompt_text = f"[{current_session_name}|{mode}]> "
            user_in = session.prompt(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not user_in:
            continue

        # ── 명령어 처리
        if user_in.startswith("/"):
            cmd, *args = user_in.split()
            if cmd == "/exit":
                break
            if cmd == "/pretty_print":
                pretty_print_enabled = not pretty_print_enabled
                status_text = "[green]활성화[/green]" if pretty_print_enabled else "[yellow]비활성화[/yellow]"
                console.print(f"고급 출력(Rich) 모드가 {status_text} 되었습니다.")
                continue
            elif cmd == "/raw":
                last_assistant_message = None
                # 리스트를 뒤에서부터 순회하며 가장 최근의 'assistant' 메시지를 찾습니다.
                for message in reversed(messages):
                    if message.get("role") == "assistant":
                        last_assistant_message = message.get("content")
                        break  # 찾았으면 즉시 중단

                if last_assistant_message:
                    # 2. 찾은 내용을 'rich'의 자동 강조 없이 순수 텍스트로 출력합니다.
                    console.print(last_assistant_message, markup=False, highlight=False)
                else:
                    # 3. 세션에 'assistant' 메시지가 하나도 없는 경우
                    console.print("[yellow]표시할 이전 답변 기록이 없습니다.[/yellow]")
                continue # 명령어 처리 후 다음 프롬프트로 넘어감
            elif cmd == "/commands":
                console.print(Panel.fit(COMMANDS, title="[yellow]/명령어[/yellow]"))
            elif cmd == "/select_model":
                #console.print()
                old_model = model                                                                            
                model = select_model(model)
                if model != old_model:
                    save_session(name, messages, model)
                    console.print(f"[green]모델 변경: {old_model} → 현재: {model}[/green]")
                else:
                    console.print(f"[green]모델 변경없음: {model}[/green]")
                #console.print()
            elif cmd == "/all_files":
                selector = FileSelector()
                attached = selector.start()
                console.print(f"[yellow]파일 {len(attached)}개 선택됨: {','.join(attached)}[/yellow]")
            elif cmd == "/files":
                attached = sorted(list(set(args)))
                console.print(f"[yellow]파일 {len(attached)}개 선택됨: {','.join(attached)}[/yellow]")
            elif cmd == "/clearfiles":
                attached = []
            elif cmd == "/mode":
                
                parser = argparse.ArgumentParser(prog="/mode", description="모드와 세션을 변경합니다.")
                parser.add_argument("mode_name", choices=["dev", "general", "teacher"], help="변경할 모드 이름")
                parser.add_argument("-s", "--session", dest="session_name", default=None, help="사용할 세션 이름")

                try:
                    # argparse는 에러 시 sys.exit()를 호출하므로 try-except로 감싸야 앱이 종료되지 않음
                    parsed_args = parser.parse_args(args)
                except SystemExit:
                    # 잘못된 인자가 들어오면 도움말을 보여주고 다음 프롬프트로 넘어감
                    continue

                new_mode = parsed_args.mode_name
                
                # 1. 모드/세션 변경 전, 현재 대화 내용 저장
                save_session(current_session_name, messages, model)
                
                # 2. 새로운 세션 이름 결정 (옵션 vs 기본값)
                if parsed_args.session_name:
                    # 사용자가 -s 옵션으로 세션을 '명시적'으로 지정한 경우
                    new_session_name = parsed_args.session_name
                    console.print(f"[cyan]'{new_mode}' 모드를 세션 '{new_session_name}'(으)로 로드합니다.[/cyan]")
                else:
                    # -s 옵션이 없는 '기본' 전환 로직
                    if new_mode in ["dev", "teacher"]:
                        new_session_name = "default"
                    else: # general
                        new_session_name = "general"
                
                # 첨부파일 초기화
                if new_session_name != current_session_name or mode != new_mode:
                    if attached:
                        attached.clear()
                        console.print("[dim]첨부 파일 목록이 초기화되었습니다.[/dim]")
                
                # 3. 세션 데이터 교체 (필요 시)
                if new_session_name != current_session_name:
                    current_session_name = new_session_name
                    data = load_session(current_session_name)
                    messages = data["messages"]
                    if data["model"] != model:
                        model = data["model"]
                        console.print(f"[cyan]세션에 저장된 모델로 변경: {model}[/cyan]")
                
                # 4. 최종 모드 설정 및 상태 출력
                mode = new_mode
                console.print(f"[green]전환 완료. 현재 모드: [bold]{mode}[/bold], 세션: [bold]{current_session_name}[/bold][/green]")
                
            elif cmd == "/reset":
                #messages.clear()
                #console.print("[yellow]세션 초기화[/yellow]")
                # 1. 현재 세션 파일 경로를 가져옵니다.
                current_session_path = SESSION_DIR / f"session_{current_session_name}.json"

                if not current_session_path.exists():
                    console.print(f"[yellow]세션 '{current_session_name}'에 대한 저장된 파일이 없어 초기화할 내용이 없습니다.[/yellow]")
                    messages.clear() # 메모리만 초기화
                    continue

                # 2. 백업 파일 경로를 생성합니다 (타임스탬프 포함).
                backup_dir = SESSION_DIR / "backup"
                backup_dir.mkdir(exist_ok=True)

                timestamp = time.strftime("%Y%m%d_%H%M%S")

                backup_filename = f"session_{current_session_name}_{timestamp}.json"
                backup_session_path = backup_dir / backup_filename

                try:
                    shutil.move(str(current_session_path), str(backup_session_path))
                    messages.clear()
                    save_session(current_session_name, messages, model)

                    backup_display_path = backup_session_path.relative_to(BASE_DIR)
                    console.print(
                        Panel.fit(
                            f"세션 '{current_session_name}'이 초기화되었습니다.\n"
                            f"[dim]이전 데이터는 아래 경로에 백업되었습니다:[/dim]\n"
                            f"[green]{backup_display_path}[/green]",
                            title="[yellow]세션 초기화 및 백업 완료[/yellow]"
                        )
                    )
                except Exception as e:
                    console.print(f"[bold red]오류: 세션 초기화 및 백업에 실패했습니다.[/bold red]")
                    console.print(f"[dim]{e}[/dim]")

            elif cmd == "/savefav" and args:
                if messages and messages[-1]["role"] == "user":
                    content = messages[-1]["content"]
                    
                    # content가 리스트(멀티파트 메시지)인 경우, 텍스트 부분만 추출
                    if isinstance(content, list):
                        text_parts = [part["text"] for part in content if part.get("type") == "text"]
                        # 텍스트가 여러 개 있을 수 있으므로 공백으로 합침
                        content_to_save = " ".join(text_parts).strip()
                    else:
                        # 기존 로직 (content가 문자열인 경우)
                        content_to_save = content

                    if content_to_save:
                        save_favorite(args[0], content_to_save)
                        console.print(f"[green]'{args[0]}' 즐겨찾기 저장 완료: \"{content_to_save[:50]}...\"[/green]")
                    else:
                        console.print("[yellow]즐겨찾기에 저장할 텍스트 내용이 없습니다.[/yellow]")
                else:
                    console.print("[yellow]저장할 사용자 질문이 없습니다.[/yellow]")
            elif cmd == "/usefav" and args:
                fav = load_favorites().get(args[0])
                if fav:
                    user_in = fav
                else:
                    console.print("[red]즐겨찾기 없음[/red]")
                    continue
            elif cmd == "/favs":
                for k, v in load_favorites().items():
                    console.print(f"[cyan]{k}[/cyan]: {v[:80]}…")
            elif cmd == "/diffme":
                if not attached or not last_resp:
                    console.print("[yellow]비교 대상 없음[/yellow]")
                    continue
                for f in attached:
                    p = Path(f)
                    if p.suffix.lower() in PLAIN_EXTS:
                        original = read_plain_file(p)
                        for lang, code in extract_code_blocks(last_resp):
                            render_diff(original, code, lang or "text")
            elif cmd == "/diffcode":
                if len(messages) < 4:
                    console.print("[yellow]비교할 GPT 응답이 부족[/yellow]")
                    continue
                old = messages[-4]["content"]
                for (ln_old, code_old), (ln_new, code_new) in zip(
                    extract_code_blocks(old), extract_code_blocks(last_resp)
                ):
                    render_diff(code_old, code_new, ln_new or ln_old or "text")
            else:
                console.print("[yellow]알 수 없는 명령[/yellow]")
            continue  # 명령어 처리 끝

        # ── 파일 첨부 포함 user message 생성
        msg_obj: Dict[str, Any]
        if attached:
            parts = [{"type": "text", "text": user_in}]
            for f in attached:
                parts.append(prepare_content_part(Path(f)))
            msg_obj = {"role": "user", "content": parts}
        else:
            msg_obj = {"role": "user", "content": user_in}

        messages.append(msg_obj)

        # ── OpenRouter 호출
        reply = ask_stream(messages, model, mode, pretty_print=pretty_print_enabled)
        if reply is None:
            messages.pop()  # 실패 시 user message 제거
            continue

        messages.append({"role": "assistant", "content": reply})
        save_session(current_session_name, messages, model)
        last_resp = reply

        # ── 후처리
        code_blocks = extract_code_blocks(reply)
        if code_blocks:
            saved_files = save_code_blocks(code_blocks)
            if saved_files:
                saved_paths_text = Text("\n".join(
                    f"  • {p.relative_to(BASE_DIR)}" for p in saved_files                          
                ))                  
                console.print(Panel.fit(
                    saved_paths_text,
                    title="[green]💾 코드 블록 저장 완료[/green]",
                    border_style="dim",
                    title_align="left"
                ))
            
            #for lang, code in code_blocks:
            #    console.print(Syntax(code, lang or "text"))

        if copy_clip:
            try:
                pyperclip.copy(reply)
                console.print("[green]클립보드 복사[/green]")
            except pyperclip.PyperclipException:
                console.print("[yellow]클립보드 실패[/yellow]")

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        safe_session_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        md_filename = f"{safe_session_name}_{timestamp}_{len(messages)//2}.md"
        saved_path = MD_OUTPUT_DIR.joinpath(md_filename)
        try:
            saved_path.write_text(reply, encoding="utf-8")
            display_path_str = str(saved_path.relative_to(BASE_DIR))
            console.print(Panel.fit(
                    Text(display_path_str),
                    title="[green]💾 응답 파일 저장 완료[/green]",
                    border_style="dim",
                    title_align="left"
                ))
        except Exception as e:
            console.print(f"[red]마크다운 파일 저장 실패 ({md_filename}): {e}[/red]") 


        # 자동 초기화
        if attached:
            attached = []
            console.print("[dim]첨부 파일 초기화[/dim]")


# ──────────────────────────────────────────────────────
# 11. 단일 prompt 모드
# ──────────────────────────────────────────────────────
def single_prompt(text: str) -> None:
    temp_session = [{"role": "user", "content": text}]
    reply = ask_stream(temp_session, "openai/gpt-4o", "general")
    if reply:
        console.print(reply)

# ────────────────────────────────
# main
# ────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="터미널에서 AI와 상호작용하는 CLI 도구",
        formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("prompt", nargs="?", default=None, help="단일 질문을 입력하고 바로 답변을 받습니다.")
    ap.add_argument("-s", "--session", default="default", help="대화형 모드에서 사용할 세션 이름 (기본값: default)")
    ap.add_argument("--copy", action="store_true", help="대화형 모드에서 AI의 응답을 클립보드로 복사합니다.")
    ap.add_argument("--model", default="openai/gpt-4o", help="단일 프롬프트 모드에서 사용할 모델 (기본값: openai/gpt-4o)")
    args = ap.parse_args()

    # 인자로 프롬프트가 주어진 경우 -> 단일 실행 모드
    if args.prompt:
        console.print(f"[dim]모델: {args.model}...[/dim]")
        # 메시지 객체 생성
        messages = [{"role": "user", "content": args.prompt}]
        
        # 스트리밍 호출 및 답변 출력
        reply = ask_stream(messages, args.model, "general", pretty_print=True)
        
        # 답변을 파일로 저장 (선택적)
        if reply:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            MD_OUTPUT_DIR.joinpath(f"single_prompt_{timestamp}.md").write_text(reply, encoding="utf-8")
        
        sys.exit(0) # 실행 후 즉시 종료

    # 인자로 프롬프트가 없는 경우 -> 대화형 채팅 모드
    else:
        chat_mode(args.session, args.copy)

if __name__ == "__main__":
    main()