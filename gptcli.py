from __future__ import annotations

# ── stdlib
import argparse
import base64
import json
import os
import re
import sys
import time
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Set, Union

# ── 3rd-party
import pyperclip
import urwid
from dotenv import load_dotenv
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter, FuzzyCompleter, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.application.current import get_app
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.table import Table
from rich.box import ROUNDED

# ── local
import src.constants as constants
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.theme import ThemeManager
from src.gptcli.services.tokens import TokenEstimator
from src.gptcli.services.ai_stream import AIStreamParser
from src.gptcli.ui.completion import PathCompleterWrapper, AttachedFileCompleter, ConditionalCompleter
from src.gptcli.ui.file_selector import FileSelector
from src.gptcli.ui.diff_view import DiffListBox, CodeDiffer
from src.gptcli.models.model_searcher import ModelSearcher
from src.gptcli.utils.common import Utils

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