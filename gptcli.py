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
import subprocess
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from typing import Union  # FileSelector 타입 힌트용

# ── 3rd-party
import PyPDF2
import requests
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
from rich.table import Table
from rich.box import ROUNDED
import io
import tiktoken
from PIL import Image

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

COMPACT_ATTACHMENTS = True  # 첨부파일 압축 모드 (기본값: 비활성화)

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
CODE_OUTPUT_DIR = BASE_DIR / "gpt_codes"
MODELS_FILE = CONFIG_DIR / "ai_models.txt"
DEFAULT_IGNORE_FILE = CONFIG_DIR / ".gptignore_default"

OUTPUT_DIR.mkdir(exist_ok=True)
MD_OUTPUT_DIR.mkdir(exist_ok=True)
CODE_OUTPUT_DIR.mkdir(exist_ok=True)

#TRIMMED_HISTORY = 20
DEFAULT_CONTEXT_LENGTH = 200000
CONTEXT_TRIM_RATIO = 0.7
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

class TokenEstimator:
    def __init__(self, model: str = "gpt-4"):
        """
        모델별 토크나이저 초기화
        - gpt-4, gpt-3.5-turbo: cl100k_base
        - older models: p50k_base
        """
        try:
            self.encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")
    
    def count_text_tokens(self, text: str) -> int:
        """텍스트의 정확한 토큰 수 계산"""
        return len(self.encoder.encode(text))
    
    def calculate_image_tokens(self, width: int, height: int, detail: str = "auto") -> int:
        """
        OpenAI의 공식 이미지 토큰 계산 방식
        
        detail 옵션:
        - "low": 항상 85 토큰 (512x512 이하로 리사이즈)
        - "high": 타일 기반 계산 (더 정확한 분석)
        - "auto": 이미지 크기에 따라 자동 선택
        """
        
        # Low detail: 고정 비용
        if detail == "low":
            return 85
        
        # High detail: 타일 기반 계산
        # 1. 이미지를 2048x2048 이내로 조정
        if width > 2048 or height > 2048:
            ratio = min(2048/width, 2048/height)
            width = int(width * ratio)
            height = int(height * ratio)
        
        # 2. 짧은 변을 768px로 조정
        if min(width, height) > 768:
            if width < height:
                height = int(height * 768 / width)
                width = 768
            else:
                width = int(width * 768 / height)
                height = 768
        
        # 3. 512x512 타일로 나누기
        tiles_x = math.ceil(width / 512)
        tiles_y = math.ceil(height / 512)
        total_tiles = tiles_x * tiles_y
        
        # 4. 토큰 계산: 베이스(85) + 타일당 170
        return 85 + (170 * total_tiles)
    
    def estimate_image_tokens(self, image_input: Union[Path, str], detail: str = "auto") -> int:
        """이미지 파일 또는 base64 문자열의 토큰 추정"""
        try:
            # base64 문자열인 경우
            if isinstance(image_input, str):
                # base64 문자열에서 이미지 디코드
                try:
                    # data:image/...;base64, 접두사 제거
                    if image_input.startswith('data:'):
                        image_input = image_input.split(',')[1]
                    
                    image_data = base64.b64decode(image_input)
                    img = Image.open(io.BytesIO(image_data))
                    width, height = img.size
                    
                    # base64는 보통 고화질로 처리
                    if detail == "auto":
                        detail = "high"
                    
                    return self.calculate_image_tokens(width, height, detail)
                except Exception:
                    # base64 디코딩 실패 시 길이 기반 추정
                    return len(image_input) // 4
            
            # Path 객체인 경우 (기존 로직)
            elif isinstance(image_input, Path):
                with Image.open(image_input) as img:
                    width, height = img.size
                    
                    # 파일 크기 기반 detail 자동 선택
                    if detail == "auto":
                        file_size_mb = image_input.stat().st_size / (1024 * 1024)
                        detail = "low" if file_size_mb < 0.5 else "high"
                    
                    return self.calculate_image_tokens(width, height, detail)
            else:
                raise ValueError(f"지원하지 않는 입력 타입: {type(image_input)}")
                
        except Exception as e:
            console.print(f"[yellow]이미지 토큰 추정 실패: {e}[/yellow]")
            # 폴백: 기본값 반환
            return 1105  # GPT-4V 평균 토큰 수
    
    def estimate_pdf_tokens(self, pdf_path: Path) -> int:
        """
        PDF 토큰 추정 (대략적)
        일부 모델만 PDF를 직접 지원하며, 
        대부분 텍스트 추출 후 처리
        """
        try:
            
            
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    text += page.extract_text()
                
                return self.count_text_tokens(text)
        except ImportError:
            # PyPDF2가 없으면 파일 크기 기반 추정
            file_size_kb = pdf_path.stat().st_size / 1024
            return int(file_size_kb * 3)  # 1KB ≈ 3 토큰 (대략)
        except Exception:
            # 폴백: base64 크기 기반
            return len(base64.b64encode(pdf_path.read_bytes())) // 4

def optimize_image_for_api(path: Path, max_dimension: int = 1024, quality: int = 85) -> str:
    """
    이미지를 API에 적합하게 최적화
    - 크기 축소
    - JPEG 압축
    - base64 인코딩
    """
    try:
        with Image.open(path) as img:
            # EXIF 회전 정보 적용
            img = img.convert('RGB')
            
            # 크기 조정 (비율 유지)
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
            # 메모리 버퍼에 JPEG로 저장
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            
            # base64 인코딩
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
            
    except ImportError:
        console.print("[yellow]Pillow가 설치되지 않아 이미지 최적화를 건너뜁니다. (pip install Pillow)[/yellow]")
        return encode_base64(path)
    except Exception as e:
        console.print(f"[yellow]이미지 최적화 실패 ({path.name}): {e}[/yellow]")
        return encode_base64(path)

token_estimator = TokenEstimator()

def prepare_content_part(path: Path, optimize_images: bool = True) -> Dict[str, Any]:
    """파일을 API 요청용 컨텐츠로 변환"""
    
    if path.suffix.lower() in IMG_EXTS:
        # 이미지 크기 확인
        file_size_mb = path.stat().st_size / (1024 * 1024)
        
        if file_size_mb > 20:  # 20MB 이상
            return {
                "type": "text",
                "text": f"[오류: {path.name} 이미지가 너무 큽니다 ({file_size_mb:.1f}MB). 20MB 이하로 줄여주세요.]"
            }
        
        # 이미지 최적화
        if optimize_images and file_size_mb > 1:  # 1MB 이상이면 압축
            console.print(f"[dim]이미지 최적화 중: {path.name} ({file_size_mb:.1f}MB)...[/dim]")
            base64_data = optimize_image_for_api(path)
            estimated_tokens = token_estimator.estimate_image_tokens(base64_data, detail="auto")
        else:
            base64_data = encode_base64(path)
            estimated_tokens = token_estimator.estimate_image_tokens(path, detail="auto")
        
        
        if estimated_tokens > 10000:
            console.print(f"[yellow]경고: {path.name}이 약 {estimated_tokens:,} 토큰을 사용합니다.[/yellow]")
        
        data_url = f"data:{mimetypes.guess_type(path)[0] or 'image/jpeg'};base64,{base64_data}"
        return {
            "type": "image_url",
            "image_url": {
                "url": data_url, 
                "detail": "auto", 
                "image_name": path.name, # 내부 참조용
            }
        }
    
    elif path.suffix.lower() == PDF_EXT:
        estimated_tokens = token_estimator.estimate_pdf_tokens(path)
        console.print(f"[dim]PDF 토큰: 약 {estimated_tokens:,}개[/dim]")

        # PDF는 그대로 (일부 모델만 지원)
        data_url = f"data:application/pdf;base64,{encode_base64(path)}"
        return {
            "type": "file",
            "file": {"filename": path.name, "file_data": data_url},
        }
    
    # 텍스트 파일
    text = read_plain_file(path)
    tokens = token_estimator.count_text_tokens(text)
    console.print(f"[dim]텍스트 토큰: {tokens:,}개[/dim]")
    return {
        "type": "text",
        "text": f"\n\n[파일: {path}]\n```\n{text}\n```",
    }

