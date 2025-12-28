# src/gptcli/tools/executor.py
"""
개별 Tool 실행기.

각 Tool(Read, Write, Edit, Bash, Grep, Glob)의 실제 실행 로직을 구현합니다.
모든 실행기는 결과 문자열을 반환하며, 오류 발생 시 오류 메시지를 반환합니다.
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from rich.console import Console


class ToolExecutor:
    """
    Tool 실행기 클래스.

    각 Tool의 실행 로직을 메서드로 제공합니다.
    실행 결과는 (success: bool, result: str) 튜플로 반환됩니다.
    """

    # 기본 설정
    DEFAULT_READ_LIMIT: int = 2000
    DEFAULT_BASH_TIMEOUT: int = 120
    MAX_OUTPUT_LENGTH: int = 30000

    def __init__(self, base_dir: Path, console: Console):
        """
        Args:
            base_dir: 프로젝트 기본 디렉터리 (상대 경로 해석 기준)
            console: Rich Console 인스턴스
        """
        self.base_dir = base_dir
        self.console = console
        self._ripgrep_available: Optional[bool] = None

    def _resolve_path(self, file_path: str) -> Path:
        """파일 경로를 절대 경로로 해석합니다."""
        p = Path(file_path)
        if not p.is_absolute():
            p = self.base_dir / p
        return p.resolve()

    def _truncate_output(self, output: str) -> str:
        """출력이 너무 길면 자릅니다."""
        if len(output) > self.MAX_OUTPUT_LENGTH:
            return output[:self.MAX_OUTPUT_LENGTH] + f"\n\n... (truncated, total {len(output)} chars)"
        return output

    # ─────────────────────────────────────────────
    # Read Tool
    # ─────────────────────────────────────────────
    def execute_read(
        self,
        file_path: str,
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT
    ) -> Tuple[bool, str]:
        """
        파일 내용을 읽습니다 (cat -n 형식).

        Args:
            file_path: 파일 경로
            offset: 시작 라인 번호 (1부터)
            limit: 최대 읽을 라인 수

        Returns:
            (success, result) 튜플
        """
        try:
            path = self._resolve_path(file_path)

            if not path.exists():
                return False, f"오류: 파일을 찾을 수 없습니다: {path}"

            if not path.is_file():
                return False, f"오류: 파일이 아닙니다: {path}"

            # 파일 읽기
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            # offset/limit 적용
            start_idx = max(0, offset - 1)
            end_idx = start_idx + limit
            selected_lines = lines[start_idx:end_idx]

            # cat -n 형식 출력 (라인 번호 포함)
            result_lines = []
            for i, line in enumerate(selected_lines, start=offset):
                result_lines.append(f"{i:>6}\t{line}")

            result = "\n".join(result_lines)

            # 요약 정보 추가
            total_lines = len(lines)
            shown_lines = len(selected_lines)
            summary = f"\n\n[{path.name}] {shown_lines}/{total_lines} lines (offset: {offset})"

            return True, self._truncate_output(result + summary)

        except Exception as e:
            return False, f"오류: 파일 읽기 실패: {e}"

    # ─────────────────────────────────────────────
    # Write Tool
    # ─────────────────────────────────────────────
    def execute_write(
        self,
        file_path: str,
        content: str
    ) -> Tuple[bool, str]:
        """
        파일에 내용을 씁니다.

        Args:
            file_path: 파일 경로
            content: 쓸 내용

        Returns:
            (success, result) 튜플
        """
        try:
            path = self._resolve_path(file_path)

            # 부모 디렉터리 생성
            path.parent.mkdir(parents=True, exist_ok=True)

            # 기존 파일 여부 확인
            existed = path.exists()

            # 파일 쓰기
            path.write_text(content, encoding="utf-8")

            # 결과 메시지
            action = "덮어썼습니다" if existed else "생성했습니다"
            lines = len(content.splitlines())
            chars = len(content)

            return True, f"✅ 파일을 {action}: {path}\n   ({lines} lines, {chars} chars)"

        except Exception as e:
            return False, f"오류: 파일 쓰기 실패: {e}"

    # ─────────────────────────────────────────────
    # Edit Tool
    # ─────────────────────────────────────────────
    def execute_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str
    ) -> Tuple[bool, str]:
        """
        파일에서 문자열을 치환합니다.

        Args:
            file_path: 파일 경로
            old_string: 찾을 문자열 (유일해야 함)
            new_string: 대체할 문자열

        Returns:
            (success, result) 튜플
        """
        try:
            path = self._resolve_path(file_path)

            if not path.exists():
                return False, f"오류: 파일을 찾을 수 없습니다: {path}"

            content = path.read_text(encoding="utf-8")

            # 매칭 횟수 확인
            count = content.count(old_string)

            if count == 0:
                return False, f"오류: 지정한 문자열을 찾을 수 없습니다.\n검색 문자열:\n{old_string[:200]}..."

            if count > 1:
                return False, (
                    f"오류: 문자열이 {count}번 발견되었습니다. "
                    f"유일한 문자열을 지정해야 합니다.\n"
                    f"더 많은 컨텍스트를 포함하여 유일하게 만들어 주세요."
                )

            # 치환 수행
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")

            # 변경 통계
            old_lines = old_string.count("\n") + 1
            new_lines = new_string.count("\n") + 1
            diff = new_lines - old_lines

            diff_str = f"+{diff}" if diff > 0 else str(diff)

            return True, f"✅ 파일 수정 완료: {path}\n   ({old_lines} → {new_lines} lines, {diff_str})"

        except Exception as e:
            return False, f"오류: 파일 편집 실패: {e}"

    # ─────────────────────────────────────────────
    # Bash Tool
    # ─────────────────────────────────────────────
    def execute_bash(
        self,
        command: str,
        description: Optional[str] = None,
        timeout: int = DEFAULT_BASH_TIMEOUT
    ) -> Tuple[bool, str]:
        """
        셸 명령을 실행합니다.

        Args:
            command: 실행할 명령
            description: 명령 설명 (로깅용)
            timeout: 타임아웃 (초)

        Returns:
            (success, result) 튜플
        """
        try:
            # 명령 실행
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.base_dir)
            )

            # 출력 결합
            output_parts = []

            if result.stdout:
                output_parts.append(result.stdout)

            if result.stderr:
                output_parts.append(f"[stderr]\n{result.stderr}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            # 종료 코드 추가
            if result.returncode != 0:
                output += f"\n\n[exit code: {result.returncode}]"
                return False, self._truncate_output(output)

            return True, self._truncate_output(output)

        except subprocess.TimeoutExpired:
            return False, f"오류: 명령 시간 초과 ({timeout}초)"

        except Exception as e:
            return False, f"오류: 명령 실행 실패: {e}"

    # ─────────────────────────────────────────────
    # Grep Tool
    # ─────────────────────────────────────────────
    def _check_ripgrep(self) -> bool:
        """ripgrep 사용 가능 여부를 확인합니다."""
        if self._ripgrep_available is None:
            self._ripgrep_available = shutil.which("rg") is not None
        return self._ripgrep_available

    def execute_grep(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        output_mode: str = "files_with_matches",
        case_insensitive: bool = False
    ) -> Tuple[bool, str]:
        """
        패턴 검색을 수행합니다.

        Args:
            pattern: 정규식 패턴
            path: 검색 경로 (기본: 현재 디렉터리)
            glob: 파일 필터 glob
            output_mode: 출력 모드 (files_with_matches, content, count)
            case_insensitive: 대소문자 무시

        Returns:
            (success, result) 튜플
        """
        try:
            search_path = self._resolve_path(path) if path else self.base_dir

            if self._check_ripgrep():
                return self._grep_with_ripgrep(
                    pattern, search_path, glob, output_mode, case_insensitive
                )
            else:
                return self._grep_with_grep(
                    pattern, search_path, glob, output_mode, case_insensitive
                )

        except Exception as e:
            return False, f"오류: 검색 실패: {e}"

    def _grep_with_ripgrep(
        self,
        pattern: str,
        search_path: Path,
        glob: Optional[str],
        output_mode: str,
        case_insensitive: bool
    ) -> Tuple[bool, str]:
        """ripgrep으로 검색합니다."""
        cmd = ["rg"]

        # 출력 모드
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:  # content
            cmd.extend(["-n", "--color=never"])

        # 대소문자 무시
        if case_insensitive:
            cmd.append("-i")

        # glob 필터
        if glob:
            cmd.extend(["--glob", glob])

        cmd.append(pattern)
        cmd.append(str(search_path))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(self.base_dir)
        )

        output = result.stdout if result.stdout else "(no matches)"

        if result.returncode == 1:  # no matches
            return True, "(no matches)"

        if result.returncode != 0:
            return False, f"오류: {result.stderr}"

        return True, self._truncate_output(output)

    def _grep_with_grep(
        self,
        pattern: str,
        search_path: Path,
        glob: Optional[str],
        output_mode: str,
        case_insensitive: bool
    ) -> Tuple[bool, str]:
        """grep으로 검색합니다 (ripgrep 폴백)."""
        cmd = ["grep", "-r"]

        # 출력 모드
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:  # content
            cmd.append("-n")

        # 대소문자 무시
        if case_insensitive:
            cmd.append("-i")

        # glob 필터 (--include)
        if glob:
            cmd.extend(["--include", glob])

        cmd.append(pattern)
        cmd.append(str(search_path))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(self.base_dir)
        )

        output = result.stdout if result.stdout else "(no matches)"

        if result.returncode == 1:  # no matches
            return True, "(no matches)"

        if result.returncode != 0:
            return False, f"오류: {result.stderr}"

        return True, self._truncate_output(output)

    # ─────────────────────────────────────────────
    # Glob Tool
    # ─────────────────────────────────────────────
    def execute_glob(
        self,
        pattern: str,
        path: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        glob 패턴으로 파일을 검색합니다.

        Args:
            pattern: glob 패턴
            path: 검색 시작 디렉터리

        Returns:
            (success, result) 튜플
        """
        try:
            search_path = self._resolve_path(path) if path else self.base_dir

            if not search_path.exists():
                return False, f"오류: 디렉터리를 찾을 수 없습니다: {search_path}"

            # glob 검색
            matches = list(search_path.glob(pattern))

            if not matches:
                return True, "(no matches)"

            # 수정 시간 기준 정렬 (최신 순)
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

            # 상대 경로로 변환
            result_lines = []
            for p in matches[:500]:  # 최대 500개
                try:
                    rel_path = p.relative_to(self.base_dir)
                    result_lines.append(str(rel_path))
                except ValueError:
                    result_lines.append(str(p))

            result = "\n".join(result_lines)

            # 요약
            total = len(matches)
            shown = min(total, 500)
            summary = f"\n\n[{shown}/{total} files]"

            return True, self._truncate_output(result + summary)

        except Exception as e:
            return False, f"오류: glob 검색 실패: {e}"

    # ─────────────────────────────────────────────
    # 통합 실행 인터페이스
    # ─────────────────────────────────────────────
    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Tool 이름과 인자를 받아 적절한 실행기를 호출합니다.

        Args:
            tool_name: Tool 이름 (Read, Write, Edit, Bash, Grep, Glob)
            arguments: Tool 인자 딕셔너리

        Returns:
            (success, result) 튜플
        """
        executors = {
            "Read": lambda: self.execute_read(
                file_path=arguments.get("file_path", ""),
                offset=arguments.get("offset", 1),
                limit=arguments.get("limit", self.DEFAULT_READ_LIMIT)
            ),
            "Write": lambda: self.execute_write(
                file_path=arguments.get("file_path", ""),
                content=arguments.get("content", "")
            ),
            "Edit": lambda: self.execute_edit(
                file_path=arguments.get("file_path", ""),
                old_string=arguments.get("old_string", ""),
                new_string=arguments.get("new_string", "")
            ),
            "Bash": lambda: self.execute_bash(
                command=arguments.get("command", ""),
                description=arguments.get("description"),
                timeout=arguments.get("timeout", self.DEFAULT_BASH_TIMEOUT)
            ),
            "Grep": lambda: self.execute_grep(
                pattern=arguments.get("pattern", ""),
                path=arguments.get("path"),
                glob=arguments.get("glob"),
                output_mode=arguments.get("output_mode", "files_with_matches"),
                case_insensitive=arguments.get("case_insensitive", False)
            ),
            "Glob": lambda: self.execute_glob(
                pattern=arguments.get("pattern", ""),
                path=arguments.get("path")
            )
        }

        executor = executors.get(tool_name)

        if executor is None:
            return False, f"오류: 알 수 없는 Tool: {tool_name}"

        return executor()
