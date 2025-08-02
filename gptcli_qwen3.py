import os
import re
import json
import glob
import readline
import threading
import itertools
import difflib
import time
import argparse
import sys
import base64
from pathlib import Path
from typing import List, Dict, Any
from functools import partial

# External libraries / 외부 라이브러리
import pyperclip
import requests
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from dotenv import load_dotenv
from openai import OpenAI
import urwid
import pathspec
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings

# --- Configuration and Initialization / 설정 및 초기화 ---

# Load environment variables / 환경 변수 로드
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Validate API key / API 키 검증
if not OPENROUTER_API_KEY:
    print("OPENROUTER_API_KEY 환경 변수가 설정되지 않았습니다. .env 파일을 확인해주세요.")
    sys.exit(1)

# Initialize OpenRouter Client / OpenRouter 클라이언트 초기화
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

console = Console()

# Global settings / 전역 설정
TRIMMED_HISTORY_LENGTH = 100
CLOSE_TO_LIMIT = 80000  # Context length warning threshold / 컨텍스트 길이 경고 임계값

# Path settings / 경로 설정
BASE_DIR = Path(os.getcwd())
SESSION_DIR = BASE_DIR / ".gpt_sessions"
SESSION_FILE = lambda name: SESSION_DIR / f"session_{name}.json"
PROMPT_HISTORY_FILE = BASE_DIR / ".gpt_prompt_history.txt"
FAVORITES_FILE = BASE_DIR / ".gpt_favorites.json"
IGNORE_FILE = BASE_DIR / ".gptignore"
OUTPUT_DIR = BASE_DIR / "gpt_outputs"
MD_OUTPUT_DIR = BASE_DIR / "gpt_markdowns"
AI_MODELS_FILE = BASE_DIR / "ai_models.txt"
EXCLUDE_PATTERNS = ["secret", "private", "key", "api"]

# Create necessary directories / 필요한 디렉토리 생성
SESSION_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MD_OUTPUT_DIR.mkdir(exist_ok=True)

# Event for controlling loading animation / 로딩 애니메이션 제어 이벤트
stop_loading = threading.Event()

# Supported text file extensions / 지원되는 텍스트 파일 확장자
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.c', '.cpp',
    '.h', '.hpp', '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala',
    '.ex', '.exs', '.r', '.m', '.mm', '.sh', '.bash', '.zsh', '.ps1', '.bat',
    '.cmd', '.yml', '.yaml', '.json', '.xml', '.toml', '.ini', '.cfg', '.conf',
    '.html', '.htm', '.css', '.scss', '.sass', '.less', '.sql', '.dockerfile',
    '.makefile', '.cmake', '.gradle', '.vue', '.svelte', '.astro', '.lua',
    '.vim', '.el'
}

# --- Helper Functions / 보조 함수 ---

def load_json(filename, default=None):
    if filename.exists():
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return default if default is not None else {}

def save_json(filename, data):
    filename.parent.mkdir(exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False) # Ensure Korean is saved correctly / 한국어가 올바르게 저장되도록

def session_manager(session_name):
    """Load or initialize a session / 세션 로드 또는 초기화"""
    session_path = SESSION_FILE(session_name)
    data = load_json(session_path, {"messages": [], "model": "openai/gpt-4o"})
    if isinstance(data, list): # Backward compatibility / 이전 버전 호환성
        data = {"messages": data, "model": "openai/gpt-4o"}
    return data, session_path

def save_session(session_name, messages, model):
    save_json(SESSION_FILE(session_name), {"messages": messages, "model": model})

def load_favorites():
    return load_json(FAVORITES_FILE, {})

def save_favorite(name, prompt):
    favs = load_favorites()
    favs[name] = prompt
    save_json(FAVORITES_FILE, favs)

def list_favorites():
    favs = load_favorites()
    if not favs:
        console.print("[yellow]저장된 즐겨찾기가 없습니다.")
        return
    for name, prompt in favs.items():
        console.print(f"[cyan]{name}[/cyan]: {prompt[:50]}...")

def mask_sensitive(content):
    """Mask sensitive information in content / 내용에서 민감한 정보 마스킹"""
    for key in EXCLUDE_PATTERNS:
        pattern = rf"({re.escape(key)}\s*=\s*)(['\"]?).*?\2(?=\n|$)"
        content = re.sub(pattern, r"\1[REDACTED]", content, flags=re.IGNORECASE)
    return content

