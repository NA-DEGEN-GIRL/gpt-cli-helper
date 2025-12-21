# src/gptcli/core/commands.py
from __future__ import annotations
import shlex
from typing import Callable, Dict, List, Optional, Protocol

class Command(Protocol):
    """
    개별 명령 객체 인터페이스.
    - name: '/<name>'에서 <name>에 해당.
    - run(args): 인자 리스트를 받아 실행. True를 반환하면 메인 루프 종료 신호.
    """
    name: str
    def run(self, args: List[str]) -> Optional[bool]: ...

class SimpleCallbackCommand:
    """
    핸들러 메서드(Callable[[List[str]], Optional[bool]])를 감싸는 최소 구현 Command.
    기존 CommandHandler.* 메서드를 손쉽게 라우터에 등록할 때 사용합니다.
    """
    def __init__(self, name: str, callback: Callable[[List[str]], Optional[bool]]):
        self.name = name
        self._cb = callback

    def run(self, args: List[str]) -> Optional[bool]:
        return self._cb(args)

class CommandRouter:
    """
    '/명령 인자...' 라인을 파싱해 등록된 Command로 위임하는 라우터.
    - 접두사(prefix)는 기본 '/'.
    - 인자 파싱은 shlex.split로 안전하게 처리(공백/따옴표 포함 경로 등).
    """
    def __init__(self, printer: Callable[..., None], prefix: str = "/") -> None:
        self._print = printer
        self._cmds: Dict[str, Command] = {}
        self._prefix = prefix

    # ── 등록/조회 ─────────────────────────────────────────────

    def register(self, cmd: Command) -> None:
        """단일 명령을 등록합니다."""
        self._cmds[cmd.name] = cmd

    def register_many(self, commands: List[Command]) -> None:
        """여러 명령을 한꺼번에 등록합니다."""
        for c in commands:
            self.register(c)

    def get(self, name: str) -> Optional[Command]:
        """명령 이름으로 Command를 조회합니다."""
        return self._cmds.get(name)

    def exists(self, name: str) -> bool:
        """해당 이름의 명령이 등록되어 있는지 확인합니다."""
        return name in self._cmds

    def names(self) -> List[str]:
        """등록된 명령 이름들을 정렬하여 반환합니다."""
        return sorted(self._cmds.keys())

    def help_text(self) -> str:
        """등록된 명령어를 한 줄 문자열로 요약해 반환합니다."""
        if not self._cmds:
            return "(no commands registered)"
        return " ".join(f"/{n}" for n in self.names())

    # ── 내부 파서/디스패처 ─────────────────────────────────────

    def _parse(self, line: str) -> Optional[tuple[str, List[str]]]:
        """
        내부 파서: 접두사를 제거한 뒤 shlex로 토큰화합니다.
        반환: (명령어 이름, 인자 리스트) 또는 None(파싱 불가/비명령).
        """
        if not line:
            return None
        s = line.strip()
        if not s.startswith(self._prefix):
            return None

        payload = s[len(self._prefix):]
        try:
            tokens = shlex.split(payload)
        except ValueError:
            # 따옴표 불일치 등 파싱 오류 시, 안전 폴백
            tokens = payload.split()

        if not tokens:
            return None
        name, args = tokens[0], tokens[1:]
        return name, args

    def dispatch(self, line: str) -> bool:
        """
        명령 실행:
        - 라인이 접두사로 시작하지 않으면 False(일반 질문 처리).
        - 알 수 없는 명령이면 경고 후 False.
        - 실행 결과가 True면 메인 루프 종료 신호로 간주합니다.
        """
        parsed = self._parse(line)
        if not parsed:
            return False

        name, args = parsed
        cmd = self._cmds.get(name)
        if not cmd:
            self._print("[yellow]알 수 없는 명령어입니다. '/commands'를 참고하세요.[/yellow]", highlight=False)
            return False

        try:
            ret = cmd.run(args)
            return bool(ret)
        except Exception as e:
            # 라우팅 경로에서 예외를 삼켜 UI를 살리고, 구체 내용은 상위에서 로깅/알림
            self._print(f"[red]명령 실행 중 오류: {e}[/red]", highlight=False)
            return False