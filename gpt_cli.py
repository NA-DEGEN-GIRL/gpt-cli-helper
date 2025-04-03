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
from pathlib import Path

import pyperclip
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
console = Console()

# Paths and File Config
CWD = Path(os.getcwd())
SESSION_FILE = lambda name: CWD / f".gpt_session_{name}.json"
HISTORY_FILE = CWD / ".gpt_prompt_history.txt"
FAVORITES_FILE = CWD / ".gpt_favorites.json"
IGNORE_FILE = CWD / ".gptignore"
OUTPUT_DIR = CWD / "gpt_outputs"
MD_OUTPUT_DIR = CWD / "gpt_markdowns"
EXCLUDE_PATTERNS = ["secret", "private", "key", "api"]

# Global variable for loading animation
stop_loading = None

# Command List
COMMANDS = """
/commands                  → 명령어 리스트
/files [file1 file2 ...]   → 여러 파일 설정
/clearfiles                → 파일 초기화
/model [gpt-4|gpt-3.5|...] → 모델 변경
/mode [dev|general]        → GPT 역할 전환
/savefav [이름]            → 마지막 질문 즐겨찾기 등록
/usefav [이름]             → 즐겨찾기 불러오기
/favs                      → 즐겨찾기 목록 출력
/diffme                    → 내 코드와 GPT 응답 비교
/reset                     → 세션 초기화
/exit                      → 종료
"""

commands = [line.split()[0] for line in COMMANDS.strip().split('\n')]

# Define Autocompletion
def completer(text, state):
    options = [cmd for cmd in commands if cmd.startswith(text)]
    options += glob.glob(text + '*')
    options.append(None)
    return options[state]

readline.set_completer_delims(' \t\n')
readline.parse_and_bind('tab: complete')
readline.set_completer(completer)

# Function Definitions
def load_session(name):
    path = SESSION_FILE(name)
    return json.load(open(path, "r")) if path.exists() else []

def save_session(name, messages):
    with open(SESSION_FILE(name), "w") as f:
        json.dump(messages, f, indent=2)

def save_history(prompt):
    with open(HISTORY_FILE, "a") as f:
        f.write(prompt + "\n")

def load_favorites():
    return json.load(open(FAVORITES_FILE, "r")) if FAVORITES_FILE.exists() else {}

def save_favorite(name, prompt):
    favs = load_favorites()
    favs[name] = prompt
    with open(FAVORITES_FILE, "w") as f:
        json.dump(favs, f, indent=2)

def list_favorites():
    for name, prompt in load_favorites().items():
        console.print(f"[cyan]{name}[/cyan]: {prompt}")

def read_code_file(path):
    if IGNORE_FILE.exists():
        with open(IGNORE_FILE, "r") as f:
            if any(p in path for p in f.read().splitlines()):
                return f"[무시된 파일: {path}]"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return mask_sensitive(f.read())
    except Exception as e:
        return f"[파일 읽기 실패: {e}]"

def mask_sensitive(content):
    for key in EXCLUDE_PATTERNS:
        pattern = rf"({re.escape(key)}\s*=\s*)(['\"]?).*?\2(?=\n|$)"
        content = re.sub(pattern, r"\1[REDACTED]", content, flags=re.IGNORECASE)
    return content

def extract_code_blocks(text):
    pattern = r"```(?:([\w+-]*)\n)?([\s\S]*?)```(?:\n|$)"
    return re.findall(pattern, text, re.DOTALL)

def save_code_blocks(blocks):
    OUTPUT_DIR.mkdir(exist_ok=True)
    paths = []

    ext_mapping = {
        "python": "py",
        "javascript": "js",
        "js": "js",
        "text": "txt",
        "html": "html",
        "css": "css",
        "java": "java",
        "c": "c",
        "cpp": "cpp",
        "typescript": "ts",
        "ts": "ts",
    }

    for i, (lang, code) in enumerate(blocks, 1):
        ext = ext_mapping.get(lang, "txt")
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
    MD_OUTPUT_DIR.mkdir(exist_ok=True)
    path = MD_OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[blue].md 파일 저장됨 → {path}")

def loading_animation():
    for c in itertools.cycle(['|', '/', '-', '\\']):
        if stop_loading:
            break
        console.print(f'[cyan]Loading {c}', end='\r', style="bold cyan")
        time.sleep(0.1)

def ask_gpt(messages, model="gpt-4o", mode="dev", summary=""):
    global stop_loading
    stop_loading = False
    t = threading.Thread(target=loading_animation)
    t.start()

    system_prompt = {
        "role": "system",
        "content": summary if summary else (
            "너는 숙련된 프로그래밍 전문가야. 다양한 언어의 코드 작성, 리팩터링, 설명, 디버깅을 도와줄 수 있어."
            if mode == "dev" else
            "당신은 친절하고 유용한 AI 어시스턴트입니다. 다양한 일상 질문에도 친절하게 답변해주세요."
        )
    }
    messages = [system_prompt] + messages
    trimmed = messages[-40:] if len(messages) > 40 else messages
    res = client.chat.completions.create(model=model, messages=trimmed)
    stop_loading = True
    t.join()
    console.print(" " * 20, end='\r')
    return res.choices[0].message

