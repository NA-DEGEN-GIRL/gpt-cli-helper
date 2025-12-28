# src/gptcli/services/ai_stream.py
from __future__ import annotations
import json
import re, time, sys
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.live import Live
from openai import OpenAI, OpenAIError
import src.constants as constants


# ============================================================================
# Tool Call 버퍼 헬퍼 (스트리밍 시 조각을 조합)
# ============================================================================
class ToolCallBuffer:
    """
    스트리밍 응답에서 tool_calls 조각을 index별로 조합합니다.

    OpenAI API는 tool_calls를 스트리밍할 때 각 delta에 다음 형식으로 전달합니다:
    delta.tool_calls = [
        {
            "index": 0,
            "id": "call_xxx",  # 첫 조각에만 있음
            "type": "function",
            "function": {
                "name": "ToolName",  # 첫 조각에만 있음
                "arguments": "{\\"key\\"  # 조각별로 이어짐
            }
        }
    ]
    """

    def __init__(self):
        self._calls: Dict[int, Dict[str, Any]] = {}

    def add_delta(self, tool_calls_delta: List[Any]) -> None:
        """delta.tool_calls 조각을 버퍼에 추가합니다."""
        if not tool_calls_delta:
            return

        for tc in tool_calls_delta:
            idx = tc.index if hasattr(tc, 'index') else tc.get('index', 0)

            # 해당 index의 버퍼가 없으면 초기화
            if idx not in self._calls:
                self._calls[idx] = {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": ""
                    }
                }

            call = self._calls[idx]

            # id 업데이트 (첫 조각에만 있음)
            tc_id = tc.id if hasattr(tc, 'id') else tc.get('id')
            if tc_id:
                call["id"] = tc_id

            # function 정보 업데이트
            func = tc.function if hasattr(tc, 'function') else tc.get('function')
            if func:
                func_name = func.name if hasattr(func, 'name') else func.get('name')
                func_args = func.arguments if hasattr(func, 'arguments') else func.get('arguments')

                if func_name:
                    call["function"]["name"] = func_name
                if func_args:
                    call["function"]["arguments"] += func_args

    def get_tool_calls(self) -> List[Dict[str, Any]]:
        """완성된 tool_calls 목록을 반환합니다."""
        if not self._calls:
            return []
        # index 순서대로 정렬하여 반환
        return [self._calls[idx] for idx in sorted(self._calls.keys())]

    def has_calls(self) -> bool:
        """tool_calls가 있는지 여부."""
        return bool(self._calls)

    def get_current_status(self) -> str:
        """현재 버퍼링 중인 tool_calls 상태 문자열."""
        if not self._calls:
            return ""
        parts = []
        for idx in sorted(self._calls.keys()):
            call = self._calls[idx]
            name = call["function"]["name"] or "..."
            parts.append(f"[{idx}] {name}")
        return ", ".join(parts)

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
        pretty_print: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto"
    ) -> Optional[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]]:
        """
        API 요청을 보내고, 스트리밍 응답을 파싱하여 실시간으로 렌더링합니다.

        Args:
            system_prompt (Dict): 시스템 프롬프트 객체.
            final_messages (List[Dict]): 컨텍스트가 트리밍된 최종 메시지 목록.
            model (str): 사용할 모델 이름.
            pretty_print (bool): Rich 라이브러리를 사용한 고급 출력 여부.
            tools (Optional[List[Dict]]): Tool 스키마 목록 (Function Calling용).

        Returns:
            Optional[Tuple[str, Dict, List[Dict]]]: (전체 응답 문자열, 토큰 사용량 정보, tool_calls 목록) 튜플.
                                         실패 시 None.
        """
        self._reset_state()
        usage_info = None
        tool_call_buffer = ToolCallBuffer()  # tool_calls 버퍼링용

        model_online = model if model.endswith(":online") else f"{model}:online"

        # Gemini 모델은 tools + reasoning을 함께 사용할 때 thought_signature 오류 발생
        # tools 사용 시 Gemini에서는 reasoning을 비활성화
        is_gemini = "gemini" in model.lower()
        if tools and is_gemini:
            extra_body = {}  # Gemini + tools: reasoning 비활성화
        else:
            extra_body = {'reasoning': {}}  # 기본값: reasoning 활성화

        # API 호출 파라미터 구성
        api_params = {
            "model": model_online,
            "messages": [system_prompt] + final_messages,
            "stream": True,
            "extra_body": extra_body,
        }

        # tools가 제공된 경우 추가
        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = tool_choice

            # 디버그: Tool 모드 활성화 상태 표시
            choice_str = "[강제]" if tool_choice == "required" else "[자동]"
            self.console.print(f"[dim]🔧 Tools 활성화: {len(tools)}개 도구 {choice_str}[/dim]", highlight=False)

        try:
            with self.console.status("[cyan]Loading...", spinner="dots"):
                stream = self.client.chat.completions.create(**api_params)
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
                        # tool_calls 처리
                        if hasattr(delta, 'tool_calls') and delta.tool_calls:
                            tool_call_buffer.add_delta(delta.tool_calls)
                        # content 처리
                        content = getattr(delta, "reasoning", "") or getattr(delta, "content", "")
                        if content:
                            self.full_reply += content
                            self.console.print(content, end="", markup=False, highlight=False)
            except KeyboardInterrupt: self.console.print("\n[yellow]⚠️ 응답 중단.[/yellow]", highlight=False)
            except StopIteration: pass
            finally: self.console.print()
            return self.full_reply, usage_info, tool_call_buffer.get_tool_calls()

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

                # tool_calls 처리 (Pretty Print 모드)
                if delta and hasattr(delta, 'tool_calls') and delta.tool_calls:
                    tool_call_buffer.add_delta(delta.tool_calls)
                    # tool_calls가 있으면 content 없이 루프 계속
                    if not (delta.content or (hasattr(delta, 'reasoning') and delta.reasoning)):
                        continue

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

        # tool_calls 결과 표시
        tool_calls = tool_call_buffer.get_tool_calls()
        if tool_calls:
            self.console.print(f"\n[cyan]🔧 Tool 호출 {len(tool_calls)}개 감지됨[/cyan]", highlight=False)
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "unknown")
                args_preview = tc.get("function", {}).get("arguments", "")[:50]
                self.console.print(f"  [dim]• {name}({args_preview}...)[/dim]", highlight=False)

        return self.full_reply, usage_info, tool_calls

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
