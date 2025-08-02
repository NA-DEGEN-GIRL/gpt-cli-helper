from __future__ import annotations

# ── stdlib
import argparse
import base64
import difflib
import itertools
import json
import mimetypes
import pty                                                                                       
import os                                                                                        
import select                                                                                    
import termios                                                                                   
import tty                                                                                       
import subprocess
import struct                                                                                    
import fcntl
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── 3rd-party
import pyperclip
import urwid
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pathspec import PathSpec
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# ────────────────────────────────
# 환경 초기화 / ENV INIT
# ────────────────────────────────
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
MODELS_FILE = BASE_DIR / "ai_models.txt"

OUTPUT_DIR.mkdir(exist_ok=True)
MD_OUTPUT_DIR.mkdir(exist_ok=True)

TRIMMED_HISTORY = 20
console = Console()
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
                                                                                                 
    # 1. 스크롤 맨 위로(커서 홈 + 화면 지우기)                                                   
    #sys.stdout.write('\033[H\033[2J')                                                            
    #sys.stdout.flush()                                                                           
                                                                                                 
    items = [urwid.Text("모델 선택 (Enter)"), urwid.Divider()]                                   
    body: List[urwid.Widget] = []                                                                
    result: List[Optional[str]] = [None]                                                         
    def raise_exit(val: Optional[str]) -> None:                                                  
        result[0] = val                                                                          
        raise urwid.ExitMainLoop()                                                               
    for m in models:                                                                             
        disp = m.split("/")[-1]                                                                  
        btn = urwid.Button(disp)                                                                 
        urwid.connect_signal(btn, "click", lambda button, model=m: raise_exit(model))            
        body.append(urwid.AttrMap(btn, None, focus_map="myfocus"))   # myfocus 적용도 가능       
                                                                                                 
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
    safe_text = mask_sensitive(text)
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


# ──────────────────────────────────────────────────────
# 5. 코드 블록 추출 / 저장
# ──────────────────────────────────────────────────────
def _extract_code_blocks(markdown: str) -> List[Tuple[str, str]]:
    pattern = r"```(?:([\w+-]*)\n)?([\s\S]*?)```(?:\n|$)"
    return re.findall(pattern, markdown)

def extract_code_blocks(markdown: str) -> List[Tuple[str, str]]:
    """
    State-machine 기반으로 마크다운에서 코드 블록을 추출합니다.
    ask_stream의 실시간 파싱 로직과 동일한 원리로, 정규식보다 안정적입니다.
    """
    blocks = []
    lines = markdown.split('\n')
    
    current_state = "NORMAL"  # "NORMAL" 또는 "IN_CODE"
    code_buffer = []
    language = ""

    for line in lines:
        # 코드 블록 시작 ``` 감지
        if line.rstrip().startswith("```") and current_state == "NORMAL":
            current_state = "IN_CODE"
            language = line.strip()[3:].strip() or "text"
            code_buffer = []  # 새 블록을 위해 버퍼 초기화
        
        # 코드 블록 종료 ``` 감지
        elif line.rstrip().startswith("```") and current_state == "IN_CODE":
            blocks.append((language, "\n".join(code_buffer)))
            current_state = "NORMAL"
            language = ""
            code_buffer = []
        
        # 코드 블록 내용 수집
        elif current_state == "IN_CODE":
            code_buffer.append(line)

    # 파일 끝까지 코드 블록이 닫히지 않은 엣지 케이스 처리
    if current_state == "IN_CODE" and code_buffer:
        blocks.append((language, "\n".join(code_buffer)))
        
    return blocks