def render_diff(a, b, lang="python"):
    diff = list(difflib.unified_diff(
        a.splitlines(),
        b.splitlines(),
        lineterm=""
    ))

    if not diff:
        console.print("No changes detected.", style="green")
        return

    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            console.print(line)
            continue
        if line.startswith("-"):
            syntax = Syntax('- ' + line[1:], lang, theme="monokai", background_color="#330000")
            console.print(syntax)
        elif line.startswith("+"):
            syntax = Syntax('+ ' + line[1:], lang, theme="monokai", background_color="#000000")
            console.print(syntax)
        elif line.startswith("@@"):
            console.print(line, style="cyan")
        else:
            syntax = Syntax('  ' + line[1:], lang, theme="monokai", background_color="#2d2d2d")
            console.print(syntax, style="dim")

def render_response(content, last=""):
    try:
        blocks_new = extract_code_blocks(content)
        
        if not blocks_new:
            console.print(content)
            return

        start_index = 0
        for i, (lang, code) in enumerate(blocks_new):
            code_block_str = f"```{lang}\n{code}```" if lang else f"```\n{code}```"
            length_code_block = len(code_block_str)
            end_index = content.find(code_block_str, start_index)
            if end_index > start_index:
                console.print(content[start_index:end_index].strip())
            if lang:
                console.print(f"[bold cyan]Language: {lang}[/bold cyan]")
            console.print(Syntax(code, lang or "text", theme="monokai", word_wrap=True))
            start_index = end_index + length_code_block

        if start_index < len(content):
            console.print(content[start_index:].strip())

    except Exception as e:
        console.print(f"[red]렌더링 중 오류 발생: {e}")

def chat_mode(session_name, copy_enabled=False):
    mode = ["dev"]
    summary = [""]
    console.print(Panel.fit(COMMANDS, title="[bold yellow]/명령어 목록", border_style="yellow"), markup=False)

    session = load_session(session_name)
    files, last, model = [], "", ["gpt-4o"]
    console.print(f"[bold cyan]GPT CLI 세션 시작: {session_name} (모델: {model[0]})")

    while True:
        try:
            msg = input("GPT> ").strip()
            if not msg:
                continue

            if msg == "/commands":
                console.print(Panel.fit(COMMANDS, title="[bold yellow]/명령어 목록", border_style="yellow"), markup=False)
                continue
            if msg == "/exit":
                break
            if msg.startswith("/mode"):
                mode[0] = msg.split()[1]
                console.print(f"[green]모드 변경됨 → {mode[0]}")
                continue
            if msg.startswith("/model"):
                model[0] = msg.split()[1]
                console.print(f"[green]모델 변경됨: {model[0]}")
                continue
            if msg.startswith("/files"):
                files = msg.split()[1:]
                console.print(f"[yellow]파일 설정됨: {', '.join(files)}")
                continue
            if msg == "/clearfiles":
                files = []
                continue
            if msg == "/reset":
                session = []
                continue
            if msg.startswith("/savefav"):
                save_favorite(msg.split()[1], session[-2]["content"])
                continue
            if msg == "/favs":
                list_favorites()
                continue
            if msg.startswith("/usefav"):
                name = msg.split()[1]
                favs = load_favorites()
                msg = favs.get(name, msg)
            if msg == "/diffme":
                for path in files:
                    original = read_code_file(path)
                    for lang, gpt_code in extract_code_blocks(last):
                        render_diff(original, gpt_code, lang)
                continue
            if msg == "/diffcode":
                new_blocks = extract_code_blocks(last)
                old_blocks = extract_code_blocks(session[-3]["content"]) if len(session) > 2 else []
                for i in range(min(len(new_blocks), len(old_blocks))):
                    lang_new, new_code = new_blocks[i]
                    lang_old, old_code = old_blocks[i]
                    render_diff(old_blocks[i][1], new_blocks[i][1], lang=lang_new or lang_old)
                continue

            prompt = msg
            for path in files:
                prompt += f"\n\n[파일: {path}]\n```\n{read_code_file(path)}\n```"
            session.append({"role": "user", "content": prompt})
            save_history(prompt)

            console.print(Syntax(" ", "python", theme="monokai", background_color="#008C45"))
            console.print(Syntax(" ", "python", theme="monokai", background_color="#F4F5F0"))
            console.print(Syntax(" ", "python", theme="monokai", background_color="#CD212A"))

            res = ask_gpt(session, model=model[0], mode=mode[0], summary=summary[0])
            session.append({"role": res.role, "content": res.content})
            save_session(session_name, session)
            render_response(res.content, last)
            last = res.content

            if copy_enabled:
                try:
                    pyperclip.copy(res.content)
                    console.print("[green][복사 완료]")
                except pyperclip.PyperclipException:
                    console.print("[yellow][클립보드 복사 실패 — xclip 또는 GUI 환경이 없는 경우입니다]")

            save_markdown(res.content, f"response_{len(session)}.md")
            for f in save_code_blocks(extract_code_blocks(res.content)):
                console.print(f"[green]코드 저장됨 → {f}")

        except KeyboardInterrupt:
            break

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
        session = load_session(args.session)
        q = args.prompt
        if args.file:
            q += f"\n\n[첨부 코드]\n```\n{read_code_file(args.file)}\n```"
        session.append({"role": "user", "content": q})
        save_history(q)
        res = ask_gpt(session)
        session.append({"role": res.role, "content": res.content})
        save_session(args.session, session)
        render_response(res.content)

        if args.copy:
            try:
                pyperclip.copy(res.content)
                console.print("[green][복사 완료]")
            except pyperclip.PyperclipException:
                console.print("[yellow][클립보드 복사 실패 — xclip 또는 GUI 환경이 없는 경우입니다]")

        save_markdown(res.content, f"response_{len(session)}.md")
        if args.save:
            for f in save_code_blocks(extract_code_blocks(res.content)):
                console.print(f"[green]코드 저장됨 → {f}")
