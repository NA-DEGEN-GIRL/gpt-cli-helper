# src/gptcli/tools/permission.py
"""
Tool 실행 권한 및 신뢰 수준 관리.

TrustLevel:
- FULL: 모든 Tool 자동 실행 (기본값)
- READ_ONLY: Read/Grep/Glob만 자동 허용
- NONE: 모든 Tool 실행 전 사용자 확인 필요

위험 명령 패턴 (rm -rf, mkfs 등)은 신뢰 수준과 관계없이 항상 확인을 요청합니다.
"""
from __future__ import annotations

import difflib
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.columns import Columns


class TrustLevel(Enum):
    """Tool 실행 신뢰 수준."""
    FULL = "full"           # 모든 Tool 자동 실행
    READ_ONLY = "read_only" # 읽기 전용 Tool만 자동
    NONE = "none"           # 항상 확인


# 읽기 전용 Tool 목록
READ_ONLY_TOOLS: Set[str] = {"Read", "Grep", "Glob"}

# 쓰기 Tool 목록
WRITE_TOOLS: Set[str] = {"Write", "Edit", "Bash"}

# 위험한 명령 패턴 (Bash Tool에서 항상 확인)
DANGEROUS_PATTERNS: List[str] = [
    r"\brm\s+(-[rf]+\s+)*(/|~|\.\.|/etc|/usr|/var|/home|\*)",  # rm -rf /
    r"\bmkfs\b",                         # 파일시스템 포맷
    r"\bdd\s+if=.*of=/dev/",             # 디스크 덮어쓰기
    r">\s*/dev/sd[a-z]",                 # 디스크 직접 쓰기
    r"\bchmod\s+(-R\s+)?777\s+/",        # 전체 권한 변경
    r"\bchown\s+(-R\s+)?.*\s+/",         # 전체 소유자 변경
    r":\(\)\s*\{\s*:\|:&\s*\};\s*:",     # fork bomb
    r"\bsudo\s+rm\b",                    # sudo rm
    r"\bsudo\s+dd\b",                    # sudo dd
    r">\s*/etc/passwd",                  # passwd 덮어쓰기
    r">\s*/etc/shadow",                  # shadow 덮어쓰기
    r"\bgit\s+push\s+.*--force",         # force push
    r"\bgit\s+reset\s+--hard\s+HEAD~",   # 위험한 git reset
]