def load_gitignore_patterns():
    """Load patterns from .gptignore file / .gptignore 파일에서 패턴 로드"""
    ignore_patterns = []
    if IGNORE_FILE.exists():
        spec = pathspec.PathSpec.from_lines('gitwildmatch', IGNORE_FILE.read_text().splitlines())
        return spec
    return None

def get_all_files(base_path: Path, ignore_spec=None) -> List[Dict[str, Any]]:
    """Recursively get all files, applying .gptignore patterns / 재귀적으로 모든 파일 가져오되, .gptignore 패턴 적용"""
    all_files = []
    for item in sorted(base_path.iterdir()):
        rel_path = item.relative_to(BASE_DIR)
        if ignore_spec and ignore_spec.match_file(str(rel_path)):
            continue
        if item.name.startswith('.'):
            continue
            
        is_text_file = item.suffix.lower() in TEXT_EXTENSIONS
        is_binary_file = item.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.pdf'}
        
        if item.is_file() and (is_text_file or is_binary_file):
            all_files.append({
                'path': item,
                'rel_path': rel_path,
                'name': item.name,
                'is_dir': False,
                'is_text': is_text_file,
                'is_binary': is_binary_file
            })
        elif item.is_dir():
            all_files.append({
                'path': item,
                'rel_path': rel_path,
                'name': item.name,
                'is_dir': True,
                'is_text': False,
                'is_binary': False
            })
            # Recursively add child items / 재귀적으로 하위 항목 추가
            child_files = get_all_files(item, ignore_spec)
            for child in child_files:
                child['depth'] = child.get('depth', 0) + 1
            all_files.extend(child_files)
    return all_files

def read_file_content(path: Path) -> Dict[str, str]:
    """Prepare file content for API request / API 요청을 위해 파일 내용 준비"""
    try:
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}:
            # Image file handling / 이미지 파일 처리
            with open(path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/{path.suffix[1:]};base64,{image_data}"
                }
            }
        elif path.suffix.lower() == '.pdf':
            # PDF file handling / PDF 파일 처리
            with open(path, 'rb') as f:
                pdf_data = base64.b64encode(f.read()).decode('utf-8')
            return {
                "type": "file",
                "file": {
                    "filename": path.name,
                    "file_data": f"data:application/pdf;base64,{pdf_data}"
                }
            }
        else:
            # Text file handling / 텍스트 파일 처리
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return {
                "type": "text",
                "text": f"\n\n[파일: {path}]\n```\n{content}\n```"
            }
    except Exception as e:
        return {"type": "text", "text": f"\n\n[파일 읽기 오류: {path} - {e}]"}

def extract_code_blocks(text):
    """Extract code blocks from text / 텍스트에서 코드 블록 추출"""
    pattern = r"```(?:([\w+-]*)\n)?([\s\S]*?)```(?:\n|$)"
    return re.findall(pattern, text, re.DOTALL)

