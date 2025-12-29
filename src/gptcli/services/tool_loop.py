# src/gptcli/services/tool_loop.py
"""
Tool 실행 루프 오케스트레이터.

AI 응답에 tool_calls가 포함된 경우, 각 Tool을 실행하고
그 결과를 다시 AI에 전달하는 루프를 관리합니다.

흐름:
1. 사용자 입력 → AI 호출 (tools 포함)
2. AI 응답에 tool_calls 있음?
   - No → 응답 출력 및 종료
   - Yes → 각 tool 실행 → 결과를 messages에 추가 → 2번으로 반복
3. 최대 반복 횟수 도달 시 경고 후 종료
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from src.gptcli.tools.registry import ToolRegistry
from src.gptcli.tools.permission import TrustLevel
from src.gptcli.tools.schemas import TOOL_SCHEMAS
from src.gptcli.services.ai_stream import AIStreamParser
from src.gptcli.models.capabilities import supports_tools, get_supported_parameters


class ToolLoopService:
    """
    Tool 실행 루프를 관리하는 서비스.

    AI가 tool_calls를 반환하면, 각 Tool을 실행하고 결과를 다시
    AI에 전달하는 루프를 수행합니다.
    """

    # 최대 루프 반복 횟수 (무한 루프 방지)
    MAX_ITERATIONS: int = 50

    def __init__(
        self,
        base_dir: Path,
        console: Console,
        parser: AIStreamParser,
        trust_level: TrustLevel = TrustLevel.FULL
    ):
        """
        Args:
            base_dir: 프로젝트 기본 디렉터리
            console: Rich Console 인스턴스
            parser: AIStreamParser 인스턴스
            trust_level: 초기 신뢰 수준
        """
        self.base_dir = base_dir
        self.console = console
        self.parser = parser
        self.registry = ToolRegistry(base_dir, console, trust_level)

        # Tool 모드 활성화 여부
        self.enabled: bool = True
        # Tool 강제 모드 (tool_choice: "required")
        self.force_mode: bool = False

    def set_enabled(self, enabled: bool) -> None:
        """Tool 모드를 활성화/비활성화합니다."""
        self.enabled = enabled
        status = "활성화" if enabled else "비활성화"
        self.console.print(f"[green]Tool 모드 {status}됨[/green]", highlight=False)

    def set_force_mode(self, force: bool) -> None:
        """Tool 강제 모드를 설정합니다."""
        self.force_mode = force
        if force:
            self.console.print(
                "[yellow]🔧 Tool 강제 모드 ON[/yellow] - 모델이 항상 Tool을 사용합니다.",
                highlight=False
            )
            self.console.print(
                "[dim]주의: 일반 대화에서도 Tool을 호출하므로 부자연스러울 수 있습니다.[/dim]",
                highlight=False
            )
        else:
            self.console.print(
                "[green]🔧 Tool 강제 모드 OFF[/green] - 모델이 자동으로 Tool 사용 여부를 결정합니다.",
                highlight=False
            )

    def set_trust_level(self, level: TrustLevel) -> None:
        """신뢰 수준을 변경합니다."""
        self.registry.set_trust_level(level)

    def _get_tool_calls_signature(self, tool_calls: List[Dict[str, Any]]) -> str:
        """
        tool_calls의 시그니처를 생성합니다 (반복 감지용).

        동일한 Tool을 동일한 인자로 호출하면 같은 시그니처가 됩니다.
        이를 통해 무한 루프(같은 작업 반복)를 감지합니다.
        """
        parts = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "")
            parts.append(f"{name}:{args}")
        return "|".join(sorted(parts))

    def get_trust_status(self) -> str:
        """현재 신뢰 상태 문자열을 반환합니다."""
        return self.registry.get_trust_status()

    def get_tools_for_api(self, model: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        """
        API 호출에 전달할 tools 파라미터와 tool_choice를 반환합니다.
        Tool 모드가 비활성화되었거나 모델이 tools를 지원하지 않으면 (None, "auto")를 반환합니다.

        Args:
            model: 모델 ID (tool 지원 여부 확인용)

        Returns:
            (Tool 스키마 목록 또는 None, tool_choice 값)
        """
        if not self.enabled:
            return None, "auto"

        # 모델이 tools를 지원하는지 확인
        if not supports_tools(model):
            return None, "auto"

        # 강제 모드면 "required", 아니면 "auto"
        tool_choice = "required" if self.force_mode else "auto"
        return TOOL_SCHEMAS, tool_choice

    def check_model_tool_support(self, model: str) -> Tuple[bool, List[str]]:
        """
        모델의 Tool 지원 여부와 지원 파라미터를 확인합니다.

        Args:
            model: 모델 ID

        Returns:
            (supports_tools, supported_parameters) 튜플
        """
        has_support = supports_tools(model)
        params = get_supported_parameters(model)
        return has_support, params

    def run_with_tools(
        self,
        system_prompt: Dict[str, Any],
        messages: List[Dict[str, Any]],
        model: str,
        pretty_print: bool = True
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Tool 실행 루프를 포함한 AI 호출을 수행합니다.

        Args:
            system_prompt: 시스템 프롬프트 객체
            messages: 대화 메시지 목록 (이 목록은 수정되지 않음 - 임시 복사본 사용)
            model: 사용할 모델 이름
            pretty_print: 고급 출력 모드 여부

        Returns:
            (최종 응답 문자열, 사용량 정보) 튜플.
            실패 시 None.

        Note:
            Tool 실행 중간 메시지(tool_calls, tool results)는 세션에 저장하지 않습니다.
            오직 최종 텍스트 응답만 반환하여 저장하도록 합니다.
            이는 Anthropic API의 tool_use/tool_result 페어링 요구사항을 충족시키기 위함입니다.
        """
        # 모델 Tool 지원 여부 확인
        tools, tool_choice = self.get_tools_for_api(model)
        has_support, params = self.check_model_tool_support(model)

        # 디버그: 모델의 Tool 지원 상태 출력
        model_short = model.split("/")[-1] if "/" in model else model

        if self.enabled:
            force_indicator = " [강제]" if self.force_mode else ""
            if has_support:
                self.console.print(
                    f"[dim]🔧 모델 '{model_short}'의 Tool 지원: ✅{force_indicator}[/dim]",
                    highlight=False
                )
            else:
                self.console.print(
                    f"[dim]🔧 모델 '{model_short}'의 Tool 지원: ❌ (supported_params={params})[/dim]",
                    highlight=False
                )

        # 모델이 tools를 지원하지 않으면 경고 메시지 출력
        if self.enabled and tools is None:
            if not has_support:
                self.console.print(
                    f"\n[yellow]⚠️ '{model_short}' 모델은 Tool Calling을 지원하지 않습니다.[/yellow]",
                    highlight=False
                )
                self.console.print(
                    "[dim]Tool 없이 일반 대화 모드로 진행합니다. "
                    "Tool을 사용하려면 지원 모델로 변경하세요 "
                    "(예: anthropic/claude-opus-4, openai/gpt-4o)[/dim]",
                    highlight=False
                )

        iteration = 0
        final_response = ""
        final_usage = None

        # 원본 messages를 수정하지 않고 임시 복사본 사용
        working_messages = list(messages)

        # 반복 감지용: 이전 tool_calls 기록
        previous_tool_calls_signature = None
        repeat_count = 0
        MAX_REPEATS = 2  # 동일 작업 최대 반복 횟수

        # Tool force 모드에서 쓰기 없이 읽기만 반복하는 경우 감지
        WRITE_TOOLS = {"Write", "Edit", "Bash"}
        consecutive_read_only = 0
        MAX_READ_ONLY_ITERATIONS = 5  # 연속 5회 읽기만 하면 경고

        while iteration < self.MAX_ITERATIONS:
            iteration += 1

            # AI 호출
            result = self.parser.stream_and_parse(
                system_prompt,
                working_messages,
                model,
                pretty_print,
                tools=tools,
                tool_choice=tool_choice
            )

            if result is None:
                return None

            response_text, usage_info, tool_calls = result
            final_response = response_text
            final_usage = usage_info

            # tool_calls가 없으면 루프 종료 (최종 텍스트 응답)
            if not tool_calls:
                break

            # 반복 감지: 동일한 tool_calls 패턴인지 확인
            current_signature = self._get_tool_calls_signature(tool_calls)
            if current_signature == previous_tool_calls_signature:
                repeat_count += 1
                if repeat_count >= MAX_REPEATS:
                    self.console.print(
                        f"\n[yellow]⚠️ 동일한 Tool 호출이 {MAX_REPEATS}회 반복됨. 무한 루프 방지를 위해 종료합니다.[/yellow]",
                        highlight=False
                    )
                    # tool_choice를 none으로 설정하여 최종 응답 요청
                    self.console.print(
                        "[dim]→ 모델에 최종 응답 요청 중...[/dim]",
                        highlight=False
                    )
                    final_result = self.parser.stream_and_parse(
                        system_prompt,
                        working_messages,
                        model,
                        pretty_print,
                        tools=None  # Tool 없이 최종 응답 요청
                    )
                    if final_result:
                        final_response = final_result[0]
                        final_usage = final_result[1]
                    break
            else:
                repeat_count = 0
                previous_tool_calls_signature = current_signature

            # Tool force 모드에서 쓰기 Tool 없이 읽기만 반복하는지 체크
            if self.force_mode:
                tool_names = [tc.get("function", {}).get("name", "") for tc in tool_calls]
                has_write_tool = any(name in WRITE_TOOLS for name in tool_names)

                if has_write_tool:
                    consecutive_read_only = 0  # 쓰기 Tool 사용 시 카운터 리셋
                else:
                    consecutive_read_only += 1
                    if consecutive_read_only >= MAX_READ_ONLY_ITERATIONS:
                        self.console.print(
                            f"\n[yellow]⚠️ Tool 강제 모드에서 {MAX_READ_ONLY_ITERATIONS}회 연속 읽기만 수행됨.[/yellow]",
                            highlight=False
                        )
                        self.console.print(
                            "[dim]→ 모델이 수정을 수행하지 않고 있습니다. 최종 응답 요청 중...[/dim]",
                            highlight=False
                        )
                        # Tool 없이 최종 응답 요청
                        final_result = self.parser.stream_and_parse(
                            system_prompt,
                            working_messages,
                            model,
                            pretty_print,
                            tools=None
                        )
                        if final_result:
                            final_response = final_result[0]
                            final_usage = final_result[1]
                        break

            # 임시 메시지에 Assistant 메시지 추가 (tool_calls 포함)
            # Gemini의 경우 tool_calls에 thought_signature가 포함되어 있으며,
            # 이를 그대로 보존해야 다음 턴에서 오류가 발생하지 않음
            assistant_message = {
                "role": "assistant",
                "content": response_text if response_text else None,
                "tool_calls": tool_calls  # thought_signature는 tool_calls 내부에 이미 포함됨
            }
            working_messages.append(assistant_message)

            # 각 Tool 실행
            self.console.print(
                f"\n[bold cyan]━━━ Tool 실행 ({len(tool_calls)}개) ━━━[/bold cyan]",
                highlight=False
            )

            tool_results = self.registry.execute_tool_calls(
                tool_calls,
                auto_confirm=False,
                show_result=True
            )

            # 임시 메시지에 Tool 결과 추가
            for tool_result in tool_results:
                working_messages.append(tool_result)

            self.console.print(
                f"\n[dim]━━━ Tool 실행 완료, AI에 결과 전달 중... (반복 {iteration}/{self.MAX_ITERATIONS}) ━━━[/dim]",
                highlight=False
            )

        # 최대 반복 횟수 도달 경고
        if iteration >= self.MAX_ITERATIONS:
            self.console.print(
                f"\n[yellow]⚠️ 최대 Tool 반복 횟수({self.MAX_ITERATIONS})에 도달했습니다.[/yellow]",
                highlight=False
            )

        return final_response, final_usage

    def run_single(
        self,
        system_prompt: Dict[str, Any],
        messages: List[Dict[str, Any]],
        model: str,
        pretty_print: bool = True
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Tool 없이 단일 AI 호출을 수행합니다.

        기존의 단순 호출과 동일하지만, 반환값 형식을 맞추기 위한 래퍼입니다.

        Returns:
            (응답 문자열, 사용량 정보) 튜플.
            실패 시 None.
        """
        result = self.parser.stream_and_parse(
            system_prompt,
            messages,
            model,
            pretty_print,
            tools=None
        )

        if result is None:
            return None

        response_text, usage_info, _ = result
        return response_text, usage_info