def _save_code_blocks(blocks: Sequence[Tuple[str, str]]) -> List[Path]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    saved: List[Path] = []
    ext_map = {"python": "py", "javascript": "js", "typescript": "ts", "text": "txt"}
    for i, (lang, code) in enumerate(blocks, 1):
        ext = ext_map.get(lang.lower(), "txt") if lang else "txt"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        p = OUTPUT_DIR / f"gpt_output_{timestamp}_{i}.{ext}"
        cnt = 1
        while p.exists():
            p = OUTPUT_DIR / f"gpt_output_{timestamp}_{i}_{cnt}.{ext}"
            cnt += 1
        p.write_text(code, encoding="utf-8")
        saved.append(p)
    return saved

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
def _ask_stream(
    messages: List[Dict[str, Any]],
    model: str,
    mode: str,
) -> Optional[str]:
    system_prompt = {
        "role": "system",
        "content": (
            "너는 숙련된 프로그래밍 전문가야. 항상 한국어로 답한다."
            if mode == "dev"
            else "당신은 친절한 AI 어시스턴트입니다. 주로 한국어로 답하세요."
        ),
    }
    model_online = model if model.endswith(":online") else f"{model}:online"
    
    with console.status("[cyan]Thinking...", spinner="dots"):
        try:
            stream = client.chat.completions.create(
                model=model_online,
                messages=[system_prompt] + messages[-TRIMMED_HISTORY:],
                stream=True,
                extra_body={'reasoning':{}},
            )
        except OpenAIError as e:
            console.print(f"[red]API 오류: {e}[/red]")
            return None

    # 상태 머신 변수들
    full_reply = ""
    current_state = "NORMAL"
    buffer = ""
    code_buffer = ""
    language = "text"
    normal_buffer = ""
    last_flush_time = time.time()

    console.print("[bold]GPT:[/bold]")
    
    # stream을 iterator로 변환
    stream_iter = iter(stream)
    
    try:
        while True:
            chunk = next(stream_iter)
            delta = chunk.choices[0].delta

            # Reasoning 처리 (delta.reasoning이 있는 경우)                                        
            if hasattr(delta, 'reasoning') and delta.reasoning and current_state == "NORMAL":     
                if normal_buffer:                                                                 
                    console.print(normal_buffer, end="", markup=False)                            
                    normal_buffer = ""                                                            
                                                                                                      
                    current_state = "IN_REASONING"                                                    
                    reasoning_buffer = delta.reasoning                                                
                                                                                                      
                    # Reasoning Live 패널                                                             
                    with Live(                                                                        
                        Panel(                                                                        
                            "[dim]추론 시작...[/dim]",                                                
                            height=10,                                                                
                            title="[magenta]🤔 추론 과정 (Reasoning)[/magenta]",                      
                            border_style="magenta"                                                    
                        ),                                                                            
                        console=console,                                                              
                        auto_refresh=True,                                                            
                        refresh_per_second=4,                                                         
                        transient=True                                                                
                    ) as live:                                                                        
                        # reasoning이 계속되는 동안                                                   
                        while True:                                                                  
                            try:
                                chunk = next(stream_iter)                                             
                                delta = chunk.choices[0].delta                                        
                                                                                                    
                                # 추가 reasoning 내용                                                 
                                if hasattr(delta, 'reasoning') and delta.reasoning:                   
                                    reasoning_buffer += delta.reasoning                               
                                                                                                    
                                    # 패널 업데이트                                                   
                                    lines = reasoning_buffer.splitlines()                             
                                    total_lines = len(lines)                                          
                                                                                                    
                                    if total_lines > 8:                                               
                                        display_lines = lines[-8:]                                    
                                        display_text = f"[dim]... ({total_lines - 8}줄 생략)...[/dim]\n"                                                                                      
                                        display_text += "\n".join(f"[italic]{l}[/italic]" for l in display_lines)                                                                                    
                                    else:                                                             
                                        display_text = "\n".join(f"[italic]{l}[/italic]" for l in lines)                                                                                            
                                                                                                    
                                    status = f"[dim]{total_lines}줄 분석중...[/dim]"                  
                                    live.update(                                                      
                                        Panel(                                                        
                                            display_text,                                             
                                            height=10,                                                
                                            title=f"[magenta]🤔 추론 과정 {status}[/magenta]",        
                                            border_style="magenta"                                    
                                        )                                                             
                                    )                                                                 
                                                                                                    
                                # reasoning이 끝나고 content가 시작되면                               
                                if delta.content:                                                     
                                    content = delta.content                                           
                                    full_reply += content                                             
                                    buffer += content                                                 
                                    current_state = "NORMAL"                                          
                                    break                                                             
                                                                                                        
                            except StopIteration:                                                     
                                current_state = "NORMAL"                                              
                                break                                                                 
                                                                                                    
                # Reasoning 완료 메시지                                                           
                console.print("[dim]✓ 추론 완료[/dim]\n")                                         
                continue
            if not (delta and delta.content):
                continue

            content = delta.content
            full_reply += content
            buffer += content

            # 줄바꿈 기준 처리
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                if current_state == "NORMAL":
                    # 코드 블록 시작 감지
                    if line.rstrip().startswith("```"):
                        # 남은 normal_buffer 출력
                        if normal_buffer:
                            console.print(normal_buffer, end="", markup=False)
                            normal_buffer = ""
                        
                        language = line.strip()[3:] or "text"
                        current_state = "IN_CODE"
                        code_buffer = ""
                        
                        #console.print()  # 빈 줄
                        
                        # 고정 높이 Live 패널로 코드 표시
                        with Live(
                            Panel(
                                "[dim]대기중...[/dim]", 
                                height=20, 
                                title=f"[yellow]코드 입력중 ({language})[/yellow]",
                                border_style="dim"
                            ),
                            console=console,
                            auto_refresh=True,
                            refresh_per_second=5,
                            transient=True  # 완료되면 사라짐
                        ) as live:
                            # 코드 블록이 끝날 때까지 계속
                            while current_state == "IN_CODE":
                                try:
                                    chunk = next(stream_iter)
                                    delta = chunk.choices[0].delta
                                    if delta and delta.content:
                                        content = delta.content
                                        full_reply += content
                                        buffer += content
                                        
                                        while "\n" in buffer:
                                            line, buffer = buffer.split("\n", 1)
                                            
                                            if line.rstrip() == "```":
                                                current_state = "NORMAL"
                                                break
                                            
                                            code_buffer += line + "\n"
                                            
                                            # 패널 업데이트
                                            lines = code_buffer.splitlines()
                                            total_lines = len(lines)
                                            
                                            # 최대 10줄만 표시 (패널 높이 - 2)
                                            if total_lines > 10:
                                                display_lines = lines[-10:]
                                                display_text = f"[dim]... ({total_lines - 10}줄 생략) ...[/dim]\n"
                                                display_text += "\n".join(f"[cyan]{l}[/cyan]" for l in display_lines)
                                            else:
                                                display_text = "\n".join(f"[cyan]{l}[/cyan]" for l in lines)
                                            
                                            # 라인 수 정보 추가
                                            status_info = f"[dim]{total_lines}줄[/dim]"
                                            live.update(
                                                Panel(
                                                    display_text,
                                                    height=12,
                                                    title=f"[yellow]코드 입력중 ({language}) {status_info}[/yellow]",
                                                    border_style="dim"
                                                )
                                            )
                                        
                                        if current_state == "NORMAL":
                                            break
                                            
                                except StopIteration:
                                    current_state = "NORMAL" 
                                    break
                        
                        # Live 종료 후 최종 하이라이팅 코드 출력
                        if code_buffer.rstrip():
                            console.print(f"[bold cyan]```{language}[/bold cyan]")

                            lines = code_buffer.rstrip().split('\n')

                            min_indent = float('inf')
                            for line in lines:
                                if line.strip():  # 빈 줄이 아닌 경우
                                    indent = len(line) - len(line.lstrip()) 
                                    min_indent = min(min_indent, indent)
                            
                            if min_indent < float('inf') and min_indent > 0:
                                normalized_lines = []
                                for line in lines:
                                    if line.strip():
                                        normalized_lines.append(line[min_indent:]) 
                                    else:
                                        normalized_lines.append('')
                                code_to_display = '\n'.join(normalized_lines)
                            else:
                                code_to_display = code_buffer.rstrip() 


                            syntax_block = Syntax(
                                code_to_display,
                                language,
                                theme="monokai",
                                line_numbers=True,
                                code_width=None,
                                word_wrap=True
                            )
                            console.print(syntax_block)
                            console.print("[bold cyan]```[/bold cyan]")
                        #console.print()
                        
                    else:
                        # 일반 텍스트 버퍼에 추가
                        normal_buffer += line + "\n"
                
                elif current_state == "IN_CODE":
                    # 이미 Live 내부에서 처리됨
                    pass

            # 버퍼에 남은 내용 처리
            if current_state == "NORMAL" and buffer:
                normal_buffer += buffer
                buffer = ""
                
                # 일반 텍스트 출력
                current_time = time.time()
                if len(normal_buffer) > 20 or (current_time - last_flush_time > 0.25):
                    console.print(normal_buffer, end="", markup=False)
                    normal_buffer = ""
                    last_flush_time = current_time
                    
    except StopIteration:
        pass

    # 남은 내용 처리
    if current_state == "NORMAL" and normal_buffer:
        console.print(normal_buffer, end="", markup=False)
    elif current_state == "IN_CODE":
        console.print("\n[yellow]// 경고: 코드 블록이 제대로 닫히지 않았습니다[/yellow]")
        if code_buffer:
            console.print(Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True))

    console.print()
    return full_reply