def save_code_blocks(blocks):
    """Save extracted code blocks to files / 추출된 코드 블록을 파일에 저장"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    paths = []
    ext_mapping = {"python": "py", "javascript": "js", "js": "js", 
                   "text": "txt", "html": "html", "css": "css", 
                   "java": "java", "c": "c", "cpp": "cpp", 
                   "typescript": "ts", "ts": "ts"}

    for i, (lang, code) in enumerate(blocks, 1):
        ext = ext_mapping.get(lang.lower(), "txt")
        base_filename = f"gpt_output_{i}"
        path = OUTPUT_DIR / f"{base_filename}.{ext}"

        counter = 1
        while path.exists():
            path = OUTPUT_DIR / f"{base_filename}_{counter}.{ext}"
            counter += 1

        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        paths.append(path)

    return paths

def save_markdown(content, filename="gpt_response.md"):
    """Save response to a markdown file / 응답을 마크다운 파일에 저장"""
    MD_OUTPUT_DIR.mkdir(exist_ok=True)
    path = MD_OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[blue].md 파일 저장됨 → {path}")

def loading_animation():
    """Display a loading animation / 로딩 애니메이션 표시"""
    for c in itertools.cycle(['|', '/', '-', '\\']):
        if stop_loading.is_set():
            break
        console.print(f'[cyan]Loading {c}', end='\r')
        time.sleep(0.1)

def ask_gpt_streaming(messages, model="openai/gpt-4o", mode="dev"):
    """Query GPT with streaming response / 스트리밍 응답으로 GPT에 질의"""
    global stop_loading
    stop_loading.clear()
    t = threading.Thread(target=loading_animation)
    t.start()

    system_prompt = {
        "role": "system",
        "content": (
            "You are a skilled programming expert. Help with writing, refactoring, explaining, and debugging code in various languages. Always respond in Korean."
            if mode == "dev" else
            "You are a friendly and helpful AI assistant. Answer various daily questions kindly. Respond primarily in Korean."
        )
    }
    
    full_messages = [system_prompt] + messages
    trimmed = full_messages[-TRIMMED_HISTORY_LENGTH:] if len(full_messages) > TRIMMED_HISTORY_LENGTH else full_messages
    
    # Add web search plugin / 웹 검색 플러그인 추가
    model_with_online = f"{model}:online"
    
    try:
        stream = client.chat.completions.create(
            model=model_with_online,
            messages=trimmed,
            stream=True
        )
        
        stop_loading.set()
        t.join()
        console.print(" " * 20, end='\r')
        
        full_response = ""
        with Live(Text(""), console=console, refresh_per_second=10) as live:
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    live.update(Text(full_response))
        
        return full_response
        
    except Exception as e:
        stop_loading.set()
        t.join()
        console.print(" " * 20, end='\r')
        console.print(f"[red]오류 발생: {e}")
        return None

def model_selector(current_model):
    """Interactive model selection UI / 대화형 모델 선택 UI"""
    models = []
    if AI_MODELS_FILE.exists():
        with open(AI_MODELS_FILE, 'r') as f:
            models = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    if not models:
        console.print("[red]ai_models.txt 파일이 없거나 비어있습니다.")
        return current_model

    class ModelItem(urwid.Button):
        def __init__(self, model, index):
            self.model = model
            self.index = index
            # Display short name (after last '/') / 짧은 이름 표시 (마지막 '/' 이후)
            display_name = model.split('/')[-1] if '/' in model else model
            super().__init__(display_name)
            urwid.connect_signal(self, 'click', self.on_click)
            
        def on_click(self, button):
            raise urwid.ExitMainLoop()
    
    selected_model = [None]
    
    def on_select(button, model):
        selected_model[0] = model
        raise urwid.ExitMainLoop()
    
    # Build UI widgets / UI 위젯 구성
    model_widgets = []
    for i, model in enumerate(models):
        display_name = model.split('/')[-1] if '/' in model else model
        button = urwid.Button(display_name)
        urwid.connect_signal(button, 'click', on_select, user_args=[model])
        model_widgets.append(urwid.AttrMap(button, None, focus_map='reversed'))
    
    listbox = urwid.ListBox(urwid.SimpleFocusListWalker(model_widgets))
    header = urwid.Text("AI 모델 선택 - [Enter]: 선택, [Q]: 취소")
    frame = urwid.Frame(listbox, header=header)
    
    def unhandled_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
    
    # Run the UI / UI 실행
    loop = urwid.MainLoop(frame, unhandled_input=unhandled_input)
    
    try:
        loop.run()
    except KeyboardInterrupt:
        return current_model
    
    return selected_model[0] or current_model

class FileSelector:
    """Interactive file selection UI / 대화형 파일 선택 UI"""
    def __init__(self):
        self.files = []
        self.selected = set()
        self.expanded = set()
        self.ignore_spec = load_gitignore_patterns()
        
    def load_files(self):
        """Load file list / 파일 목록 로드"""
        all_items = get_all_files(BASE_DIR, self.ignore_spec)
        self.files = []
        
        for item in all_items:
            rel_path = item['path'].relative_to(BASE_DIR)
            depth = len(rel_path.parts) - 1
            item['depth'] = depth
            item['parent'] = item['path'].parent if depth > 0 else None
            self.files.append(item)
    
    def toggle_selection(self, index):
        """Toggle file/folder selection / 파일/폴더 선택 토글"""
        if index < len(self.files):
            item = self.files[index]
            path = item['path']
            
            if item['is_dir']:
                if path in self.selected:
                    self.selected.discard(path)
                    # Deselect children / 하위 항목 해제
                    for f in self.files:
                        if str(f['path']).startswith(str(path) + os.sep):
                            self.selected.discard(f['path'])
                else:
                    self.selected.add(path)
                    # Select children / 하위 항목 선택
                    for f in self.files:
                        if str(f['path']).startswith(str(path) + os.sep) and not f['is_dir']:
                            self.selected.add(f['path'])
            else:
                if path in self.selected:
                    self.selected.discard(path)
                else:
                    self.selected.add(path)
    
    def toggle_expand(self, index):
        """Toggle directory expansion / 디렉토리 확장 토글"""
        if index < len(self.files) and self.files[index]['is_dir']:
            path = self.files[index]['path']
            if path in self.expanded:
                self.expanded.discard(path)
            else:
                self.expanded.add(path)
    
    def select_all(self):
        """Select all files / 모든 파일 선택"""
        for item in self.files:
            if not item['is_dir']:
                self.selected.add(item['path'])
    
    def deselect_all(self):
        """Deselect all files / 모든 파일 선택 해제"""
        self.selected.clear()

def interactive_file_selector(initial_selection=None):
    """Run the interactive file selector / 대화형 파일 선택기 실행"""
    selector = FileSelector()
    selector.load_files()
    if initial_selection:
        for path_str in initial_selection:
            path = Path(path_str)
            if path.exists():
                selector.selected.add(path)
    
    class FileItem(urwid.Text):
        def __init__(self, item, index):
            self.item = item
            self.index = index
            prefix = "  " * item.get('depth', 0)
            
            if item['is_dir']:
                icon = "▼ " if item['path'] in selector.expanded else "▶ "
                check = "[D] " if item['path'] in selector.selected else "[ ] "
                text = f"{prefix}{check}{icon}{item['name']}/"
            else:
                check = "[X] " if item['path'] in selector.selected else "[ ] "
                text = f"{prefix}{check}{item['name']}"
            
            super().__init__(text)
            
        def keypress(self, size, key):
            if key == ' ':
                selector.toggle_selection(self.index)
            elif key == 'enter' and self.item['is_dir']:
                selector.toggle_expand(self.index)
            else:
                return key
    
    class FileListBox(urwid.ListBox):
        def keypress(self, size, key):
            if key == 'a': # Select All / 전체 선택
                selector.select_all()
                self.refresh()
            elif key == 'n': # Deselect All / 전체 해제
                selector.deselect_all()
                self.refresh()
            elif key == 'q': # Cancel / 취소
                raise urwid.ExitMainLoop()
            elif key == 's': # Done / 완료
                raise urwid.ExitMainLoop()
            else:
                return super().keypress(size, key)
        
        def refresh(self):
            self.body[:] = [FileItem(item, i) for i, item in enumerate(selector.files)]
    
    def update_footer():
        count = len([p for p in selector.selected if not p.is_dir()])
        footer.set_text(f"선택된 파일: {count}개")
    
    def on_key(key):
        listbox.keypress((0,), key)
        update_footer()
    
    visible_items = selector.files
    listbox = FileListBox(urwid.SimpleFocusListWalker([
        FileItem(item, i) for i, item in enumerate(visible_items)
    ]))
    
    header = urwid.Text("파일 선택기 - [Space]: 선택/해제, [Enter]: 폴더 펼치기/접기, [A]: 전체선택, [N]: 전체해제, [S]: 완료, [Q]: 취소")
    footer = urwid.Text("선택된 파일: 0개")
    
    frame = urwid.Frame(listbox, header=header, footer=footer)
    
    loop = urwid.MainLoop(frame, unhandled_input=on_key)
    
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    
    # Return selected file paths (excluding directories) / 선택된 파일 경로 반환 (디렉토리 제외)
    selected_files = [str(p) for p in selector.selected if not p.is_dir()]
    return selected_files

def render_diff(a, b, lang="python"):
    """Render diff output / diff 출력 렌더링"""
    # (Existing code, kept as is / 기존 코드, 변경 없음)
    diff = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm=""))
    if not diff:
        console.print("No changes detected.", style="green")
        return
    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            console.print(line)
        elif line.startswith("-"):
            console.print(Syntax('-' + line[1:], lang, theme="monokai", line_numbers=False, background_color="#330000"))
        elif line.startswith("+"):
            console.print(Syntax('+' + line[1:], lang, theme="monokai", line_numbers=False, background_color="#003300"))
        elif line.startswith("@@"):
            console.print(line, style="cyan")
        else:
            console.print(Syntax(line, lang, theme="monokai", line_numbers=False, background_color="#2d2d2d"), style="dim")

def render_response(content, last=""):
    """Render the final response / 최종 응답 렌더링"""
    try:
        blocks_new = extract_code_blocks(content)
        
        if blocks_new:
            console.print("\n[dim]--- 코드 블록 발견 ---[/dim]")
            for lang, code in blocks_new:
                if lang:
                    console.print(f"[bold cyan]Language: {lang}[/bold cyan]")
                console.print(Syntax(code, lang or "text", theme="monokai", word_wrap=True))
        
    except Exception as e:
        console.print(f"[red]렌더링 중 오류 발생: {e}")

def chat_mode(session_name, copy_enabled=False):
    """Main chat mode loop / 메인 채팅 모드 루프"""
    commands_desc = """