def trim_messages_by_tokens(messages: List[Dict], max_tokens: int) -> List[Dict]:
    """
    메시지 리스트를 토큰 제한에 맞게 트리밍합니다.
    이미지, PDF, 텍스트 각각의 토큰을 정확히 계산합니다.
    """
    def calculate_message_tokens(msg: Dict) -> int:
        """단일 메시지의 토큰 수를 정확히 계산"""
        total_tokens = 0
        
        # role 토큰 (대략 4-5 토큰)
        total_tokens += 5
        
        content = msg.get("content", "")
        
        # 1. content가 문자열인 경우 (일반 텍스트 메시지)
        if isinstance(content, str):
            total_tokens += token_estimator.count_text_tokens(content)
        
        # 2. content가 리스트인 경우 (멀티파트 메시지)
        elif isinstance(content, list):
            for part in content:
                part_type = part.get("type", "")
                
                if part_type == "text":
                    # 텍스트 파트
                    text_content = part.get("text", "")
                    total_tokens += token_estimator.count_text_tokens(text_content)
                
                elif part_type == "image_url":
                    # 이미지 파트
                    image_url = part.get("image_url", {}).get("url", "")
                    
                    if "base64," in image_url:
                        # base64 인코딩된 이미지
                        try:
                            # 데이터 URL에서 base64 부분 추출
                            base64_data = image_url.split("base64,")[1]
                            
                            
                            image_bytes = base64.b64decode(base64_data)
                            with Image.open(io.BytesIO(image_bytes)) as img:
                                width, height = img.size
                                # detail 설정 확인 (기본값: auto)
                                detail = part.get("image_url", {}).get("detail", "auto")
                                image_tokens = token_estimator.calculate_image_tokens(width, height, detail)
                                total_tokens += image_tokens
                        except Exception as e:
                            # 이미지 처리 실패 시 base64 길이 기반 추정
                            console.print(f"[dim yellow]이미지 토큰 계산 실패, 대략적으로 추정: {e}[/dim yellow]")
                            total_tokens += len(base64_data) // 4
                    else:
                        # URL 이미지는 고정 토큰 사용
                        total_tokens += 85  # low detail 기본값
                
                elif part_type == "file":
                    # PDF 파트
                    file_data = part.get("file", {})
                    file_content = file_data.get("file_data", "")
                    
                    if "base64," in file_content:
                        # PDF는 텍스트 추출이 복잡하므로 대략적 추정
                        base64_data = file_content.split("base64,")[1]
                        # PDF는 대략 1KB당 3토큰으로 추정
                        pdf_size_kb = len(base64.b64decode(base64_data)) / 1024
                        total_tokens += int(pdf_size_kb * 3)
                    else:
                        # 기본값
                        total_tokens += 1000
        
        # 메시지 구조 오버헤드 (약 10-20 토큰)
        total_tokens += 10
        
        return total_tokens
    
    # 메시지별 토큰 계산 및 캐싱
    message_tokens = []
    for msg in messages:
        tokens = calculate_message_tokens(msg)
        message_tokens.append((msg, tokens))
    
    # 가장 최근 메시지부터 선택하여 토큰 제한 내에 맞추기
    trimmed_messages = []
    current_tokens = 0
    
    for msg, tokens in reversed(message_tokens):
        if current_tokens + tokens > max_tokens:
            break
        trimmed_messages.append(msg)
        current_tokens += tokens

    if not trimmed_messages and messages:
        # 가장 최근 메시지의 토큰 수 확인
        last_msg, last_tokens = message_tokens[-1]
        
        console.print(Panel.fit(
            f"[bold red]⚠️ 컨텍스트 초과 경고[/bold red]\n\n"
            f"현재 입력이 모델의 토큰 제한을 초과했습니다:\n"
            f"• 마지막 메시지: {last_tokens:,} 토큰\n"
            f"• 허용된 제한: {max_tokens:,} 토큰\n"
            f"• 초과량: {last_tokens - max_tokens:,} 토큰\n\n"
            f"[yellow]제안사항:[/yellow]\n"
            f"1. 첨부 파일 크기를 줄이거나 개수를 줄여주세요\n"
            f"2. 질문을 더 간결하게 작성해주세요\n"
            f"3. /reset으로 세션을 초기화하고 다시 시도하세요",
            title="[red]토큰 제한 초과[/red]",
            border_style="red"
        ))
        
        # 최소한 시스템 메시지라도 보내기 위해 빈 리스트 대신 
        # 간단한 오류 메시지 반환
        return [{
            "role": "user",
            "content": "이전 컨텍스트가 너무 커서 제거되었습니다. 새로운 대화를 시작합니다."
        }]
    
    # 토큰 사용량 로깅
    if len(trimmed_messages) < len(messages):
        removed_count = len(messages) - len(trimmed_messages)
        console.print(
            f"[dim]컨텍스트 트리밍: {removed_count}개 메시지 제거 "
            f"(사용: {current_tokens:,}/{max_tokens:,} 토큰)[/dim]"
        )
    
    return list(reversed(trimmed_messages))

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

class PathCompleterWrapper(Completer):
    """
    PathCompleter를 /files 명령어에 맞게 감싸는 최종 완성 버전.
    스페이스로 구분된 여러 파일 입력을 완벽하게 지원합니다.
    """
    def __init__(self, command_prefix: str, path_completer: Completer):
        self.command_prefix = command_prefix
        self.path_completer = path_completer

    def get_completions(self, document: Document, complete_event):
        # 1. 사용자가 "/files " 뒤에 있는지 확인합니다.
        #    커서 위치가 명령어 길이보다 짧으면 자동 완성을 시도하지 않습니다.
        if document.cursor_position < len(self.command_prefix):
            return

        # 2. 커서 바로 앞의 '단어'를 가져옵니다. 이것이 핵심입니다.
        # WORD=True 인자는 슬래시(/)나 점(.)을 단어의 일부로 인식하게 합니다.
        # 예: "/files main.py src/co" -> "src/co"
        word_before_cursor = document.get_word_before_cursor(WORD=True)

        # 3. 만약 단어가 없다면 (예: "/files main.py ") 아무것도 하지 않습니다.
        #if not word_before_cursor:
        #    return

        # 4. '현재 단어'만을 내용으로 하는 가상의 Document 객체를 만듭니다.
        doc_for_path = Document(
            text=word_before_cursor,
            cursor_position=len(word_before_cursor)
        )

        # 5. 이 가상 문서를 PathCompleter에게 전달합니다.
        #    PathCompleter는 이제 'src/co'에 대한 제안('src/components/')을 올바르게 생성합니다.
        yield from self.path_completer.get_completions(doc_for_path, complete_event)


class AttachedFileCompleter(Completer):
    """
    오직 '첨부된 파일 목록' 내에서만 경로 자동완성을 수행하는 전문 Completer.
    WordCompleter가 겪는 경로 관련 '단어' 인식 문제를 완벽히 해결합니다.
    """
    def __init__(self, attached_relative_paths: List[str]):
        self.attached_paths = sorted(list(set(attached_relative_paths)))

    def get_completions(self, document: Document, complete_event):
        # 1. 사용자가 현재 입력 중인 '단어'를 가져옵니다. (WORD=True로 경로 문자 인식)
        word_before_cursor = document.get_word_before_cursor(WORD=True)
        
        if not word_before_cursor:
            return

        # 2. 첨부된 모든 경로 중에서, 현재 입력한 단어로 '시작하는' 경로를 모두 찾습니다.
        for path in self.attached_paths:
            if path.startswith(word_before_cursor):
                # 3. 찾은 경로를 Completion 객체로 만들어 반환합니다.
                #    start_position=-len(word_before_cursor) 는
                #    '현재 입력 중인 단어 전체를 이 completion으로 교체하라'는 의미입니다.
                #    이것이 이 문제 해결의 핵심입니다.
                yield Completion(
                    path,
                    start_position=-len(word_before_cursor),
                    display_meta="[첨부됨]"
                )

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
            try:
                # 1. 자동완성 후보가 될 상대 경로 리스트를 생성합니다.
                relative_paths = [
                    str(Path(p).relative_to(BASE_DIR)) for p in attached_filenames
                ]
                
                # 2. WordCompleter 대신, 우리가 만든 AttachedFileCompleter를 사용합니다.
                self.attached_completer = AttachedFileCompleter(relative_paths)

            except ValueError:
                # BASE_DIR 외부 경로 등 예외 발생 시, 전체 경로를 사용
                self.attached_completer = AttachedFileCompleter(attached_filenames)
            '''
            relative_paths = [
                str(Path(p).relative_to(BASE_DIR)) for p in attached_filenames
            ]
            self.attached_completer =  FuzzyCompleter(
                WordCompleter(relative_paths, ignore_case=True)
            )
            '''
            #self.attached_completer =  FuzzyCompleter(WordCompleter(attached_filenames, ignore_case=True))
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

def select_model(current_model: str, current_context: int) -> Tuple[str, int]:
    if not MODELS_FILE.exists():
        console.print(f"[yellow]{MODELS_FILE} 가 없습니다. 기본 모델을 유지합니다.[/yellow]")
        return current_model, current_context
    
    models_with_context: List[Dict[str, Any]] = []
    try:
        lines = MODELS_FILE.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split()
                model_id = parts[0]
                context_length = DEFAULT_CONTEXT_LENGTH
                if len(parts) >= 2:
                    try:
                        context_length = int(parts[1])
                    except ValueError:
                        # 숫자로 변환 실패 시 기본값 사용
                        pass
                models_with_context.append({"id": model_id, "context": context_length})
    except IOError as e:
        console.print(f"[red]모델 파일({MODELS_FILE}) 읽기 오류: {e}[/red]")
        return current_model, current_context

    header_text = urwid.Text([
        "모델 선택 (Enter로 선택, Q로 취소)\n",
        ("info", f"현재 모델: {current_model.split('/')[-1]} (CTX: {current_context:,})")
    ])
    items = [header_text, urwid.Divider()]
    
    body: List[urwid.Widget] = []
    result: List[Optional[Dict[str, Any]]] = [None] 
    
    def raise_exit(val: Optional[Dict[str, Any]]) -> None:
        result[0] = val
        raise urwid.ExitMainLoop()

    for m in models_with_context:
        model_id = m["id"]
        context_len = m["context"]
        disp = model_id.split("/")[-1]

        label = f"   {disp:<40} [CTX: {context_len:,}]"
        # 현재 모델 강조
        if model_id == current_model:
            label = f"-> {disp:<40} [CTX: {context_len:,}] (현재)"

        btn = urwid.Button(label)
        urwid.connect_signal(btn, "click", lambda _, model_info=m: raise_exit(model_info))
        body.append(urwid.AttrMap(btn, None, focus_map="myfocus"))

    listbox = urwid.ListBox(urwid.SimpleFocusListWalker(items + body))
    
    def unhandled(key: str) -> None:
        if isinstance(key, str) and key.lower() == "q":
            raise_exit(None) # 취소 시 None 전달
            
    urwid.MainLoop(listbox, palette=PALETTE, unhandled_input=unhandled).run()
    
    # TUI 종료 후 결과 처리
    if result[0]:
        # 선택된 모델이 있으면 해당 모델의 ID와 컨텍스트 길이 반환
        return result[0]['id'], result[0]['context']
    else:
        # 취소했으면 기존 모델 정보 그대로 반환
        return current_model, current_context