def __ask_stream(
    messages: List[Dict[str, Any]],
    model: str,
    mode: str,
) -> Optional[str]:
    system_prompt = {
        "role": "system",
        "content": (
            "너는 숙련된 프로그래밍 전문가야. 항상 한국어로 답한다."
            if mode == "dev"
            else "당신은 친절한 AI 어시스턴트입니다. 주로 한국어로 답하세요."
        ),
    }
    model_online = model if model.endswith(":online") else f"{model}:online"
    
    # reasoning 지원 모델 감지 및 extra_body 설정
    use_reasoning = any(x in model.lower() for x in ['o1-', 'reasoning'])
    extra_body = {'reasoning': {}} if use_reasoning else {}

    with console.status("[cyan]Thinking...", spinner="dots"):
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

    # 상태 머신 변수 초기화
    full_reply = ""
    current_state = "NORMAL"
    buffer = ""
    code_buffer, language = "", "text"
    normal_buffer, last_flush_time = "", time.time()
    reasoning_buffer = ""

    console.print("[bold]GPT:[/bold]")
    stream_iter = iter(stream)
    
    try:
        while True:
            chunk = next(stream_iter)
            delta = chunk.choices[0].delta

            # Reasoning 처리 (Live 패널, transient=True 사용)
            if hasattr(delta, 'reasoning') and delta.reasoning:
                if normal_buffer: console.print(normal_buffer, end="", markup=False); normal_buffer = ""
                
                with Live(console=console, auto_refresh=True, refresh_per_second=4, transient=True) as live:
                    reasoning_buffer = delta.reasoning
                    while True:
                        try:
                            # 패널 내용 생성
                            lines = reasoning_buffer.splitlines()
                            total_lines = len(lines)
                            display_text = "\n".join(f"[italic]{l}[/italic]" for l in lines[-8:])
                            if total_lines > 8:
                                display_text = f"[dim]... ({total_lines - 8}줄 생략) ...[/dim]\n{display_text}"
                            
                            # 패널 업데이트
                            panel = Panel(display_text, height=10, title=f"[magenta]🤔 추론 과정 ({total_lines}줄)[/magenta]", border_style="magenta")
                            live.update(panel)

                            # 다음 청크 처리
                            chunk = next(stream_iter)
                            delta = chunk.choices[0].delta
                            if hasattr(delta, 'reasoning') and delta.reasoning:
                                reasoning_buffer += delta.reasoning
                            elif delta.content:
                                buffer += delta.content
                                break  # reasoning 종료, content 시작
                        except StopIteration:
                            break # 스트림 종료
                continue # 다음 메인 루프 반복

            if not (delta and delta.content):
                continue
            
            # 메인 콘텐츠 버퍼링 및 파싱
            full_reply += delta.content
            buffer += delta.content

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                if current_state == "NORMAL":
                    # 코드 블록 시작 감지
                    if line.rstrip().startswith("```"):
                        if normal_buffer: console.print(normal_buffer, end="", markup=False); normal_buffer = ""
                        
                        language = line.strip()[3:] or "text"
                        current_state = "IN_CODE"
                        code_buffer = ""
                        
                        # Live 객체 생성 (transient=False가 핵심)
                        live = Live(console=console, auto_refresh=True, refresh_per_second=5)
                        with live:
                            # 코드 블록 종료까지 루프
                            while current_state == "IN_CODE":
                                # 임시 패널 업데이트
                                lines, total_lines = code_buffer.splitlines(), len(code_buffer.splitlines())
                                panel_height, display_height = 12, 10
                                
                                display_text = "\n".join(f"[cyan]{l}[/cyan]" for l in lines[-display_height:])
                                if total_lines > display_height:
                                    display_text = f"[dim]... ({total_lines - display_height}줄 생략) ...[/dim]\n{display_text}"
                                
                                temp_panel = Panel(display_text, height=panel_height, title=f"[yellow]코드 입력중 ({language}) {total_lines}줄[/yellow]", border_style="dim")
                                live.update(temp_panel)
                                
                                # 다음 청크를 가져와 code_buffer 채우기
                                try:
                                    chunk = next(stream_iter)
                                    if chunk.choices[0].delta and chunk.choices[0].delta.content:
                                        full_reply += chunk.choices[0].delta.content
                                        buffer += chunk.choices[0].delta.content
                                        
                                        while "\n" in buffer:
                                            sub_line, buffer = buffer.split("\n", 1)
                                            if sub_line.rstrip() == "```":
                                                current_state = "NORMAL"
                                                break
                                            code_buffer += sub_line + "\n"
                                        
                                        if current_state == "NORMAL":
                                            break
                                except StopIteration:
                                    current_state = "NORMAL"; break
                            
                            # 루프 종료 후, 최종 결과물로 업데이트하고 Live 종료
                            if code_buffer.rstrip():
                                syntax_block = Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True, word_wrap=True)
                                final_panel = Panel(syntax_block, title=f"[green]코드 ({language})[/green]", border_style="green")
                                live.update(final_panel)
                            else:
                                live.update("")  # 빈 블록이면 아무것도 남기지 않음
                            live.stop()
                            
                    else:
                        normal_buffer += line + "\n"
                # current_state == "IN_CODE" 경우는 위에서 모두 처리됨

            # 버퍼에 남은 일반 텍스트가 있다면 출력
            if current_state == "NORMAL" and buffer:
                normal_buffer += buffer; buffer = ""
            
            current_time = time.time()
            if normal_buffer and (len(normal_buffer) > 20 or (current_time - last_flush_time > 0.25)):
                console.print(normal_buffer, end="", markup=False)
                normal_buffer = ""
                last_flush_time = current_time

    except StopIteration:
        pass

    # 스트림 종료 후 남은 버퍼 처리
    if normal_buffer: console.print(normal_buffer, end="", markup=False)
    if current_state == "IN_CODE" and code_buffer:
        console.print("\n[yellow]경고: 코드 블록이 제대로 닫히지 않았습니다.[/yellow]")
        console.print(Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True))

    console.print()
    return full_reply

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
    else:  # general 모드
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
    current_state = "NORMAL"
    buffer = ""
    code_buffer, language = "", "text"
    normal_buffer, last_flush_time = "", time.time()
    reasoning_buffer = ""
    
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

            if not (delta and delta.content): continue
            
            full_reply += delta.content
            buffer += delta.content

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                if current_state == "NORMAL":
                    if line.rstrip().startswith("```"):
                        if normal_buffer: console.print(normal_buffer, end="", markup=False); normal_buffer = ""
                        
                        language = line.strip()[3:] or "text"
                        current_state = "IN_CODE"
                        code_buffer = ""
                        
                        live = Live(console=console, auto_refresh=True, refresh_per_second=5)
                        with live:
                            while current_state == "IN_CODE":
                                lines, total_lines = code_buffer.splitlines(), len(code_buffer.splitlines())
                                panel_height, display_height = 12, 10
                                
                                display_text = "\n".join(f"[cyan]{l}[/cyan]" for l in lines[-display_height:])
                                if total_lines > display_height:
                                    display_text = f"[dim]... ({total_lines - display_height}줄 생략) ...[/dim]\n{display_text}"
                                
                                temp_panel = Panel(display_text, height=panel_height, title=f"[yellow]코드 입력중 ({language}) {total_lines}줄[/yellow]", border_style="dim")
                                live.update(temp_panel)
                                
                                try:
                                    chunk = next(stream_iter)
                                    if chunk.choices[0].delta and chunk.choices[0].delta.content:
                                        full_reply += chunk.choices[0].delta.content
                                        buffer += chunk.choices[0].delta.content
                                        
                                        while "\n" in buffer:
                                            sub_line, buffer = buffer.split("\n", 1)
                                            if sub_line.rstrip() == "```":
                                                current_state = "NORMAL"; break
                                            code_buffer += sub_line + "\n"
                                        
                                        if current_state == "NORMAL": break
                                except StopIteration:
                                    current_state = "NORMAL"; break
                            
                            if code_buffer.rstrip():
                                syntax_block = Syntax(code_buffer.rstrip(), language, theme="monokai", line_numbers=True, word_wrap=True)
                                # 바로 이 부분을 Panel.fit() 으로 수정했습니다.
                                final_panel = Panel.fit(syntax_block, title=f"[green]코드 ({language})[/green]", border_style="green")
                                live.update(final_panel)
                            else:
                                live.update("")
                            live.stop()
                            
                    else:
                        normal_buffer += line + "\n"

            if current_state == "NORMAL" and buffer:
                normal_buffer += buffer; buffer = ""
            
            current_time = time.time()
            if normal_buffer and (len(normal_buffer) > 20 or (current_time - last_flush_time > 0.25)):
                console.print(normal_buffer, end="", markup=False)
                normal_buffer = ""; last_flush_time = current_time

    except StopIteration:
        pass

    if normal_buffer: console.print(normal_buffer, end="", markup=False)
    if current_state == "IN_CODE" and code_buffer:
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
    prompt_continuation="         ",
)