/commands                  → 명령어 리스트
/files                     → 파일 선택
/clearfiles                → 선택된 파일 초기화
/select_model              → AI 모델 선택
/mode [dev|general]        → GPT 역할 전환
/savefav [이름]            → 마지막 질문 즐겨찾기 등록
/usefav [이름]             → 즐겨찾기 불러오기
/favs                      → 즐겨찾기 목록 출력
/diffme                    → 내 코드와 GPT 응답 비교
/diffcode                  → 종전의 코드와 비교
/reset                     → 세션 초기화
/exit                      → 종료
"""
    console.print(Panel.fit(commands_desc, title="[bold yellow]/명령어 목록", border_style="yellow"), markup=False)

    session_data, session_path = session_manager(session_name)
    session = session_data["messages"]
    model = [session_data["model"]]
    
    files_to_send = []
    last_response = ""
    mode = ["dev"]
    console.print(f"[bold cyan]GPT CLI 세션 시작: {session_name} (모델: {model[0]})")

    # Setup for multi-line input / 멀티라인 입력 설정
    bindings = KeyBindings()
    @bindings.add('c-d')
    def _(event):
        buffer = event.app.current_buffer
        if buffer.text:
            buffer.validate_and_handle()
        else:
            event.app.exit(result='')

    session_history = FileHistory(PROMPT_HISTORY_FILE)
    prompt_session = PromptSession(history=session_history, key_bindings=bindings)

    while True:
        try:
            msg = prompt_session.prompt("GPT> ", multiline=True)
            if not msg.strip():
                continue

            msg = msg.strip()
            if msg == "/commands":
                console.print(Panel.fit(commands_desc, title="[bold yellow]/명령어 목록", border_style="yellow"), markup=False)
                continue
            if msg == "/exit":
                break
            if msg.startswith("/mode"):
                parts = msg.split()
                if len(parts) > 1:
                    mode[0] = parts[1]
                    console.print(f"[green]모드 변경됨 → {mode[0]}")
                continue
            if msg == "/select_model":
                new_model = model_selector(model[0])
                model[0] = new_model
                console.print(f"[green]모델 변경됨: {model[0]}")
                continue
            if msg == "/files":
                files_to_send = interactive_file_selector(files_to_send)
                console.print(f"[yellow]선택된 파일 {len(files_to_send)}개")
                continue
            if msg == "/clearfiles":
                files_to_send = []
                console.print("[green]파일 목록 초기화됨")
                continue
            if msg == "/reset":
                session = []
                console.print("[green]세션 초기화됨")
                continue
            if msg.startswith("/savefav"):
                parts = msg.split()
                if len(parts) > 1 and len(session) >= 2:
                    save_favorite(parts[1], session[-2]["content"])
                    console.print(f"[green]즐겨찾기 저장됨: {parts[1]}")
                continue
            if msg == "/favs":
                list_favorites()
                continue
            if msg.startswith("/usefav"):
                parts = msg.split()
                if len(parts) > 1:
                    name = parts[1]
                    favs = load_favorites()
                    if name in favs:
                        msg = favs[name]
                        console.print(f"[cyan]즐겨찾기 '{name}' 로드됨[/cyan]")
                    else:
                        console.print(f"[red]즐겨찾기 '{name}'를 찾을 수 없습니다.")
                        continue
            # Add other commands as needed / 기타 명령어 추가 가능
            
            # Process user message with files / 사용자 메시지와 파일 함께 처리
            content_parts = [{"type": "text", "text": msg + "\n\n답변은 한국어로 주세요."}]
            for file_path_str in files_to_send:
                file_path = Path(file_path_str)
                if file_path.exists():
                    file_content = read_file_content(file_path)
                    content_parts.append(file_content)
            
            # Build final user message / 최종 사용자 메시지 구성
            if len(content_parts) == 1:
                user_message = {"role": "user", "content": content_parts[0]["text"]}
            else:
                user_message = {"role": "user", "content": content_parts}

            session.append(user_message)
            
            # Visual separator (Korean flag colors) / 시각적 구분선 (대한민국 국기 색상)
            console.print(Syntax(" ", "python", theme="monokai", background_color="#008C45"))
            console.print(Syntax(" ", "python", theme="monokai", background_color="#F4F5F0"))
            console.print(Syntax(" ", "python", theme="monokai", background_color="#CD212A"))

            # Get streaming response / 스트리밍 응답 받기
            response_content = ask_gpt_streaming(session, model=model[0], mode=mode[0])
            
            if response_content:
                session.append({"role": "assistant", "content": response_content})
                save_session(session_name, session, model[0])
                last_response = response_content

                console.print() # Add a newline / 줄바꿈 추가
                render_response(response_content, last_response)

                if copy_enabled and response_content:
                    try:
                        pyperclip.copy(response_content)
                        console.print("[green][복사 완료]")
                    except pyperclip.PyperclipException:
                        console.print("[yellow][클립보드 복사 실패 — xclip 또는 GUI 환경이 필요한 경우입니다]")

                save_markdown(response_content, f"response_{len(session)//2}.md") # Account for user/assistant pairs / 사용자/어시스턴트 쌍을 고려
                saved_files = save_code_blocks(extract_code_blocks(response_content))
                for f in saved_files:
                    console.print(f"[green]코드 저장됨 → {f}")
            
            # IMPORTANT: Clear files after sending to prevent duplicate attachment / 중요: 중복 첨부 방지를 위해 전송 후 파일 목록 초기화
            if files_to_send:
                files_to_send = []
                console.print("[dim]첨부 파일이 자동 초기화되었습니다.[/dim]")

        except KeyboardInterrupt:
            console.print("\n[yellow]입력 취소됨. /exit로 종료하세요.[/yellow]")
            continue
        except Exception as e:
            console.print(f"[red]치명적인 오류 발생: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", help="질문 내용")
    parser.add_argument("-f", "--file", help="코드 파일 경로")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--session", default="default")
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()

    if args.chat or not args.prompt:
        chat_mode(args.session, copy_enabled=args.copy)
    elif args.prompt:
        # Non-interactive mode for single prompt / 단일 프롬프트에 대한 비대화형 모드
        # This part is kept simple for MVP / MVP를 위해 간단히 유지
        session_data, session_path = session_manager(args.session)
        session = session_data["messages"]
        model = session_data["model"]
        
        q = args.prompt
        files_to_send = [args.file] if args.file else []
        
        content_parts = [{"type": "text", "text": q + "\n\n답변은 한국어로 주세요."}]
        for file_path_str in files_to_send:
            file_path = Path(file_path_str)
            if file_path.exists():
                file_content = read_file_content(file_path)
                content_parts.append(file_content)
        
        user_message = {"role": "user", "content": content_parts}
        session.append(user_message)
        
        response_content = ask_gpt_streaming(session, model=model)
        if response_content:
            session.append({"role": "assistant", "content": response_content})
            save_session(args.session, session, model)
            render_response(response_content)

            if args.copy and response_content:
                try:
                    pyperclip.copy(response_content)
                    console.print("[green][복사 완료]")
                except pyperclip.PyperclipException:
                    console.print("[yellow][클립보드 복사 실패 — xclip 또는 GUI 환경이 필요한 경우입니다]")

            save_markdown(response_content, f"response_{len(session)//2}.md")
            if args.save:
                saved_files = save_code_blocks(extract_code_blocks(response_content))
                for f in saved_files:
                    console.print(f"[green]코드 저장됨 → {f}")