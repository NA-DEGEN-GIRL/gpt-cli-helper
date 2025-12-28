# src/gptcli/tools/registry.py
"""
Tool 스키마와 실행기의 매핑 레지스트리.

이 모듈은 스키마 정의와 실행기를 연결하여,
Tool 호출 시 적절한 실행기를 찾아 실행할 수 있도록 합니다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .schemas import TOOL_SCHEMAS, get_tool_names
from .executor import ToolExecutor
from .permission import PermissionManager, TrustLevel


class ToolRegistry:
    """
    Tool 레지스트리.

    스키마, 실행기, 권한 관리자를 통합하여
    Tool 호출의 전체 흐름을 관리합니다.
    """

    def __init__(
        self,
        base_dir: Path,
        console: Console,
        trust_level: TrustLevel = TrustLevel.FULL
    ):
        """
        Args:
            base_dir: 프로젝트 기본 디렉터리
            console: Rich Console 인스턴스
            trust_level: 초기 신뢰 수준
        """
        self.base_dir = base_dir
        self.console = console
        self.executor = ToolExecutor(base_dir, console)
        self.permission = PermissionManager(console, trust_level)

    def get_schemas(self) -> List[Dict[str, Any]]:
        """API에 전달할 Tool 스키마 목록을 반환합니다."""
        return TOOL_SCHEMAS

    def get_available_tools(self) -> List[str]:
        """사용 가능한 Tool 이름 목록을 반환합니다."""
        return get_tool_names()

    def set_trust_level(self, level: TrustLevel) -> None:
        """신뢰 수준을 변경합니다."""
        self.permission.set_trust_level(level)

    def get_trust_status(self) -> str:
        """현재 신뢰 상태 문자열을 반환합니다."""
        return self.permission.get_status_string()

    def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        auto_confirm: bool = False,
        show_result: bool = True
    ) -> Tuple[str, str]:
        """
        단일 tool_call을 실행합니다.

        Args:
            tool_call: API 응답의 tool_call 객체
                {
                    "id": "call_xxx",
                    "type": "function",
                    "function": {
                        "name": "ToolName",
                        "arguments": "{\"key\": \"value\"}"  # JSON 문자열
                    }
                }
            auto_confirm: 자동 확인 모드
            show_result: 결과를 콘솔에 출력할지 여부

        Returns:
            (tool_call_id, result) 튜플
        """
        tool_call_id = tool_call.get("id", "unknown")
        function_info = tool_call.get("function", {})
        tool_name = function_info.get("name", "unknown")
        arguments_str = function_info.get("arguments", "{}")

        # JSON 인자 파싱
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            error_msg = f"오류: 인자 파싱 실패: {e}"
            if show_result:
                self.console.print(f"[red]{error_msg}[/red]", highlight=False)
            return tool_call_id, error_msg

        # 실행 중 표시
        if show_result:
            self._display_tool_execution(tool_name, arguments)

        # 권한 확인
        if not self.permission.check_permission(tool_name, arguments, auto_confirm):
            result = "오류: 사용자가 실행을 거부했습니다."
            if show_result:
                self.console.print(f"[yellow]{result}[/yellow]", highlight=False)
            return tool_call_id, result

        # 실행
        success, result = self.executor.execute(tool_name, arguments)

        # 결과 표시
        if show_result:
            self._display_tool_result(tool_name, success, result)

        return tool_call_id, result

    def execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        auto_confirm: bool = False,
        show_result: bool = True
    ) -> List[Dict[str, Any]]:
        """
        여러 tool_call을 실행하고 결과 메시지 목록을 반환합니다.

        Args:
            tool_calls: tool_call 객체 목록
            auto_confirm: 자동 확인 모드
            show_result: 결과를 콘솔에 출력할지 여부

        Returns:
            tool 결과 메시지 목록 (API에 전달할 형식)
            [{"role": "tool", "tool_call_id": "xxx", "content": "result"}, ...]

        Note:
            Gemini 모델의 경우 thought_signature가 tool_call에 포함될 수 있으며,
            이를 tool result에 함께 전달해야 합니다.
        """
        results = []

        for tool_call in tool_calls:
            tool_call_id, result = self.execute_tool_call(
                tool_call,
                auto_confirm=auto_confirm,
                show_result=show_result
            )

            tool_result_msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result
            }

            # Gemini용 thought_signature 보존
            # tool_call에 thought_signature가 있으면 tool result에도 포함
            thought_sig = tool_call.get("thought_signature")
            if thought_sig:
                tool_result_msg["thought_signature"] = thought_sig

            results.append(tool_result_msg)

        return results

    def _display_tool_execution(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Tool 실행 시작을 표시합니다. (간략 버전 - 상세는 permission에서)"""
        # Tool별 아이콘
        icons = {
            "Read": "📖",
            "Write": "📝",
            "Edit": "✏️",
            "Bash": "💻",
            "Grep": "🔍",
            "Glob": "📂"
        }
        icon = icons.get(tool_name, "🔧")
        file_path = arguments.get("file_path", "")

        # 간략 헤더만 출력 (상세 미리보기는 permission 확인에서 보여줌)
        if tool_name == "Edit":
            old_str = arguments.get("old_string", "")
            new_str = arguments.get("new_string", "")
            old_lines = old_str.count("\n") + 1 if old_str else 0
            new_lines = new_str.count("\n") + 1 if new_str else 0
            diff = new_lines - old_lines
            diff_str = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "±0"
            self.console.print(
                f"\n{icon} [bold cyan]{tool_name}[/bold cyan] [dim]{file_path}[/dim] "
                f"[yellow]({old_lines}→{new_lines}줄, {diff_str})[/yellow]",
                highlight=False
            )
        elif tool_name == "Write":
            content = arguments.get("content", "")
            lines = content.count("\n") + 1 if content else 0
            self.console.print(
                f"\n{icon} [bold cyan]{tool_name}[/bold cyan] [dim]{file_path}[/dim] "
                f"[yellow]({lines}줄)[/yellow]",
                highlight=False
            )
        elif tool_name == "Bash":
            cmd = arguments.get("command", "")
            if len(cmd) > 60:
                cmd = cmd[:60] + "..."
            self.console.print(
                f"\n{icon} [bold cyan]{tool_name}[/bold cyan] [yellow]$ {cmd}[/yellow]",
                highlight=False
            )
        else:
            pattern = arguments.get("pattern", "")
            self.console.print(
                f"\n{icon} [bold cyan]{tool_name}[/bold cyan] [dim]{file_path or pattern}[/dim]",
                highlight=False
            )

    def _display_tool_result(self, tool_name: str, success: bool, result: str) -> None:
        """Tool 실행 결과를 표시합니다."""
        # 결과가 짧으면 직접 출력, 길면 패널로
        if len(result) < 500:
            style = "green" if success else "red"
            self.console.print(f"[{style}]{result}[/{style}]", highlight=False)
        else:
            title_style = "green" if success else "red"
            title = f"[{title_style}]{tool_name} 결과[/{title_style}]"
            # 긴 결과는 처음 2000자만 표시
            display_result = result[:2000] + "..." if len(result) > 2000 else result
            self.console.print(
                Panel(display_result, title=title, border_style="dim"),
                highlight=False
            )