class PermissionManager:
    """
    Tool 실행 권한 관리자.

    사용자의 신뢰 수준에 따라 Tool 실행 허용 여부를 결정하고,
    위험한 명령에 대해서는 항상 사용자 확인을 요청합니다.
    """

    def __init__(self, console: Console, trust_level: TrustLevel = TrustLevel.FULL):
        self.console = console
        self.trust_level = trust_level
        self._compiled_dangerous = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]

    def set_trust_level(self, level: TrustLevel) -> None:
        """신뢰 수준을 변경합니다."""
        self.trust_level = level
        self.console.print(
            f"[green]신뢰 수준 변경: {level.value}[/green]",
            highlight=False
        )

    def is_dangerous_command(self, command: str) -> bool:
        """명령이 위험한 패턴에 해당하는지 확인합니다."""
        for pattern in self._compiled_dangerous:
            if pattern.search(command):
                return True
        return False

    def check_permission(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        auto_confirm: bool = False
    ) -> bool:
        """
        Tool 실행 권한을 확인합니다.

        Args:
            tool_name: 실행할 Tool 이름
            arguments: Tool 인자
            auto_confirm: 자동 확인 모드 (테스트용)

        Returns:
            실행 허용 여부 (True/False)
        """
        # Bash 명령의 위험 패턴 검사 (신뢰 수준과 무관하게)
        if tool_name == "Bash":
            command = arguments.get("command", "")
            if self.is_dangerous_command(command):
                return self._prompt_dangerous_confirm(tool_name, command, auto_confirm)

        # 신뢰 수준에 따른 자동 허용
        if self.trust_level == TrustLevel.FULL:
            return True

        if self.trust_level == TrustLevel.READ_ONLY:
            if tool_name in READ_ONLY_TOOLS:
                return True
            return self._prompt_confirm(tool_name, arguments, auto_confirm)

        # TrustLevel.NONE: 항상 확인
        return self._prompt_confirm(tool_name, arguments, auto_confirm)

    def _prompt_confirm(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        auto_confirm: bool
    ) -> bool:
        """일반 Tool 실행 확인 프롬프트."""
        if auto_confirm:
            return True

        self.console.print(f"\n[yellow]⚠ Tool 실행 요청: {tool_name}[/yellow]", highlight=False)

        # Tool별 상세 표시
        if tool_name == "Edit":
            self._display_edit_confirm(arguments)
        elif tool_name == "Write":
            self._display_write_confirm(arguments)
        else:
            # 기타 Tool은 기존 방식
            for key, value in arguments.items():
                display_value = str(value)
                if len(display_value) > 100:
                    display_value = display_value[:100] + "..."
                self.console.print(f"  [dim]{key}:[/dim] {display_value}", highlight=False)

        try:
            response = input("\n실행하시겠습니까? [Y/n]: ").strip().lower()
            return response in ("", "y", "yes", "ㅛ", "ㅇ")
        except (EOFError, KeyboardInterrupt):
            return False

    def _display_edit_confirm(self, arguments: Dict[str, Any]) -> None:
        """Edit Tool 확인 시 변경 내용을 unified diff 형식으로 표시."""
        file_path = arguments.get("file_path", "")
        old_str = arguments.get("old_string", "")
        new_str = arguments.get("new_string", "")

        # 라인 수 변화 계산
        old_lines = old_str.count("\n") + 1 if old_str else 0
        new_lines = new_str.count("\n") + 1 if new_str else 0
        diff_count = new_lines - old_lines
        diff_str = f"+{diff_count}" if diff_count > 0 else str(diff_count) if diff_count < 0 else "±0"

        # 실제 파일에서 old_string의 시작 줄 번호 찾기
        start_line = self._find_line_number(file_path, old_str)

        # 헤더 정보
        line_info = f"L{start_line}" if start_line > 0 else ""
        self.console.print(
            f"  📄 [bold]{file_path}[/bold] [cyan]{line_info}[/cyan]  [dim]│[/dim]  "
            f"[red]-{old_lines}줄[/red] [green]+{new_lines}줄[/green] [yellow]({diff_str})[/yellow]",
            highlight=False
        )

        # unified diff 생성
        old_lines_list = old_str.splitlines(keepends=True)
        new_lines_list = new_str.splitlines(keepends=True)

        # 마지막 줄에 개행이 없으면 추가 (diff 표시 일관성)
        if old_lines_list and not old_lines_list[-1].endswith('\n'):
            old_lines_list[-1] += '\n'
        if new_lines_list and not new_lines_list[-1].endswith('\n'):
            new_lines_list[-1] += '\n'

        diff_lines = list(difflib.unified_diff(
            old_lines_list,
            new_lines_list,
            fromfile=f"a/{Path(file_path).name}",
            tofile=f"b/{Path(file_path).name}",
            lineterm=""
        ))

        # diff 결과를 Rich Text로 렌더링
        diff_text = self._render_diff_text(diff_lines, max_lines=40)

        panel = Panel(
            diff_text,
            title="[bold yellow]📝 변경 내용 (Diff)[/bold yellow]",
            border_style="yellow",
            padding=(0, 1)
        )
        self.console.print(panel)

    def _render_diff_text(self, diff_lines: List[str], max_lines: int = 40) -> Text:
        """
        Diff 라인들을 Rich Text로 변환합니다.

        - '---', '+++' 헤더: 파일명 스타일
        - '@@' 헝크 헤더: cyan
        - '-' 삭제 라인: 빨간 배경
        - '+' 추가 라인: 초록 배경
        - ' ' 컨텍스트 라인: 기본색
        """
        result = Text()
        line_count = 0
        total_lines = len(diff_lines)

        for i, line in enumerate(diff_lines):
            if line_count >= max_lines and i < total_lines - 3:
                # 생략 표시 후 마지막 3줄은 보여줌
                omitted = total_lines - i - 3
                if omitted > 0:
                    result.append(f"\n    ... ⋮ {omitted}줄 생략 ⋮ ...\n", style="dim italic")
                    # 마지막 3줄로 점프
                    for last_line in diff_lines[-3:]:
                        self._append_diff_line(result, last_line)
                    break

            self._append_diff_line(result, line)
            line_count += 1

        return result

    def _append_diff_line(self, text: Text, line: str) -> None:
        """개별 diff 라인을 Text 객체에 추가합니다."""
        # 줄 끝 개행 제거 후 처리, 마지막에 개행 추가
        line = line.rstrip('\n')

        if line.startswith('---'):
            text.append(line + "\n", style="bold red")
        elif line.startswith('+++'):
            text.append(line + "\n", style="bold green")
        elif line.startswith('@@'):
            text.append(line + "\n", style="bold cyan")
        elif line.startswith('-'):
            # 삭제 라인: 빨간 배경
            text.append(line + "\n", style="white on #5f0000")
        elif line.startswith('+'):
            # 추가 라인: 초록 배경
            text.append(line + "\n", style="white on #005f00")
        else:
            # 컨텍스트 라인 (공백으로 시작)
            text.append(line + "\n", style="dim")

    def _find_line_number(self, file_path: str, search_str: str) -> int:
        """파일에서 문자열의 시작 줄 번호를 찾습니다."""
        if not file_path or not search_str:
            return 0
        try:
            path = Path(file_path)
            if not path.exists():
                return 0
            content = path.read_text(encoding="utf-8", errors="replace")
            idx = content.find(search_str)
            if idx == -1:
                return 0
            # idx 위치까지의 줄바꿈 개수 + 1 = 줄 번호
            return content[:idx].count("\n") + 1
        except Exception:
            return 0

    def _display_write_confirm(self, arguments: Dict[str, Any]) -> None:
        """Write Tool 확인 시 작성 내용 표시 (Rich Panel + Syntax)."""
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")

        # 파일 확장자로 언어 추론
        lang = self._guess_language(file_path)

        lines = content.count("\n") + 1 if content else 0
        chars = len(content)

        # 헤더
        self.console.print(
            f"  📄 [bold]{file_path}[/bold]  [dim]│[/dim]  "
            f"[cyan]{lines}줄[/cyan], [dim]{chars}자[/dim]",
            highlight=False
        )

        # 내용 미리보기 (Panel + Syntax)
        display_content = content if lines <= 30 else self._smart_truncate(content, 30)
        syntax = Syntax(display_content, lang, theme="monokai", line_numbers=True)
        panel = Panel(
            syntax,
            title="[bold blue]작성할 내용[/bold blue]",
            border_style="blue",
            padding=(0, 1)
        )
        self.console.print(panel)

    def _guess_language(self, file_path: str) -> str:
        """파일 경로에서 언어를 추론합니다."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "tsx", ".jsx": "jsx", ".java": "java",
            ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
            ".go": "go", ".rs": "rust", ".rb": "ruby",
            ".php": "php", ".sh": "bash", ".bash": "bash",
            ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".html": "html", ".css": "css", ".scss": "scss",
            ".sql": "sql", ".md": "markdown", ".xml": "xml",
        }
        if file_path:
            ext = Path(file_path).suffix.lower()
            return ext_map.get(ext, "text")
        return "text"

    def _smart_truncate(self, text: str, max_lines: int = 25) -> str:
        """코드를 스마트하게 자릅니다 (앞 15줄 + ... + 뒤 8줄)."""
        if not text:
            return "(empty)"

        lines = text.split("\n")
        total = len(lines)

        if total <= max_lines:
            return text

        # 앞부분을 더 많이 보여줌 (보통 중요한 변경이 앞에 있음)
        head_lines = max_lines - 8
        tail_lines = 5
        omitted = total - head_lines - tail_lines

        result = lines[:head_lines]
        result.append(f"")
        result.append(f"    ... ⋮ {omitted}줄 생략 ⋮ ...")
        result.append(f"")
        result.extend(lines[-tail_lines:])

        return "\n".join(result)

    def _prompt_dangerous_confirm(
        self,
        tool_name: str,
        command: str,
        auto_confirm: bool
    ) -> bool:
        """위험 명령 확인 프롬프트 (더 강조된 경고)."""
        if auto_confirm:
            return False  # 위험 명령은 자동 확인 모드에서도 거부

        self.console.print(
            f"\n[bold red]🚨 위험한 명령 감지![/bold red]",
            highlight=False
        )
        self.console.print(f"[red]명령: {command}[/red]", highlight=False)
        self.console.print(
            "[yellow]이 명령은 시스템에 심각한 영향을 줄 수 있습니다.[/yellow]",
            highlight=False
        )

        try:
            response = input("\n정말 실행하시겠습니까? 'yes'를 입력하세요: ").strip().lower()
            return response == "yes"
        except (EOFError, KeyboardInterrupt):
            return False

    def get_status_string(self) -> str:
        """현재 신뢰 수준 상태 문자열을 반환합니다."""
        level_emoji = {
            TrustLevel.FULL: "🟢",
            TrustLevel.READ_ONLY: "🟡",
            TrustLevel.NONE: "🔴",
        }
        level_desc = {
            TrustLevel.FULL: "전체 허용",
            TrustLevel.READ_ONLY: "읽기만 허용",
            TrustLevel.NONE: "항상 확인",
        }
        emoji = level_emoji.get(self.trust_level, "⚪")
        desc = level_desc.get(self.trust_level, "알 수 없음")
        return f"{emoji} Trust: {desc} ({self.trust_level.value})"