class ModelSearcher:
    """OpenRouter 모델을 검색하고 TUI를 통해 선택하여 `ai_models.txt`를 업데이트합니다."""
    
    API_URL = "https://openrouter.ai/api/v1/models"
    
    def __init__(self):
        self.all_models_map: Dict[str, Dict[str, Any]] = {}
        self.selected_ids: Set[str] = set()
        self.expanded_ids: Set[str] = set()
        self.display_models: List[Dict[str, Any]] = []

    def _fetch_all_models(self) -> bool:
        try:
            with console.status("[cyan]OpenRouter에서 모델 목록을 가져오는 중...", spinner="dots"):
                response = requests.get(self.API_URL, timeout=10)
                response.raise_for_status()
            self.all_models_map = {m['id']: m for m in response.json().get("data", [])}
            return True if self.all_models_map else False
        except requests.RequestException as e:
            console.print(f"[red]API 실패: {e}[/red]"); return False

    def _get_existing_model_ids(self) -> Set[str]:
        if not MODELS_FILE.exists(): return set()
        try:
            lines = MODELS_FILE.read_text(encoding="utf-8").splitlines()
            return {line.strip().split()[0] for line in lines if line.strip() and not line.strip().startswith("#")}
        except Exception: return set()

    def _save_models(self):
        try:
            final_ids = sorted(list(self.selected_ids))
            with MODELS_FILE.open("w", encoding="utf-8") as f:
                f.write("# OpenRouter.ai Models (gpt-cli auto-generated)\n\n")
                for model_id in final_ids:
                    model_data = self.all_models_map.get(model_id, {})
                    #f.write(f"{model_id} {model_data.get('context_length', 0)}\n")
                    context_len = model_data.get('context_length') or DEFAULT_CONTEXT_LENGTH
                    f.write(f"{model_id} {context_len}\n")
            console.print(f"[green]성공: {len(final_ids)}개 모델로 '{MODELS_FILE}' 업데이트 완료.[/green]")
        except Exception as e:
            console.print(f"[red]저장 실패: {e}[/red]")
            
    class Collapsible(urwid.Pile):
        def __init__(self, model_data, is_expanded, is_selected, in_existing):
            self.model_id = model_data.get('id', 'N/A')
            
            checked = "✔" if is_selected else " "
            arrow = "▼" if is_expanded else "▶"
            context_length = model_data.get("context_length")
            context_str = f"[CTX: {context_length:,}]" if context_length else "[CTX: N/A]"

            
            style = "key" if in_existing else "default"
            
            # 첫 번째 라인: 포커스 시 배경색이 변경되도록 AttrMap으로 감쌈
            line1_cols = urwid.Columns([
                ('pack', urwid.Text(f"[{checked}] {arrow}")),
                ('weight', 1, urwid.Text(self.model_id)),
                ('pack', urwid.Text(context_str)),
            ], dividechars=1)
            line1_wrapped = urwid.AttrMap(line1_cols, style, focus_map='myfocus')

            widget_list = [line1_wrapped]
            
            if is_expanded:
                desc_text = model_data.get('description') or "No description available."
                desc_content = urwid.Text(desc_text, wrap='space')
                padded_content = urwid.Padding(desc_content, left=4, right=2)
                desc_box = urwid.LineBox(padded_content, tlcorner=' ', tline=' ', lline=' ', 
                                         trcorner=' ', blcorner=' ', rline=' ', bline=' ', brcorner=' ')
                # 설명 부분은 포커스와 상관없이 항상 동일한 배경
                widget_list.append(urwid.AttrMap(desc_box, 'info_bg'))

            super().__init__(widget_list)

        def selectable(self) -> bool:
            return True

    def start(self, keywords: List[str]):
        if not self._fetch_all_models(): return

        existing_ids = self._get_existing_model_ids()
        self.selected_ids = existing_ids.copy()

        model_ids_to_display = set(self.all_models_map.keys()) if not keywords else existing_ids.copy()
        if keywords:
            for mid, mdata in self.all_models_map.items():
                search_text = f"{mid} {mdata.get('name', '')}".lower()
                if any(kw.lower() in search_text for kw in keywords):
                    model_ids_to_display.add(mid)

        self.display_models = [self.all_models_map[mid] for mid in sorted(list(model_ids_to_display)) if mid in self.all_models_map]
        self.display_models.sort(key=lambda m: (m['id'] not in existing_ids, m.get('id', '')))

        list_walker = urwid.SimpleFocusListWalker([])
        listbox = urwid.ListBox(list_walker)

        def refresh_list():
            try:
                current_focus_pos = listbox.focus_position
            except IndexError:
                current_focus_pos = 0 # 리스트가 비어있으면 0으로 초기화

            widgets = [
                self.Collapsible(m, m['id'] in self.expanded_ids, m['id'] in self.selected_ids, m['id'] in existing_ids)
                for m in self.display_models
            ]
            list_walker[:] = widgets #! .contents가 아니라 슬라이싱으로 할당
            
            if widgets:
                listbox.focus_position = min(current_focus_pos, len(widgets) - 1)
        
        refresh_list()
        listbox.focus_position = 0

        def keypress(key: str):
            if isinstance(key, tuple): return
            try:
                model_widget = list_walker[listbox.focus_position]
                model_id = model_widget.model_id
            except (IndexError, AttributeError): return

            if key == 'enter':
                self.expanded_ids.symmetric_difference_update({model_id})
            elif key == ' ':
                self.selected_ids.symmetric_difference_update({model_id})
            elif key.lower() == 'a':
                self.selected_ids.update(m['id'] for m in self.display_models)
            elif key.lower() == 'n':
                for m in self.display_models:
                    self.selected_ids.discard(m['id'])
        
        help_text = urwid.Text([ "명령어: ", ("key", "Enter"),":설명 ", ("key", "Space"),":선택 ", ("key", "A/N"),":전체선택/해제 ", ("key", "S"),":저장 ", ("key", "Q"),":취소" ])
        header_title = lambda: f"{len(self.display_models)}개 모델 표시됨 (전체 {len(self.selected_ids)}개 선택됨)"
        header = urwid.Pile([urwid.Text(header_title()), help_text, urwid.Divider()])
        frame = urwid.Frame(listbox, header=header)
        
        save_triggered = False
        def exit_handler(key):
            nonlocal save_triggered
            if isinstance(key, tuple): return
            if key.lower() == 's':
                save_triggered = True; raise urwid.ExitMainLoop()
            elif key.lower() == 'q':
                raise urwid.ExitMainLoop()
            
            keypress(key)
            refresh_list()
            #header.widget_list[0].set_text(header_title())
            header.contents[0][0].set_text(header_title())
        
        screen = urwid.raw_display.Screen()
        main_loop = urwid.MainLoop(frame, palette=PALETTE, screen=screen, unhandled_input=exit_handler)
        
        try: main_loop.run()
        finally: screen.clear()

        if save_triggered:
            self._save_models()
        else:
            console.print("[dim]모델 선택이 취소되었습니다.[/dim]")
        

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


def save_session(
    name: str, 
    msgs: List[Dict[str, Any]], 
    model: str, 
    context_length: int,
    usage_history: List[Dict] = None  # 추가
) -> None:
    filtered_history = [u for u in (usage_history or []) if u is not None]

    save_json(SESSION_FILE(name), {
        "messages": msgs,
        "model": model,
        "context_length": context_length,
        "usage_history": usage_history or [],  # 토큰 사용 기록
        "total_usage": {  # 누적 통계
            "total_prompt_tokens": sum(u.get("prompt_tokens", 0) for u in (usage_history or [])),
            "total_completion_tokens": sum(u.get("completion_tokens", 0) for u in (usage_history or [])),
            "total_tokens": sum(u.get("total_tokens", 0) for u in (usage_history or [])),
        },
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    })


def load_favorites() -> Dict[str, str]:
    return load_json(FAVORITES_FILE, {})


def save_favorite(name: str, prompt: str) -> None:
    favs = load_favorites()
    favs[name] = prompt
    save_json(FAVORITES_FILE, favs)


def ignore_spec() -> Optional[PathSpec]:
    """
    전역(.gptignore_default) 및 프로젝트별(.gptignore) 무시 규칙을
    결합하여 최종 PathSpec 객체를 생성합니다.
    """
    default_patterns = []
    if DEFAULT_IGNORE_FILE.exists():
        default_patterns = DEFAULT_IGNORE_FILE.read_text().splitlines()

    user_patterns = []
    if IGNORE_FILE.exists():
        user_patterns = IGNORE_FILE.read_text().splitlines()
    
    # 1. 전역 규칙과 프로젝트 규칙을 합치고, set으로 중복을 제거합니다.
    # combined_patterns = set(default_patterns + user_patterns)

    # 1. 전역 규칙과 프로젝트 규칙을 합치되, 순서를 유지하며 중복을 제거합니다.
    #    gitignore 규칙은 뒤에 오는 패턴이 앞의 패턴을 덮어쓸 수 있으므로
    #    set()을 사용하면 패턴 순서가 뒤섞여 "!" 패턴이 올바르게 동작하지 않습니다.
    #    따라서 dict.fromkeys를 활용해 입력 순서를 보존하며 중복만 제거합니다.
    combined_patterns = list(dict.fromkeys(default_patterns + user_patterns))
    
    # 2. 빈 줄이나 주석(#)을 필터링하여 최종 패턴 리스트를 만듭니다.
    final_patterns = [
        p.strip() for p in combined_patterns if p.strip() and not p.strip().startswith("#")
    ]
    
    if not final_patterns:
        return None
        
    return PathSpec.from_lines("gitwildmatch", final_patterns)

def is_ignored(p: Path, spec: Optional[PathSpec]) -> bool:
    """
    주어진 경로가 ignore spec에 의해 무시되어야 하는지 확인합니다.
    디렉터리일 경우 경로 끝에 '/'를 추가하여 정확도를 높입니다.
    """
    if not spec:
        return False
    
    try:
        # 1. 프로젝트 루트(BASE_DIR)에 대한 상대 경로를 계산합니다.
        relative_path_str = p.relative_to(BASE_DIR).as_posix()
    except ValueError:
        # p가 BASE_DIR 외부에 있는 경로일 경우, 무시 규칙의 대상이 아니므로 False를 반환합니다.
        return False

    # 2. [핵심 수정] 경로가 디렉터리인 경우, 명시적으로 끝에 슬래시를 추가합니다.
    #    이렇게 해야 "gpt_codes/" 같은 디렉터리 전용 패턴이 올바르게 동작합니다.
    if p.is_dir() and not relative_path_str.endswith('/'):
        relative_path_str += '/'
        
    # 3. 수정된 경로 문자열로 매칭을 수행합니다.
    return spec.match_file(relative_path_str)


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

SENSITIVE_KEYS = ["secret", "private", "key", "api"]

