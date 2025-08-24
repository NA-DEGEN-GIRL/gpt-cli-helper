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
        model_disp = self.model.split('/', 1)[-1] if isinstance(self.model, str) else str(self.model)
        parts = [model_disp, f"session: {self.current_session_name}", f"mode: {self.mode}"]
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
                    #should_exit = self.command_handler.dispatch(user_input)
                    should_exit = self.router.dispatch(user_input)
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