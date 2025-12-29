from __future__ import annotations

# ── stdlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 3rd-party
import urwid
from dotenv import load_dotenv
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import PathCompleter, WordCompleter, FuzzyCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import Condition
from prompt_toolkit.application.current import get_app
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# ── local
import src.constants as constants
from src.gptcli.core.commands import CommandRouter, SimpleCallbackCommand
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.theme import ThemeManager
from src.gptcli.services.tokens import TokenEstimator
from src.gptcli.services.ai_stream import AIStreamParser
from src.gptcli.services.sessions import SessionService
from src.gptcli.services.tool_loop import ToolLoopService
from src.gptcli.services.summarization import SummarizationService
from src.gptcli.tools.permission import TrustLevel
from src.gptcli.tools.schemas import estimate_tool_schemas_tokens
from src.gptcli.ui.completion import PathCompleterWrapper, ConditionalCompleter
from src.gptcli.utils.common import Utils
from src.gptcli.commands.handler import CommandHandler

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
        self._pasted_text_counter: int = 0  # 긴 텍스트 붙여넣기 카운터
        self._pasted_content: Optional[str] = None  # 압축 표시된 원본 텍스트 저장
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            default_headers={
                "HTTP-Referer": os.getenv("APP_URL", "https://github.com/user/gpt-cli"),
                "X-Title": os.getenv("APP_TITLE", "GPT-CLI"),
            }
        )
        
        self.parser = AIStreamParser(self.client, self.console)
        self.token_estimator = TokenEstimator(console=self.console)
        self.sessions = SessionService(self.config, self.console)
        self.command_handler = CommandHandler(self, self.config, self.sessions)

        # --- Tool Loop Service 초기화 ---
        self.tool_loop = ToolLoopService(
            base_dir=self.config.BASE_DIR,
            console=self.console,
            parser=self.parser,
            trust_level=TrustLevel.READ_ONLY  # 기본값: 읽기 전용 (안전 모드)
        )
        # Tool 모드 기본 활성화
        self.tool_mode_enabled: bool = True

        # --- Summarization Service 초기화 ---
        self.summarization_service = SummarizationService(
            console=self.console,
            token_estimator=self.token_estimator,
            parser=self.parser,
            config={
                "threshold": constants.SUMMARIZATION_THRESHOLD,
                "min_messages": constants.MIN_MESSAGES_TO_SUMMARIZE,
                "keep_recent": constants.KEEP_RECENT_MESSAGES,
                "max_levels": constants.MAX_SUMMARY_LEVELS,
            }
        )

        self.router = CommandRouter(self.console.print)
        self._register_commands()

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

    def _register_commands(self) -> None:
        """
        CommandRouter에 기존 CommandHandler 메서드를 래핑해 등록합니다.
        반환값 True를 주는 명령(예: /exit)은 메인 루프를 종료합니다.
        """
        h = self.command_handler

        def reg(name: str, fn):
            # 주의: late-binding 방지 위해 기본 인자에 fn 바인딩
            self.router.register(
                SimpleCallbackCommand(name, lambda args, _fn=fn: _fn(args))
            )

        # 종료
        reg("exit", h.handle_exit)

        # 모드/테마/출력
        reg("compact_mode", h.handle_compact_mode)
        reg("pretty_print", h.handle_pretty_print)
        reg("mode", h.handle_mode)
        reg("theme", h.handle_theme)

        # 모델
        reg("select_model", h.handle_select_model)
        reg("search_models", h.handle_search_models)

        # 파일/TUI/디프
        reg("all_files", h.handle_all_files)
        reg("files", h.handle_files)
        reg("clearfiles", h.handle_clearfiles)
        reg("diff_code", h.handle_diff_code)

        # 세션/백업/초기화
        reg("session", h.handle_session)
        reg("backup", h.handle_backup)
        reg("reset", h.handle_reset)

        # 즐겨찾기
        reg("savefav", h.handle_savefav)
        reg("usefav", h.handle_usefav)
        reg("favs", h.handle_favs)

        # 최근 응답/보기/복사
        reg("last_response", h.handle_last_response)
        reg("raw", h.handle_raw)
        reg("copy", h.handle_copy)

        # 기타
        reg("commands", h.handle_commands)
        reg("show_context", h.handle_show_context)
        reg("edit", h.handle_edit)

        # Tool 관련
        reg("tools", h.handle_tools)
        reg("trust", h.handle_trust)
        reg("toolforce", h.handle_toolforce)

        # 요약 관련
        reg("summarize", h.handle_summarize)
        reg("show_summary", h.handle_show_summary)

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

        # Bracketed Paste: 긴 텍스트 붙여넣기 감지 및 압축 표시
        PASTE_LINE_THRESHOLD = 10
        gptcli_instance = self  # 클로저에서 self 참조

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            data = event.data  # 붙여넣기된 텍스트

            # 다양한 줄바꿈 문자 처리 (\r\n, \r, \n)
            normalized = data.replace('\r\n', '\n').replace('\r', '\n')
            lines = normalized.split('\n')
            line_count = len(lines)

            if line_count >= PASTE_LINE_THRESHOLD:
                # 원본 저장
                gptcli_instance._pasted_text_counter += 1
                gptcli_instance._pasted_content = data

                # 압축 표시 문자열 생성 (빈 줄이 아닌 첫 내용 찾기)
                first_content = ""
                for line in lines:
                    stripped = line.strip()
                    if stripped:
                        first_content = stripped[:50] + "..." if len(stripped) > 50 else stripped
                        break

                collapsed = f"[Pasted text #{gptcli_instance._pasted_text_counter} +{line_count} lines: {first_content}]"

                # 기존 버퍼 내용 + 압축 문자열
                event.current_buffer.insert_text(collapsed)
            else:
                # 짧은 텍스트는 그냥 삽입
                gptcli_instance._pasted_content = None
                event.current_buffer.insert_text(data)

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

    def _display_collapsed_input(self, text: str, line_threshold: int = 10) -> bool:
        """
        긴 텍스트 입력을 압축된 형태로 표시합니다.

        Args:
            text: 사용자 입력 텍스트
            line_threshold: 압축 표시 임계값 (기본 10줄)

        Returns:
            True if collapsed display was shown, False otherwise
        """
        lines = text.split('\n')
        line_count = len(lines)

        if line_count < line_threshold:
            return False

        self._pasted_text_counter += 1

        # 첫 3줄 미리보기
        preview_lines = lines[:3]
        preview = '\n'.join(preview_lines)
        if len(preview) > 150:
            preview = preview[:150] + "..."

        # 압축된 형태로 표시
        collapsed_header = f"[dim]├─ Pasted text #{self._pasted_text_counter} [cyan]+{line_count} lines[/cyan][/dim]"
        self.console.print(collapsed_header)

        # 미리보기를 들여쓰기하여 표시
        for line in preview_lines[:2]:
            display_line = line[:80] + "..." if len(line) > 80 else line
            self.console.print(f"[dim]│  {display_line}[/dim]")
        self.console.print(f"[dim]│  ...[/dim]")
        self.console.print(f"[dim]└─[/dim]")

        return True

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
        model_disp = self.model.split('/', 1)[-1] if isinstance(self.model, str) else str(self.model)
        parts = [model_disp, f"session: {self.current_session_name}", f"mode: {self.mode}"]
        if self.attached:
            parts.append(f"{len(self.attached)} files")
        if self.compact_mode:
            parts.append("compact mode")
        # Tool 모드 상태 표시
        if self.tool_mode_enabled:
            parts.append("🔧 tools")

        return f"[ {' | '.join(parts)} ]\nQ>> "

    def _handle_chat_message(self, user_input: str):
        """일반 채팅 메시지를 처리하는 전체 파이프라인입니다."""
        # 1. 메시지 객체 생성 및 대화 기록 추가
        user_message = self._prepare_user_message(user_input)
        self.messages.append(user_message)

        # 2. Compact 모드 적용
        messages_to_send = self.get_messages_for_sending()
        system_prompt_content = Utils.get_system_prompt_content(self.mode)

        reserve_map = {200000: 32000, 128000: 16000}
        reserve_for_completion = reserve_map.get(self.model_context, 4096)

        # Tool 모드가 활성화되어 있으면 Tool 스키마 토큰도 계산
        tools_tokens = estimate_tool_schemas_tokens() if self.tool_mode_enabled else 0
        system_prompt_tokens = self.token_estimator.count_text_tokens(system_prompt_content)

        # 2.5. 자동 요약 확인 및 수행 (컨텍스트 임계값 초과 시)
        messages_to_send, was_summarized = self.summarization_service.check_and_summarize(
            messages=messages_to_send,
            model=self.model,
            model_context_limit=self.model_context,
            system_prompt_tokens=system_prompt_tokens,
            reserve_for_completion=reserve_for_completion,
            tools_tokens=tools_tokens
        )

        # 요약이 수행되었으면 self.messages도 업데이트
        if was_summarized:
            self.messages = messages_to_send.copy()

        # 3. 컨텍스트 트리밍 (요약 이후에도 필요할 수 있음)
        final_messages = Utils.trim_messages_by_tokens(
            messages=messages_to_send,
            model_name=self.model,
            model_context_limit=self.model_context,
            system_prompt_text=system_prompt_content,
            token_estimator=self.token_estimator,
            console=self.console,
            reserve_for_completion=reserve_for_completion,
            trim_ratio=constants.CONTEXT_TRIM_RATIO,
            tools_tokens=tools_tokens
        )

        if not final_messages:
            self.messages.pop() # 전송 실패 시 마지막 메시지 제거
            return

        # 3. API 호출 및 응답 스트리밍 (Tool Loop 사용)
        system_prompt = {"role": "system", "content": system_prompt_content}

        # Tool 모드가 활성화된 경우 Tool Loop 사용
        if self.tool_mode_enabled:
            result = self.tool_loop.run_with_tools(
                system_prompt, final_messages, self.model, self.pretty_print_enabled
            )
        else:
            # Tool 모드 비활성화 시 기존 방식
            result = self.tool_loop.run_single(
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
        # Tool 모드와 일반 모드 모두 동일한 반환 형식: (response, usage)
        # Tool 실행 중간 메시지(tool_calls, tool results)는 세션에 저장하지 않음
        # 이는 Anthropic API의 tool_use/tool_result 페어링 요구사항 때문
        self.last_response, usage_info = result

        self.last_reply_code_blocks = Utils.extract_code_blocks(self.last_response)

        # 최종 텍스트 응답만 저장 (tool_calls 없는 순수 텍스트)
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

        # Tool 모드 안내
        if self.tool_mode_enabled:
            trust_status = self.tool_loop.get_trust_status()
            self.console.print(
                f"\n[bold cyan]🔧 Tool 모드 활성화[/bold cyan] | {trust_status}",
                highlight=False
            )
            self.console.print(
                "[dim]AI가 Read/Grep/Glob으로 파일을 읽을 수 있습니다. "
                "Write/Edit/Bash는 실행 전 확인을 요청합니다.[/dim]",
                highlight=False
            )
            self.console.print(
                "[dim]/trust full → 모든 Tool 자동 실행 | /trust none → 항상 확인 | /tools → Tool 모드 OFF[/dim]\n",
                highlight=False
            )
        
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
                    #should_exit = self.command_handler.dispatch(user_input)
                    should_exit = self.router.dispatch(user_input)
                    if should_exit:
                        break
                else:
                    # 압축 표시된 붙여넣기가 있으면 원본 사용
                    if self._pasted_content:
                        actual_input = self._pasted_content
                        # 전송 시 원본 정보 + 미리보기 표시
                        normalized = actual_input.replace('\r\n', '\n').replace('\r', '\n')
                        lines = normalized.split('\n')
                        line_count = len(lines)
                        char_count = len(actual_input)

                        # 빈 줄이 아닌 첫 3줄 미리보기
                        preview_lines = [l.strip() for l in lines if l.strip()][:3]
                        self.console.print(f"[dim]📤 전송: {line_count}줄, {char_count:,}자[/dim]")
                        self.console.print("[dim]┌─────────────────────────────────────[/dim]")
                        for pl in preview_lines:
                            display = pl[:60] + "..." if len(pl) > 60 else pl
                            self.console.print(f"[dim]│ {display}[/dim]")
                        if len(preview_lines) < len([l for l in lines if l.strip()]):
                            self.console.print("[dim]│ ...[/dim]")
                        self.console.print("[dim]└─────────────────────────────────────[/dim]")

                        self._pasted_content = None
                    else:
                        actual_input = user_input
                    self._handle_chat_message(actual_input)

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

def main() -> None:
    # 스크립트 위치 기준으로 .env 로드 (어디서 실행해도 동작)
    script_dir = Path(__file__).parent.resolve()
    load_dotenv(script_dir / ".env")
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