PALETTE = [                               
    ('key', 'yellow', 'black'),
    ('info', 'dark gray', 'black'),
    ('myfocus', 'black', 'light gray'),
    ('info_bg', '', 'dark gray'), 
    ('info_fg', 'dark gray', ''),
    # Diff 뷰를 위한 스타일 추가
    ('diff_add', 'black', 'dark green'),
    ('diff_remove', 'black', 'dark red'),
    ('header', 'white', 'dark blue'),
     # Response 관련 - 배경색 제거 또는 black으로 변경
    ('response_header', 'white,bold', 'black'),  # 'dark blue' -> 'black'
    ('response_selected', 'black', 'light gray'),  # 선택된 항목만 회색 배경
    ('response_normal', 'light gray', 'black'),   # 일반 항목은 검정 배경
    
    # 파일 관련
    ('file_selected', 'black', 'light gray'),
    ('file_normal', 'light gray', 'black'),
    
    # Preview 관련
    ('preview', 'light gray', 'black'),
    ('preview_border', 'dark gray', 'black')
]



class CodeDiffer:
    def __init__(self, attached_files: List[str], session_name: str, messages: List[Dict]):
        self.attached_files = [Path(p) for p in attached_files]
        self.session_name = session_name
        self.expanded_items: Set[str] = set()
        self.selected_for_diff: List[Dict] = []
        self.previewing_item_id: Optional[str] = None
        self.preview_offset = 0
        self.preview_lines_per_page = 10  # 한 페이지에 표시할 라인 수
        
        self.display_items: List[Dict] = []
        self.response_files: Dict[int, List[Path]] = self._scan_response_files()

        self.list_walker = urwid.SimpleFocusListWalker([])
        self.listbox = urwid.ListBox(self.list_walker)
        self.preview_text = urwid.Text("")
        self.preview_box = urwid.LineBox(self.preview_text, title="Preview")
        
        # 주 레이아웃은 Pile을 사용하되, 미리보기는 동적으로 추가/제거
        self.main_pile = urwid.Pile([self.listbox])
        
        # 키 도움말 업데이트
        footer_text = "↑/↓:이동 | Enter:확장/프리뷰 | Space:선택 | D:Diff | Q:종료 | PgUp/Dn:스크롤"
        self.footer = urwid.AttrMap(urwid.Text(footer_text), 'header')
        
        self.frame = urwid.Frame(self.main_pile, footer=self.footer)
        self.main_loop: Optional[urwid.MainLoop] = None
        
        # diff 뷰 관련 추가
        self.diff_scroll_offset = 0
        self.diff_widget: Optional[urwid.Widget] = None

    def _scan_response_files(self) -> Dict[int, List[Path]]:
        if not CODE_OUTPUT_DIR.is_dir(): return {}
        pattern = re.compile(rf"codeblock_{re.escape(self.session_name)}_(\d+)_.*")
        msg_files: Dict[int, List[Path]] = {}
        for p in CODE_OUTPUT_DIR.glob(f"codeblock_{self.session_name}_*"):
            match = pattern.match(p.name)
            if match:
                msg_id = int(match.group(1))
                if msg_id not in msg_files: msg_files[msg_id] = []
                msg_files[msg_id].append(p)
        return {k: sorted(v) for k, v in sorted(msg_files.items(), reverse=True)}

    def _render_all(self, keep_focus=True):
        pos = 0
        if keep_focus:
            try: pos = self.listbox.focus_position
            except IndexError: pos = 0
        
        self.display_items = []
        widgets = []
        if self.attached_files:
            section_id = "local_files"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(urwid.AttrMap(urwid.SelectableIcon(f"{arrow} Current Local Files ({len(self.attached_files)})"), 'header', 'header'))
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in self.attached_files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"local_{p.name}"
                    widgets.append(urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus'))
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "local"})

        for msg_id, files in self.response_files.items():
            section_id = f"response_{msg_id}"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(urwid.AttrMap(urwid.SelectableIcon(f"{arrow} Response #{msg_id}"), 'header', 'header'))
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"response_{msg_id}_{p.name}"
                    widgets.append(urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus'))
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "response", "msg_id": msg_id})

        self.list_walker[:] = widgets
        if widgets: self.listbox.focus_position = min(pos, len(widgets) - 1)
        self._update_preview()

    def _update_preview(self):
        item_id = self.previewing_item_id
        is_previewing = len(self.main_pile.contents) > 1

        if not item_id:
            if is_previewing: self.main_pile.contents.pop()
            return

        item_data = next((item for item in self.display_items if item.get('id') == item_id), None)
        if not (item_data and item_data['type'] == 'file'):
            if is_previewing: self.main_pile.contents.pop()
            return
            
        try:
            content = item_data['path'].read_text(encoding='utf-8', errors='ignore').splitlines()
            
            # 스크롤 가능한 범위 계산
            total_lines = len(content)
            end_line = min(self.preview_offset + self.preview_lines_per_page, total_lines)
            
            preview_text = "\n".join(content[self.preview_offset : end_line])
            
            # 스크롤 정보를 타이틀에 표시
            scroll_info = f" [{self.preview_offset+1}-{end_line}/{total_lines}]"
            title = f"Preview: {item_data['path'].name}{scroll_info}"
            
            self.preview_box.set_title(title)
            self.preview_text.set_text(preview_text)
            
            if not is_previewing:
                self.main_pile.contents.insert(0, (self.preview_box, self.main_pile.options('pack')))
        except Exception as e:
            self.preview_text.set_text(f"[Error]: {e}")
            if not is_previewing:
                self.main_pile.contents.insert(0, (self.preview_box, self.main_pile.options('pack')))
    
    def handle_input(self, key):
        if not isinstance(key, str): return

        # 프리뷰 스크롤 처리
        if self.previewing_item_id and key in ('page up', 'page down'):
            try:
                item_data = next(item for item in self.display_items if item.get('id') == self.previewing_item_id)
                content = item_data['path'].read_text('utf-8').splitlines()
                total_lines = len(content)
                max_offset = max(0, total_lines - self.preview_lines_per_page)
                
                if key == 'page down':
                    # 한 페이지 아래로 스크롤
                    self.preview_offset = min(max_offset, self.preview_offset + self.preview_lines_per_page)
                elif key == 'page up':
                    # 한 페이지 위로 스크롤
                    self.preview_offset = max(0, self.preview_offset - self.preview_lines_per_page)
                
                self._update_preview()
            except (StopIteration, IOError): pass
            return

        try:
            pos = self.listbox.focus_position
            item = self.display_items[pos]
        except IndexError:
            self.frame.keypress(self.main_loop.screen_size, key)
            return

        if key == 'q': raise urwid.ExitMainLoop()
        
        elif key == 'enter':
            if item['type'] == 'section':
                self.expanded_items.symmetric_difference_update({item['id']})
            elif item['type'] == 'file':
                item_id = item['id']
                self.previewing_item_id = None if self.previewing_item_id == item_id else item_id
                self.preview_offset = 0
            self._render_all()
        
        elif key == ' ':
            if item['type'] == 'file':
                self.handle_selection(item)
                self._render_all(keep_focus=True)

        elif key.lower() == 'd':
            if len(self.selected_for_diff) == 2: self._show_diff_view()
            else: self.footer.original_widget.set_text(f"[!] 2개 항목을 선택해야 diff가 가능합니다. (현재 {len(self.selected_for_diff)}개 선택됨)")
        
        else:
            self.frame.keypress(self.main_loop.screen_size, key)
    
    def handle_selection(self, item):
        is_in_list = any(s['id'] == item['id'] for s in self.selected_for_diff)
        if not is_in_list:
            if len(self.selected_for_diff) >= 2:
                self.footer.original_widget.set_text("[!] 2개 이상 선택할 수 없습니다.")
                return
            if item.get('source') == 'local':
                self.selected_for_diff = [s for s in self.selected_for_diff if s.get('source') != 'local']
            self.selected_for_diff.append(item)
        else:
            self.selected_for_diff = [s for s in self.selected_for_diff if s['id'] != item['id']]
        self.footer.original_widget.set_text(f" {len(self.selected_for_diff)}/2 선택됨. 'd' 키를 눌러 diff를 실행하세요.")
    
    def _show_diff_view(self):
        if len(self.selected_for_diff) != 2: return
        item1, item2 = self.selected_for_diff
        
        # 시간 순서로 정렬 (local이나 더 오래된 것이 왼쪽)
        if item1.get("source") == "local" or item1.get("msg_id", 0) < item2.get("msg_id", 0):
            old_item, new_item = item1, item2
        else:
            old_item, new_item = item2, item1

        try:
            old_content = old_item['path'].read_text('utf-8').splitlines()
            new_content = new_item['path'].read_text('utf-8').splitlines()
        except Exception as e:
            self.footer.original_widget.set_text(f"Error: {e}")
            return
            
        # unified diff 생성
        diff = list(difflib.unified_diff(
            old_content, 
            new_content, 
            fromfile=f"a/{old_item['path'].name}", 
            tofile=f"b/{new_item['path'].name}", 
            lineterm='',
            n=3  # 컨텍스트 라인 수
        ))
        
        if not diff:
            self.footer.original_widget.set_text("두 파일이 동일합니다.")
            return
        
        # diff 라인을 위젯으로 변환 (배경색 없이)
        diff_widgets = []
        for line in diff:
            if line.startswith('+++'):
                # 새 파일 헤더
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('light green', ''))
            elif line.startswith('---'):
                # 원본 파일 헤더
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('light red', ''))
            elif line.startswith('@@'):
                # 위치 정보
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('yellow', ''))
            elif line.startswith('+'):
                # 추가된 라인 (텍스트 색상만)
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('light green', ''))
            elif line.startswith('-'):
                # 삭제된 라인 (텍스트 색상만)
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('light red', ''))
            else:
                # 컨텍스트 라인
                widget = urwid.AttrMap(urwid.Text(line), urwid.AttrSpec('light gray', ''))
            
            diff_widgets.append(widget)

        # diff 뷰를 위한 ListBox 생성
        self.diff_scroll_offset = 0
        diff_walker = urwid.SimpleFocusListWalker(diff_widgets)
        diff_listbox = urwid.ListBox(diff_walker)
        
        # 스크롤 정보를 표시할 헤더
        header_text = urwid.Text(f"Diff: {old_item['path'].name} → {new_item['path'].name}")
        header = urwid.AttrMap(header_text, 'header')
        
        # 푸터에 키 도움말
        footer_text = "PgUp/Dn: 스크롤 | Q: 닫기"
        diff_footer = urwid.AttrMap(urwid.Text(footer_text), 'header')
        
        diff_frame = urwid.Frame(diff_listbox, header=header, footer=diff_footer)

        # 원본 위젯 저장
        original_widget = self.main_loop.widget
        
        def diff_input_handler(key):
            if isinstance(key, str):
                if key.lower() == 'q':
                    # 원본 뷰로 복귀
                    self.main_loop.widget = original_widget
                    self.main_loop.unhandled_input = self.handle_input
                elif key == 'page up':
                    # diff 뷰 스크롤 업
                    try:
                        current_pos = diff_listbox.focus_position
                        new_pos = max(0, current_pos - 10)
                        diff_listbox.focus_position = new_pos
                    except IndexError:
                        pass
                elif key == 'page down':
                    # diff 뷰 스크롤 다운
                    try:
                        current_pos = diff_listbox.focus_position
                        new_pos = min(len(diff_widgets) - 1, current_pos + 10)
                        diff_listbox.focus_position = new_pos
                    except IndexError:
                        pass
                else:
                    # 기본 키 처리
                    diff_frame.keypress(self.main_loop.screen_size, key)
        
        # diff 뷰로 전환
        self.main_loop.unhandled_input = diff_input_handler
        self.main_loop.widget = diff_frame

    def start(self):
        self._render_all(keep_focus=False)
        self.main_pile.focus_item = self.listbox
        
        screen = urwid.raw_display.Screen()
        self.main_loop = urwid.MainLoop(self.frame, palette=PALETTE, screen=screen, unhandled_input=self.handle_input)
        
        try: self.main_loop.run()
        finally: screen.clear()

