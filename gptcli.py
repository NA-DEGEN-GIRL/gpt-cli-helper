from __future__ import annotations

# ── stdlib
import argparse
import base64
import difflib
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
from typing import Any, Dict, List, Optional, Sequence, Tuple, Set
from typing import Union  # FileSelector 타입 힌트용
from dataclasses import dataclass, field

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
from prompt_toolkit.application.current import get_app
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
from pygments import lex as pyg_lex
from pygments.lexers import guess_lexer_for_filename, TextLexer
import src.constants as constants

class TokenEstimator:
    def __init__(self, console: Console, model: str = "gpt-4"):
        """
        모델별 토크나이저 초기화
        - gpt-4, gpt-3.5-turbo: cl100k_base
        - older models: p50k_base
        """
        self.console = console
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
            self.console.print(f"[yellow]이미지 토큰 추정 실패: {e}[/yellow]")
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
            self.console.print("[yellow]경고: PyPDF2가 설치되지 않았습니다. ...[/yellow]")
            # PyPDF2가 없으면 파일 크기 기반 추정
            file_size_kb = pdf_path.stat().st_size / 1024
            return int(file_size_kb * 3)  # 1KB ≈ 3 토큰 (대략)
        except Exception as e:
            self.console.print(f"[yellow]PDF 토큰 추정 실패 ({e}). ...[/yellow]")
            # 폴백: base64 크기 기반
            return len(base64.b64encode(pdf_path.read_bytes())) // 4