# ──────────────────────────────────────────────────────
# 10. 메인 대화 루프
# ──────────────────────────────────────────────────────
COMMANDS = """
/commands            → 명령어 리스트
/pretty_print        → 고급 출력(Rich) ON/OFF 토글
/select_model        → 모델 선택 TUI
/model <slug>        → 모델 직접 변경
/all_files           → 파일 선택기(TUI)
/files f1 f2 ...     → 수동 파일 지정
/clearfiles          → 첨부파일 초기화
/mode [dev|general]  → 시스템 프롬프트 모드
/savefav [name]      → 질문 즐겨찾기
/usefav [name]       → 즐겨찾기 사용
/favs                → 즐겨찾기 목록
/diffme              → 선택파일 vs GPT 코드 비교
/diffcode            → 이전↔현재 GPT 코드 비교
/reset               → 세션 리셋
/exit                → 종료
""".strip()


def chat_mode(name: str, copy_clip: bool) -> None:
    data = load_session(name)
    messages: List[Dict[str, Any]] = data["messages"]
    model = data["model"]
    mode = "dev"
    attached: List[str] = []
    last_resp = ""
    pretty_print_enabled = True 

    console.print(Panel.fit(COMMANDS, title="[yellow]/명령어[/yellow]"))
    console.print(f"[cyan]세션('{name}') 시작 – 모델: {model}[/cyan]")

    while True:
        try:
            user_in = prompt_session.prompt("Question> ").strip()
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
            elif cmd == "/commands":
                console.print(Panel.fit(COMMANDS, title="[yellow]/명령어[/yellow]"))
            elif cmd == "/select_model":
                #console.print()
                old_model = model                                                                            
                model = select_model(model)
                console.print(f"[green]모델 변경: {old_model} → 현재: {model}[/green]")
                #console.print()
            elif cmd == "/model" and args:
                model = args[0]
                console.print(f"[green]모델 변경 → {model}[/green]")
            elif cmd == "/all_files":
                selector = FileSelector()
                attached = selector.start()
                console.print(f"[yellow]파일 {len(attached)}개 선택됨: {','.join(attached)}[/yellow]")
            elif cmd == "/files":
                attached = sorted(list(set(args)))
                console.print(f"[yellow]파일 {len(attached)}개 선택됨: {','.join(attached)}[/yellow]")
            elif cmd == "/clearfiles":
                attached = []
            elif cmd == "/mode" and args:
                mode = args[0]
            elif cmd == "/reset":
                messages.clear()
                console.print("[yellow]세션 초기화[/yellow]")
            elif cmd == "/savefav" and args:
                if messages and messages[-1]["role"] == "user":
                    save_favorite(args[0], messages[-1]["content"])
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
        save_session(name, messages, model)
        last_resp = reply

        # ── 후처리
        code_blocks = extract_code_blocks(reply)
        if code_blocks:
            saved_files = save_code_blocks(code_blocks)
            if saved_files:
                saveed_paths_text = Text("\n".join(
                    f"  • {p.relative_to(BASE_DIR)}" for p in saved_files                          
                ))                  
                console.print(Panel.fit(
                    saveed_paths_text,
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

        
        MD_OUTPUT_DIR.joinpath(f"response_{len(messages)//2}.md").write_text(reply)

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
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", help="단일 프롬프트")
    ap.add_argument("-s", "--session", default="default", help="세션 이름")
    ap.add_argument("--copy", action="store_true", help="응답 클립보드 복사")
    args = ap.parse_args()

    if args.prompt:
        # single-prompt mode (기존 chat_mode 내부 함수 활용)
        chat_mode = globals()["chat_mode"]  # noqa
        chat_mode(args.session, False)  # quick path
    else:
        chat_mode = globals()["chat_mode"]  # noqa
        chat_mode(args.session, args.copy)


if __name__ == "__main__":
    main()