def read_plain_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"[파일 읽기 실패: {e}]"


def encode_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


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

def save_code_blocks(blocks: Sequence[Tuple[str, str]], current_session_name: str, current_msg_id: int) -> List[Path]:
    CODE_OUTPUT_DIR.mkdir(exist_ok=True)
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
        
        #timestamp = time.strftime("%Y%m%d_%H%M%S")
        p = CODE_OUTPUT_DIR / f"codeblock_{current_session_name}_{current_msg_id}_{i}.{ext}"
        cnt = 1
        while p.exists():
            p = CODE_OUTPUT_DIR / f"codeblock_{current_session_name}_{current_msg_id}_{i}_{cnt}.{ext}"
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


def display_attachment_tokens(attached_files: List[str], compact_mode: bool = False) -> None:
    """첨부 파일들의 토큰 사용량을 시각적으로 표시"""
    if not attached_files:
        return
    
    table = Table(title="📎 첨부 파일 토큰 분석", box=ROUNDED, title_style="bold cyan")
    table.add_column("파일명", style="bright_white", width=30)
    table.add_column("타입", style="yellow", width=10)
    table.add_column("크기", style="green", width=12, justify="right")
    table.add_column("예상 토큰", style="cyan", width=15, justify="right")
    
    total_tokens = 0
    file_details = []
    
    for file_path in attached_files:
        path = Path(file_path)
        if not path.exists():
            continue
            
        # 파일 크기
        file_size = path.stat().st_size
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"
        
        # 파일 타입 판별
        if path.suffix.lower() in IMG_EXTS:
            file_type = "🖼️ 이미지"
            tokens = token_estimator.estimate_image_tokens(path)
        elif path.suffix.lower() == PDF_EXT:
            file_type = "📄 PDF"
            tokens = token_estimator.estimate_pdf_tokens(path)
        else:
            file_type = "📝 텍스트"
            try:
                text = read_plain_file(path)
                tokens = token_estimator.count_text_tokens(text)
            except:
                tokens = 0
        
        # 파일명 줄이기 (너무 길면)
        display_name = path.name
        if len(display_name) > 28:
            display_name = display_name[:25] + "..."
        
        table.add_row(display_name, file_type, size_str, f"{tokens:,}")
        total_tokens += tokens
        file_details.append((path.name, tokens))
    
    # 요약 행 추가
    table.add_section()
    table.add_row(
        "[bold]합계[/bold]", 
        f"[bold]{len(attached_files)}개[/bold]", 
        "", 
        f"[bold yellow]{total_tokens:,}[/bold yellow]"
    )
    
    console.print(table)
    
    if compact_mode:
        console.print(
            "[dim green]📦 Compact 모드 활성화됨: "
            "과거 메시지의 첨부파일이 자동으로 압축됩니다.[/dim green]"
        )

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
    model_context_limit: int,
    pretty_print: bool = True,
    current_attached_files: List[str] = None
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

    target_max_tokens = int(model_context_limit * CONTEXT_TRIM_RATIO)
    final_messages = trim_messages_by_tokens(messages, target_max_tokens)
    
    if len(final_messages) < len(messages):
        console.print(f"[dim]컨텍스트 관리: 모델의 토큰 제한({target_max_tokens})에 맞추기 위해 대화 기록을 {len(messages)}개에서 {len(final_messages)}개로 조정했습니다.[/dim]")

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
    usage_info = None

    # reasoning 지원 모델 감지 및 extra_body 설정
    use_reasoning = True #any(x in model.lower() for x in ['o1-', 'reasoning'])
    extra_body = {'reasoning': {}} if use_reasoning else {}

    with console.status("[cyan]Loading...", spinner="dots"):
        try:
            stream = client.chat.completions.create(
                model=model_online,
                messages=[system_prompt] + final_messages,
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
                # usage 정보 캡처 추가
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                        "timestamp": time.time(),
                        "model": model
                    }

                if chunk.choices[0].delta and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply += content
                    # 서식 없이 그대로 출력
                    console.print(content, end="", markup=False)
        except StopIteration:
            pass
        finally:
            console.print()  # 마지막 줄바꿈
        return full_reply, usage_info

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

            
            # usage 정보 캡처 (마지막 청크에 포함됨)
            if hasattr(chunk, 'usage') and chunk.usage:
                usage_info = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                    "timestamp": time.time(),
                    "model": model
                }

            delta = chunk.choices[0].delta

            if hasattr(delta, 'reasoning') and delta.reasoning:
                if normal_buffer: console.print(normal_buffer, end="", markup=False); normal_buffer = ""
                
                # with Live(...) as live: 대신 수동 제어로 변경
                live = Live(console=console, auto_refresh=True, refresh_per_second=4, transient=True)
                live.start()  # Live 객체 수동 시작
                try:
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
                finally:
                    # 루프가 어떻게 끝나든 반드시 Live를 중지하고 화면을 정리합니다.
                    live.stop()
                
                # ▼▼▼ 여기가 핵심적인 수정사항입니다 ▼▼▼
                # Live 객체가 화면에서 사라진 후, 다음 출력이 깨지는 것을 방지하기 위해
                # 빈 줄을 한 번 출력하여 터미널의 커서 위치와 상태를 동기화합니다.
                console.line() 
                continue

            if not (delta and delta.content): 
                continue
            
            full_reply += delta.content
            buffer += delta.content
            #full_reply = simple_markdown_to_rich(full_reply)

            while "\n" in buffer: # and not buffer.endswith('\n'):
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
                        
                        live = Live(console=console, auto_refresh=True, refresh_per_second=4)
                        live.start() # Live 수동 시작
                        try:
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

                        finally:
                            # 루프가 정상 종료되거나 예외로 중단되더라도 항상 Live를 중지합니다.
                            # 이렇게 하면 최종 렌더링 결과가 화면에 고정됩니다.
                            live.stop()
                        
                        console.line()
                    else:
                        normal_buffer += line + "\n"

            # 백틱 3개이상 코드 구분을 캐치 못할것을 대비하여 백틱 하나로 끝나면 일단 대기
            if not in_code_block and buffer:
                if buffer.endswith('`'):
                    continue # pass가 아니라 continue여야함
                else:
                    normal_buffer += buffer
                    buffer = ""

            current_time = time.time()
            if normal_buffer and (len(normal_buffer) > 5 or (current_time - last_flush_time > 0.25)):
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
    return full_reply, usage_info


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
/commands                     → 명령어 리스트
/compact_mode                 → 첨부파일 압축 모드 ON/OFF 토글
/pretty_print                 → 고급 출력(Rich) ON/OFF 토글
/last_response                → 마지막 응답 고급 출력으로 다시 보기
/raw                          → 마지막 응답 raw 출력
/select_model                 → 모델 선택 TUI
/search_models gpt grok ...   → 모델 검색 및 `ai_models.txt` 업데이트
/all_files                    → 파일 선택기(TUI)
/files f1 f2 ...              → 수동 파일 지정
/clearfiles                   → 첨부파일 초기화
/mode <dev|general>           → 시스템 프롬프트 모드
/savefav <name>               → 질문 즐겨찾기
/usefav <name>                → 즐겨찾기 사용
/favs                         → 즐겨찾기 목록
/edit                         → 외부 편집기로 긴 질문 작성
/diff_code                    → 코드 블록 비교 뷰어 열기
/reset                        → 세션 리셋 & 자동 백업
/restore                      → 백업에서 세션 복원
/show_context                 → 현재 컨텍스트 사용량 확인
/exit                         → 종료
""".strip()

def convert_to_placeholder_message(msg: Dict) -> Dict:
    """
    메시지의 첨부파일을 플레이스홀더로 변환합니다.
    원본을 수정하지 않고 새로운 딕셔너리를 반환합니다.
    """
    import copy
    
    # 깊은 복사로 원본 보호
    new_msg = copy.deepcopy(msg)
    
    if isinstance(new_msg.get("content"), str):
        return new_msg
    
    text_content = ""
    attachments_info = []
    
    cnt = 0
    for part in new_msg.get("content", []):
        
        if part.get("type") == "text":
            # 0 부분은 파일 첨부가 아님
            if cnt == 0:
                text_content = part.get("text","")
            else:
                attachment_name = part.get("text","").split("\n```")[0].strip().split(":")[1].strip().replace("]","")
                attachments_info.append(f"{attachment_name}")    

        elif part.get("type") == "image_url":
            image_name = part.get("image_url", {}).get("image_name", "이미지")
            attachments_info.append(f"📷 {image_name}")

        elif part.get("type") == "file":
            filename = part.get("file", {}).get("filename", "파일")
            attachments_info.append(f"📄 {filename}")

        cnt += 1
    
    if attachments_info:
        attachment_summary = "[첨부: " + ", ".join(attachments_info) + "]"
        new_msg["content"] = text_content + "\n" + attachment_summary

    else:
        new_msg["content"] = text_content
    
    return new_msg

def get_last_assistant_message(messages: List[Dict[str, Any]]) -> Optional[str]:
    """
    대화 기록에서 가장 최근의 'assistant' 역할을 가진 메시지 내용을 찾아 반환합니다.
    없으면 None을 반환합니다.
    """
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            # content가 문자열인지 확인 후 반환
            if isinstance(content, str):
                return content
    return None

def estimate_message_tokens(messages: List[Dict]) -> int:
    """메시지 리스트의 전체 토큰 수를 추정"""
    total = 0
    for msg in messages:
        if isinstance(msg.get("content"), str):
            total += token_estimator.count_text_tokens(msg["content"])
        elif isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if part.get("type") == "text":
                    total += token_estimator.count_text_tokens(part["text"])
                elif part.get("type") == "image_url":
                    # base64 부분 추출 후 토큰 추정
                    url = part.get("image_url", {}).get("url", "")
                    if "base64," in url:
                        base64_part = url.split("base64,")[1]
                        total += token_estimator.estimate_image_tokens(base64_part)
                elif part.get("type") == "file":
                    # PDF 등의 파일
                    total += 1000  # 기본값
    return total

def chat_mode(name: str, copy_clip: bool) -> None:
    # 1. 초기 모드는 항상 'dev'로 고정
    mode = "dev"
    current_session_name = name
    compact_mode = COMPACT_ATTACHMENTS
    
    data = load_session(current_session_name)
    messages: List[Dict[str, Any]] = data["messages"]
    model = data["model"]

    model_context: int = data.get("context_length", DEFAULT_CONTEXT_LENGTH) 
    usage_history: List[Dict] = data.get("usage_history", [])

    attached: List[str] = []
    last_resp = ""
    pretty_print_enabled = True 

    # 1. 기본 명령어 자동 완성기 생성
    command_list = [cmd.split()[0] for cmd in COMMANDS.strip().split('\n')]
    command_completer = FuzzyCompleter(WordCompleter(command_list, ignore_case=True))

    # 1-2. .gptignore를 존중하는 파일 목록 생성 -> 파일 완성기
    spec = ignore_spec()
    def path_filter(filename: str) -> bool:
        #return not is_ignored(Path(filename), spec)
        p = Path(filename)
        absolute_p = p.resolve()
        return not is_ignored(absolute_p, spec)
        
    real_path_completer = PathCompleter(
        file_filter=path_filter, 
        expanduser=True, 
        only_directories=False
    )
    
    # 3. PathCompleter를 우리만의 래퍼로 감쌉니다.
    wrapped_file_completer = PathCompleterWrapper(
        command_prefix="/files ", 
        path_completer=real_path_completer
    )

    # 4. 최종 조건부 완성기를 설정합니다.
    conditional_completer = ConditionalCompleter(
        command_completer=command_completer,
        file_completer=wrapped_file_completer  # 교체!
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
    session.prompt_continuation = " "
    session.completer = conditional_completer
    session.key_bindings = key_bindings
    session.complete_while_typing = True

    console.print(Panel.fit(COMMANDS, title="[yellow]/명령어[/yellow]"))
    console.print(f"[cyan]세션('{current_session_name}') 시작 – 모델: {model}[/cyan]", highlight=False)

    
    while True:
        try:
            # ✅ 루프 시작 시, 최신 'attached' 목록으로 completer를 업데이트!
            #attached_filenames = [Path(p).name for p in attached]
            conditional_completer.update_attached_file_completer(attached)
            files_info = f"| {len(attached)} files " if attached else ""
            mode_indicator = f"| {mode}"
            compact_indicator = " 📦" if compact_mode else ""  # 압축 모드 아이콘
            prompt_text = f"[ {current_session_name} {mode_indicator} {files_info}{compact_indicator}] Q>> "
            user_in = session.prompt(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not user_in:
            continue

        # ── 명령어 처리
        if user_in.strip() == "/edit":
            # 1. 임시 파일을 생성합니다.
            temp_file_path = BASE_DIR / ".gpt_prompt_edit.tmp"
            temp_file_path.touch()

            # 2. 시스템 기본 편집기 (EDITOR 환경 변수) 또는 vim을 실행합니다.
            if sys.platform == 'win32':
                default_editor = 'notepad'
            else:
                default_editor = 'vim'

            editor = os.environ.get("EDITOR", "vim")
            console.print(f"[dim]외부 편집기 ({editor})를 실행합니다...[/dim]")

            try:
                # 사용자가 편집기를 닫을 때까지 대기합니다.
                subprocess.run([editor, str(temp_file_path)], check=True)
                
                # 3. 편집이 끝나면 파일 내용을 읽어옵니다.
                user_in = temp_file_path.read_text(encoding="utf-8").strip()
                
                # 생성된 임시 파일 삭제
                temp_file_path.unlink()

                console.print(user_in, markup=False, highlight=False)

                if not user_in:
                    console.print("[yellow]입력이 비어있어 취소되었습니다.[/yellow]")
                    continue # 다음 프롬프트 루프로
                else:
                    console.print("[yellow]긴입력 완료[/yellow]")

            except FileNotFoundError:
                console.print(f"[red]오류: 편집기 '{editor}'를 찾을 수 없습니다. EDITOR 환경 변수를 확인해주세요.[/red]")
                continue

            except subprocess.CalledProcessError:
                console.print(f"[red]오류: 편집기 '{editor}'가 오류와 함께 종료되었습니다.[/red]")
                if temp_file_path.exists(): temp_file_path.unlink()
                continue

            except Exception as e:
                console.print(f"[red]오류: {e}[/red]")
                if temp_file_path.exists(): temp_file_path.unlink()

            #continue

        elif user_in.startswith("/"):
            cmd, *args = user_in.split()
            if cmd == "/exit":
                break
            elif cmd == "/compact_mode":
                compact_mode = not compact_mode
                status = "[green]활성화[/green]" if compact_mode else "[yellow]비활성화[/yellow]"
                console.print(f"첨부파일 압축 모드가 {status}되었습니다.")
                console.print("[dim]활성화 시: 과거 메시지의 첨부파일이 파일명만 남고 제거됩니다.[/dim]")
                continue
            elif cmd == "/show_context":
                # 기존 코드에 usage_history 정보 추가
                total_tokens = 0
                image_count = 0
                
                # ... (기존 코드)
                
                # usage_history 통계 추가
                if usage_history:
                    actual_count = sum(1 for u in usage_history if not u.get("estimated"))
                    estimated_count = sum(1 for u in usage_history if u.get("estimated"))
                    total_used = sum(u.get("total_tokens", 0) for u in usage_history)
                    
                    console.print(Panel.fit(
                        f"메시지: {len(messages)}개\n"
                        f"이미지: {image_count}개\n"
                        f"예상 토큰: {total_tokens:,}\n"
                        f"모델 한계: {model_context:,}\n"
                        f"사용률: {(total_tokens/model_context)*100:.1f}%\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"실제 API 호출: {actual_count}회\n"
                        f"추정 API 호출: {estimated_count}회\n"
                        f"누적 토큰 사용: {total_used:,}",
                        title="[cyan]컨텍스"))
                continue
            elif cmd == "/diff_code":
                differ = CodeDiffer(attached, current_session_name, messages)
                differ.start()
                continue
            elif cmd == "/pretty_print":
                pretty_print_enabled = not pretty_print_enabled
                status_text = "[green]활성화[/green]" if pretty_print_enabled else "[yellow]비활성화[/yellow]"
                console.print(f"고급 출력(Rich) 모드가 {status_text} 되었습니다.")
                continue
            elif cmd == "/last_response":
                last_assistant_message = get_last_assistant_message(messages)
                if last_assistant_message:
                    console.print(Panel(
                        Markdown(last_assistant_message, code_theme="monokai"),
                        title="[yellow]Last Response[/yellow]", 
                        border_style="dim"
                    ))
                else:
                    console.print("[yellow]다시 표시할 이전 답변이 없습니다.[/yellow]")
                continue
            elif cmd == "/raw":
                last_assistant_message = get_last_assistant_message(messages)
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
                old_model = model
                new_model, new_context = select_model(model, model_context)
                
                if new_model != old_model:
                    model = new_model
                    model_context = new_context
                    save_session(current_session_name, messages, model, model_context, usage_history)
                    console.print(f"[green]모델 변경: {old_model} → {model} (컨텍스트: {model_context})[/green]")
                else:
                    console.print(f"[green]모델 변경없음: {model}[/green]")
                continue

            elif cmd == "/search_models":
                #if not args:
                #    console.print("[yellow]검색할 키워드를 한 개 이상 입력해주세요. (예: /search_models gpt-4 claude)[/yellow]")
                #    continue
                
                searcher = ModelSearcher()
                searcher.start(args) # args가 키워드 리스트가 됨
                continue # 명령어 처리 후 다음 프롬프트로
            
            elif cmd == "/all_files":
                selector = FileSelector()
                attached = selector.start()
                console.print(f"[yellow]파일 {len(attached)}개 선택됨: {','.join(attached)}[/yellow]")
                if attached:
                    # 🎯 첨부 파일 토큰 분석 표시
                    display_attachment_tokens(attached, compact_mode)
            elif cmd == "/files":
                current_attached_paths = set(Path(p) for p in attached)
                newly_added_paths = set()

                feedback_messages = [] 
                #all_paths = []
                spec = ignore_spec() # .gptignore 규칙 로드
                
                # FileSelector의 재귀 탐색 로직을 재사용하기 위해 임시 인스턴스 생성
                # 이것이 중복 코드를 방지하는 가장 효율적인 방법입니다.
                temp_selector = FileSelector()

                for arg in args:
                    p = Path(arg)
                    try:
                        p_resolved = p.resolve(strict=True)
                    except FileNotFoundError:
                        console.print(f"[yellow]경고: '{arg}' 경로를 찾을 수 없습니다. 무시합니다.[/yellow]")
                        continue

                    if p_resolved.is_file():
                        # 인자가 파일이면, .gptignore 규칙 확인 후 추가
                        if not is_ignored(p_resolved, spec):
                            newly_added_paths.add(p_resolved)
                        else:
                            console.print(f"[dim]'{p_resolved.name}' 파일은 .gptignore 규칙에 의해 무시됩니다.[/dim]")

                    elif p_resolved.is_dir():
                        # 인자가 디렉터리면, 재귀적으로 모든 하위 파일 탐색
                        console.print(f"[dim]'{p_resolved.name}' 디렉터리에서 파일들을 재귀적으로 탐색합니다...[/dim]")
                        files_in_dir = temp_selector.get_all_files_in_dir(p_resolved)
                        newly_added_paths.update(files_in_dir)


                final_path_set = current_attached_paths.union(newly_added_paths)
                
                # 중복을 제거하고 절대 경로 문자열로 변환하여 최종 attached 리스트 생성
                attached = sorted([str(p) for p in set(final_path_set)])
                
                if attached:
                    display_paths = [str(Path(p).relative_to(BASE_DIR)) for p in attached]
                    console.print(f"[yellow]파일 {len(attached)}개 선택됨: {', '.join(display_paths)}[/yellow]")
                    
                    # 🎯 첨부 파일 토큰 분석 표시
                    display_attachment_tokens(attached, compact_mode)
                else:
                    console.print("[yellow]선택된 파일이 없습니다.[/yellow]")
                
                
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
                save_session(current_session_name, messages, model, model_context, usage_history)
                
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
                    usage_history = data.get("usage_history",[])
                    if data["model"] != model:
                        model = data["model"]
                        console.print(f"[cyan]세션에 저장된 모델로 변경: {model}[/cyan]")
                
                # 4. 최종 모드 설정 및 상태 출력
                mode = new_mode
                console.print(f"[green]전환 완료. 현재 모드: [bold]{mode}[/bold], 세션: [bold]{current_session_name}[/bold][/green]")
                
            elif cmd == "/reset":
                # 1. 현재 세션 파일 경로를 가져옵니다.
                current_session_path = SESSION_DIR / f"session_{current_session_name}.json"
                
                # 2. 백업 디렉터리 생성
                backup_dir = SESSION_DIR / "backup"
                backup_dir.mkdir(exist_ok=True)
                
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                
                # 3. 세션 파일 백업
                session_backup_success = False
                if current_session_path.exists():
                    backup_filename = f"session_{current_session_name}_{timestamp}.json"
                    backup_session_path = backup_dir / backup_filename
                    
                    try:
                        shutil.move(str(current_session_path), str(backup_session_path))
                        session_backup_success = True
                    except Exception as e:
                        console.print(f"[red]세션 백업 실패: {e}[/red]")
                
                # 4. 🎯 gpt_codes 폴더의 코드 블록 파일들 백업
                code_backup_dir = CODE_OUTPUT_DIR / "backup" / f"{current_session_name}_{timestamp}"
                code_files_backed_up = []
                
                if CODE_OUTPUT_DIR.exists():
                    # 현재 세션과 관련된 코드 파일들 찾기
                    pattern = f"codeblock_{current_session_name}_*"
                    matching_files = list(CODE_OUTPUT_DIR.glob(pattern))
                    
                    if matching_files:
                        code_backup_dir.mkdir(parents=True, exist_ok=True)
                        
                        for code_file in matching_files:
                            try:
                                # 파일을 백업 디렉터리로 이동
                                backup_path = code_backup_dir / code_file.name
                                shutil.move(str(code_file), str(backup_path))
                                code_files_backed_up.append(code_file.name)
                            except Exception as e:
                                console.print(f"[yellow]코드 파일 백업 실패 ({code_file.name}): {e}[/yellow]")
                
                # 5. 메모리 초기화
                messages.clear()
                usage_history.clear()
                save_session(current_session_name, messages, model, model_context, usage_history)
                
                # 6. 결과 보고
                if session_backup_success or code_files_backed_up:
                    backup_info = []
                    
                    if session_backup_success:
                        session_display_path = backup_session_path.relative_to(BASE_DIR)
                        backup_info.append(f"[green]세션 데이터:[/green]\n  {session_display_path}")
                    
                    if code_files_backed_up:
                        code_display_path = code_backup_dir.relative_to(BASE_DIR)
                        backup_info.append(
                            f"[green]코드 파일 {len(code_files_backed_up)}개:[/green]\n  {code_display_path}/"
                        )
                        
                        # 백업된 파일 목록 표시 (최대 5개)
                        for i, filename in enumerate(code_files_backed_up[:5]):
                            backup_info.append(f"    • {filename}")
                        if len(code_files_backed_up) > 5:
                            backup_info.append(f"    ... 외 {len(code_files_backed_up) - 5}개")
                    
                    console.print(
                        Panel(
                            f"세션 '{current_session_name}'이 초기화되었습니다.\n\n"
                            f"[bold]백업 위치:[/bold]\n" + "\n".join(backup_info),
                            title="[yellow]✅ 세션 초기화 및 백업 완료[/yellow]",
                            border_style="green"
                        )
                    )
                else:
                    console.print(f"[yellow]세션 '{current_session_name}'이 초기화되었습니다. (백업할 데이터 없음)[/yellow]")
                
                continue
            elif cmd == "/restore":
                # 백업 디렉터리 확인
                backup_dir = SESSION_DIR / "backup"
                if not backup_dir.exists():
                    console.print("[yellow]백업 파일이 없습니다.[/yellow]")
                    continue
                
                # 현재 세션의 백업 파일들 찾기
                backup_pattern = f"session_{current_session_name}_*.json"
                backup_files = sorted(backup_dir.glob(backup_pattern), reverse=True)
                
                if not backup_files:
                    console.print(f"[yellow]세션 '{current_session_name}'의 백업이 없습니다.[/yellow]")
                    continue
                
                # TUI로 백업 선택
                def select_backup():
                    items = []
                    result = [None]
                    
                    def raise_exit(backup_file):
                        result[0] = backup_file
                        raise urwid.ExitMainLoop()
                    
                    header = urwid.Text([
                        ("bold", f"세션 '{current_session_name}' 백업 목록"),
                        "\n",
                        ("info", "Enter로 선택, Q로 취소")
                    ])
                    items = [header, urwid.Divider()]
                    
                    for backup_file in backup_files[:20]:  # 최대 20개 표시
                        # 타임스탬프 파싱
                        timestamp_str = backup_file.stem.split('_')[-2] + '_' + backup_file.stem.split('_')[-1]
                        try:
                            # 타임스탬프를 읽기 쉬운 형식으로 변환
                            dt = time.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                            display_time = time.strftime("%Y-%m-%d %H:%M:%S", dt)
                        except:
                            display_time = timestamp_str
                        
                        # 백업 파일 크기 확인
                        file_size = backup_file.stat().st_size / 1024  # KB
                        
                        # 백업 내용 미리보기 (메시지 개수 확인)
                        try:
                            with open(backup_file, 'r', encoding='utf-8') as f:
                                backup_data = json.load(f)
                                msg_count = len(backup_data.get("messages", []))
                                model_info = backup_data.get("model", "unknown")
                                
                                # 토큰 사용량 정보가 있으면 표시
                                total_usage = backup_data.get("total_usage", {})
                                total_tokens = total_usage.get("total_tokens", 0)
                                
                                label = f"📅 {display_time} | 💬 {msg_count}개 메시지 | 🤖 {model_info.split('/')[-1]} | 🔢 {total_tokens:,} 토큰 | 📦 {file_size:.1f}KB"
                        except:
                            label = f"📅 {display_time} | 📦 {file_size:.1f}KB"
                        
                        btn = urwid.Button(label)
                        urwid.connect_signal(btn, "click", lambda _, f=backup_file: raise_exit(f))
                        items.append(urwid.AttrMap(btn, None, focus_map="myfocus"))
                    
                    listbox = urwid.ListBox(urwid.SimpleFocusListWalker(items))
                    
                    def unhandled(key):
                        if isinstance(key, str) and key.lower() == "q":
                            raise_exit(None)
                    
                    urwid.MainLoop(listbox, palette=PALETTE, unhandled_input=unhandled).run()
                    return result[0]
                
                selected_backup = select_backup()
                
                if not selected_backup:
                    console.print("[dim]복원이 취소되었습니다.[/dim]")
                    continue
                
                # 현재 세션을 백업 (복원 전 안전장치)
                if messages:  # 현재 내용이 있으면 백업
                    safety_backup_dir = SESSION_DIR / "backup" / "pre_restore"
                    safety_backup_dir.mkdir(parents=True, exist_ok=True)
                    
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    safety_backup_path = safety_backup_dir / f"session_{current_session_name}_pre_restore_{timestamp}.json"
                    
                    save_json(safety_backup_path, {
                        "messages": messages,
                        "model": model,
                        "context_length": model_context,
                        "usage_history": usage_history,
                        "note": "자동 생성된 복원 전 백업"
                    })
                
                # 백업 파일에서 데이터 로드
                try:
                    restored_data = load_json(selected_backup, {})
                    
                    # 세션 데이터 복원
                    messages = restored_data.get("messages", [])
                    model = restored_data.get("model", model)
                    model_context = restored_data.get("context_length", model_context)
                    usage_history = restored_data.get("usage_history", [])
                    
                    # 현재 세션에 저장
                    save_session(current_session_name, messages, model, model_context, usage_history)
                    
                    # 코드 파일 복원 확인
                    timestamp_str = selected_backup.stem.split('_')[-2] + '_' + selected_backup.stem.split('_')[-1]
                    code_backup_dir = CODE_OUTPUT_DIR / "backup" / f"{current_session_name}_{timestamp_str}"
                    
                    restored_code_files = 0
                    if code_backup_dir.exists():
                        # 기존 코드 파일들 백업
                        existing_code_files = list(CODE_OUTPUT_DIR.glob(f"codeblock_{current_session_name}_*"))
                        if existing_code_files:
                            pre_restore_code_dir = CODE_OUTPUT_DIR / "backup" / f"pre_restore_{time.strftime('%Y%m%d_%H%M%S')}"
                            pre_restore_code_dir.mkdir(parents=True, exist_ok=True)
                            for f in existing_code_files:
                                shutil.move(str(f), str(pre_restore_code_dir / f.name))
                        
                        # 백업된 코드 파일들 복원
                        for code_file in code_backup_dir.glob("*"):
                            target_path = CODE_OUTPUT_DIR / code_file.name
                            shutil.copy2(str(code_file), str(target_path))
                            restored_code_files += 1
                    
                    # 결과 보고
                    console.print(
                        Panel(
                            f"[green]✅ 복원 완료![/green]\n\n"
                            f"복원된 데이터:\n"
                            f"• 메시지: {len(messages)}개\n"
                            f"• 모델: {model}\n"
                            f"• 컨텍스트: {model_context:,}\n"
                            f"• 코드 파일: {restored_code_files}개\n\n"
                            f"[dim]원본: {selected_backup.relative_to(BASE_DIR)}[/dim]",
                            title="[green]세션 복원 성공[/green]",
                            border_style="green"
                        )
                    )
                    
                    # 첨부 파일 초기화
                    attached = []
                    
                except Exception as e:
                    console.print(f"[red]복원 실패: {e}[/red]")
                    continue

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
            elif cmd == "/show_context":
                total_tokens = 0
                image_count = 0
                
                for msg in messages:
                    msg_str = json.dumps(msg, ensure_ascii=False)
                    
                    if isinstance(msg.get("content"), list):
                        for part in msg["content"]:
                            if part.get("type") == "image_url":
                                image_count += 1
                                # 이미지 토큰 추정
                                if "base64," in part["image_url"]["url"]:
                                    base64_part = part["image_url"]["url"].split("base64,")[1]
                                    total_tokens += token_estimator.estimate_image_tokens(base64_part)
                    
                    total_tokens += len(msg_str) // 4
                
                console.print(Panel.fit(
                    f"메시지: {len(messages)}개\n"
                    f"이미지: {image_count}개\n"
                    f"예상 토큰: {total_tokens:,}\n"
                    f"모델 한계: {model_context:,}\n"
                    f"사용률: {(total_tokens/model_context)*100:.1f}%",
                    title="[cyan]컨텍스트 사용량[/cyan]"
                ))
            
            else:
                console.print("[yellow]알 수 없는 명령[/yellow]")
            continue  # 명령어 처리 끝

        # ── 파일 첨부 포함 user message 생성
        msg_obj: Dict[str, Any]
        if attached:
            parts = []
        
            # 첨부 파일 정보를 먼저 텍스트로 명시
            file_info_lines = ["📎 첨부 파일 목록:"]
            image_count = 0
            pdf_count = 0
            image_files = []
            pdf_files = []
            for f in attached:
                path = Path(f)
                if path.suffix.lower() in IMG_EXTS:
                    image_files.append(path)
                    image_count += 1
                elif path.suffix.lower() == PDF_EXT:
                    pdf_files.append(path)
                    pdf_count += 1
                    
            # 사용자 입력과 파일 정보를 함께 전송
            combined_text = user_in
            if image_count > 0:
                if image_count == 1:
                    combined_text += f"\n\n[첨부이미지정보: image_name={image_files[0].name}]"
                else:
                    combined_text += f"\n\n[첨부이미지들 정보"
                    for i, img_path in enumerate(image_files, 1):
                        combined_text += f"\n  {i}번: image_name={img_path.name}"
                    combined_text += "]"

            if pdf_count > 0:
                if pdf_count == 1:
                    combined_text += f"\n\n[첨부pdf정보: pdf_name={pdf_files[0].name}]"
                else:
                    combined_text += f"\n\n[첨부pdf들 정보"
                    for i, pdf_path in enumerate(pdf_files, 1):
                        combined_text += f"\n  {i}번: pdf_name={pdf_path.name}"
                    combined_text += "]"
            

            parts.append({"type": "text", "text": combined_text})
            
            # 실제 파일 컨텐츠 추가
            for f in attached:
                content = prepare_content_part(Path(f))
                parts.append(content)
            
            msg_obj = {"role": "user", "content": parts}
            
        else:
            msg_obj = {"role": "user", "content": user_in}
        
        messages.append(msg_obj)

        # ── Compact mode 처리 (API 전송용 메시지 별도 생성)
        messages_to_send = messages.copy()  # 얕은 복사
        
        if compact_mode:
            # 과거 메시지들의 첨부파일을 플레이스홀더로 변환
            compressed_messages = []
            for i, msg in enumerate(messages_to_send):
                # 마지막 메시지는 압축하지 않음 (현재 질문)
                if i == len(messages_to_send) - 1:
                    compressed_messages.append(msg)
                elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    # 첨부파일이 있는 과거 user 메시지를 압축
                    compressed_msg = convert_to_placeholder_message(msg)
                    compressed_messages.append(compressed_msg)
                else:
                    compressed_messages.append(msg)
            
            messages_to_send = compressed_messages
            
            # 압축 효과 계산 및 표시
            if attached and i < len(messages_to_send) - 1:
                original_tokens = estimate_message_tokens(messages)
                compressed_tokens = estimate_message_tokens(messages_to_send)
                saved_tokens = original_tokens - compressed_tokens
                
                console.print(
                    f"[dim]컨텍스트 압축: {saved_tokens:,} 토큰 절약됨[/dim]"
                )
        
        '''
        for msg in messages_to_send:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                console.print(msg.get("content")[0])
            #console.print(msg)
            #sys.exit(0)
        '''
        #sys.exit(0)        

        # ── OpenRouter 호출
        result = ask_stream(
            messages_to_send, 
            model, 
            mode, 
            model_context_limit=model_context, 
            pretty_print=pretty_print_enabled,
            current_attached_files=attached
        )

        
        if result is None:
            failed_usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "timestamp": time.time(),
                "model": model,
                "status": "failed",  # 실패 표시
                "estimated": True
            }
            #usage_history.append(failed_usage)
            messages.pop()  # 실패 시 user message 제거
            continue

        reply, usage_info = result

        # ✅ 여기에 토큰 추정 로직 추가
        if not usage_info:
            # API가 usage 정보를 제공하지 않은 경우, 수동으로 추정
            console.print("[dim yellow]토큰 사용량을 추정합니다...[/dim yellow]")
            
            system_prompt_tokens = 0
            if mode == "dev":
                system_prompt_tokens = token_estimator.count_text_tokens("""당신은 터미널(CLI) 환경에 특화된...""")
            elif mode == "general":
                system_prompt_tokens = token_estimator.count_text_tokens("""당신은 매우 친절하고...""")
            elif mode == "teacher":
                system_prompt_tokens = token_estimator.count_text_tokens("""당신은 코드 분석의 대가...""")
            
            prompt_tokens = system_prompt_tokens
            for msg in messages:
                if isinstance(msg.get("content"), str):
                    prompt_tokens += token_estimator.count_text_tokens(msg["content"])
                elif isinstance(msg.get("content"), list):
                    for part in msg["content"]:
                        if part.get("type") == "text":
                            prompt_tokens += token_estimator.count_text_tokens(part["text"])
                        elif part.get("type") == "image_url":
                            # 이미지 토큰 추정 (base64 길이 기반)
                            if "base64," in part["image_url"]["url"]:
                                base64_part = part["image_url"]["url"].split("base64,")[1]
                                prompt_tokens += token_estimator.estimate_image_tokens(base64_part)
            
            # 응답의 토큰 계산
            completion_tokens = token_estimator.count_text_tokens(reply or "")
            
            usage_info = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "timestamp": time.time(),
                "model": model,
                "estimated": True  # 추정값임을 표시
            }
            
            console.print(
                f"[dim yellow]추정 토큰: "
                f"입력 {prompt_tokens:,} + "
                f"출력 {completion_tokens:,} = "
                f"총 {prompt_tokens + completion_tokens:,}[/dim yellow]"
            )

        
        if usage_info:
            #usage_history.append(usage_info)
            
            # 실시간 사용량 표시
            console.print(
                f"[dim]토큰 사용: "
                f"입력 {usage_info['prompt_tokens']:,} + "
                f"출력 {usage_info['completion_tokens']:,} = "
                f"총 {usage_info['total_tokens']:,}[/dim]"
            )

        usage_history.append(usage_info)
        messages.append({"role": "assistant", "content": reply})
        save_session(current_session_name, messages, model, model_context, usage_history)
        last_resp = reply

        # ── 후처리
        code_blocks = extract_code_blocks(reply)
        if code_blocks:
            current_msg_id = sum(1 for m in data["messages"] if m["role"] == "assistant") # 몇번째 대답인지
            saved_files = save_code_blocks(code_blocks, current_session_name, current_msg_id)
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


def create_default_ignore_file_if_not_exists():
    """전역 기본 .gptignore 파일이 없으면, 합리적인 기본값으로 생성합니다."""
    if DEFAULT_IGNORE_FILE.exists():
        return

    # 이전에 코드에 내장했던 기본 패턴들
    default_patterns_content = """
# 이 파일은 모든 프로젝트에 공통으로 적용되는 전역 gptcli 무시 규칙입니다.
# 사용자가 자유롭게 수정할 수 있습니다.

# --- 일반적인 무시 목록 ---
.DS_Store
.env
*.pyc
*.swp

# --- Python 관련 ---
__pycache__/
.venv/
venv/
env/

# --- 버전 관리 및 IDE 설정 ---
.git/
.vscode/
.idea/

# --- 이 앱 자체의 파일들 ---
.gpt_sessions/
.gpt_prompt_history.txt
.gpt_favorites.json
.gptignore
gpt_outputs/
gpt_markdowns/
"""
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        DEFAULT_IGNORE_FILE.write_text(default_patterns_content.strip(), encoding="utf-8")
        console.print(f"[dim]전역 기본 무시 파일 생성: {DEFAULT_IGNORE_FILE}[/dim]")
    except Exception as e:
        console.print(f"[yellow]경고: 전역 기본 무시 파일을 생성하지 못했습니다: {e}[/yellow]")

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
    
    create_default_ignore_file_if_not_exists()

    main()