class GPTCLI:
    """
    GPT-CLI 애플리케이션의 메인 클래스.
    모든 상태와 헬퍼 클래스를 관리하며, 메인 루프를 실행합니다.
    """
    
    default_model = constants.DEFAULT_MODEL
    default_context_length = constants.DEFAULT_CONTEXT_LENGTH
    
    def __init__(self, session_name: str, mode: str = "dev"):
        # --- 핵심 컴포넌트 초기화 (의존성 주입) ---
        self.config = ConfigManager()
        self.theme_manager = ThemeManager(default_theme='monokai-ish')
        self.console = Console(theme=self.theme_manager.get_rich_theme())
        self._next_prompt_default: Optional[str] = None
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            default_headers={
                "HTTP-Referer": os.getenv("APP_URL", "https://github.com/user/gpt-cli"),
                "X-Title": os.getenv("APP_TITLE", "GPT-CLI"),
            }
        )
        
        self.parser = AIStreamParser(self.client, self.console)
        self.command_handler = CommandHandler(self, self.config)
        self.token_estimator = TokenEstimator(console=self.console)
        
        # --- 애플리케이션 상태 변수 ---
        self.current_session_name: str = session_name
        self.mode: str = mode
        self.messages: List[Dict] = []
        self.model: str = self.default_model
        self.model_context: int = self.default_context_length
        self.usage_history: List[Dict] = []
        self.attached: List[str] = []
        self.last_response: str = ""
        self.last_reply_code_blocks: List[Tuple[str, str]] = []
        
        # --- 애플리케이션 모드 플래그 ---
        self.compact_mode: bool = True
        self.pretty_print_enabled: bool = True
        
        # --- Prompt Toolkit 세션 설정 ---
        self.prompt_session = self._setup_prompt_session()
        
        # --- TUI 관련 참조 ---
        self.active_tui_loop: Optional[urwid.MainLoop] = None

        # 현재 세션 포인터 파일 갱신
        try:
            self.config.save_current_session_name(self.current_session_name)
        except Exception:
            pass

    def _setup_prompt_session(self) -> PromptSession:
        command_list = [cmd.split()[0] for cmd in constants.COMMANDS.strip().split('\n')]
        command_completer = FuzzyCompleter(WordCompleter(command_list, ignore_case=True))

        path_completer = PathCompleter(
            file_filter=lambda filename: not self.config.is_ignored(Path(filename), self.config.get_ignore_spec()),
            expanduser=True
        )
        #wrapped_file_completer = PathCompleterWrapper("/files ", path_completer)
        # 디렉터리 후보까지 .gptignore 필터를 적용하려면 래퍼에서 후처리 필터링 필요
        wrapped_file_completer = PathCompleterWrapper("/files ", path_completer, self.config)
        self.completer = ConditionalCompleter(command_completer, wrapped_file_completer)
        self.completer.config = self.config
        self.completer.theme_manager = self.theme_manager
        self.completer.app = self

        bindings = KeyBindings()
        class SafeAutoSuggest(AutoSuggestFromHistory):
            def get_suggestion(self, buffer, document):
                txt = document.text_before_cursor
                # 공백 제거 후, 프롬프트의 '첫 토큰'이 '_'로 시작하면 제안 비활성화
                if txt.lstrip().startswith('_') and not txt.lstrip().startswith('/'):
                    return None
                return super().get_suggestion(buffer, document)

        # 공통 조건자: 항상 get_app().current_buffer로 평가(안정)
        is_completing = Condition(lambda: get_app().current_buffer.complete_state is not None)
        buf_text = lambda: get_app().current_buffer.text
        is_slash = Condition(lambda: buf_text().strip().startswith('/'))
        is_not_slash = Condition(lambda: not buf_text().strip().startswith('/'))
        not_completing = Condition(lambda: get_app().current_buffer.complete_state is None)

        # 1) 자동완성 중: Enter -> 현재/첫 번째 completion 적용
        @bindings.add("enter", filter=is_completing)
        def _(event):
            cs = event.current_buffer.complete_state
            if cs.current_completion:
                event.current_buffer.apply_completion(cs.current_completion)
            elif cs.completions:
                event.current_buffer.apply_completion(cs.completions[0])

        # 2) 슬래시 명령어 & 자동완성 아님: Enter -> 실행(accept)
        @bindings.add("enter", filter=is_slash & not_completing)
        def _(event):
            event.current_buffer.validate_and_handle()

        # 3) 일반 텍스트 & 자동완성 아님: Enter -> 줄바꿈(멀티라인 입력)
        @bindings.add("enter", filter=is_not_slash & not_completing)
        def _(event):
            event.current_buffer.insert_text('\n')

        # Alt+Enter: 항상 실행
        @bindings.add("escape", "enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        # Esc: 버퍼 리셋
        @bindings.add("escape")
        def _(event):
            event.current_buffer.reset()

        # Ctrl+A: 전체 선택
        @bindings.add("c-a")
        def _(event):
            event.current_buffer.select_all()

        @bindings.add("_", filter=is_not_slash)
        def _(event):
            buf = event.current_buffer
            # 원래 문자 삽입
            buf.insert_text("_")
            # 공백 제거 후 정확히 '_'로 시작하는 첫 토큰인 경우만 힌트 오픈
            txt = buf.document.text_before_cursor
            if txt and txt.strip() == "_":
                try:
                    buf.start_completion(select_first=False)
                except Exception:
                    pass

        return PromptSession(
            history=FileHistory(self.config.PROMPT_HISTORY_FILE),
            #auto_suggest=AutoSuggestFromHistory(),
            auto_suggest=SafeAutoSuggest(),
            multiline=True,
            prompt_continuation="",
            completer=self.completer,
            key_bindings=bindings,
            complete_while_typing=True
        )

    def _load_initial_session(self):
        """애플리케이션 시작 시 세션 데이터를 로드합니다."""
        data = self.config.load_session(self.current_session_name)
        self.messages = data.get("messages", [])
        self.model = data.get("model", self.default_model)
        self.model_context = data.get("context_length", self.default_context_length)
        self.usage_history = data.get("usage_history", [])
        self.mode = data.get("mode", self.mode or "dev")

    def _prepare_user_message(self, user_input: str) -> Dict[str, Any]:
        """첨부 파일을 포함하여 API에 보낼 사용자 메시지 객체를 생성합니다."""
        if not self.attached:
            return {"role": "user", "content": user_input}

        content_parts = [{"type": "text", "text": user_input}]
        for file_path_str in self.attached:
            path = Path(file_path_str)
            if path.exists():
                part = Utils.prepare_content_part(path, self.console, self.token_estimator)
                if part:
                    content_parts.append(part)
        
        return {"role": "user", "content": content_parts}

    def get_messages_for_sending(self) -> List[Dict[str, Any]]:
        """Compact 모드 여부에 따라 API에 전송할 메시지 목록을 반환합니다."""
        if not self.compact_mode or len(self.messages) <= 1:
            return self.messages

        processed_messages = []
        for i, msg in enumerate(self.messages):
            # 마지막 사용자 메시지는 항상 원본 그대로 전송
            if i == len(self.messages) - 1:
                processed_messages.append(msg)
            elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
                processed_messages.append(Utils.convert_to_placeholder_message(msg))
            else:
                processed_messages.append(msg)
        return processed_messages

    def _get_prompt_string(self) -> str:
        """현재 상태를 기반으로 터미널 프롬프트 문자열을 생성합니다."""
        parts = [self.model.split('/')[1], f"session: {self.current_session_name}", f"mode: {self.mode}"]
        if self.attached:
            parts.append(f"{len(self.attached)} files")
        if self.compact_mode:
            parts.append("compact mode")
            
        return f"[ {' | '.join(parts)} ]\nQ>> "

    def _handle_chat_message(self, user_input: str):
        """일반 채팅 메시지를 처리하는 전체 파이프라인입니다."""
        # 1. 메시지 객체 생성 및 대화 기록 추가
        user_message = self._prepare_user_message(user_input)
        self.messages.append(user_message)
        
        # 2. Compact 모드 적용 및 컨텍스트 트리밍
        messages_to_send = self.get_messages_for_sending()
        system_prompt_content = Utils.get_system_prompt_content(self.mode)
        
        reserve_map = {200000: 32000, 128000: 16000}
        reserve_for_completion = reserve_map.get(self.model_context, 4096)
        
        final_messages = Utils.trim_messages_by_tokens(
            messages=messages_to_send,
            model_name=self.model,
            model_context_limit=self.model_context,
            system_prompt_text=system_prompt_content,
            token_estimator=self.token_estimator,
            console=self.console,
            reserve_for_completion=reserve_for_completion,
            trim_ratio=constants.CONTEXT_TRIM_RATIO
        )

        if not final_messages:
            self.messages.pop() # 전송 실패 시 마지막 메시지 제거
            return

        # 3. API 호출 및 응답 스트리밍
        system_prompt = {"role": "system", "content": system_prompt_content}
        result = self.parser.stream_and_parse(
            system_prompt, final_messages, self.model, self.pretty_print_enabled
        )

        try:
            self.command_handler._snap_scroll_to_bottom()
        except Exception:
            pass

        if result is None:
            self.messages.pop() # API 호출 실패/취소 시 마지막 메시지 제거
            return
            
        # 4. 응답 처리 및 저장
        self.last_response, usage_info = result
        self.last_reply_code_blocks = Utils.extract_code_blocks(self.last_response)
        
        self.messages.append({"role": "assistant", "content": self.last_response})
        
        if usage_info:
            self.usage_history.append(usage_info)

        self.config.save_session(
            self.current_session_name, self.messages, self.model, self.model_context, self.usage_history, mode=self.mode,
        )

        # 5. 후처리 (코드 블록 저장 등)
        if self.last_reply_code_blocks:
            current_msg_id = sum(1 for m in self.messages if m["role"] == "assistant")
            saved_files = self.config.save_code_blocks(self.last_reply_code_blocks, self.current_session_name, current_msg_id)
            if saved_files:
                saved_paths_text = Text("\n".join(
                    f"  • {p.relative_to(self.config.BASE_DIR)}" for p in saved_files                          
                ))                  
                self.console.print(Panel.fit(
                    saved_paths_text,
                    title="[green]💾 코드 블록 저장 완료[/green]",
                    border_style="dim",
                    title_align="left"
                ), highlight=False)

        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        safe_session_name = re.sub(r'[^a-zA-Z0-9_-]', '_', self.current_session_name)
        md_filename = f"{safe_session_name}_{timestamp}_{len(self.messages)//2}.md"
        saved_path = self.config.MD_OUTPUT_DIR.joinpath(md_filename)
        try:
            saved_path.write_text(self.last_response, encoding="utf-8")
            display_path_str = str(saved_path.relative_to(self.config.BASE_DIR))
            self.console.print(Panel.fit(
                    Text(display_path_str),
                    title="[green]💾 응답 파일 저장 완료[/green]",
                    border_style="dim",
                    title_align="left"
                ), highlight=False)
        except Exception as e:
            self.console.print(f"[red]마크다운 파일 저장 실패 ({md_filename}): {e}[/red]",highlight=False) 

        self.attached.clear()
        self.console.print("[dim]첨부 파일이 초기화되었습니다.[/dim]", highlight=False)

    def run(self):
        """애플리케이션의 메인 실행 루프."""
        self._load_initial_session()
        self.console.print(Panel.fit(constants.COMMANDS, title="[yellow]/명령어[/yellow]"))
        self.console.print(f"[cyan]세션('{self.current_session_name}') 시작 – 모델: {self.model}[/cyan]")
        
        while True:
            try:
                self.completer.update_attached_file_completer(self.attached, self.config.BASE_DIR)
                prompt_string = self._get_prompt_string()

                default_text = ""
                if self._next_prompt_default:
                    default_text = self._next_prompt_default
                    self._next_prompt_default = None

                user_input = self.prompt_session.prompt(prompt_string, default=default_text).strip()

                if not user_input:
                    continue

                if user_input.startswith('/'):
                    should_exit = self.command_handler.dispatch(user_input)
                    if should_exit:
                        break
                else:
                    self._handle_chat_message(user_input)

            except (KeyboardInterrupt, EOFError):
                break
        
        # 종료 전 마지막 세션 저장
        self.config.save_session(
            self.current_session_name,
            self.messages,
            self.model,
            self.model_context,
            self.usage_history,
            mode=self.mode,  # ← [추가]
        )

        # 현재 세션 포인터 갱신
        try:
            self.config.save_current_session_name(self.current_session_name)
        except Exception:
            pass

        self.console.print("\n[bold cyan]세션이 저장되었습니다. 안녕히 가세요![/bold cyan]")

class Utils:
    """
    특정 클래스에 속하지 않는 순수 유틸리티 함수들을 모아놓은 정적 클래스.
    모든 메서드는 의존성을 인자로 주입받습니다.
    """
    _VENDOR_SPECIFIC_OFFSET = constants.VENDOR_SPECIFIC_OFFSET

    @staticmethod
    def _load_json(path: Path, default: Any = None) -> Any:
        """JSON 파일을 안전하게 읽어옵니다. 실패 시 기본값을 반환합니다."""
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return default or {}
        return default or {}
    
    @staticmethod
    def _save_json(path: Path, data: Any) -> bool:
        """JSON 데이터를 파일에 안전하게 저장합니다."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except IOError:
            return False

    @staticmethod
    def get_system_prompt_content(mode: str) -> str:
        return constants.PROMPT_TEMPLATES.get(mode,constants.PROMPT_TEMPLATES["dev"]).strip()

    @staticmethod
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

    @staticmethod
    def optimize_image_for_api(path: Path, console: Console, max_dimension: int = 1024, quality: int = 85) -> str:
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
            return ConfigManager.encode_base64(path)
        except Exception as e:
            console.print(f"[yellow]이미지 최적화 실패 ({path.name}): {e}[/yellow]")
            return ConfigManager.encode_base64(path)

    @staticmethod
    def prepare_content_part(path: Path, console: Console, token_estimator: 'TokenEstimator', optimize_images: bool = True) -> Dict[str, Any]:
        """파일을 API 요청용 컨텐츠로 변환"""
        if path.suffix.lower() in constants.IMG_EXTS:
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
                base64_data = Utils.optimize_image_for_api(path, console)
                estimated_tokens = token_estimator.estimate_image_tokens(base64_data, detail="auto")
            else:
                base64_data = ConfigManager.encode_base64(path)
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
        
        elif path.suffix.lower() == constants.PDF_EXT:
            estimated_tokens = token_estimator.estimate_pdf_tokens(path)
            console.print(f"[dim]PDF 토큰: 약 {estimated_tokens:,}개[/dim]")

            # PDF는 그대로 (일부 모델만 지원)
            data_url = f"data:application/pdf;base64,{ConfigManager.encode_base64(path)}"
            return {
                "type": "file",
                "file": {"filename": path.name, "file_data": data_url},
            }
        
        # 텍스트 파일
        text = ConfigManager.read_plain_file(path)
        tokens = token_estimator.count_text_tokens(text)
        console.print(f"[dim]텍스트 토큰: {tokens:,}개[/dim]", highlight=False)
        return {
            "type": "text",
            "text": f"\n\n[파일: {path}]\n```\n{text}\n```",
        }

    @staticmethod
    def _count_message_tokens_with_estimator(msg: Dict[str, Any], te: 'TokenEstimator') -> int:
        total = 6  # 메시지 구조 오버헤드
        content = msg.get("content", "")
        
        if isinstance(content, str):
            total += te.count_text_tokens(content)
            return total
        
        if isinstance(content, list):
            for part in content:
                ptype = part.get("type")
                if ptype == "text":
                    total += te.count_text_tokens(part.get("text", ""))
                elif ptype == "image_url":
                    image_url = part.get("image_url", {})
                    url = image_url.get("url", "")
                    detail = image_url.get("detail", "auto")
                    if isinstance(url, str) and "base64," in url:
                        try:
                            b64 = url.split("base64,", 1)[1]
                            total += te.estimate_image_tokens(b64, detail=detail)
                        except Exception:
                            total += 1105
                    else:
                        total += 85
                elif ptype == "file":
                    file_data = part.get("file", {})
                    data_url = file_data.get("file_data", "")
                    filename = file_data.get("filename", "")
                    if isinstance(filename, str) and filename.lower().endswith(".pdf") and "base64," in data_url:
                        try:
                            b64 = data_url.split("base64,", 1)[1]
                            pdf_bytes = base64.b64decode(b64)
                            total += int(len(pdf_bytes) / 1024 * 3)
                        except Exception:
                            total += 1000
                    else:
                        total += 500
        return total
    
    @staticmethod
    def trim_messages_by_tokens(
        messages: List[Dict[str, Any]],
        model_name: str,
        model_context_limit: int,
        system_prompt_text: str,
        token_estimator: TokenEstimator, # [추가] 의존성 주입
        console: Console,              # [추가] 의존성 주입
        reserve_for_completion: int = 4096,
        trim_ratio: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """컨텍스트 한계에 맞춰 메시지 목록을 트리밍"""
        te = token_estimator
        trim_ratio = float(os.getenv("GPTCLI_TRIM_RATIO", "0.75")) if trim_ratio is None else float(trim_ratio)

        sys_tokens = te.count_text_tokens(system_prompt_text or "")
        if sys_tokens >= model_context_limit:
            console.print("[red]시스템 프롬프트가 모델 컨텍스트 한계를 초과합니다.[/red]",highlight=False)
            return []

        # 벤더별 추가 오프셋
        vendor_offset = 0
        clean_model_name = model_name.lower()
        for vendor, offset in Utils._VENDOR_SPECIFIC_OFFSET.items():
            if vendor in clean_model_name:
                vendor_offset = offset
                console.print(f"[dim]벤더별 오프셋 적용({vendor}): -{vendor_offset:,} 토큰[/dim]", highlight=False)
                break

        available_for_prompt = model_context_limit - sys_tokens - reserve_for_completion - vendor_offset

        if available_for_prompt <= 0:
            console.print("[red]예약 공간과 오프셋만으로 컨텍스트가 가득 찼습니다.[/red]",highlight=False)
            return []

        prompt_budget = int(available_for_prompt * trim_ratio)

        # 메시지별 토큰 산출
        per_message = [(m, Utils._count_message_tokens_with_estimator(m, te)) for m in messages]

        trimmed: List[Dict[str, Any]] = []
        used = 0
        for m, t in reversed(per_message):
            if used + t > prompt_budget:
                break
            trimmed.append(m)
            used += t
        trimmed.reverse()

        if not trimmed and messages:
            last = messages[-1]
            if isinstance(last.get("content"), list):
                text_parts = [p for p in last["content"] if p.get("type") == "text"]
                minimal = {"role": last.get("role", "user"), "content": text_parts[0]["text"] if text_parts else ""}
                if Utils._count_message_tokens_with_estimator(minimal, te) <= prompt_budget:
                    console.print("[yellow]최신 메시지의 첨부를 제거하여 텍스트만 전송합니다.[/yellow]")
                    return [minimal]
            console.print("[red]컨텍스트 한계로 인해 메시지를 전송할 수 없습니다. 입력을 줄여주세요.[/red]")
            return []

        if len(trimmed) < len(messages):
            removed = len(messages) - len(trimmed)
            console.print(
                f"[dim]컨텍스트 트리밍: {removed}개 제거 | "
                f"[dim]최신 메시지: {len(trimmed)}개 사용 | "
                f"사용:{used:,}/{prompt_budget:,} (총 프롬프트 여유:{available_for_prompt:,} | "
                f"ratio:{trim_ratio:.2f})[/dim]",
                highlight=False
            )
        else:
            # 트리밍이 발생하지 않아도 로그 출력
            console.print(
                f"[dim]컨텍스트 사용:{used:,}/{prompt_budget:,} "
                f"(sys:{sys_tokens:,} | reserve:{reserve_for_completion:,} | ratio:{trim_ratio:.2f} | offset:{vendor_offset:,})[/dim]",
                highlight=False
            )
            
        return trimmed

    @staticmethod
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
            delimiter_info = Utils._parse_backticks(line)

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
    
    @staticmethod
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
    
    @staticmethod
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

class PathCompleterWrapper(Completer):
    """
    PathCompleter를 /files 명령어에 맞게 감싸는 최종 완성 버전.
    스페이스로 구분된 여러 파일 입력을 완벽하게 지원합니다.
    """
    def __init__(self, command_prefix: str, path_completer: Completer, config: 'ConfigManager'):
        self.command_prefix = command_prefix
        self.path_completer = path_completer
        self.config = config

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

        # 5. PathCompleter 제안을 받아 '무시 규칙'으로 후처리 필터링합니다.
        #    여기서 핵심은 comp.text를 '현재 단어의 디렉터리 컨텍스트'에 맞춰 절대경로로 복원하는 것입니다.
        spec = self.config.get_ignore_spec()
        
        for comp in self.path_completer.get_completions(doc_for_path, complete_event):
            try:
                # comp.start_position을 반영하여 "적용 후 최종 단어"를 구성
                start_pos = getattr(comp, "start_position", 0) or 0
                base = word_before_cursor or ""
                # 음수면 좌측으로 그만큼 잘라낸 뒤 comp.text로 대체
                cut = len(base) + start_pos if start_pos < 0 else len(base)
                final_word = (base[:cut] + (comp.text or "")).strip()

                # 최종 단어 → 절대 경로
                cand_path = Path(final_word).expanduser()
                if cand_path.is_absolute():
                    p_full = cand_path.resolve()
                else:
                    p_full = (self.config.BASE_DIR / cand_path).resolve()
                # 실제 존재하는 후보만 필터링(미존재 조각은 통과시켜 타이핑 진행 가능)
                if p_full.exists() and self.config.is_ignored(p_full, spec):
                    continue  # 무시 대상은 자동완성에서 숨김
            except Exception:
                # 문제가 있어도 자동완성 전체를 막지 않음
                pass
            yield comp

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
        self.config: Optional[ConfigManager] = None 
        self.theme_manager: Optional[ThemeManager] = None

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
    
    def update_attached_file_completer(self, attached_filenames: List[str], base_dir: Path):
        if attached_filenames:
            try:
                # 1. 자동완성 후보가 될 상대 경로 리스트를 생성합니다.
                relative_paths = [
                    str(Path(p).relative_to(base_dir)) for p in attached_filenames
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

        if stripped_text.startswith('/theme'):
            words = stripped_text.split()
            # "/theme" 또는 "/theme v" 처럼 테마명 입력 중
            if len(words) <= 1 or (len(words) == 2 and not text.endswith(' ')):
                # 테마 목록 자동완성
                if self.theme_manager:
                    theme_names = self.theme_manager.get_available_themes()
                    theme_completer = FuzzyCompleter(
                        WordCompleter(theme_names, ignore_case=True, 
                                    meta_dict={name: "코드 하이라이트 테마" for name in theme_names})
                    )
                    yield from theme_completer.get_completions(document, complete_event)
                    return
                
        if stripped_text.startswith('/session'):
            words = stripped_text.split()
            # "/session" 또는 "/session <pa" 처럼 세션명 입력 중
            if len(words) <= 1 or (len(words) == 2 and not text.endswith(' ')):
                if self.config:
                    session_names = self.config.get_session_names(include_backups=True,exclude_current=getattr(self.app,"current_session_name",None))
                    session_completer = FuzzyCompleter(
                        WordCompleter(session_names, ignore_case=True)
                    )
                    yield from session_completer.get_completions(document, complete_event)
                    return
            
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
            
class ModelSearcher:
    """OpenRouter 모델을 검색하고 TUI를 통해 선택하여 `ai_models.txt`를 업데이트합니다."""
    
    API_URL = constants.API_URL
    
    def __init__(self, config: 'ConfigManager', theme_manager: 'ThemeManager', console: 'Console'):
        self.config = config
        self.theme_manager = theme_manager
        self.console = console

        self.all_models_map: Dict[str, Dict[str, Any]] = {}
        self.selected_ids: Set[str] = set()
        self.expanded_ids: Set[str] = set()
        self.display_models: List[Dict[str, Any]] = []

    def _fetch_all_models(self) -> bool:
        try:
            with self.console.status("[cyan]OpenRouter에서 모델 목록을 가져오는 중...", spinner="dots"):
                response = requests.get(self.API_URL, timeout=10)
                response.raise_for_status()
            self.all_models_map = {m['id']: m for m in response.json().get("data", [])}
            return True if self.all_models_map else False
        except requests.RequestException as e:
            self.console.print(f"[red]API 실패: {e}[/red]"); return False

    def _get_existing_model_ids(self) -> Set[str]:
        if not self.config.MODELS_FILE.exists(): return set()
        try:
            lines = self.config.MODELS_FILE.read_text(encoding="utf-8").splitlines()
            return {line.strip().split()[0] for line in lines if line.strip() and not line.strip().startswith("#")}
        except Exception: return set()

    def _save_models(self):
        try:
            final_ids = sorted(list(self.selected_ids))
            with self.config.MODELS_FILE.open("w", encoding="utf-8") as f:
                f.write("# OpenRouter.ai Models (gpt-cli auto-generated)\n\n")
                for model_id in final_ids:
                    model_data = self.all_models_map.get(model_id, {})
                    #f.write(f"{model_id} {model_data.get('context_length', 0)}\n")
                    context_len = model_data.get('context_length') or constants.DEFAULT_CONTEXT_LENGTH
                    f.write(f"{model_id} {context_len}\n")
            self.console.print(f"[green]성공: {len(final_ids)}개 모델로 '{self.config.MODELS_FILE}' 업데이트 완료.[/green]")
        except Exception as e:
            self.console.print(f"[red]저장 실패: {e}[/red]")
            
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
        palette = self.theme_manager.get_urwid_palette()
        main_loop = urwid.MainLoop(frame, palette=palette, screen=screen, unhandled_input=exit_handler)
        
        main_loop.run()
        #finally: screen.clear()

        if save_triggered:
            self._save_models()
        else:
            self.console.print("[dim]모델 선택이 취소되었습니다.[/dim]")

class DiffListBox(urwid.ListBox):
    """마우스 이벤트를 안전하게 처리하는 diff 전용 ListBox"""
    
    def mouse_event(self, size, event, button, col, row, focus):
        """마우스 이벤트 처리"""
        # 휠 스크롤만 처리
        if button == 4:  # 휠 업
            self.keypress(size, 'up')
            return True
        elif button == 5:  # 휠 다운
            self.keypress(size, 'down')
            return True
        
        # 클릭은 무시 (header/footer 깜빡임 방지)
        return True

class FileSelector:
    def __init__(self, config: 'ConfigManager', theme_manager: 'ThemeManager') -> None:
        """
        Args:
            config (ConfigManager): 경로 및 무시 규칙을 제공하는 설정 관리자.
        """
        self.config = config
        self.theme_manager = theme_manager
        self.spec = self.config.get_ignore_spec()
        self.items: List[Tuple[Path, bool]] = []  # (path, is_dir)
        self.selected: set[Path] = set()
        self.expanded: set[Path] = set()

    def refresh(self) -> None:
        self.items.clear()
        def visit_dir(path: Path, depth: int):
            path = path.resolve()
            # [변경] 전역 is_ignored 대신 self.config.is_ignored 사용
            if depth > 0 and self.config.is_ignored(path, self.spec):
                return
            
            self.items.append((path, True))
            
            if path in self.expanded:
                try:
                    children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
                    for child in children:
                        if child.is_dir():
                            visit_dir(child, depth + 1)
                        elif child.is_file():
                            # [변경] 전역 is_ignored 대신 self.config.is_ignored 사용
                            if self.config.is_ignored(child, self.spec):
                                continue
                            # 확장자/휴리스틱 필터 제거, ignore만 통과하면 추가
                            #if child.suffix.lower() in (*constants.PLAIN_EXTS, *constants.IMG_EXTS, constants.PDF_EXT):
                            self.items.append((child.resolve(), False))
                except Exception:
                    pass

        # [변경] 전역 BASE_DIR 대신 self.config.BASE_DIR 사용
        visit_dir(self.config.BASE_DIR, 0)
    
    def get_all_files_in_dir(self, folder: Path) -> set[Path]:                                       
        """주어진 폴더 내 모든 하위 파일을 무시 규칙을 적용하여 반환합니다."""
        result = set()
        if self.config.is_ignored(folder, self.spec):
            return result
        try:
            for entry in folder.iterdir():                                                           
                if self.config.is_ignored(entry, self.spec):
                    continue
                if entry.is_dir():                                                                   
                    result.update(self.get_all_files_in_dir(entry))                                       
                elif entry.is_file():                                                                
                    #if entry.suffix.lower() in (*constants.PLAIN_EXTS, *constants.IMG_EXTS, constants.PDF_EXT):                    
                    result.add(entry.resolve())
        except Exception:                                                                            
            pass                                                                                     
        return result
    
    def folder_all_selected(self, folder: Path) -> bool:                                             
        """해당 폴더의 모든 허용 파일이 선택되었는지 확인합니다."""
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files) and all_files.issubset(self.selected)                                 
                                                                                                    
    def folder_partial_selected(self, folder: Path) -> bool:                                         
        """해당 폴더의 파일 중 일부만 선택되었는지 확인합니다."""
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files & self.selected) and not all_files.issubset(self.selected)             
                                                                                                        
    # TUI
    def start(self) -> List[str]:
        """TUI를 시작하고 사용자가 선택한 파일 경로 목록을 반환합니다."""
        self.refresh()

        def mkwidget(data: Tuple[Path, bool]) -> urwid.Widget:                                           
            path, is_dir = data                                                                          
            try:
                relative_path = path.relative_to(self.config.BASE_DIR)
                depth = len(relative_path.parts) - (0 if is_dir or path == self.config.BASE_DIR else 1)
            except ValueError:
                depth = 0 # BASE_DIR 외부에 있는 경우
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
                self.selected = self.get_all_files_in_dir(self.config.BASE_DIR)
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
            ("info", f"현재 위치: {self.config.BASE_DIR}")
        ])
        
        header = urwid.Pile([
            help_text,
            urwid.Divider(),
        ])

        frame = urwid.Frame(listbox, header=header)
        palette = self.theme_manager.get_urwid_palette()
        urwid.MainLoop(                                                                                  
            frame,                                                                                       
            palette=palette,
            unhandled_input=keypress,                                                                    
        ).run() 
        return [str(p) for p in sorted(self.selected) if p.is_file()]

class CodeDiffer:
    def __init__(self, attached_files: List[str], session_name: str, messages: List[Dict],
                 theme_manager: 'ThemeManager', config: 'ConfigManager', console: 'Console'):
        # 입력 데이터
        self.attached_files = [Path(p) for p in attached_files]
        self.session_name = session_name
        self.messages = messages
        self.theme_manager = theme_manager
        self.config = config
        self.console = console

        # 상태
        self.expanded_items: Set[str] = set()
        self.selected_for_diff: List[Dict] = []
        self.previewing_item_id: Optional[str] = None
        self.preview_offset = 0
        self.preview_lines_per_page = 30

        self._visible_preview_lines: Optional[int] = None

        self._lexer_cache: Dict[str, Any] = {}

        # 표시/리스트 구성
        self.display_items: List[Dict] = []
        self.response_files: Dict[int, List[Path]] = self._scan_response_files()

        self.list_walker = urwid.SimpleFocusListWalker([])
        self.listbox = urwid.ListBox(self.list_walker)
        self.preview_text = urwid.Text("", wrap='clip')                         # flow
        self.preview_body = urwid.AttrMap(self.preview_text, {None: 'preview'})         # 배경 스타일
        self.preview_filler = urwid.Filler(self.preview_body, valign='top')     # flow → box
        self.preview_adapted = urwid.BoxAdapter(self.preview_filler, 1)         # 고정 높이(초기 1줄)
        self.preview_box = urwid.LineBox(self.preview_adapted, title="Preview") # 테두리(+2줄)
        self.preview_widget = urwid.AttrMap(self.preview_box, {None:'preview_border'}) # 외곽 스타일

        self._visible_preview_lines: Optional[int] = None  # 동적 가시 줄수 캐시
        self.main_pile = urwid.Pile([self.listbox])
        
        self.default_footer_text = "↑/↓:이동 | Enter:확장/프리뷰 | Space:선택 | D:Diff | Q:종료 | PgUp/Dn:스크롤"
        self.footer = urwid.AttrMap(urwid.Text(self.default_footer_text), 'header')
        self.footer_timer: Optional[threading.Timer] = None # 활성 타이머 추적

        self.frame = urwid.Frame(self.main_pile, footer=self.footer)
        self.main_loop: Optional[urwid.MainLoop] = None

        # diff 뷰 복귀/복원용
        self._old_input_filter = None
        self._old_unhandled_input = None
        self._old_widget = None

        self.h_offset = 0  # diff 뷰 가로 오프셋
        self.preview_h_offset = 0  # preview 뷰 가로 오프셋
        self.max_line_length = 0  # 현재 보이는 줄 중 최대 길이

        self.context_lines = 3 # 기본 문맥 줄 수
        self.show_full_diff = False # 전체 보기 모드 토글 상태
        
    def _get_lexer_for_path(self, path: Path) -> Any:
        key = path.suffix.lower()
        if key in self._lexer_cache:
            return self._lexer_cache[key]
        try:
            lexer = guess_lexer_for_filename(path.name, "")
        except Exception:
            lexer = TextLexer()
        self._lexer_cache[key] = lexer
        return lexer

    def _lex_file_by_lines(self, file_path: Path, lexer=None) -> Dict[int, List[Tuple]]:
        """
        파일 전체를 한 번에 렉싱한 후, 줄별로 토큰을 분리합니다.
        이렇게 하면 멀티라인 docstring이 String.Doc으로 올바르게 인식됩니다.
        
        Returns:
            Dict[int, List[Tuple]]: {줄번호(0-based): [(token_type, value), ...]}
        """
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return {}
        
        if lexer is None:
            try:
                lexer = self._get_lexer_for_path(file_path)
            except Exception:
                lexer = TextLexer()
        
        # 전체 파일을 한 번에 렉싱
        tokens = list(pyg_lex(content, lexer))
        
        # 줄별로 토큰 분리
        line_tokens = {}
        current_line = 0
        current_line_tokens = []
        
        for ttype, value in tokens:
            if not value:
                continue
            
            # 멀티라인 값 처리
            if '\n' in value:
                lines = value.split('\n')
                
                # 첫 번째 줄 부분
                if lines[0]:
                    current_line_tokens.append((ttype, lines[0]))
                
                # 첫 줄 저장
                if current_line_tokens:
                    line_tokens[current_line] = current_line_tokens
                
                # 중간 줄들
                for i in range(1, len(lines) - 1):
                    current_line += 1
                    if lines[i]:
                        line_tokens[current_line] = [(ttype, lines[i])]
                    else:
                        line_tokens[current_line] = []
                
                # 마지막 줄 시작
                current_line += 1
                current_line_tokens = []
                if lines[-1]:
                    current_line_tokens.append((ttype, lines[-1]))
            else:
                # 단일 라인 토큰
                current_line_tokens.append((ttype, value))
        
        # 마지막 줄 저장
        if current_line_tokens:
            line_tokens[current_line] = current_line_tokens
        
        return line_tokens
    
    def _get_cols(self) -> int:
        try:
            if self.main_loop:
                cols, _ = self.main_loop.screen.get_cols_rows()
                return cols
        except Exception:
            pass
        return 120 # safe fallback

    def _calc_preview_visible_lines(self) -> int:
        """
        터미널 rows에 맞춰 프리뷰 본문(코드) 가시 줄 수를 계산.
        LineBox 테두리 2줄을 제외하고 반환.
        """
        try:
            cols, rows = self.main_loop.screen.get_cols_rows()
        except Exception:
            rows = 24  # 폴백

        min_list_rows = 6   # 목록이 최소 확보할 줄 수
        footer_rows = 1     # Frame footer 가정
        safety = 1

        # 프리뷰에 할당 가능한 총 높이(테두리 포함)
        available_total = max(0, rows - (min_list_rows + footer_rows + safety))
        # 본문 라인 수 = 총 높이 - 2(위/아래 테두리)
        visible_lines = max(1, available_total - 2)
        # 유저 설정보다 크지 않게 제한
        return min(self.preview_lines_per_page, visible_lines)

    def _ensure_preview_in_pile(self, adapted_widget: urwid.Widget) -> None:
        """
        Pile 맨 아래에 프리뷰 박스를 넣되, 이미 있으면 교체한다.
        """
        if len(self.main_pile.contents) == 1:
            # 아직 프리뷰가 없으면 추가
            self.main_pile.contents.append((adapted_widget, self.main_pile.options('pack')))
        else:
            # 이미 프리뷰가 있으면 위젯/옵션을 교체
            self.main_pile.contents[-1] = (adapted_widget, self.main_pile.options('pack'))

    # ─────────────────────────────────────────────
    # (추가) 프리뷰 스크롤 헬퍼
    # ─────────────────────────────────────────────
    def _scroll_preview(self, key: str) -> None:
        if not self.previewing_item_id:
            return
        try:
            item_data = next(item for item in self.display_items if item.get('id') == self.previewing_item_id)
            content = item_data['path'].read_text(encoding='utf-8', errors='ignore').splitlines()
            total_lines = len(content)
            max_offset = max(0, total_lines - self.preview_lines_per_page)

            if key == 'page down':
                self.preview_offset = min(max_offset, self.preview_offset + self.preview_lines_per_page)
            elif key == 'page up':
                self.preview_offset = max(0, self.preview_offset - self.preview_lines_per_page)

            self._update_preview()
        except StopIteration:
            pass
        except IOError:
            pass

    # 임시 메시지를 표시하고 원래대로 복원하는 헬퍼 메서드
    def _show_temporary_footer(self, message: str, duration: float = 2.0):
        # 기존 타이머가 있으면 제거
        if self.footer_timer is not None:
            self.main_loop.remove_alarm(self.footer_timer)
            self.footer_timer = None

        # 새 메시지 표시
        self.footer.original_widget.set_text(message)
        # draw_screen()은 set_alarm_in 콜백에서 호출되므로 여기서 필요 없음
        
        # 지정된 시간 후에 원래 푸터로 복원하는 알람 설정
        self.footer_timer = self.main_loop.set_alarm_in(
            sec=duration,
            callback=self._restore_default_footer
        )

    # 기본 푸터 메시지로 복원하는 메서드
    def _restore_default_footer(self, loop, user_data=None):
        self.footer.original_widget.set_text(self.default_footer_text)
        # 알람 ID 정리
        self.footer_timer = None
        # 화면 갱신은 루프가 자동으로 처리하므로 draw_screen() 호출 불필요

    def handle_selection(self, item):
        is_in_list = any(s['id'] == item['id'] for s in self.selected_for_diff)
        if not is_in_list:
            if len(self.selected_for_diff) >= 2:
                self._show_temporary_footer("[!] 2개 이상 선택할 수 없습니다.")
                return
            if item.get('source') == 'local':
                self.selected_for_diff = [s for s in self.selected_for_diff if s.get('source') != 'local']
            self.selected_for_diff.append(item)
        else:
            self.selected_for_diff = [s for s in self.selected_for_diff if s['id'] != item['id']]
        self._show_temporary_footer(f" {len(self.selected_for_diff)}/2 선택됨. 'd' 키를 눌러 diff를 실행하세요.")

    def _scan_response_files(self) -> Dict[int, List[Path]]:
        if not self.config.CODE_OUTPUT_DIR.is_dir(): return {}
        pattern = re.compile(rf"codeblock_{re.escape(self.session_name)}_(\d+)_.*")
        msg_files: Dict[int, List[Path]] = {}
        for p in self.config.CODE_OUTPUT_DIR.glob(f"codeblock_{self.session_name}_*"):
            match = pattern.match(p.name)
            if match:
                msg_id = int(match.group(1))
                if msg_id not in msg_files: msg_files[msg_id] = []
                msg_files[msg_id].append(p)
        return {k: sorted(v) for k, v in sorted(msg_files.items(), reverse=True)}
    
    def _render_all(self, keep_focus: bool = True):
        pos = 0
        if keep_focus:
            try:
                pos = self.listbox.focus_position
            except IndexError:
                pos = 0

        self.display_items = []
        widgets = []

        # 로컬 파일 섹션
        if self.attached_files:
            section_id = "local_files"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(
                urwid.AttrMap(
                    urwid.SelectableIcon(f"{arrow} Current Local Files ({len(self.attached_files)})"),
                    'header', 'header'
                )
            )
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in self.attached_files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"local_{p.name}"
                    widgets.append(
                        urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus')
                    )
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "local"})

        # response 파일 섹션
        for msg_id, files in self.response_files.items():
            section_id = f"response_{msg_id}"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(
                urwid.AttrMap(
                    urwid.SelectableIcon(f"{arrow} Response #{msg_id}"),
                    'header', 'header'
                )
            )
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"response_{msg_id}_{p.name}"
                    widgets.append(
                        urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus')
                    )
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "response", "msg_id": msg_id})

        if not widgets:
            placeholder = urwid.Text(("info", "No files found.\n- 첨부 파일을 추가하거나\n- 코드 블록이 저장된 디렉터리(gpt_codes 또는 gpt_outputs/codes)를 확인하세요."))
            widgets.append(urwid.AttrMap(placeholder, None, focus_map='myfocus'))
            self.display_items.append({"id": "placeholder", "type": "placeholder"})

        self.list_walker[:] = widgets
        if widgets:
            self.listbox.focus_position = min(pos, len(widgets) - 1)

        self._update_preview()

    def _render_preview(self):
        """파일 프리뷰를 렌더링 (가로 스크롤 지원)"""
        item_id = self.previewing_item_id
        is_previewing = len(self.main_pile.contents) > 1

        if not item_id:
            if is_previewing:
                self.main_pile.contents.pop()
            return

        item_data = next((it for it in self.display_items if it.get('id') == item_id), None)
        if not (item_data and item_data['type'] == 'file'):
            if is_previewing:
                self.main_pile.contents.pop()
            return

        try:
            file_path = item_data['path']
            all_lines = file_path.read_text(encoding='utf-8', errors='ignore').splitlines()
            total = len(all_lines)

            # 1) 가시 줄 수 산정
            visible_lines = self._calc_preview_visible_lines()
            self._visible_preview_lines = visible_lines

            # 2) 세로 오프셋 보정
            max_offset = max(0, total - visible_lines)
            if self.preview_offset > max_offset:
                self.preview_offset = max_offset

            start = self.preview_offset
            end = min(start + visible_lines, total)

            # 3) 최대 줄 길이 계산 (가로 스크롤 범위 결정용)
            visible_line_texts = all_lines[start:end]
            self.max_line_length = max(len(line.expandtabs(4)) for line in visible_line_texts) if visible_line_texts else 0

            # 4) 제목 갱신 (가로 스크롤 정보 포함)
            info = f" [{start+1}-{end}/{total}]"
            if self.preview_h_offset > 0:
                info += f" [H:{self.preview_h_offset}]"
            if self.max_line_length > 100:
                info += " [←→]"
            self.preview_box.set_title(f"Preview: {file_path.name}{info}")

            # 5) 전체 파일 렉싱 (한 번만)
            line_tokens_dict = self._lex_file_by_lines(file_path)
            
            # 6) 테마 가져오기
            preview_theme_name = self.theme_manager.current_theme_name
            preview_theme = self.theme_manager._FG_THEMES.get(preview_theme_name, {})
            
            markup = []
            digits = max(2, len(str(total)))
            
            for idx in range(start, end):
                # 줄번호
                lno_attr = self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black')
                markup.append((lno_attr, f"{idx+1:>{digits}} │ "))
                
                # 가로 오프셋 적용을 위한 코드 재구성
                line_text = all_lines[idx].expandtabs(4)
                
                # 가로 오프셋 적용
                if self.preview_h_offset > 0:
                    # 왼쪽에 더 있음을 표시
                    if line_text and self.preview_h_offset < len(line_text):
                        markup.append((self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black'), "←"))
                        # 오프셋만큼 잘라냄
                        visible_text = line_text[self.preview_h_offset:]
                    else:
                        visible_text = ""
                else:
                    visible_text = line_text
                
                # 토큰화된 렌더링
                if idx in line_tokens_dict:
                    # 오프셋이 적용된 visible_text를 다시 토큰화해야 함
                    # 하지만 이미 토큰화된 데이터가 있으므로, 위치 기반으로 처리
                    accumulated_pos = 0
                    for ttype, value in line_tokens_dict[idx]:
                        token_start = accumulated_pos
                        token_end = accumulated_pos + len(value)
                        
                        # 토큰이 가시 영역에 포함되는지 확인
                        if token_end > self.preview_h_offset:
                            # 토큰의 가시 부분만 추출
                            if token_start < self.preview_h_offset:
                                # 토큰의 일부가 잘림
                                visible_value = value[self.preview_h_offset - token_start:]
                            else:
                                # 토큰 전체가 보임
                                visible_value = value
                            
                            base = self.theme_manager._simplify_token_type(ttype)
                            fg_color = preview_theme.get(base, 'white')
                            attr = self.theme_manager._mk_attr(fg_color, constants.PREVIEW_BG, 'black')
                            markup.append((attr, visible_value))
                        
                        accumulated_pos = token_end
                else:
                    # 토큰 정보가 없으면 일반 텍스트로
                    if visible_text:
                        attr = self.theme_manager._mk_attr('light gray', constants.PREVIEW_BG, 'black')
                        markup.append((attr, visible_text))
                
                # 오른쪽에 더 있음을 표시
                if self.preview_h_offset + len(visible_text) < len(line_text):
                    markup.append((self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black'), "→"))
                
                # 줄바꿈 추가
                if idx < end - 1:
                    markup.append('\n')

            # 7) 마크업 적용
            self.preview_text.set_text(markup)

            # 8) 높이 조정
            self.preview_adapted.height = visible_lines

            # 9) Pile에 추가/교체
            if not is_previewing:
                self.main_pile.contents.append((self.preview_widget, self.main_pile.options('pack')))
            else:
                self.main_pile.contents[-1] = (self.preview_widget, self.main_pile.options('pack'))

        except Exception as e:
            error_attr = self.theme_manager._mk_attr('light red', constants.PREVIEW_BG, 'black')
            self.preview_text.set_text([(error_attr, f"Preview error: {e}")])
            self.preview_adapted.height = max(1, self._visible_preview_lines or 1)
            if not is_previewing:
                self.main_pile.contents.append((self.preview_widget, self.main_pile.options('pack')))
            else:
                self.main_pile.contents[-1] = (self.preview_widget, self.main_pile.options('pack'))

    # _update_preview 메서드도 수정 (단순히 _render_preview 호출)
    def _update_preview(self):
        """프리뷰 업데이트 - _render_preview를 호출"""
        self._render_preview()  # item 파라미터는 사용하지 않으므로 None 전달

    def _input_filter(self, keys, raw):
        try:
            if self.main_loop and self.main_loop.widget is self.frame:
                out = []
                for k in keys:
                    # 마우스 휠
                    if isinstance(k, tuple) and len(k) >= 2 and self.previewing_item_id:
                        ev, btn = k[0], k[1]
                        if ev == 'mouse press' and btn in (4, 5):
                            try:
                                it = next(x for x in self.display_items if x.get('id') == self.previewing_item_id)
                                total = len(it['path'].read_text(encoding='utf-8', errors='ignore').splitlines())
                                lines_per_page = self._visible_preview_lines or self.preview_lines_per_page
                                max_off = max(0, total - lines_per_page)
                                step = max(1, lines_per_page // 2)
                                if btn == 4:
                                    self.preview_offset = max(0, self.preview_offset - step)
                                else:
                                    self.preview_offset = min(max_off, self.preview_offset + step)
                                self._update_preview()
                                try: self.main_loop.draw_screen()
                                except Exception: pass
                            except Exception:
                                pass
                            continue  # 소비

                    if isinstance(k, str) and self.previewing_item_id:
                        kl = k.lower()
                        handled = False
                        if kl in ('page up', 'page down', 'home', 'end'):
                            try:
                                it = next(x for x in self.display_items if x.get('id') == self.previewing_item_id)
                                total = len(it['path'].read_text(encoding='utf-8', errors='ignore').splitlines())
                                lines_per_page = self._visible_preview_lines or self.preview_lines_per_page
                                max_off = max(0, total - lines_per_page)

                                if   kl == 'page up':
                                    self.preview_offset = max(0, self.preview_offset - lines_per_page)
                                elif kl == 'page down':
                                    self.preview_offset = min(max_off, self.preview_offset + lines_per_page)
                                elif kl == 'home':
                                    self.preview_offset = 0
                                elif kl == 'end':
                                    self.preview_offset = max_off

                                self._update_preview()
                                try: self.main_loop.draw_screen()
                                except Exception: pass
                                handled = True
                            except Exception:
                                handled = False

                        if handled:
                            continue  # 소비

                    out.append(k)
                return out
        except Exception:
            return keys
        return keys
    
    def handle_input(self, key):
        if not isinstance(key, str):
            return
        
        # preview 가로 스크롤 처리
        if self.previewing_item_id:
            handled = False
            
            if key == 'right':
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset += 10
                    self._update_preview()
                    handled = True
            
            elif key == 'left':
                if self.preview_h_offset > 0:
                    self.preview_h_offset = max(0, self.preview_h_offset - 10)
                    self._update_preview()
                    handled = True
            
            elif key == 'shift right':
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset = min(self.max_line_length - 40, self.preview_h_offset + 30)
                    self._update_preview()
                    handled = True
            
            elif key == 'shift left':
                if self.preview_h_offset > 0:
                    self.preview_h_offset = max(0, self.preview_h_offset - 30)
                    self._update_preview()
                    handled = True
            
            elif key in ('home', 'g'):
                if self.preview_h_offset > 0:
                    self.preview_h_offset = 0
                    self._update_preview()
                    handled = True
            
            elif key in ('end', 'G'):
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset = max(0, self.max_line_length - 40)
                    self._update_preview()
                    handled = True
            
            if handled:
                return

        try:
            pos = self.listbox.focus_position
            item = self.display_items[pos]
        except IndexError:
            # 프레임으로 전달
            self.frame.keypress(self.main_loop.screen_size, key)
            return

        if key.lower() == 'q':
            raise urwid.ExitMainLoop()

        elif key == 'enter':
            if item['type'] == 'section':
                # 섹션 확장/축소
                if item['id'] in self.expanded_items:
                    self.expanded_items.remove(item['id'])
                else:
                    self.expanded_items.add(item['id'])
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
            if len(self.selected_for_diff) == 2:
                self._show_diff_view()
            else:
                message = f"[!] 2개 항목을 선택해야 diff가 가능합니다. (현재 {len(self.selected_for_diff)}개 선택됨)"
                self._show_temporary_footer(message)
        else:
            # 기본 처리는 프레임으로
            self.frame.keypress(self.main_loop.screen_size, key)

    def _build_diff_line_widget(
        self,
        kind: str,
        code_line: str,
        old_no: Optional[int],
        new_no: Optional[int],
        digits_old: int,
        digits_new: int,
        line_tokens: Optional[List[Tuple]] = None,  # 사전 렉싱된 토큰
        h_offset: int = 0
    ) -> urwid.Text:
        """
        사전 렉싱된 토큰 정보를 활용한 diff 라인 렌더링
        """

        bg, fb_bg = self.theme_manager._bg_for_kind(kind)
        fgmap = self.theme_manager.get_fg_map_for_diff(kind) or self.theme_manager.get_fg_map_for_diff('ctx')
        
        sign_char = '+' if kind == 'add' else '-' if kind == 'del' else ' '
        old_s = f"{old_no}" if old_no is not None else ""
        new_s = f"{new_no}" if new_no is not None else ""

        parts: List[Tuple[urwid.AttrSpec, str]] = []
        
        # 구터는 항상 표시 (스크롤 영향 없음)
        parts.append((self.theme_manager._mk_attr(self.theme_manager._SIGN_FG[kind], bg, fb_bg), f"{sign_char} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._LNO_OLD_FG, bg, fb_bg), f"{old_s:>{digits_old}} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._LNO_NEW_FG, bg, fb_bg), f"{new_s:>{digits_new}} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._SEP_FG, bg, fb_bg), "│ "))
        
        # 코드 부분에 가로 오프셋 적용
        safe = code_line.expandtabs(4).replace('\n','').replace('\r','')
        
        # 오프셋 적용
        if h_offset > 0:
            if h_offset < len(safe):
                safe = safe[h_offset:]
            else:
                safe = ""
        
        # 스크롤 표시
        if h_offset > 0 and safe:
            parts.append((self.theme_manager._mk_attr('dark gray', bg, fb_bg), "←"))
        
        # ✅ 핵심: 사전 렉싱된 토큰 사용
        if line_tokens:
            # 토큰 정보가 있으면 정확한 하이라이팅
            accumulated_pos = 0
            for ttype, value in line_tokens:
                token_start = accumulated_pos
                token_end = accumulated_pos + len(value)
                
                # 오프셋 적용된 가시 영역 체크
                if token_end > h_offset:
                    if token_start < h_offset:
                        # 토큰이 잘림
                        visible_value = value[h_offset - token_start:]
                    else:
                        # 전체 보임
                        visible_value = value
                    base = self.theme_manager._simplify_token_type(ttype)
                    #self.console.print(fgmap.get(base, 'white'), base)
                    #time.sleep(1)
                    parts.append((self.theme_manager._mk_attr(fgmap.get(base, 'white'), bg, fb_bg), visible_value))
                
                accumulated_pos = token_end
        else:
            # 토큰 정보가 없으면 기존 방식 (줄 단위 렉싱)
            if safe:
                try:
                    lexer = TextLexer()
                    for ttype, value in pyg_lex(safe, lexer):
                        if not value:
                            continue
                        v = value.replace('\n','').replace('\r','')
                        if not v:
                            continue
                        base = self.theme_manager._simplify_token_type(ttype)
                        parts.append((self.theme_manager._mk_attr(fgmap.get(base, 'white'), bg, fb_bg), v))
                except:
                    parts.append((self.theme_manager._mk_attr('white', bg, fb_bg), safe))
        
        # 오른쪽 스크롤 표시
        original_len = len(code_line.expandtabs(4))
        visible_len = len(safe)
        if h_offset + visible_len < original_len:
            parts.append((self.theme_manager._mk_attr('dark gray', bg, fb_bg), "→"))
        
        # 패딩
        padding = ' ' * 200
        parts.append((self.theme_manager._mk_attr('default', bg, fb_bg), padding))

        return urwid.Text(parts, wrap='clip')

    def _show_diff_view(self):
        import re
        if len(self.selected_for_diff) != 2:
            return

        item1, item2 = self.selected_for_diff
        # old/new 결정(응답 순서 기준)
        if item1.get("source") == "local" or item1.get("msg_id", 0) < item2.get("msg_id", 0):
            old_item, new_item = item1, item2
        else:
            old_item, new_item = item2, item1

        try:
            old_text = old_item['path'].read_text(encoding='utf-8', errors='ignore')
            new_text = new_item['path'].read_text(encoding='utf-8', errors='ignore')
            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
        except Exception as e:
            self.footer.original_widget.set_text(f"Error: {e}")
            return

        # ✅ 핵심: 전체 파일을 먼저 렉싱하여 토큰 정보 획득
        try:
            old_lexer = self._get_lexer_for_path(old_item['path'])
        except Exception:
            old_lexer = TextLexer()
        
        try:
            new_lexer = self._get_lexer_for_path(new_item['path'])
        except Exception:
            new_lexer = TextLexer()
        
        # 전체 파일 렉싱하여 줄별 토큰 매핑 생성
        old_line_tokens = self._lex_file_by_lines(old_item['path'], old_lexer)
        new_line_tokens = self._lex_file_by_lines(new_item['path'], new_lexer)

        if self.show_full_diff:
            context_lines = max(len(old_lines), len(new_lines))
        else:
            context_lines = self.context_lines

        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{old_item['path'].name}",
            tofile=f"b/{new_item['path'].name}",
            lineterm='',
            n=context_lines
        ))
        if not diff:
            self.footer.original_widget.set_text("두 파일이 동일합니다.")
            return

        # 필요한 변수들
        digits_old = max(2, len(str(len(old_lines))))
        digits_new = max(2, len(str(len(new_lines))))

        # 가로 스크롤 상태
        h_offset_ref = {'value': 0}
        max_line_len = 0
        
        # diff 라인에서 최대 길이 계산
        for line in diff:
            if line and not line.startswith('@@'):
                content = line[1:] if line[0] in '+-' else line
                max_line_len = max(max_line_len, len(content.expandtabs(4)))

        # diff 위젯 생성 함수 수정
        def generate_diff_widgets(h_offset: int) -> List[urwid.Widget]:
            widgets: List[urwid.Widget] = []
            
            # 파일 헤더
            widgets.append(urwid.Text(('diff_file_old', f"--- a/{old_item['path'].name}"), wrap='clip'))
            widgets.append(urwid.Text(('diff_file_new', f"+++ b/{new_item['path'].name}"), wrap='clip'))
            
            # 헝크 파서
            old_ln = None
            new_ln = None
            hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
            
            i = 0
            while i < len(diff):
                line = diff[i]

                # 파일 헤더 스킵
                if line.startswith('---') and i == 0:
                    i += 1; continue
                if line.startswith('+++') and i == 1:
                    i += 1; continue

                # 헝크 헤더
                if line.startswith('@@'):
                    m = hunk_re.match(line)
                    if m:
                        old_ln = int(m.group(1))
                        new_ln = int(m.group(3))
                    widgets.append(urwid.Text(('diff_hunk', line), wrap='clip'))
                    i += 1
                    continue

                # ✅ 수정된 emit_kind: 토큰 정보 활용
                def emit_kind(kind: str, old_no: Optional[int], new_no: Optional[int], content: str):
                    # 원본 파일에서 해당 라인의 토큰 정보 가져오기
                    line_tokens = None
                    if kind == 'del' and old_no is not None:
                        # 삭제된 라인은 old 파일의 토큰 정보 사용
                        line_tokens = old_line_tokens.get(old_no - 1)  # 0-based index
                    elif kind == 'add' and new_no is not None:
                        # 추가된 라인은 new 파일의 토큰 정보 사용
                        line_tokens = new_line_tokens.get(new_no - 1)  # 0-based index
                    elif kind == 'ctx':
                        # context 라인은 둘 다 같으므로 new 파일 사용
                        if new_no is not None:
                            line_tokens = new_line_tokens.get(new_no - 1)
                    
                    widgets.append(
                        self._build_diff_line_widget(
                            kind=kind,
                            code_line=content,
                            old_no=old_no,
                            new_no=new_no,
                            digits_old=digits_old,
                            digits_new=digits_new,
                            line_tokens=line_tokens,  # 토큰 정보 전달
                            h_offset=h_offset,
                        )
                    )

                # '-' 다음이 '+' 페어
                if line.startswith('-') and i + 1 < len(diff) and diff[i+1].startswith('+'):
                    old_line = line[1:]
                    new_line = diff[i+1][1:]
                    emit_kind('del', old_ln, None, old_line)
                    emit_kind('add', None, new_ln, new_line)
                    if old_ln is not None: old_ln += 1
                    if new_ln is not None: new_ln += 1
                    i += 2
                    continue

                # 단일 라인
                if line.startswith('-'):
                    emit_kind('del', old_ln, None, line[1:])
                    if old_ln is not None: old_ln += 1
                elif line.startswith('+'):
                    emit_kind('add', None, new_ln, line[1:])
                    if new_ln is not None: new_ln += 1
                elif line.startswith(('---','+++')):
                    widgets.append(urwid.Text(('diff_meta', line), wrap='clip'))
                else:
                    content = line[1:] if line.startswith(' ') else line
                    emit_kind('ctx', old_ln, new_ln, content)
                    if old_ln is not None: old_ln += 1
                    if new_ln is not None: new_ln += 1

                i += 1
            
            return widgets

        # 초기 위젯 생성
        diff_walker = urwid.SimpleFocusListWalker(generate_diff_widgets(0))
        diff_listbox = DiffListBox(diff_walker)

        header = urwid.AttrMap(
            urwid.Text(f"Diff: {old_item['path'].name} → {new_item['path'].name}", wrap='clip'),
            'header'
        )
        
        # footer 업데이트 함수
        def update_footer():
            scroll_info = ""
            if h_offset_ref['value'] > 0:
                scroll_info = f" [H:{h_offset_ref['value']}]"
            if max_line_len > 100:
                scroll_info += f" [←→: 가로스크롤]"
            context_info = f" [+/-/F: 문맥({self.context_lines})]" if not self.show_full_diff else " [문맥: 전체]"
            footer_text = f"PgUp/Dn: 스크롤 | Home/End: 처음/끝 | ←→: 가로 | Q: 닫기{scroll_info}{context_info}"
            return urwid.AttrMap(urwid.Text(footer_text, wrap='clip'), 'header')
        
        diff_footer = update_footer()
        diff_frame = urwid.Frame(diff_listbox, header=header, footer=diff_footer)

        # 기존 상태 백업
        self._old_widget = self.main_loop.widget
        self._old_unhandled_input = self.main_loop.unhandled_input
        self._old_input_filter = self.main_loop.input_filter
        self.main_loop.widget = diff_frame

        # ✅ 핵심: diff 뷰 재생성 함수
        def regenerate_diff_view():
            # 현재 포커스 위치 저장
            try:
                current_focus = diff_listbox.focus_position
            except:
                current_focus = 0
            
            # 위젯 리스트 재생성
            new_widgets = generate_diff_widgets(h_offset_ref['value'])
            diff_walker[:] = new_widgets
            
            # 포커스 복원
            if new_widgets:
                diff_listbox.focus_position = min(current_focus, len(new_widgets) - 1)
            
            # footer 업데이트
            diff_frame.footer = update_footer()
            
            # 화면 다시 그리기
            self.main_loop.draw_screen()

        # 키 처리
        def diff_unhandled(key):
            if isinstance(key, str):
                if key.lower() == 'q':
                    # 원래 상태 복원
                    self.main_loop.widget = self._old_widget
                    self.main_loop.unhandled_input = self._old_unhandled_input
                    self.main_loop.input_filter = self._old_input_filter
                    try:
                        self.main_loop.draw_screen()
                    except Exception:
                        pass

                elif key == '+':
                    self.context_lines = min(self.context_lines + 2, 99)
                    self.show_full_diff = False
                    regenerate_diff_view()

                elif key == '-':
                    self.context_lines = max(0, self.context_lines - 2)
                    self.show_full_diff = False
                    regenerate_diff_view()

                elif key == 'f':
                    self.show_full_diff = not self.show_full_diff
                    regenerate_diff_view()
                
                # ✅ 가로 스크롤 처리
                elif key == 'right':
                    if h_offset_ref['value'] < max_line_len - 40:  # 여유 40자
                        h_offset_ref['value'] += 10
                        regenerate_diff_view()
                
                elif key == 'left':
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = max(0, h_offset_ref['value'] - 10)
                        regenerate_diff_view()
                
                elif key == 'shift right':  # 빠른 스크롤
                    if h_offset_ref['value'] < max_line_len - 40:
                        h_offset_ref['value'] = min(max_line_len - 40, h_offset_ref['value'] + 30)
                        regenerate_diff_view()
                
                elif key == 'shift left':  # 빠른 스크롤
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = max(0, h_offset_ref['value'] - 30)
                        regenerate_diff_view()
                
                elif key in ('home', 'g'):  # 줄 시작으로
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = 0
                        regenerate_diff_view()
                
                elif key in ('end', 'G'):  # 줄 끝으로
                    if h_offset_ref['value'] < max_line_len - 40:
                        h_offset_ref['value'] = max_line_len - 40
                        regenerate_diff_view()

        self.main_loop.unhandled_input = diff_unhandled


    def start(self):
        self._render_all(keep_focus=False)

        if len(self.list_walker) > 0:
            try:
                self.listbox.focus_position = 0
            except IndexError:
                pass

        screen = urwid.raw_display.Screen()
        # 마우스 트래킹 활성화 (가능한 터미널에서 휠 이벤트 수신)
        try:
            screen.set_terminal_properties(colors=256)
            screen.set_mouse_tracking()
            
        except Exception as e:
            self.console.print(f"[yellow]경고: 터미널 속성 설정 실패 ({e})[/yellow]")

        palette = self.theme_manager.get_urwid_palette()
        self.main_loop = urwid.MainLoop(
            self.frame,
            palette=palette,
            screen=screen,
            unhandled_input=self.handle_input,
            input_filter=self._input_filter
        )
        self.theme_manager.apply_to_urwid_loop(self.main_loop)
        self.main_loop.run()
        
class ThemeManager:
    """
    Urwid과 Rich의 테마, 팔레트, 문법 하이라이팅 관련 로직을 전담하는 클래스.
    """
    # --- 클래스 레벨 상수: 테마 데이터베이스 ---
    _FG_THEMES = constants.THEME_DATA

    _COLOR_ALIASES = constants.COLOR_ALIASES

    _TRUECOLOR_FALLBACKS = constants.TRUECOLOR_FALLBACKS

    _SIGN_FG = constants.SIGN_FG
    _LNO_OLD_FG = constants.LNO_OLD_FG
    _LNO_NEW_FG = constants.LNO_NEW_FG
    _SEP_FG = constants.SEP_FG
    #_PREVIEW_BG = constants.PREVIEW_BG

    _FG_MAP_DIFF: Dict[str, Dict[str, str]] = {}

    def __init__(self, default_theme: str = 'monokai-ish'):
        self.current_theme_name: str = ""
        self.current_urwid_palette: List[Tuple] = []
        self.set_theme(default_theme)

    @classmethod
    def get_available_themes(cls) -> List[str]:
        """사용 가능한 모든 테마의 이름 목록을 반환합니다."""
        return sorted(cls._FG_THEMES.keys())

    def set_theme(self, theme_name: str):
        if theme_name not in self._FG_THEMES:
            raise KeyError(f"알 수 없는 테마: '{theme_name}'.")
        self.current_theme_name = theme_name
        self.current_urwid_palette = self._generate_urwid_palette()
        theme_map = self._FG_THEMES[theme_name]
        self._FG_MAP_DIFF = {'add': theme_map, 'del': theme_map, 'ctx': theme_map}

    def set_diff_theme(self, kind: str, theme_name: str):
        if kind not in ('add','del','ctx'):
            raise ValueError("Invalid diff kind")
        if theme_name not in self._FG_THEMES:
            raise KeyError("Unknown theme")
        # kind 하나만 변경
        self._FG_MAP_DIFF[kind] = self._FG_THEMES[theme_name]

    def set_global_theme(self, name: str):
        """
        프리뷰(syn_*) 팔레트 + diff 전경색 맵을 모두 동일 테마로 통일.
        """
        self.set_theme(name)

    def _demote_truecolor_to_256(self, spec: str, default: str = 'white') -> str:
        """
        HEX → 256색 근사로 강등
        """
        if spec is None or spec == '':
            return spec or default
        color, attrs = self._split_color_attrs(spec)
        if color and color.startswith('#'):
            color = self._TRUECOLOR_FALLBACKS.get(color.lower(), default)
        out = color or default
        if attrs:
            out += ',' + attrs
        return out
    
    def _normalize_color_spec(self, spec: str) -> str:
        """
        - COLOR_ALIASES 적용
        - 대소문자 정규화
        """
        if spec is None or spec == '':
            return spec
        color, attrs = self._split_color_attrs(spec)
        if not color:
            return spec
        key = color.lower()
        color = self._COLOR_ALIASES.get(key, color)  # 별칭이면 HEX로 치환
        out = color
        if attrs:
            out += ',' + attrs
        return out

    def _mk_attr(self, fg: str, bg: str, fb_bg: str = 'default') -> urwid.AttrSpec:
        """색상 문자열로 urwid.AttrSpec 객체를 생성합니다."""
        fg_norm = self._normalize_color_spec(fg) if fg else fg
        bg_norm = self._normalize_color_spec(bg) if bg else bg
        try:
            return urwid.AttrSpec(fg_norm, bg_norm)
        except Exception:
            # 폴백
            fg_f = self._demote_truecolor_to_256(fg_norm, default='white') if fg_norm else fg_norm
            bg_f = self._demote_truecolor_to_256(bg_norm, default=fb_bg) if bg_norm else fb_bg
        try:
            return urwid.AttrSpec(fg_f, bg_f)
        except Exception:
            return urwid.AttrSpec('white', fb_bg)

    def _bg_for_kind(self, kind: str) -> tuple[str, str]:
        if kind == 'add':
            return (constants.DIFF_ADD_BG, constants.DIFF_ADD_BG_FALLBACK)
        if kind == 'del':
            return (constants.DIFF_DEL_BG, constants.DIFF_DEL_BG_FALLBACK)
        return (constants.DIFF_CTX_BG, constants.DIFF_CTX_BG_FALLBACK)

    def get_fg_map_for_diff(self, kind: str) -> Dict[str, str]:
        if not self._FG_MAP_DIFF:
            base = self._FG_THEMES.get(self.current_theme_name) or self._FG_THEMES.get('monokai-ish')
            self._FG_MAP_DIFF = {'add': base, 'del': base, 'ctx': base}
        return self._FG_MAP_DIFF.get(kind, self._FG_THEMES.get(self.current_theme_name, {}))
    
    @staticmethod
    def _simplify_token_type(tt) -> str:
        # pygments.token을 함수 내부에서 import하여 클래스 로드 시점 의존성 제거
        from pygments.token import (Keyword, String, Number, Comment, Name, Operator, Punctuation, Text, Whitespace)
        # Docstring을 주석으로 처리 (Pygments가 이미 정확히 분류함)
        if tt in String.Doc or tt == String.Doc:
            return 'doc'
        # 멀티라인 문자열도 주석으로 처리하고 싶다면
        if tt == String.Double.Triple or tt == String.Single.Triple:
            return 'com'
        # 일반 주석
        if tt in Comment:
            return 'com'
        # 나머지 문자열은 str로
        if tt in String:
            return 'str'
        # 키워드
        if tt in Keyword or tt in Keyword.Namespace or tt in Keyword.Declaration:
            return 'kw'
        # 숫자
        if tt in Number:
            return 'num'
        # 이름/식별자
        if tt in Name.Function:
            return 'func'
        if tt in Name.Class:
            return 'cls'
        if tt in Name:
            return 'name'
        # 연산자/구두점
        if tt in Operator:
            return 'op'
        if tt in Punctuation:
            return 'punc'
        # 공백/기타
        if tt in (Text, Whitespace):
            return 'text'
        return 'text'

    def get_urwid_palette(self) -> List[Tuple]:
        """현재 테마에 맞는 Urwid 팔레트를 반환합니다."""
        return self.current_urwid_palette

    def get_rich_theme(self) -> Theme:
        """현재 테마에 맞는 Rich 테마 객체를 반환합니다."""
        return Theme({
            "markdown.h1": "bold bright_white",
            "markdown.h2": "bold bright_white",
            "markdown.h3": "bold bright_white",
            "markdown.list": "cyan",
            "markdown.block_quote": "italic #8b949e",
            "markdown.code": "bold white on #484f58",
            "markdown.hr": "yellow",
            "markdown.link": "underline bright_white"
        })

    def apply_to_urwid_loop(self, loop: Optional[urwid.MainLoop]):
        """실행 중인 Urwid MainLoop에 현재 팔레트를 즉시 적용합니다."""
        if not loop:
            return
        try:
            loop.screen.register_palette(self.current_urwid_palette)
            loop.draw_screen()
        except Exception:
            pass

    def _generate_urwid_palette(self) -> List[Tuple]:
        """현재 테마를 기반으로 Urwid 팔레트 목록을 동적으로 생성합니다."""
        theme = self._FG_THEMES.get(self.current_theme_name, self._FG_THEMES['monokai'])
        
        # 팔레트 구성
        palette = [
            ('key', 'yellow', 'black'),
            ('info', 'dark gray', 'black'),
            ('myfocus', 'black', 'light gray'),
            ('info_bg', '', 'dark gray'), 
            ('header', 'white', 'black'),
            ('preview', 'default', constants.PREVIEW_BG),
            ('preview_border', 'dark gray', constants.PREVIEW_BG),
            ('syn_lno', 'dark gray', constants.PREVIEW_BG),

            # ▼ restore 전용 가독성 개선 팔레트
            ('list_row', 'default', 'default'),
            ('list_focus', 'default', 'dark gray'),
            ('muted', 'dark gray', 'default'),
            ('row_title', 'white,bold', 'default'),
            ('badge', 'black', 'dark cyan'),
            ('badge_focus', 'white', 'dark blue'),

            ('muted_focus', 'light gray', 'dark gray'),
            ('row_title_focus', 'white,bold', 'dark gray'),
        ]
        
        # 프리뷰 문법 하이라이팅 (syn_*)
        syn_keys = ['text', 'kw', 'str', 'num', 'com', 'doc', 'name', 'func', 'cls', 'op', 'punc']
        for key in syn_keys:
            fg = self._color_for_palette(theme.get(key, 'white'))
            palette.append((f'syn_{key}', fg, constants.PREVIEW_BG))
        
        # Diff 문법 하이라이팅 (diff_code_*)
        for key in syn_keys:
            fg = self._color_for_palette(theme.get(key, 'white'))
            palette.append((f'diff_code_{key}', fg, 'default'))

        # Diff 고정 색상
        palette.extend([
            ('diff_file_old', 'light red,bold', 'black'),
            ('diff_file_new', 'light green,bold', 'black'),
            ('diff_hunk', 'yellow', 'black'),
            ('diff_meta', 'dark gray', 'black'),
            ('diff_lno', 'dark gray', 'default'),
            ('diff_add_bg', 'white', 'dark green'),
            ('diff_del_bg', 'white', 'dark red'),
        ])

        return palette

    # --- Private Helper Methods (기존 전역 함수들을 클래스 메서드로 변환) ---
    @staticmethod
    def _split_color_attrs(spec: str) -> Tuple[str, str]:
        """
        'light cyan,bold' → ('light cyan', 'bold')
        '#fffacd' → ('#fffacd', '')
        'default' → ('default', '')
        """
        if spec is None: return None, ''
        s = str(spec).strip()
        if not s: return '', ''
        parts = [p.strip() for p in s.split(',') if p.strip()]
        color = parts[0] if parts else ''
        attrs = ','.join(parts[1:]) if len(parts) > 1 else ''
        return color, attrs

    def _color_for_palette(self, spec: str, default: str = 'white') -> str:
        if not spec: return default
        color, attrs = self._split_color_attrs(spec)
        if color:
            color_lower = color.lower()
            color = self._COLOR_ALIASES.get(color_lower, color)
            if color.startswith('#'):
                color = self._TRUECOLOR_FALLBACKS.get(color.lower(), default)
        out = color or default
        if attrs: out += ',' + attrs
        return out

class ConfigManager:
    """
    설정 파일, 경로, 세션, 즐겨찾기 등 모든 파일 시스템 I/O를 관리하는 클래스.
    이 클래스의 인스턴스는 애플리케이션 설정의 단일 진실 공급원이 됩니다.
    """
    def __init__(self, base_dir: Optional[Path] = None, config_dir: Optional[Path] = None):
        """
        모든 경로를 초기화하고 필요한 디렉터리와 기본 설정 파일을 생성합니다.

        Args:
            base_dir (Optional[Path]): 프로젝트의 루트 디렉터리. 기본값은 현재 작업 디렉터리.
            config_dir (Optional[Path]): ai_models.txt 등이 위치한 설정 디렉터리.
                                         기본값은 '~/codes/gpt_cli'.
        """
        # --- 경로 정의 ---
        self.BASE_DIR = Path(base_dir) if base_dir else Path.cwd()
        self.CONFIG_DIR = (config_dir or Path.home() / "codes" / "gpt_cli").resolve()
        
        self.SESSION_DIR = self.BASE_DIR / ".gpt_sessions"
        self.SESSION_BACKUP_DIR = self.SESSION_DIR / "backups"

        self.MD_OUTPUT_DIR = self.BASE_DIR / "gpt_markdowns"
        self.CODE_OUTPUT_DIR = self.BASE_DIR / "gpt_codes"

        # --- 파일 경로 정의 ---
        self.PROMPT_HISTORY_FILE = self.BASE_DIR / ".gpt_prompt_history.txt"
        self.FAVORITES_FILE = self.BASE_DIR / ".gpt_favorites.json"
        self.IGNORE_FILE = self.BASE_DIR / ".gptignore"
        self.MODELS_FILE = self.CONFIG_DIR / "ai_models.txt"
        self.DEFAULT_IGNORE_FILE = self.CONFIG_DIR / ".gptignore_default"
        
        # 현재 세션 포인터 파일(.gpt_session)
        self.CURRENT_SESSION_FILE = self.BASE_DIR / ".gpt_session"

        # --- 자동 초기화 ---
        self._initialize_directories()
        self._create_default_ignore_file_if_not_exists()

    def _initialize_directories(self):
        """애플리케이션에 필요한 모든 디렉터리가 존재하는지 확인하고 없으면 생성합니다."""
        dirs_to_create = [
            self.CONFIG_DIR,
            self.SESSION_DIR,
            self.SESSION_BACKUP_DIR,
            self.MD_OUTPUT_DIR,
            self.CODE_OUTPUT_DIR
        ]
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)

    def _create_default_ignore_file_if_not_exists(self):
        """전역 기본 .gptignore 파일이 없으면, 합리적인 기본값으로 생성합니다."""
        if self.DEFAULT_IGNORE_FILE.exists():
            return

        default_patterns = """
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
            """
        try:
            self.DEFAULT_IGNORE_FILE.write_text(default_patterns.strip(), encoding="utf-8")
        except Exception as e:
            print(f"[경고] 전역 기본 무시 파일을 생성하지 못했습니다: {e}")

    def _backup_root_dir(self) -> Path:
        """
        세션 백업 루트(.gpt_sessions/backups) 경로.
        프로젝트에 SESSION_BACKUP_DIR 속성이 있으면 그것을, 없으면 기본값을 사용합니다.
        """
        root = getattr(self, "SESSION_BACKUP_DIR", None)
        if root is None:
            root = Path(self.SESSION_DIR) / "backups"
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return root
    
    # --- Current Session I/O ---
    def load_current_session_name(self) -> Optional[str]:
        """
        .gpt_session 파일에서 현재 세션명을 읽어 반환합니다.
        파일이 없거나 읽을 수 없으면 None.
        """
        try:
            if self.CURRENT_SESSION_FILE.exists():
                name = self.CURRENT_SESSION_FILE.read_text(encoding="utf-8").strip()
                return name or None
        except Exception:
            return None
        return None

    def save_current_session_name(self, name: str) -> None:
        """
        .gpt_session 파일에 현재 세션명을 저장합니다. 실패해도 흐름을 막지 않습니다.
        """
        try:
            self.CURRENT_SESSION_FILE.write_text(str(name).strip(), encoding="utf-8")
        except Exception:
            pass

    # --- Session Management ---
    def get_session_path(self, name: str) -> Path:
        """세션 이름에 해당하는 파일 경로를 반환합니다."""
        return self.SESSION_DIR / f"session_{name}.json"

    def load_session(self, name: str) -> Dict[str, Any]:
        """지정된 이름의 세션 데이터를 로드합니다."""
        default_data = {"messages": [], "model": "openai/gpt-5", "context_length": 200000, "usage_history": [], "mode":"dev"}
        path = self.get_session_path(name)
        data = Utils._load_json(path, default_data)
        
        # 레거시 형식 (message 리스트만 있던 경우) 호환
        if isinstance(data, list):
            return {"messages": data, **{k:v for k,v in default_data.items() if k != 'messages'}}
        
        # 키가 없는 경우 기본값으로 채워줌
        for key, value in default_data.items():
            data.setdefault(key, value)
            
        return data

    def save_session(
        self,
        name: str,
        msgs: List[Dict],
        model: str,
        context_length: int,
        usage_history: List[Dict],
        mode: Optional[str] = None,  # ← [추가]
    ) -> None:
        """세션 데이터를 파일에 저장합니다."""
        path = self.get_session_path(name)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        mode_to_save = (mode if mode is not None else existing.get("mode") or "dev")

        data = {
            "messages": msgs,
            "model": model,
            "context_length": context_length,
            "usage_history": usage_history or [],
            "mode": mode_to_save,
            "total_usage": {
                "total_prompt_tokens": sum(u.get("prompt_tokens", 0) for u in (usage_history or [])),
                "total_completion_tokens": sum(u.get("completion_tokens", 0) for u in (usage_history or [])),
                "total_tokens": sum(u.get("total_tokens", 0) for u in (usage_history or [])),
            },
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        Utils._save_json(path, data)

    def _session_name_from_backup_json(self, path: Path) -> Optional[str]:
        """
        백업 스냅샷 JSON에서 '원본 세션명'을 복원합니다.
        우선순위: backup_meta.session > name > 파일명(session_<slug>.json)
        """
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            meta = data.get("backup_meta") or {}
            name = meta.get("session") or data.get("name")
            if not name:
                # 파일명에서 복원
                stem = path.stem  # e.g. session_foo
                if stem.startswith("session_"):
                    name = stem[len("session_"):]
                else:
                    name = stem
            name = str(name).strip()
            return name or None
        except Exception:
            # JSON 파싱 실패 시 파일명 기반 폴백
            try:
                stem = path.stem
                return stem[len("session_"):] if stem.startswith("session_") else stem
            except Exception:
                return None


    def get_session_names(
        self,
        include_backups: bool = True,
        exclude_current: Optional[str] = None,
    ) -> List[str]:
        """
        세션 이름 목록을 반환합니다.
        - 라이브: .gpt_sessions/session_*.json
        - (옵션) 백업: .gpt_sessions/backups/session_*.json
        - 중복 제거: 라이브에 있으면 1회만 노출
        - 현재 세션(exclude_current)이면 목록에서 제외
        """
        names_live: Set[str] = set()
        names_backup: Set[str] = set()

        # 1) 라이브 세션 수집
        sess_dir = Path(self.SESSION_DIR)
        if sess_dir.exists():
            for f in sess_dir.glob("session_*.json"):
                if f.is_file():
                    stem = f.stem  # session_<name>
                    name = stem[len("session_"):] if stem.startswith("session_") else stem
                    name = name.strip()
                    if name:
                        names_live.add(name)

        # 2) 백업 세션 수집
        if include_backups:
            bdir = self._backup_root_dir()
            if bdir.exists():
                for bj in bdir.glob("session_*.json"):
                    if bj.is_file():
                        name = self._session_name_from_backup_json(bj)
                        if not name:
                            continue
                        # 라이브에 이미 있으면 굳이 추가하지 않음(중복 제거)
                        if name not in names_live:
                            names_backup.add(name)

        # 3) 합치고, 현재 세션 제외
        all_names: Set[str] = names_live.union(names_backup)
        if exclude_current:
            all_names.discard(exclude_current)

        return sorted(all_names)


    # --- Favorites Management ---
    
    def load_favorites(self) -> Dict[str, str]:
        """즐겨찾기 데이터를 로드합니다."""
        return Utils._load_json(self.FAVORITES_FILE, {})

    def save_favorite(self, name: str, prompt: str) -> None:
        """새로운 즐겨찾기를 저장합니다."""
        favs = self.load_favorites()
        favs[name] = prompt
        Utils._save_json(self.FAVORITES_FILE, favs)

    # --- Ignore File Management ---

    def get_ignore_spec(self) -> Optional[PathSpec]:
        """전역 및 프로젝트 .gptignore 파일을 결합하여 PathSpec 객체를 생성합니다."""
        default_patterns = []
        if self.DEFAULT_IGNORE_FILE.exists():
            default_patterns = self.DEFAULT_IGNORE_FILE.read_text('utf-8').splitlines()

        user_patterns = []
        if self.IGNORE_FILE.exists():
            user_patterns = self.IGNORE_FILE.read_text('utf-8').splitlines()
        
        # 순서를 보존하며 중복을 제거 (dict.fromkeys 트릭)
        combined_patterns = list(dict.fromkeys(default_patterns + user_patterns))
        
        final_patterns = [p.strip() for p in combined_patterns if p.strip() and not p.strip().startswith("#")]
        
        return PathSpec.from_lines("gitwildmatch", final_patterns) if final_patterns else None

    def is_ignored(self, path: Path, spec: Optional[PathSpec]) -> bool:
        """주어진 경로가 ignore spec에 의해 무시되어야 하는지 확인합니다."""
        if not spec:
            return False
        
        try:
            relative_path_str = path.relative_to(self.BASE_DIR).as_posix()
        except ValueError:
            # BASE_DIR 외부에 있는 경로는 무시 규칙의 대상이 아님
            return False

        if path.is_dir() and not relative_path_str.endswith('/'):
            relative_path_str += '/'
            
        return spec.match_file(relative_path_str)

    @staticmethod
    def read_plain_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"[파일 읽기 실패: {e}]"

    @staticmethod
    def encode_base64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    def save_code_blocks(self, blocks: Sequence[Tuple[str, str]], session_name: str, msg_id: int) -> List[Path]:
        """AI가 생성한 코드 블록을 파일로 저장합니다."""
        self.CODE_OUTPUT_DIR.mkdir(exist_ok=True)
        saved: List[Path] = []
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
            ext = ext_map.get(lang.lower() if lang else "text", "txt")
            
            # 기본 파일 경로 생성
            base_p = self.CODE_OUTPUT_DIR / f"codeblock_{session_name}_{msg_id}_{i}.{ext}"
            p = base_p
            
            # 파일이 이미 존재하면 이름에 숫자 추가
            cnt = 1
            while p.exists():
                p = self.CODE_OUTPUT_DIR / f"codeblock_{session_name}_{msg_id}_{i}_{cnt}.{ext}"
                cnt += 1
            
            p.write_text(code, encoding="utf-8")
            saved.append(p)
        return saved

class CommandHandler:
    """
    '/'로 시작하는 모든 명령어를 처리하는 전담 클래스.
    메인 애플리케이션(GPTCLI)의 인스턴스를 주입받아 그 상태에 접근하고 수정합니다.
    """
    def __init__(self, app: 'GPTCLI', config: 'ConfigManager'):
        """
        Args:
            app ('GPTCLI'): 메인 애플리케이션 인스턴스.
            config ('ConfigManager'): 설정 및 파일 I/O 관리자 인스턴스.
        """
        self.app = app
        self.console = self.app.console
        self.config = config
        self.theme_manager = app.theme_manager
        self.differ_ref: Dict[str, CodeDiffer | None] = {"inst": None}

    def _backup_root_dir(self) -> Path:
        """
        세션 백업 루트(.gpt_sessions/backups) 경로.
        프로젝트에 SESSION_BACKUP_DIR 속성이 있으면 그것을, 없으면 기본값을 사용합니다.
        """
        root = getattr(self.config, "SESSION_BACKUP_DIR", None)
        if root is None:
            root = Path(self.config.SESSION_DIR) / "backups"
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _slug(self, session_name: str) -> str:
        """
        파일/디렉터리 안전 슬러그. 대소문자 유지, [A-Za-z0-9._-] 외는 '_'로 치환.
        브라우저 생태계에서도 세션명과 파일명이 다를 수 있음을 감안(특수문자) [forum.vivaldi.net].
        """
        s = re.sub(r"[^A-Za-z0-9._-]+", "_", session_name.strip())
        s = re.sub(r"_+", "_", s).strip("._-")
        return s or "default"

    def _single_backup_json(self, session_name: str) -> Path:
        """
        세션별 단일 스냅샷 파일 경로: backups/session_<slug>.json
        (요구사항: session_<name>.json 형태)
        """
        slug = self._slug(session_name)
        return self._backup_root_dir() / f"session_{slug}.json"

    def _code_single_backup_dir(self, session_name: str) -> Path:
        """
        코드 스냅샷 디렉터리: gpt_codes/backup/<slug>/
        """
        slug = self._slug(session_name)
        return (self.config.CODE_OUTPUT_DIR / "backup" / slug).resolve()

    # --- 코드 파일 스냅샷/복원 ---

    def _remove_session_code_files(self, session_name: str) -> int:
        """
        gpt_codes 내 현재 작업본(codeblock_<session>_*) 삭제
        """
        removed = 0
        pattern = f"codeblock_{session_name}_*"
        for f in self.config.CODE_OUTPUT_DIR.glob(pattern):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        if removed:
            self.console.print(f"[dim]코드 블록 삭제: {pattern} ({removed}개)[/dim]", highlight=False)
        return removed

    def _copy_code_snapshot_single(self, session_name: str) -> int:
        """
        gpt_codes/codeblock_<session>_* → gpt_codes/backup/<slug>/ 로 '단일 스냅샷' 복사(덮어쓰기)
        """
        src_files = list(self.config.CODE_OUTPUT_DIR.glob(f"codeblock_{session_name}_*"))
        dst_dir = self._code_single_backup_dir(session_name)
        if dst_dir.exists():
            shutil.rmtree(dst_dir, ignore_errors=True)
        copied = 0
        if src_files:
            dst_dir.mkdir(parents=True, exist_ok=True)
            for f in src_files:
                try:
                    shutil.copy2(str(f), str(dst_dir / f.name))
                    copied += 1
                except Exception:
                    pass
        return copied

    def _restore_code_snapshot_single(self, session_name: str) -> Tuple[int, int]:
        """
        gpt_codes/backup/<slug>/ → gpt_codes 로 복원
        - 기존 codeblock_<session>_* 삭제 후 복사
        반환: (removed, copied)
        """
        removed = self._remove_session_code_files(session_name)
        src_dir = self._code_single_backup_dir(session_name)
        copied = 0
        if src_dir.exists() and src_dir.is_dir():
            for f in src_dir.glob("*"):
                try:
                    shutil.copy2(str(f), str(self.config.CODE_OUTPUT_DIR / f.name))
                    copied += 1
                except Exception:
                    pass
        return removed, copied

    def _delete_session_file(self, session_name: str) -> bool:
        """
        .gpt_sessions/session_<name>.json 파일을 삭제합니다.
        - 세션 전환/복원 전에 호출하여 '활성 세션 하나만' 남도록 정리.
        """
        path = self.config.get_session_path(session_name)
        try:
            if path.exists():
                path.unlink()
                self.console.print(
                    f"[dim]세션 파일 삭제: {path.relative_to(self.config.BASE_DIR)}[/dim]",
                    highlight=False
                )
                return True
        except Exception as e:
            self.console.print(
                f"[yellow]세션 파일 삭제 실패({path.name}): {e}[/yellow]",
                highlight=False
            )
        return False

    # --- 세션 단일 스냅샷/복원 ---

    def _snapshot_session_single(self, session_name: str, reason: str = "manual") -> bool:
        """
        세션별 '단일' 스냅샷 생성(덮어쓰기)
        - 세션 JSON: backups/session_<slug>.json
        - 코드 스냅샷: gpt_codes/backup/<slug>/ (codeblock_*만)
        """
        try:
            # 현재 세션이면 디스크에 먼저 flush
            if hasattr(self.app, "current_session_name") and session_name == self.app.current_session_name:
                # 기존 프로젝트의 save_session 시그니처에 맞춰 호출
                self.config.save_session(
                    session_name,
                    getattr(self.app, "messages", []),
                    getattr(self.app, "model", ""),
                    getattr(self.app, "model_context", 0),
                    getattr(self.app, "usage_history", []),
                    mode=getattr(self.app, "mode", "dev"),
                )

            # 세션 JSON 로드 → backup_meta 추가 → 단일 스냅샷으로 저장
            data = self.config.load_session(session_name)
            data = dict(data)
            data.setdefault("name", session_name)
            data["backup_meta"] = {
                "session": session_name,  # 원본 세션명 저장
                "backup_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "message_count": len(data.get("messages", [])),
                "model": data.get("model", getattr(self.app, "model", "")),
            }

            bj = self._single_backup_json(session_name)
            bj.parent.mkdir(parents=True, exist_ok=True)

            # Utils가 프로젝트에 존재한다는 가정(기존 코드와 동일 사용)
            if hasattr(self, "Utils"):
                self.Utils._save_json(bj, data)
            else:
                bj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            code_cnt = self._copy_code_snapshot_single(session_name)
            if hasattr(self, "console"):
                self.console.print(
                    f"[green]스냅샷 저장:[/green] session='{session_name}' (codes:{code_cnt}) → {bj}",
                    highlight=False
                )
            return True
        except Exception as e:
            if hasattr(self, "console"):
                self.console.print(f"[yellow]스냅샷 실패(session={session_name}): {e}[/yellow]", highlight=False)
            return False

    def _load_session_into_app(self, session_name: str) -> None:
        """
        세션 파일(.gpt_sessions/session_<name>.json)을 읽어 앱 상태에 로드
        """
        data = self.config.load_session(session_name)
        # 앱 상태 필드 갱신
        if hasattr(self.app, "current_session_name"):
            self.app.current_session_name = session_name
        self.app.messages = data.get("messages", [])
        self.app.model = data.get("model", getattr(self.app, "default_model", self.app.model))
        self.app.model_context = data.get("context_length", getattr(self.app, "default_context_length", self.app.model_context))
        self.app.usage_history = data.get("usage_history", [])
        self.app.mode = data.get("mode", getattr(self.app, "mode", "dev"))

    def _restore_session_single(self, session_name: str) -> Optional[Dict[str, Any]]:
        """
        세션별 '단일' 스냅샷에서 복원하고, 성공 시 세션 데이터를 반환합니다.
        - 세션 JSON: backups/session_<slug>.json → 실제 세션 파일로 저장
        - 코드: gpt_codes/backup/<slug>/ → gpt_codes 로 복사
        - [변경] 앱 상태를 직접 수정하지 않고, 로드된 데이터를 반환합니다.
        """
        bj = self._single_backup_json(session_name)
        if not bj.exists():
            if hasattr(self, "console"):
                self.console.print(f"[yellow]스냅샷을 찾을 수 없습니다: {bj}[/yellow]", highlight=False)
            return None

        try:
            if hasattr(self, "Utils"):
                data = self.Utils._load_json(bj, {})
            else:
                data = json.loads(bj.read_text(encoding="utf-8"))

            msgs = data.get("messages", [])
            model = data.get("model", getattr(self.app, "model", ""))
            ctx = data.get("context_length", getattr(self.app, "model_context", 0))
            usage = data.get("usage_history", [])
            mode = data.get("mode")

            # 1. 파일 시스템 작업: 세션 파일로 쓰기
            self.config.save_session(session_name, msgs, model, ctx, usage, mode=mode)

            # 2. 파일 시스템 작업: 코드 파일 복원
            removed, copied = self._restore_code_snapshot_single(session_name)
            
            if hasattr(self, "console"):
                self.console.print(
                    f"[green]복원 완료:[/green] session='{session_name}' (codes: -{removed} +{copied})",
                    highlight=False
                )
            
            # 3. [변경] 성공적으로 파일 I/O를 마친 후, 로드된 데이터를 반환
            return data
            
        except Exception as e:
            if hasattr(self, "console"):
                self.console.print(f"[red]복원 실패(session={session_name}): {e}[/red]", highlight=False)
            return None

    def dispatch(self, user_input: str) -> bool:
        """
        사용자 입력을 파싱하여 적절한 핸들러 메서드로 전달합니다.
        애플리케이션을 종료해야 할 경우 True를 반환합니다.
        """
        if not user_input.startswith('/'):
            return False

        cmd_str, *args = user_input.strip().split()
        cmd_name = cmd_str[1:]

        handler_method = getattr(self, f"handle_{cmd_name}", self.handle_unknown)
        return handler_method(args) or False

    def handle_unknown(self, args: List[str]) -> None:
        self.console.print("[yellow]알 수 없는 명령어입니다. '/commands'로 전체 목록을 확인하세요.[/yellow]",highlight=False)

    # --- 애플리케이션 및 모드 제어 ---

    def handle_exit(self, args: List[str]) -> bool:
        """애플리케이션을 종료합니다."""
        return True

    def handle_compact_mode(self, args: List[str]) -> None:
        """첨부파일 압축 모드를 토글합니다."""
        self.app.compact_mode = not self.app.compact_mode
        status = "[green]활성화[/green]" if self.app.compact_mode else "[yellow]비활성화[/yellow]"
        self.console.print(f"첨부파일 압축 모드가 {status}되었습니다.", highlight=False)
        self.console.print("[dim]활성화 시: 과거 메시지의 첨부파일이 파일명만 남고 제거됩니다.[/dim]",highlight=False)

    def handle_pretty_print(self, args: List[str]) -> None:
        """고급 출력(Rich) 모드를 토글합니다."""
        self.app.pretty_print_enabled = not self.app.pretty_print_enabled
        status = "[green]활성화[/green]" if self.app.pretty_print_enabled else "[yellow]비활성화[/yellow]"
        self.console.print(f"고급 출력(Rich) 모드가 {status} 되었습니다.", highlight=False)

    def handle_mode(self, args: List[str]) -> None:
        """
        시스템 프롬프트 모드만 변경합니다.
        - 세션 전환/백업/복원은 수행하지 않습니다.
        """
        parser = argparse.ArgumentParser(prog="/mode", add_help=False)
        parser.add_argument("mode_name", nargs='?', choices=constants.SUPPORTED_MODES, default=self.app.mode)
        
        try:
            parsed_args = parser.parse_args(args)
        except SystemExit:
            self.console.print("[red]인자 오류. 사용법: /mode [<모드>][/red]", highlight=False)
            return

        old_mode = self.app.mode
        self.app.mode = parsed_args.mode_name
        self.console.print(f"[green]모드 변경: {old_mode} → {self.app.mode}[/green]", highlight=False)

        # 변경 즉시 세션에 반영
        try:
            self.config.save_session(
                getattr(self.app, "current_session_name", "default"),
                getattr(self.app, "messages", []),
                getattr(self.app, "model", ""),
                getattr(self.app, "model_context", 0),
                getattr(self.app, "usage_history", []),
                mode=self.app.mode,
            )
        except Exception:
            pass

    def _choose_session_via_tui(self) -> Optional[str]:
        """
        스냅샷(backups)과 라이브(.gpt_sessions)를 통합한 세션 목록을 TUI로 표시하고
        사용자 선택 결과의 '세션명'을 반환합니다. 취소 시 None.
        """
        import urwid

        current = getattr(self.app, "current_session_name", None)
        # 통합 목록(현재 제외, 중복 제거)
        names = self.config.get_session_names(include_backups=True, exclude_current=current)
        if not names:
            self.console.print("[yellow]표시할 세션이 없습니다.[/yellow]", highlight=False)
            return None

        def _backup_json_for(name: str) -> Path:
            return self._single_backup_json(name)

        def _live_json_for(name: str) -> Path:
            return self.config.get_session_path(name)

        # [내부 헬퍼] JSON 경로→ 표시 라벨/세션명 추출
        def _read_label_and_name(p: Path) -> Tuple[str, str]:
            def _extract_text_from_content(content: Any) -> Tuple[str, int]:
                if isinstance(content, str):
                    return content, 0
                if isinstance(content, list):
                    text_part = ""
                    attach_cnt = 0
                    for part in content:
                        if part.get("type") == "text":
                            if not text_part:
                                text_part = part.get("text", "")
                        else:
                            attach_cnt += 1
                    return text_part, attach_cnt
                return str(content), 0

            name = p.stem.replace("session_", "")
            label = name
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                meta = data.get("backup_meta", {}) or {}
                name = str(meta.get("session") or data.get("name") or name).strip() or name
                msg_count = meta.get("message_count", len(data.get("messages", [])))
                updated = meta.get("backup_at", data.get("last_updated") or "N/A")
                model = (meta.get("model") or data.get("model") or "")
                model = model.split("/")[-1] if model else "unknown"
                size_kb = p.stat().st_size / 1024.0

                label = f"{name}   | 💬 {msg_count} | 🤖 {model} | 🕘 {updated} | 📦 {size_kb:.1f}KB"

                previews: List[str] = []
                messages = data.get("messages", [])
                displayable = [m for m in messages if m.get("role") in ("user", "assistant")]
                for m in displayable[-4:]:
                    role = m.get("role")
                    content = m.get("content", "")
                    text, attach_cnt = _extract_text_from_content(content)
                    text = (text or "").strip().replace("\n", " ")
                    if attach_cnt > 0:
                        text = f"[{attach_cnt} 첨부] {text}"
                    if len(text) > 50:
                        text = text[:48] + "…"
                    icon = "👤" if role == "user" else "🤖"
                    previews.append(f"{icon} {text}" if text else f"{icon} (빈 메시지)")
                if not previews:
                    previews = ["(메시지 없음)"]
                label += "\n   " + "\n   ".join(previews)
            except Exception:
                pass
            return label, name

        # 버튼 목록 구성
        items: List[urwid.Widget] = [
            urwid.Text("전환할 세션을 선택하세요 (Enter:선택, Q:취소)"),
            urwid.Divider()
        ]
        chosen: List[Optional[str]] = [None]

        def _exit_with(name: Optional[str]) -> None:
            chosen[0] = name
            raise urwid.ExitMainLoop()

        added = 0
        for nm in names:
            # [우선순위] 스냅샷 JSON이 있으면 그걸로 미리보기, 없으면 라이브 JSON
            bj = _backup_json_for(nm)
            lj = _live_json_for(nm)
            p = bj if bj.exists() else lj
            try:
                label, sess_name = _read_label_and_name(p)
            except Exception:
                label, sess_name = (nm, nm)
            btn = urwid.Button(label)
            urwid.connect_signal(btn, "click", lambda _, n=sess_name: _exit_with(n))
            items.append(urwid.AttrMap(btn, None, focus_map="myfocus"))
            added += 1

        if added == 0:
            self.console.print("[yellow]선택할 세션이 없습니다.[/yellow]", highlight=False)
            return None

        listbox = urwid.ListBox(urwid.SimpleFocusListWalker(items))

        def unhandled(key):
            if isinstance(key, str) and key.lower() == "q":
                _exit_with(None)

        palette = self.theme_manager.get_urwid_palette()
        loop = urwid.MainLoop(listbox, palette=palette, unhandled_input=unhandled)
        loop.run()
        # TUI 종료 후, 터미널 뷰포트 스냅(스크롤 튀는 현상 완화)
        self._snap_scroll_to_bottom()
        return chosen[0]


    def handle_session(self, args: List[str]) -> None:
        """
        세션 전환(단일 스냅샷 정책, 통합 엔트리)
        - /session            → TUI 목록에서 선택해 전환
        - /session <세션명>    → 해당 세션으로 즉시 전환(스냅샷 우선)
        """
        # [추가] 인자 없으면 TUI 진입
        if not args or not args[0].strip():
            target = self._choose_session_via_tui()
            if not target:
                self.console.print("[dim]세션 전환이 취소되었습니다.[/dim]", highlight=False)
                return
            # TUI로 얻은 target을 args처럼 이어서 처리
            args = [target]

        target = args[0].strip()
        current = getattr(self.app, "current_session_name", None)

        if current and target == current:
            self.console.print(f"[dim]이미 현재 세션입니다: '{target}'[/dim]", highlight=False)
            return

        # 1) 현재 세션 스냅샷 → 성공 시 live/코드 삭제
        if current:
            ok = self._snapshot_session_single(current, reason="switch_session")
            if ok:
                self._delete_session_file(current)
                self._remove_session_code_files(current)
            else:
                self.console.print(
                    "[yellow]경고: 스냅샷 실패로 live/코드 파일을 삭제하지 않았습니다.[/yellow]",
                    highlight=False
                )

        # 2) 타깃 세션 전환(스냅샷 우선)
        if self._single_backup_json(target).exists():
            if self._restore_session_single(target):
                # [중요] 복원 후 앱 상태/포인터 갱신
                self._load_session_into_app(target)  # ← 핵심
                try:
                    self.config.save_current_session_name(self.app.current_session_name)
                except Exception:
                    pass
                self.console.print(
                    f"[green]세션 전환 완료 → '{target}' (스냅샷 복원)[/green]",
                    highlight=False
                )
            else:
                # 손상 시 라이브로 폴백(있다면)
                tpath = self.config.get_session_path(target)
                if tpath.exists():
                    self._load_session_into_app(target)
                    self._snapshot_session_single(target, reason="migrate-live-to-snapshot")
                    self.console.print(
                        f"[yellow]스냅샷 손상 → live 로드 후 스냅샷 생성: '{target}'[/yellow]",
                        highlight=False
                    )
                else:
                    self.config.save_session(target, [], self.app.default_model, self.app.default_context_length, [],
                                             mode=getattr(self.app, "mode", "dev"))
                    self._load_session_into_app(target)
                    self.console.print(
                        f"[yellow]스냅샷 손상 → 빈 세션 생성: '{target}'[/yellow]",
                        highlight=False
                    )
        else:
            # 스냅샷이 없으면 라이브 있는지 확인
            tpath = self.config.get_session_path(target)
            if tpath.exists():
                self._load_session_into_app(target)
                self._snapshot_session_single(target, reason="migrate-live-to-snapshot")
                self.console.print(
                    f"[green]세션 전환 완료 → '{target}' (live 로드·스냅샷 생성)[/green]",
                    highlight=False
                )
            else:
                self.config.save_session(
                    target, [], self.app.default_model, self.app.default_context_length, [],
                    mode=getattr(self.app, "mode", "dev"),
                )
                self._load_session_into_app(target)
                self.console.print(
                    f"[green]새 세션 생성 → '{target}'[/green]",
                    highlight=False
                )

        # 3) 첨부 초기화
        if getattr(self.app, "attached", None):
            self.app.attached.clear()
            self.console.print("[dim]첨부 파일 목록이 초기화되었습니다.[/dim]", highlight=False)

        # 4) 현재 세션 포인터 갱신
        try:
            self.config.save_current_session_name(getattr(self.app, "current_session_name", target))
        except Exception:
            pass
    
    def handle_backup(self, args: List[str]) -> None:
        """
        현재 세션 단일 스냅샷 저장(덮어쓰기)
        사용법: /backup [reason...]
        """
        reason = "manual"
        if args:
            reason = " ".join(args).strip() or "manual"
        ok = self._snapshot_session_single(getattr(self.app, "current_session_name", "default"), reason=reason)
        if ok and hasattr(self, "console"):
            self.console.print("[green]BACKUP OK (단일 스냅샷 갱신)[/green]", highlight=False)

    def _select_model(self, current_model: str, current_context: int) -> Tuple[str, int]:
        model_file = self.config.MODELS_FILE
        default_context = self.app.default_context_length
        palette = self.theme_manager.get_urwid_palette()
        if not model_file.exists():
            self.console.print(f"[yellow]{model_file} 가 없습니다. 기본 모델을 유지합니다.[/yellow]", highlight=False)
            return current_model, current_context
        
        models_with_context: List[Dict[str, Any]] = []
        try:
            lines = model_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if line.strip() and not line.strip().startswith("#"):
                    parts = line.strip().split()
                    model_id = parts[0]
                    context_length = default_context
                    if len(parts) >= 2:
                        try:
                            context_length = int(parts[1])
                        except ValueError:
                            # 숫자로 변환 실패 시 기본값 사용
                            pass
                    models_with_context.append({"id": model_id, "context": context_length})
        except IOError as e:
            self.console.print(f"[red]모델 파일({model_file}) 읽기 오류: {e}[/red]", highlight=False)
            return current_model, current_context

        if not models_with_context:
            self.console.print(f"[yellow]선택할 수 있는 모델이 없습니다. '{model_file}' 파일을 확인해주세요.[/yellow]", highlight=False)
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
                
        urwid.MainLoop(listbox, palette=palette, unhandled_input=unhandled).run()
        
        # TUI 종료 후 결과 처리
        if result[0]:
            # 선택된 모델이 있으면 해당 모델의 ID와 컨텍스트 길이 반환
            return result[0]['id'], result[0]['context']
        else:
            # 취소했으면 기존 모델 정보 그대로 반환
            return current_model, current_context
    
    def _snap_scroll_to_bottom(self) -> None:
        """
        urwid TUI 종료 직후, 터미널 뷰포트가 위쪽에 '묶이는' 현상을 방지하기 위해
        최소 출력으로 바닥 스냅을 유발한다.
        """
        try:
            # [권장안 1] 가장 확실한 방법: 개행 + flush
            # 줄 하나를 실제로 밀어내 스크롤을 유발한다.
            self.console.print("\n", end="")  # [수정 포인트]
            try:
                self.console.file.flush()      # [수정 포인트]
            except Exception:
                pass

            # [선택안] 줄바꿈이 화면에 보이는 것이 싫다면: 커서를 위로 1줄 되돌린다.
            # - 스크롤은 유지되면서, 빈 줄이 “보이는” 효과를 최소화.
            # - ANSI: CSI 1A (커서 위로 1줄), 여기선 원시 ANSI를 직접 기록.
            if getattr(self.console, "is_terminal", False):
                try:
                    self.console.file.write("\x1b[1A")  # [선택 포인트]
                    self.console.file.flush()
                except Exception:
                    pass

        except Exception:
            pass

    def handle_select_model(self, args: List[str]) -> None:
        """TUI를 통해 AI 모델을 선택합니다."""
        old_model = self.app.model
        new_model, new_context = self._select_model(self.app.model, self.app.model_context)
        if new_model != old_model:
            self.app.model = new_model
            self.app.model_context = new_context
            self.console.print(f"[green]모델 변경: {old_model} → {self.app.model} (CTX: {self.app.model_context:,})[/green]", highlight=False)
        self._snap_scroll_to_bottom()

    def handle_search_models(self, args: List[str]) -> None:
        """키워드로 모델을 검색하고 `ai_models.txt`를 업데이트합니다."""
        searcher = ModelSearcher(
            config = self.config,
            theme_manager = self.theme_manager,
            console = self.console
        )
        searcher.start(args)
        self._snap_scroll_to_bottom()
        
    def handle_theme(self, args: List[str]) -> None:
        """코드 하이라이팅 테마를 변경합니다."""
        if not args:
            self.console.print("[yellow]사용법: /theme <테마이름>[/yellow]", highlight=False)
            self.console.print(f"가능한 테마: {', '.join(self.theme_manager.get_available_themes())}", highlight=False)
            return
        theme_name = args[0]
        try:
            self.theme_manager.set_global_theme(theme_name)
            self.console.print(f"[green]테마 적용: {theme_name}[/green]", highlight=False)
            inst = self.differ_ref.get("inst")
            if inst and inst.main_loop:
                self.theme_manager.apply_to_urwid_loop(inst.main_loop)
            if self.app.active_tui_loop:
                self.theme_manager.apply_to_urwid_loop(self.app.active_tui_loop)
        except KeyError:
            self.console.print(f"[red]알 수 없는 테마: {theme_name}[/red]", highlight=False)

    # --- 입출력 및 파일 관리 ---

    def handle_edit(self, args: List[str]) -> None:
        """
        외부 편집기를 열어 긴 프롬프트를 작성하고, 편집기 종료 후 '즉시 전송'합니다.
        프롬프트 버퍼를 건드리지 않습니다(이벤트 루프/validator 이슈 회피).
        """
        temp_file_path = self.config.BASE_DIR / ".gpt_prompt_edit.tmp"
        temp_file_path.touch()

        # 기본 편집기: $EDITOR 우선, Windows는 notepad, 그 외는 vim
        editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "vim")
        self.console.print(f"[dim]외부 편집기 ({editor})를 실행합니다...[/dim]", highlight=False)

        try:
            # 사용자가 종료할 때까지 블로킹
            subprocess.run([editor, str(temp_file_path)], check=True)

            # 편집 내용 읽기
            user_in = temp_file_path.read_text(encoding="utf-8").strip()

            # 임시 파일 정리
            try:
                temp_file_path.unlink()
            except Exception:
                pass

            if not user_in:
                self.console.print("[yellow]입력이 비어있어 취소되었습니다.[/yellow]", highlight=False)
                return
            
            self.console.print(user_in, markup=False, highlight=False)

            # 편집 내용을 즉시 전송 (프롬프트로 넘기지 않음)
            self.app._handle_chat_message(user_in)

            # 전송 후 후처리(스크롤 스냅)
            if hasattr(self, "_snap_scroll_to_bottom"):
                self._snap_scroll_to_bottom()

        except FileNotFoundError:
            self.console.print(
                f"[red]오류: 편집기 '{editor}'를 찾을 수 없습니다. EDITOR 환경 변수를 확인하세요.[/red]",
                highlight=False
            )
        except subprocess.CalledProcessError as e:
            self.console.print(
                f"[red]오류: 편집기 '{editor}'가 오류와 함께 종료되었습니다: {e}[/red]",
                highlight=False
            )
        except Exception as e:
            self.console.print(f"[red]오류: {e}[/red]", highlight=False)
        finally:
            # 혹시 남아있다면 임시 파일 제거
            try:
                if temp_file_path.exists():
                    temp_file_path.unlink()
            except Exception:
                pass

    def handle_last_response(self, args: List[str]) -> None:
        """마지막 응답을 Rich Markdown 형식으로 다시 출력합니다."""
        last_msg = self._get_last_assistant_message()
        if last_msg:
            self.console.print(Panel(Markdown(last_msg), title="[yellow]Last Response[/yellow]", border_style="dim"), highlight=False)
        else:
            self.console.print("[yellow]다시 표시할 이전 답변이 없습니다.[/yellow]", highlight=False)

    def _get_last_assistant_message(self) -> Optional[str]:
        for message in reversed(self.app.messages):
            if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                return message["content"]
        return None

    def handle_raw(self, args: List[str]) -> None:
        """마지막 응답을 raw 텍스트 형식으로 출력합니다."""
        last_msg = self._get_last_assistant_message()
        if last_msg:
            self.console.print(last_msg, markup=False, highlight=False)
        else:
            self.console.print("[yellow]표시할 이전 답변 기록이 없습니다.[/yellow]", highlight=False)
            
    def handle_copy(self, args: List[str]) -> None:
        """마지막 응답의 코드 블록을 클립보드에 복사합니다."""
        if not self.app.last_reply_code_blocks:
            self.console.print("[yellow]복사할 코드 블록이 없습니다.[/yellow]", highlight=False)
            return
        try:
            index = int(args[0]) - 1 if args else 0
            if 0 <= index < len(self.app.last_reply_code_blocks):
                _, code_to_copy = self.app.last_reply_code_blocks[index]
                pyperclip.copy(code_to_copy)
                self.console.print(f"[green]✅ 코드 블록 #{index + 1}이 클립보드에 복사되었습니다.[/green]", highlight=False)
            else:
                self.console.print(f"[red]오류: 1부터 {len(self.app.last_reply_code_blocks)} 사이의 번호를 입력하세요.[/red]", highlight=False)
        except (ValueError, IndexError):
            self.console.print("[red]오류: '/copy <숫자>' 형식으로 입력하세요.[/red]", highlight=False)
        except pyperclip.PyperclipException:
            self.console.print("[bold yellow]클립보드 복사 실패! 아래 코드를 직접 복사하세요.[/bold yellow]", highlight=False)
            self.console.print(code_to_copy, markup=False, highlight=False)

    def _display_attachment_tokens(self, attached_files: List[str], compact_mode: bool = False) -> None:
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
            if path.suffix.lower() in constants.IMG_EXTS:
                file_type = "🖼️ 이미지"
                tokens = self.app.token_estimator.estimate_image_tokens(path)
            elif path.suffix.lower() == constants.PDF_EXT:
                file_type = "📄 PDF"
                tokens = self.app.token_estimator.estimate_pdf_tokens(path)
            else:
                file_type = "📝 텍스트"
                try:
                    text = self.config.read_plain_file(path)
                    tokens = self.app.token_estimator.count_text_tokens(text)
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
        
        self.console.print(table, highlight=False)
        
        if compact_mode:
            self.console.print(
                "[dim green]📦 Compact 모드 활성화됨: "
                "과거 메시지의 첨부파일이 자동으로 압축됩니다.[/dim green]"
                , highlight=False
            )

    def handle_all_files(self, args: List[str]) -> None:
        """TUI 파일 선택기를 엽니다."""
        selector = FileSelector(config=self.config, theme_manager=self.theme_manager)
        self.app.attached = selector.start()
        if self.app.attached:
            self._display_attachment_tokens(self.app.attached, self.app.compact_mode)
        self._snap_scroll_to_bottom()

    def handle_files(self, args: List[str]) -> None:
        """수동으로 파일을 첨부합니다."""
        current_paths = set(Path(p) for p in self.app.attached)
        added_paths: Set[Path] = set()
        spec = self.config.get_ignore_spec()
        
        for arg in args:
            p = Path(arg)
            if not p.exists():
                self.console.print(f"[yellow]경고: '{arg}'를 찾을 수 없습니다.[/yellow]", highlight=False)
                continue
            
            p_resolved = p.resolve()
            if p_resolved.is_file():
                if not self.config.is_ignored(p_resolved, spec): 
                    added_paths.add(p_resolved)
            elif p_resolved.is_dir():
                # FileSelector 내의 get_all_files_in_dir는 is_ignored를 호출해야 하므로,
                # FileSelector 생성 시 config를 넘겨주는 방식으로 수정이 필요할 수 있음.
                # 여기서는 FileSelector가 config를 알아서 쓴다고 가정.
                temp_selector = FileSelector(config=self.config, theme_manager=self.theme_manager)
                added_paths.update(temp_selector.get_all_files_in_dir(p_resolved))

        self.app.attached = sorted([str(p) for p in current_paths.union(added_paths)])
        if self.app.attached:
            self.console.print(f"[green]현재 총 {len(self.app.attached)}개 파일이 첨부되었습니다.[/green]", highlight=False)
            self._display_attachment_tokens(self.app.attached, self.app.compact_mode)

    def handle_clearfiles(self, args: List[str]) -> None:
        """모든 첨부 파일을 초기화합니다."""
        self.app.attached.clear()
        self.console.print("[green]모든 첨부 파일이 제거되었습니다.[/green]", highlight=False)
        
    def handle_diff_code(self, args: List[str]) -> None:
        """코드 블록 비교 TUI를 엽니다."""
        differ = CodeDiffer(
            attached_files=self.app.attached,
            session_name=self.app.current_session_name,
            messages=self.app.messages,
            theme_manager=self.theme_manager,
            config=self.config,
            console=self.console
        )
        self.differ_ref["inst"] = differ
        differ.start()
        self.differ_ref["inst"] = None
        self._snap_scroll_to_bottom()

    # --- 세션 및 즐겨찾기 관리 ---

    def _backup_current_session(self, session_name: str) -> bool:
        """지정된 세션과 관련 코드 파일들을 안전하게 백업합니다."""
        session_path = self.config.get_session_path(session_name)
        
        # 백업할 세션 파일이 존재하고, 내용이 비어있지 않은 경우에만 진행
        if not session_path.exists() or session_path.stat().st_size == 0:
            return False

        backup_dir = self.config.SESSION_DIR / "backup"
        backup_dir.mkdir(exist_ok=True)
        code_files_backed_up = []
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        # 세션 파일 백업
        backup_path = backup_dir / f"session_{session_name}_{timestamp}.json"
        shutil.move(str(session_path), str(backup_path))
        self.console.print(f"[green]세션 백업: {backup_path.relative_to(self.config.BASE_DIR)}[/green]", highlight=False)
    
        # 관련 코드 블록 파일들 백업
        code_backup_dir = self.config.CODE_OUTPUT_DIR / "backup" / f"{session_name}_{timestamp}"
        matching_files = list(self.config.CODE_OUTPUT_DIR.glob(f"codeblock_{session_name}_*"))
        
        if matching_files:
            code_backup_dir.mkdir(parents=True, exist_ok=True)
            for code_file in matching_files:
                try:
                    shutil.move(str(code_file), str(code_backup_dir / code_file.name))
                    code_files_backed_up.append(code_file.name)
                except Exception as e:
                    self.console.print(f"[yellow]코드 파일 백업 실패 ({code_file.name}): {e}[/yellow]", highlight=False)

            self.console.print(f"[green]코드 파일 {len(matching_files)}개 백업: {code_backup_dir.relative_to(self.config.BASE_DIR)}[/green]", highlight=False)
        
        if code_files_backed_up:
            backup_info = []
            
            code_display_path = code_backup_dir.relative_to(self.config.BASE_DIR)
            backup_info.append(
                f"[green]코드 파일 {len(code_files_backed_up)}개:[/green]\n  {code_display_path}/"
            )
            
            # 백업된 파일 목록 표시 (최대 5개)
            for i, filename in enumerate(code_files_backed_up[:5]):
                backup_info.append(f"    • {filename}")
            if len(code_files_backed_up) > 5:
                backup_info.append(f"    ... 외 {len(code_files_backed_up) - 5}개")
            
            self.console.print(
                Panel(
                    f"세션 '{session_name}'이 초기화되었습니다.\n\n"
                    f"[bold]백업 위치:[/bold]\n" + "\n".join(backup_info),
                    title="[yellow]✅ 세션 초기화 및 백업 완료[/yellow]",
                    border_style="green"
                )
                , highlight=False
            )

        return True

    def _restore_flow(self, session_name: str) -> None:
        """
        공통 복원 플로우(현재 세션 스냅샷→정리→대상 복원)를 수행.
        단, '현재 세션 == 복원 대상'일 때는 사전 스냅샷을 건너뛰어
        단일 백업 슬롯을 덮어쓰는 일을 방지한다.
        """
        cur = getattr(self.app, "current_session_name", None)

        # 현재와 대상이 다를 때만 pre-restore 스냅샷 시행
        if cur and cur != session_name:
            ok = self._snapshot_session_single(cur, reason="pre-restore")
            if ok:
                self._delete_session_file(cur)
                self._remove_session_code_files(cur)
            else:
                self.console.print(
                    "[yellow]경고: 스냅샷 실패로 live/코드 파일을 삭제하지 않았습니다.[/yellow]",
                    highlight=False
                )
                # [안전장치] 스냅샷 실패 시, 복원 프로세스를 중단하여 데이터 유실 방지
                self.console.print("[red]안전을 위해 복원 작업을 중단합니다.[/red]", highlight=False)
                return
        elif cur and cur == session_name:
            # 동일 세션 복원 시에는 스냅샷 생략(백업 보호)
            self.console.print(
                "[dim]같은 세션으로 복원: 사전 스냅샷을 건너뜁니다(백업 보호).[/dim]",
                highlight=False
            )

        # [변경] 1. 모든 파일 I/O를 먼저 수행하고 결과 데이터를 받음
        restored_data = self._restore_session_single(session_name)

        # [변경] 2. 파일 작업이 성공했을 때만 앱의 메모리 상태를 갱신
        if restored_data:
            self._load_session_into_app(session_name)
            # load_session_into_app은 파일에서 다시 읽으므로, restored_data를 직접 넘길 필요는 없음
            try:
                self.config.save_current_session_name(session_name)
            except Exception:
                pass
            self.console.print(
                f"[green]세션 전환 완료 → '{session_name}'[/green]",
                highlight=False
            )
        else:
            self.console.print(
                f"[red]복원 실패: 대상 스냅샷을 찾을 수 없거나 읽기 실패[/red]",
                highlight=False
            )

    def _delete_single_snapshot(self, session_name: str) -> Tuple[bool, int]:
        """
        단일 스냅샷(세션+코드 백업)을 삭제합니다.
        - 세션 스냅샷 JSON: .gpt_sessions/backups/session_<slug>.json
        - 코드 스냅샷 디렉터리: gpt_codes/backup/<slug>/
        반환: (json_deleted, code_files_removed)
        """
        json_deleted = False
        code_removed = 0

        # 세션 스냅샷 JSON
        bj = self._single_backup_json(session_name)
        try:
            if bj.exists():
                bj.unlink()
                json_deleted = True
                self.console.print(f"[dim]스냅샷 JSON 삭제: {bj.relative_to(self.config.BASE_DIR)}[/dim]", highlight=False)
        except Exception as e:
            self.console.print(f"[yellow]스냅샷 JSON 삭제 실패({bj.name}): {e}[/yellow]", highlight=False)

        # 코드 스냅샷 디렉터리
        code_snap_dir = self._code_single_backup_dir(session_name)
        if code_snap_dir.exists() and code_snap_dir.is_dir():
            try:
                # 제거할 파일 개수 계산
                code_removed = sum(1 for _ in code_snap_dir.glob("**/*") if _.is_file())
                shutil.rmtree(code_snap_dir, ignore_errors=True)
                self.console.print(f"[dim]코드 스냅샷 삭제: {code_snap_dir.relative_to(self.config.BASE_DIR)} ({code_removed}개)[/dim]", highlight=False)
            except Exception as e:
                self.console.print(f"[yellow]코드 스냅샷 삭제 실패({code_snap_dir.name}): {e}[/yellow]", highlight=False)

        return json_deleted, code_removed

    def handle_reset(self, args: List[str]) -> None:
        """
        세션 초기화(옵션형)
        - 기본(soft reset): 리셋 직전 상태를 단일 스냅샷으로 보존 → 이후 /restore <session>으로 되돌리기 가능
        - --no-snapshot: 스냅샷을 찍지 않고 초기화(기존 스냅샷은 그대로 둠)
        - --hard: 스냅샷 생성 없이, 기존 스냅샷(JSON+코드 백업)까지 모두 삭제 → 복구 불가, 완전 초기화

        사용법:
          /reset                 # soft reset (스냅샷 생성)
          /reset --no-snapshot   # 스냅샷 없이 초기화(기존 스냅샷 유지)
          /reset --hard          # 스냅샷 생성 안 함 + 기존 스냅샷도 삭제(복구 불가)
        """
        sess = getattr(self.app, "current_session_name", "default")
        hard = "--hard" in args
        no_snapshot = "--no-snapshot" in args

        # 1) 스냅샷(soft 기본) 또는 hard/no-snapshot 분기
        if hard:
            # 하드 리셋: 스냅샷 생성하지 않고, 기존 스냅샷도 삭제
            js_del, code_del = self._delete_single_snapshot(sess)
            self.console.print(
                f"[yellow]하드 리셋: 기존 스냅샷 제거(JSON:{'O' if js_del else 'X'}, code:{code_del}개)[/yellow]",
                highlight=False
            )
        elif not no_snapshot:
            # soft: 리셋 직전 상태 스냅샷(되돌리기용 안전망)
            ok = self._snapshot_session_single(sess, reason="reset")
            if not ok:
                self.console.print(
                    "[yellow]경고: 스냅샷 실패(/restore로 되돌리기 불가할 수 있음). 초기화를 계속합니다.[/yellow]",
                    highlight=False
                )

        # 2) 라이브(메모리+파일) 초기화
        #    - 메시지/사용량 초기화
        self.app.messages = []
        self.app.usage_history = []
        #    - 세션 파일(라이브) 초기화 저장
        self.config.save_session(
            sess,
            [],
            getattr(self.app, "model", ""),
            getattr(self.app, "model_context", 0),
            [],
            mode=getattr(self.app, "mode", "dev"),
        )
        #    - 현재 작업본 코드 블록 제거
        removed_live_codes = self._remove_session_code_files(sess)

        # 3) 결과 출력
        mode_str = "HARD" if hard else ("NO-SNAPSHOT" if no_snapshot else "SOFT")
        self.console.print(
            f"[green]세션 '{sess}' 초기화 완료[/green] "
            f"(mode: {mode_str}, codes removed: {removed_live_codes})",
            highlight=False
        )

    def handle_savefav(self, args: List[str]) -> None:
        """마지막 질문을 즐겨찾기에 저장합니다."""
        if not args:
            self.console.print("[red]즐겨찾기 이름을 지정해야 합니다. (예: /savefav my_q)[/red]", highlight=False)
            return
        
        user_messages = [m for m in self.app.messages if m.get("role") == "user"]
        if not user_messages:
            self.console.print("[yellow]저장할 사용자 질문이 없습니다.[/yellow]", highlight=False)
            return

        content_to_save = ""
        content = user_messages[-1]['content']
        if isinstance(content, list):
            content_to_save = " ".join([p['text'] for p in content if p.get('type') == 'text']).strip()
        elif isinstance(content, str):
            content_to_save = content
            
        if content_to_save:
            self.config.save_favorite(args[0], content_to_save)
            self.console.print(f"[green]'{args[0]}' 즐겨찾기 저장 완료.[/green]", highlight=False)
        else:
            self.console.print("[yellow]저장할 텍스트 내용이 없습니다.[/yellow]", highlight=False)

    def handle_usefav(self, args: List[str]) -> None:
        """저장된 즐겨찾기를 현재 프롬프트에 불러옵니다."""
        if not args:
            self.console.print("[red]사용할 즐겨찾기 이름을 지정해야 합니다.[/red]", highlight=False)
            return
        
        fav_content = self.config.load_favorites().get(args[0])
        if fav_content:
            self.app._next_prompt_default = fav_content
            self.console.print("[green]프롬프트에 즐겨찾기 내용을 채워두었습니다. [Enter]로 실행하세요.[/green]", highlight=False) 
        else:
            self.console.print(f"[red]'{args[0]}' 즐겨찾기를 찾을 수 없습니다.[/red]", highlight=False)

    def handle_favs(self, args: List[str]) -> None:
        """저장된 모든 즐겨찾기 목록을 표시합니다."""
        favs = self.config.load_favorites()
        if not favs:
            self.console.print("[yellow]저장된 즐겨찾기가 없습니다.[/yellow]", highlight=False)
            return
        
        table = Table(title="⭐ 즐겨찾기 목록", box=ROUNDED)
        table.add_column("이름", style="cyan"); table.add_column("내용")
        for name, content in favs.items():
            table.add_row(name, (content[:80] + '...') if len(content) > 80 else content)
        self.console.print(table, highlight=False)
        
    def handle_commands(self, args: List[str]) -> None:
        """사용 가능한 모든 명령어 목록을 표시합니다."""
        self.console.print(Panel.fit(constants.COMMANDS, title="[yellow]/명령어[/yellow]"), highlight=False)
    
    def _build_context_report(
        self,
        model_name: str,
        model_context_limit: int,
        system_prompt_text: str,
        messages_to_send: List[Dict[str, Any]],
        reserve_for_completion: int,
        trim_ratio: float,
        compact_mode: bool,
        top_n: int = 5,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        상세 컨텍스트 보고서 문자열과 원시 통계를 반환.
        - TokenEstimator + (벤더 오프셋, trim_ratio)와 동일한 로직으로 집계
        - prompt_budget/used 를 분리 표기
        """
        te = self.app.token_estimator

        # 벤더 오프셋(트리밍과 동일한 규칙)
        vendor_offset = 0
        mname = (model_name or "").lower()
        for vendor, offset in Utils._VENDOR_SPECIFIC_OFFSET.items():
            if vendor in mname:
                vendor_offset = offset
                break

        # 시스템/예산 계산
        sys_tokens = te.count_text_tokens(system_prompt_text or "")
        available_for_prompt = max(0, model_context_limit - sys_tokens - reserve_for_completion - vendor_offset)
        prompt_budget = int(available_for_prompt * trim_ratio)

        # 전체 메시지 토큰 합계(추정)
        per_msg: List[Tuple[int, Dict[str, Any], int]] = [
            (i, m, Utils._count_message_tokens_with_estimator(m, te))
            for i, m in enumerate(messages_to_send)
        ]
        prompt_used = sum(t for _, _, t in per_msg)

        # 항목별(텍스트/이미지/PDF) 세부 집계
        text_tokens = 0
        image_tokens = 0
        pdf_tokens = 0
        image_count = 0
        pdf_count = 0

        for _, msg, _t in per_msg:
            content = msg.get("content", "")
            if isinstance(content, str):
                text_tokens += te.count_text_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    ptype = part.get("type")
                    if ptype == "text":
                        text_tokens += te.count_text_tokens(part.get("text", ""))
                    elif ptype == "image_url":
                        image_count += 1
                        image_url = part.get("image_url", {}) or {}
                        url = image_url.get("url", "")
                        detail = image_url.get("detail", "auto")
                        if isinstance(url, str) and "base64," in url:
                            try:
                                b64 = url.split("base64,", 1)[1]
                                image_tokens += te.estimate_image_tokens(b64, detail=detail)
                            except Exception:
                                image_tokens += 1105
                        else:
                            image_tokens += 85
                    elif ptype == "file":
                        pdf_count += 1
                        file_data = part.get("file", {}) or {}
                        data_url = file_data.get("file_data", "")
                        filename = (file_data.get("filename") or "").lower()
                        if filename.endswith(".pdf") and "base64," in data_url:
                            try:
                                b64 = data_url.split("base64,", 1)[1]
                                pdf_bytes = base64.b64decode(b64)
                                pdf_tokens += int(len(pdf_bytes) / 1024 * 3)
                            except Exception:
                                pdf_tokens += 1000
                        else:
                            pdf_tokens += 500

        # 총합(시스템 포함 X: 시스템은 별도 표기)
        prompt_total_est = prompt_used
        total_with_sys_and_reserve = sys_tokens + prompt_total_est + reserve_for_completion + vendor_offset

        # 진행도 바 유틸
        def _bar(percent: float, width: int = 30, fill_char="█", empty_char="░") -> str:
            p = max(0.0, min(100.0, percent))
            filled = int(round(width * p / 100.0))
            return f"{fill_char * filled}{empty_char * (width - filled)} {p:>5.1f}%"

        # 퍼센트들
        pct_total = (total_with_sys_and_reserve / model_context_limit) * 100 if model_context_limit else 0
        pct_prompt_budget = (prompt_used / prompt_budget * 100) if prompt_budget > 0 else 0

        # 상위 N개 메시지(대형) 정렬
        top = sorted(per_msg, key=lambda x: x[2], reverse=True)[:top_n]

        # 리포트 문자열 구성
        lines: List[str] = []
        lines.append("[bold]컨텍스트 세부[/bold]")
        lines.append("")
        lines.append(f"모델 한계: {model_context_limit:,}  |  trim_ratio: {trim_ratio:.2f}  |  vendor_offset: {vendor_offset:,}")
        lines.append(f"시스템 프롬프트: {sys_tokens:,} tokens")
        lines.append(f"응답 예약: {reserve_for_completion:,} tokens")
        lines.append("")
        lines.append("[bold]총합(시스템+프롬프트+예약)[/bold]")
        lines.append(_bar(pct_total))
        lines.append(f"합계: {total_with_sys_and_reserve:,} / {model_context_limit:,} tokens")
        lines.append("")
        lines.append("[bold]프롬프트 예산 사용(시스템/예약 제외)[/bold]")
        if prompt_budget > 0:
            lines.append(_bar(pct_prompt_budget))
        lines.append(f"프롬프트 사용: {prompt_used:,} / 예산 {prompt_budget:,}  (가용 {available_for_prompt:,})")
        lines.append("")
        lines.append("[bold]항목별 세부[/bold]")
        lines.append(f"- 텍스트: {text_tokens:,} tokens")
        lines.append(f"- 이미지: {image_tokens:,} tokens  (개수 {image_count})")
        lines.append(f"- PDF/파일: {pdf_tokens:,} tokens  (개수 {pdf_count})")
        if compact_mode:
            lines.append("")
            lines.append("[green]📦 Compact Mode 활성: 과거 첨부파일이 압축되어 전송량 절감 중[/green]")

        # 상위 N개 무거운 메시지
        if top:
            lines.append("")
            lines.append(f"[bold]대형 메시지 Top {len(top)}[/bold]")
            for idx, msg, tok in top:
                role = msg.get("role", "user")
                # 미리보기 텍스트 생성
                preview = ""
                content = msg.get("content", "")
                if isinstance(content, str):
                    preview = content.strip().replace("\n", " ")
                elif isinstance(content, list):
                    texts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    preview = (texts[0] if texts else "").strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                lines.append(f"- #{idx+1:>3} [{role}] {tok:,} tokens | {preview}")

        report = "\n".join(lines)

        stats = {
            "model_context_limit": model_context_limit,
            "sys_tokens": sys_tokens,
            "reserve_for_completion": reserve_for_completion,
            "vendor_offset": vendor_offset,
            "trim_ratio": trim_ratio,
            "available_for_prompt": available_for_prompt,
            "prompt_budget": prompt_budget,
            "prompt_used": prompt_used,
            "prompt_pct_used": pct_prompt_budget,
            "total_with_sys_and_reserve": total_with_sys_and_reserve,
            "total_pct": pct_total,
            "text_tokens": text_tokens,
            "image_tokens": image_tokens,
            "pdf_tokens": pdf_tokens,
            "image_count": image_count,
            "pdf_count": pdf_count,
            "top_messages": [(i, tok) for i, _, tok in top],
        }
        return report, stats

    def handle_show_context(self, args: List[str]) -> None:
        """
        현재 컨텍스트 사용량을 상세히 분석하여 표시합니다.
        - _gptcli.py와 동일하게 옵션을 지원:
        * -v / --verbose  → 기본 Top N = 10
        * --top N         → Top N을 임의로 지정
        - Compact 모드가 켜져 있으면, 압축 전/후 비교 요약 섹션을 보고서에 삽입
        """
        # 1) 옵션 파싱 (-v/--verbose, --top N)
        verbose = ("-v" in args) or ("--verbose" in args)
        top_n = 10 if verbose else 5
        try:
            if "--top" in args:
                k = args.index("--top")
                if k + 1 < len(args):
                    top_n = int(args[k + 1])
        except Exception:
            # 잘못된 값이 오면 기본값 유지
            pass

        # 2) 모델 컨텍스트에 따른 예약 토큰(휴리스틱)
        if self.app.model_context >= 200_000:
            reserve_for_completion = 32_000
        elif self.app.model_context >= 128_000:
            reserve_for_completion = 16_000
        else:
            reserve_for_completion = 4_096

        # 3) 시스템 프롬프트
        system_prompt = Utils.get_system_prompt_content(self.app.mode).strip()

        # 4) 전송 대상 메시지 준비(Compact 모드 반영)
        messages_for_estimation = self.app.get_messages_for_sending()

        # 5) Compact 모드인 경우, 원본 메시지 기준의 통계도 함께 산출하여 비교 섹션에 활용
        original_stats = None
        if self.app.compact_mode:
            # 원본 메시지로 통계만 산출(Top N=0으로 리포트는 무시, stats만 사용)
            _report_orig, original_stats = self._build_context_report(
                model_name=self.app.model,
                model_context_limit=self.app.model_context,
                system_prompt_text=system_prompt,
                messages_to_send=self.app.messages,           # 원본 메시지
                reserve_for_completion=reserve_for_completion,
                trim_ratio=constants.CONTEXT_TRIM_RATIO,
                compact_mode=False,                           # 비교용(압축 아님)
                top_n=0,
            )

        # 6) 최종 리포트(Compact 모드가 켜져 있으면 get_messages_for_sending() 결과 기준)
        report, stats = self._build_context_report(
            model_name=self.app.model,
            model_context_limit=self.app.model_context,
            system_prompt_text=system_prompt,
            messages_to_send=messages_for_estimation,
            reserve_for_completion=reserve_for_completion,
            trim_ratio=constants.CONTEXT_TRIM_RATIO,
            compact_mode=self.app.compact_mode,
            top_n=top_n,
        )

        # 7) Compact 모드 비교 섹션 삽입(원본 대비 절약량/비율)
        if self.app.compact_mode and original_stats:
            try:
                report_lines = report.split('\n')
                saved_tokens = max(0, original_stats["prompt_used"] - stats["prompt_used"])
                saved_percent = (
                    (saved_tokens / original_stats["prompt_used"] * 100.0)
                    if original_stats["prompt_used"] > 0 else 0.0
                )
                compression_info = [
                    "",
                    "[bold cyan]📦 Compact Mode 효과[/bold cyan]",
                    f"원본 프롬프트: {original_stats['prompt_used']:,} 토큰",
                    f"압축 후 프롬프트: {stats['prompt_used']:,} 토큰 ([green]-{saved_percent:.1f}%[/green])",
                    f"절약된 토큰: {saved_tokens:,}",
                ]
                # '항목별 세부' 섹션 바로 위에 삽입 시도
                insert_pos = report_lines.index("[bold]항목별 세부[/bold]")
                report_lines[insert_pos:insert_pos] = compression_info + [""]
                report = "\n".join(report_lines)
            except ValueError:
                # 해당 제목을 못 찾으면 맨 끝에 덧붙임
                report += "\n" + "\n".join(compression_info)

        # 8) 출력(경계 색상은 총 사용률에 따라 결정)
        border_style = "cyan" if stats["total_pct"] < 70 else ("yellow" if stats["total_pct"] < 90 else "red")
        self.console.print(
            Panel.fit(
                report,
                title=f"[cyan]컨텍스트 상세 (모델: {self.app.model})[/cyan]",
                border_style=border_style
            )
            , highlight=False
        )

class AIStreamParser:
    """
    OpenRouter API에서 받은 응답 스트림을 실시간으로 파싱하고 렌더링하는 클래스.
    복잡한 상태 머신을 내부에 캡슐화하여 관리합니다.
    """
    def __init__(self, client: OpenAI, console: Console):
        """
        Args:
            client (OpenAI): API 통신을 위한 OpenAI 클라이언트 인스턴스.
            console (Console): 출력을 위한 Rich Console 인스턴스.
        """
        self.client = client
        self.console = console
        self._reset_state()
        # 출력 중복 방지용 마지막 플러시 스냅샷
        self._last_emitted: str = ""
    def _emit_markup(self, text: str) -> None:
        """중복 방지 후 마크업 텍스트 출력"""
        if not text:
            return
        if text == self._last_emitted:
            return
        self.console.print(text, end="", highlight=False)
        self._last_emitted = text

    def _emit_raw(self, text: str) -> None:
        """중복 방지 후 raw 텍스트 출력"""
        if not text:
            return
        if text == self._last_emitted:
            return
        self.console.print(text, end="", markup=False, highlight=False)
        self._last_emitted = text

    def _reset_state(self):
        """새로운 스트리밍 요청을 위해 모든 상태 변수를 초기화합니다."""
        self.full_reply: str = ""
        self.in_code_block: bool = False
        self.buffer: str = ""
        self.code_buffer: str = ""
        self.language: str = "text"
        self.normal_buffer: str = ""
        self.reasoning_buffer: str = ""
        self.nesting_depth: int = 0
        self.outer_fence_char: str = "`"
        self.outer_fence_len: int = 0
        self.last_flush_time: float = 0.0
        self._last_emitted = ""

    def _simple_markdown_to_rich(self, text: str) -> str:
        """
        Rich 태그와 충돌하지 않도록 안전하게 마크다운 일부를 변환하는 렌더러.
        원본의 로직을 그대로 유지합니다.
        """
        placeholders: Dict[str, str] = {}
        placeholder_id_counter = 0

        def generate_placeholder(rich_tag_content: str) -> str:
            nonlocal placeholder_id_counter
            key = f"__GPCLI_PLACEHOLDER_{placeholder_id_counter}__"
            placeholders[key] = rich_tag_content
            placeholder_id_counter += 1
            return key

        def inline_code_replacer(match: re.Match) -> str:
            content = match.group(1).strip()
            if not content: return f"`{match.group(1)}`"
            escaped_content = content.replace('[', r'\[')
            rich_tag = f"[bold white on #484f58] {escaped_content} [/]"
            return generate_placeholder(rich_tag)

        processed_text = re.sub(r"`([^`]+)`", inline_code_replacer, text)
        
        def bold_replacer(match: re.Match) -> str:
            return generate_placeholder(f"[bold]{match.group(1)}[/bold]")

        processed_text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", bold_replacer, processed_text, flags=re.DOTALL)
        processed_text = processed_text.replace('[', r'\[')
        processed_text = re.sub(r"^(\s*)(\d+)\. ", r"\1[yellow]\2.[/yellow] ", processed_text, flags=re.MULTILINE)
        processed_text = re.sub(r"^(\s*)[\-\*] ", r"\1[bold blue]•[/bold blue] ", processed_text, flags=re.MULTILINE)

        for key in reversed(list(placeholders.keys())):
            processed_text = processed_text.replace(key, placeholders[key])
        return processed_text

    def _collapse_reasoning_live_area(self, live: Live, clear_height: int):
        """
        reasoning Live를 '완전히 없애고'(빈 줄도 남기지 않고) 화면을 당깁니다.
        - 순서:
        1) live.stop(refresh=False)로 마지막 프레임 재출력 없이 정지
        2) 커서를 패널의 첫 줄로 올림(ESC [ n F)
        3) 그 위치부터 n줄 삭제(ESC [ n M) → 아래가 위로 당겨짐
        """
        con = live.console
        try:
            # 마지막 프레임을 다시 그리지 않도록 refresh=False
            live.stop(refresh=False)
        except Exception:
            try:
                live.stop()
            except Exception:
                pass

        # 커서를 n줄 위(해당 라인의 선두)로 이동 후, n줄 삭제
        # 주: markup=False/ highlight=False로 원시 ANSI를 그대로 출력
        esc = "\x1b"
        con.print(f"{esc}[{clear_height}F{esc}[{clear_height}M", end="", markup=False, highlight=False)

    def stream_and_parse(
        self,
        system_prompt: Dict[str, Any],
        final_messages: List[Dict[str, Any]],
        model: str,
        pretty_print: bool = True
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        API 요청을 보내고, 스트리밍 응답을 파싱하여 실시간으로 렌더링합니다.

        Args:
            system_prompt (Dict): 시스템 프롬프트 객체.
            final_messages (List[Dict]): 컨텍스트가 트리밍된 최종 메시지 목록.
            model (str): 사용할 모델 이름.
            pretty_print (bool): Rich 라이브러리를 사용한 고급 출력 여부.

        Returns:
            Optional[Tuple[str, Dict]]: (전체 응답 문자열, 토큰 사용량 정보) 튜플.
                                         실패 시 None.
        """
        self._reset_state()
        usage_info = None

        model_online = model if model.endswith(":online") else f"{model}:online"
        extra_body = {'reasoning': {}} # alwyas default

        try:
            with self.console.status("[cyan]Loading...", spinner="dots"):
                stream = self.client.chat.completions.create(
                    model=model_online,
                    messages=[system_prompt] + final_messages,
                    stream=True,
                    extra_body=extra_body,
                )
        except KeyboardInterrupt:
            self.console.print("\n[yellow]⚠️ 응답이 중단되었습니다.[/yellow]", highlight=False)
            return None
        except OpenAIError as e:
            self.console.print(f"[red]API 오류: {e}[/red]", highlight=False)
            return None

        # 확연한 구분을 위함
        self.console.print(Syntax(" ", "python", theme="monokai", background_color="#F4F5F0"))
        model_name_syntax = Syntax(f"{model}:", "text", theme="monokai", background_color="#CD212A")
        self.console.print(model_name_syntax)
        self.console.print(Syntax(" ", "python", theme="monokai", background_color="#008C45"))

        # --- Raw 출력 모드 ---
        if not pretty_print:
            try:
                for chunk in stream:
                    if hasattr(chunk, 'usage') and chunk.usage: usage_info = chunk.usage.model_dump()
                    delta = chunk.choices[0].delta if (chunk.choices and chunk.choices[0]) else None
                    if delta:
                        content = getattr(delta, "reasoning", "") or getattr(delta, "content", "")
                        if content:
                            self.full_reply += content
                            self.console.print(content, end="", markup=False, highlight=False)
            except KeyboardInterrupt: self.console.print("\n[yellow]⚠️ 응답 중단.[/yellow]", highlight=False)
            except StopIteration: pass
            finally: self.console.print()
            return self.full_reply, usage_info

        # --- Pretty Print 모드 (상태 머신) ---
        stream_iter = iter(stream)
        reasoning_live = code_live = None

        try:
            while True:
                chunk = next(stream_iter)
                if hasattr(chunk, 'usage') and chunk.usage: usage_info = chunk.usage.model_dump()
                delta = chunk.choices[0].delta if (chunk.choices and chunk.choices[0]) else None
                # reasoning 단계에서 content를 먼저 버퍼링했는지 추적
                content_buffered_in_reasoning = False

                # Reasoning 처리
                if hasattr(delta, 'reasoning') and delta.reasoning:
                    # Live 패널 시작 전, 완성된 줄만 출력하고 조각은 버퍼에 남깁니다.
                    if self.normal_buffer and '\n' in self.normal_buffer:
                        parts = self.normal_buffer.rsplit('\n', 1)
                        text_to_flush, remainder = parts[0] + '\n', parts[1]
                        self._emit_markup(self._simple_markdown_to_rich(text_to_flush))
                        self.normal_buffer = remainder # 조각은 남김
                    
                    reasoning_live = Live(console=self.console, auto_refresh=True, refresh_per_second=4, transient=False)
                    reasoning_live.start()
                    self.reasoning_buffer = delta.reasoning
                    while True: # Reasoning 내부 루프
                        try:
                            lines, total_lines = self.reasoning_buffer.splitlines(), len(self.reasoning_buffer.splitlines())
                            display_text = "\n".join(f"[italic]{l}[/italic]" for l in lines[-8:])
                            if total_lines > 8: display_text = f"[dim]... ({total_lines - 8}줄 생략) ...[/dim]\n{display_text}"
                            panel = Panel(display_text, height=constants.REASONING_PANEL_HEIGHT, title=f"[magenta]🤔 추론 과정[/magenta]", border_style="magenta")
                            reasoning_live.update(panel)

                            chunk = next(stream_iter)
                            delta = chunk.choices[0].delta
                            if hasattr(delta, 'reasoning') and delta.reasoning: 
                                self.reasoning_buffer += delta.reasoning
                            elif delta and delta.content: 
                                self.buffer += delta.content
                                content_buffered_in_reasoning = True
                                break
                        except StopIteration: break
                    
                    self._collapse_reasoning_live_area(reasoning_live, clear_height=constants.REASONING_PANEL_HEIGHT)
                    reasoning_live = None
                    if not (delta and delta.content): continue

                if not (delta and delta.content): 
                    continue
                
                self.full_reply += delta.content
                if not content_buffered_in_reasoning:
                    self.buffer += delta.content

                if not self.in_code_block and (self._looks_like_start_fragment(self.buffer) or self.buffer.endswith('`')):
                    continue

                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    
                    if not self.in_code_block:
                        start = self._is_fence_start_line(line)
                        if start:
                            # Live 패널 시작 전, 완성된 줄만 출력하고 조각은 버퍼에 남깁니다.
                            if self.normal_buffer and '\n' in self.normal_buffer:
                                parts = self.normal_buffer.rsplit('\n', 1)
                                text_to_flush, remainder = parts[0] + '\n', parts[1]
                                self._emit_markup(self._simple_markdown_to_rich(text_to_flush))
                                self.normal_buffer = remainder # 조각은 남김
                            elif self.normal_buffer and '\n' not in self.normal_buffer:
                                # 조각만 있으면 출력하지 않고 넘어감
                                pass
                            else: # 버퍼가 비어있거나, 이미 줄바꿈으로 끝나는 경우
                                self._emit_markup(self._simple_markdown_to_rich(self.normal_buffer))
                                self.normal_buffer = ""

                            self.outer_fence_char, self.outer_fence_len, self.language = start
                            self.language = self.language or "text"
                            self.in_code_block = True
                            self.nesting_depth = 0
                            self.code_buffer = ""
                            
                            code_live = Live(console=self.console, auto_refresh=True, refresh_per_second=4, transient=False)
                            code_live.start()
                            try:
                                while self.in_code_block: # Code Block 내부 루프
                                    lines = self.code_buffer.splitlines()
                                    total_lines = len(lines)
                                    display_height = constants.CODE_PREVIEW_PANEL_HEIGHT - 2
                                    display_code = "\n".join(lines[-display_height:])
                                    if total_lines > display_height: display_code = f"... ({total_lines - display_height}줄 생략) ...\n{display_code}"
                                    
                                    live_syntax = Syntax(display_code, self.language, theme="monokai", background_color="#272822")
                                    panel_height = min(constants.CODE_PREVIEW_PANEL_HEIGHT, len(display_code.splitlines()) + 2)
                                    temp_panel = Panel(live_syntax, height=panel_height, title=f"[yellow]코드 입력중 ({self.language})[/yellow]", border_style="dim")
                                    code_live.update(temp_panel)
                                    
                                    chunk = next(stream_iter)
                                    if hasattr(chunk, 'usage') and chunk.usage: usage_info = chunk.usage.model_dump()
                                    delta = chunk.choices[0].delta if (chunk.choices and chunk.choices[0]) else None
                                    if delta and delta.content:
                                        self.full_reply += delta.content
                                        self.buffer += delta.content

                                    while "\n" in self.buffer:
                                        sub_line, self.buffer = self.buffer.split("\n", 1)
                                        close_now = self._is_fence_close_line(sub_line, self.outer_fence_char, self.outer_fence_len)
                                        start_in_code = self._is_fence_start_line(sub_line)

                                        if start_in_code and start_in_code[1] >= self.outer_fence_len and start_in_code[2]:
                                            self.nesting_depth += 1
                                            self.code_buffer += sub_line + "\n"
                                        elif close_now:
                                            if self.nesting_depth > 0: self.nesting_depth -= 1
                                            else: self.in_code_block = False; break
                                            self.code_buffer += sub_line + "\n"
                                        else: self.code_buffer += sub_line + "\n"
                                    if not self.in_code_block: break

                                    if self.buffer and self._looks_like_close_fragment(self.buffer, self.outer_fence_char, self.outer_fence_len): continue
                            finally:
                                if self.code_buffer.rstrip():
                                    syntax_block = Syntax(self.code_buffer.rstrip(), self.language, theme="monokai", line_numbers=True, word_wrap=True)
                                    code_live.update(Panel(syntax_block, title=f"[green]코드 ({self.language})[/green]", border_style="green"))
                                code_live.stop()
                        else: self.normal_buffer += line + "\n"
                    else: # 방어 코드
                        self.code_buffer += line + "\n"

                if not self.in_code_block and self.buffer:
                    if not self._looks_like_start_fragment(self.buffer):
                        self.normal_buffer += self.buffer
                        self.buffer = ""
                
                if self.normal_buffer and (len(self.normal_buffer) > 5 or (time.time() - self.last_flush_time > 0.25)):
                    if '\n' in self.normal_buffer:
                        parts = self.normal_buffer.rsplit('\n', 1)
                        text_to_flush, self.normal_buffer = parts[0] + '\n', parts[1]
                        #self.console.print(self._simple_markdown_to_rich(text_to_flush), end="", highlight=False)
                        self._emit_markup(self._simple_markdown_to_rich(text_to_flush))
                        self.last_flush_time = time.time()
        
        except (KeyboardInterrupt, StopIteration):
            if isinstance(reasoning_live, Live) and reasoning_live.is_started: self._collapse_reasoning_live_area(reasoning_live, constants.REASONING_PANEL_HEIGHT)
            if isinstance(code_live, Live) and code_live.is_started: code_live.stop()
            if isinstance(sys.exc_info()[1], KeyboardInterrupt): 
                self.console.print("\n[yellow]⚠️ 응답이 중단되었습니다.[/yellow]", highlight=False)

        finally: # 스트림이 정상/비정상 종료될 때 마지막 남은 버퍼 처리
            if self.normal_buffer: 
                #self.console.print(self._simple_markdown_to_rich(self.normal_buffer), end="", highlight=False)
                self._emit_markup(self._simple_markdown_to_rich(self.normal_buffer))
                self.normal_buffer = ""
            if self.in_code_block and self.code_buffer:
                self.console.print("\n[yellow]경고: 코드 블록이 제대로 닫히지 않았습니다.[/yellow]", highlight=False)
                self.console.print(Syntax(self.code_buffer.rstrip(), self.language, theme="monokai", line_numbers=True), highlight=False)
            if reasoning_live and reasoning_live.is_started: self._collapse_reasoning_live_area(reasoning_live, constants.REASONING_PANEL_HEIGHT)
            if code_live and code_live.is_started: code_live.stop()

        self.console.print()
        return self.full_reply, usage_info
    
    @staticmethod
    def _is_fence_start_line(line: str) -> Optional[Tuple[str, int, str]]:
        """
        '완전한 한 줄'(개행 제거)에 대해 '줄 시작 펜스'인지 판정(엄격).
        - ^[ \t]{0,3} (```...) [ \t]* <info_token>? [ \t]*$
        - info_token: 언어 토큰 1개만 허용([A-Za-z0-9_+.\-#]+), 그 뒤에는 공백만 허용
        - 예) '```python'         → 시작으로 인정
            '```python   '      → 시작으로 인정
            '```'               → 시작으로 인정
            '```python 이런식'  → 시작으로 인정하지 않음(설명 문장)
            '문장 중간 ```python' → 시작으로 인정하지 않음(인라인)
        반환: (fence_char('`'), fence_len(>=3), info_token or "")
        """
        if line is None:
            return None
        s = line.rstrip("\r")
        # 모든 들여쓰기 허용
        m = re.match(r'^\s*(?P<fence>(?P<char>`){3,})[ \t]*(?P<info>[A-Za-z0-9_+\-.#]*)[ \t]*$', s)
        if not m:
            return None

        fence_char = m.group('char')
        # fence 연속 길이 산출
        n = 0
        for ch in s.lstrip():
            if ch == fence_char:
                n += 1
            else:
                break
        if n < 3:
            return None

        info = (m.group('info') or "").strip()
        # info는 '한 개 토큰'만 허용(공백 불가) → 정규식에서 이미 보장됨
        return fence_char, n, info

    @staticmethod
    def _is_fence_close_line(line: str, fence_char: str, fence_len: int) -> bool:
        """
        '완전한 한 줄'이 닫힘 펜스인지 판정 (들여쓰기 유연).
        - ^\s* fence_char{fence_len,} [ \t]*$
        """
        if line is None:
            return False
        s = line.rstrip("\r")
        pattern = rf'^\s*{re.escape(fence_char)}{{{max(3, fence_len)},}}[ \t]*$'
        return re.match(pattern, s) is not None

    @staticmethod
    def _looks_like_start_fragment(fragment: str) -> bool:
        """
        개행 없는 조각이 '줄 시작 펜스'처럼 보이면 True (들여쓰기 유연).
        """
        if not fragment or "\n" in fragment:
            return False
        return re.match(r'^\s*(`{3,})', fragment) is not None

    @staticmethod
    def _looks_like_close_fragment(fragment: str, fence_char: str, fence_len: int) -> bool:
        """
        개행 없는 조각이 '줄 시작 닫힘 펜스'처럼 보이면 True.
        """
        if not fragment or "\n" in fragment:
            return False
        s = fragment.strip()
        return re.match(rf'^{re.escape(fence_char)}{{{max(3, fence_len)},}}\s*$', s) is not None

def main() -> None:
    load_dotenv()
    try:
        cfg = ConfigManager()
        chosen_session = cfg.load_current_session_name() or "default"
        sess_data = cfg.load_session(chosen_session)
        chosen_mode = sess_data.get("mode", "dev")
        app = GPTCLI(session_name=chosen_session, mode=chosen_mode)
        app.run()

    except KeyboardInterrupt:
        print("\n사용자에 의해 종료되었습니다. 안녕히 가세요!")
        sys.exit(0)
    except Exception as e:
        print(f"\n[오류] 예기치 못한 문제가 발생했습니다: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()