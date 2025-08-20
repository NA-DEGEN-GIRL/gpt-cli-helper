# src/gptcli/services/sessions.py
from __future__ import annotations
import json, time, shutil, base64, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from src.gptcli.services.config import ConfigManager
from src.gptcli.utils.common import Utils

class SessionService:
    """
    세션 스냅샷/복원/삭제 + 코드 스냅샷 관리 전담 서비스.
    CommandHandler에서 파일 I/O 책임을 분리하여 앱 결합을 낮춘다.
    """
    def __init__(self, config: ConfigManager, console: Console) -> None:
        self.config = config
        self.console = console

    def get_backup_json_path(self, session_name: str) -> Path:
        """세션 스냅샷 JSON 경로를 반환(읽기 전용 용도)."""
        return self._single_backup_json(session_name)

    def has_backup(self, session_name: str) -> bool:
        """해당 세션의 스냅샷 JSON이 존재하는지 여부."""
        return self._single_backup_json(session_name).exists()
    
    # 내부 경로 유틸 ------------------------------
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

    @staticmethod
    def _slug(session_name: str) -> str:
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

    # 라이브/코드 정리 ------------------------------
    def delete_live_session_file(self, session_name: str) -> bool:
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
            self.console.print(f"[yellow]세션 파일 삭제 실패({path.name}): {e}[/yellow]", highlight=False)
        return False

    def remove_session_code_files(self, session_name: str) -> int:
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
        removed = self.remove_session_code_files(session_name)
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

    # 스냅샷/복원/삭제 ------------------------------
    def snapshot_single(self, session_name: str, app_state: Dict[str, Any], reason: str = "manual") -> bool:
        """
        현재 세션 상태를 '단일 스냅샷'으로 저장(덮어쓰기).
        app_state: {"messages","model","model_context","usage_history","mode","current_session_name"}
        """
        try:
            # 현재 세션이면 먼저 라이브 저장(플러시)
            if app_state.get("current_session_name") == session_name:
                self.config.save_session(
                    session_name,
                    app_state.get("messages", []),
                    app_state.get("model", ""),
                    app_state.get("model_context", 0),
                    app_state.get("usage_history", []),
                    mode=app_state.get("mode", "dev"),
                )

            # 라이브 JSON을 읽어 메타 추가 후 백업 JSON에 기록
            data = self.config.load_session(session_name)
            data = dict(data)
            data.setdefault("name", session_name)
            data["backup_meta"] = {
                "session": session_name,
                "backup_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "message_count": len(data.get("messages", [])),
                "model": data.get("model", app_state.get("model", "")),
            }

            bj = self._single_backup_json(session_name)
            bj.parent.mkdir(parents=True, exist_ok=True)
            Utils._save_json(bj, data)

            code_cnt = self._copy_code_snapshot_single(session_name)
            self.console.print(
                f"[green]스냅샷 저장:[/green] session='{session_name}' (codes:{code_cnt}) → {bj}",
                highlight=False
            )
            return True
        except Exception as e:
            self.console.print(f"[yellow]스냅샷 실패(session={session_name}): {e}[/yellow]", highlight=False)
            return False

    def restore_single(self, session_name: str) -> Optional[Dict[str, Any]]:
        """
        단일 스냅샷에서 복원.
        - 백업 JSON을 라이브 JSON으로 쓰고
        - 코드 스냅샷 디렉터리의 파일을 작업 디렉터리로 복원.
        성공 시 복원된 세션 데이터(dict) 반환.
        """
        bj = self._single_backup_json(session_name)
        if not bj.exists():
            self.console.print(f"[yellow]스냅샷을 찾을 수 없습니다: {bj}[/yellow]", highlight=False)
            return None

        try:
            data = Utils._load_json(bj, {})
            msgs = data.get("messages", [])
            model = data.get("model", "")
            ctx = data.get("context_length", 0)
            usage = data.get("usage_history", [])
            mode = data.get("mode")

            # 라이브 JSON 쓰기
            self.config.save_session(session_name, msgs, model, ctx, usage, mode=mode)
            # 코드 복원
            removed, copied = self._restore_code_snapshot_single(session_name)
            self.console.print(
                f"[green]복원 완료:[/green] session='{session_name}' (codes: -{removed} +{copied})",
                highlight=False
            )
            return data
        except Exception as e:
            self.console.print(f"[red]복원 실패(session={session_name}): {e}[/red]", highlight=False)
            return None

    def delete_single_snapshot(self, session_name: str) -> Tuple[bool, int]:
        """
        단일 스냅샷(JSON+코드 백업)을 삭제.
        반환: (json_deleted, code_files_removed)
        """
        json_deleted = False
        code_removed = 0

        bj = self._single_backup_json(session_name)
        try:
            if bj.exists():
                bj.unlink()
                json_deleted = True
                self.console.print(f"[dim]스냅샷 JSON 삭제: {bj.relative_to(self.config.BASE_DIR)}[/dim]", highlight=False)
        except Exception as e:
            self.console.print(f"[yellow]스냅샷 JSON 삭제 실패({bj.name}): {e}[/yellow]", highlight=False)

        code_snap_dir = self._code_single_backup_dir(session_name)
        if code_snap_dir.exists() and code_snap_dir.is_dir():
            try:
                code_removed = sum(1 for _ in code_snap_dir.glob("**/*") if _.is_file())
                shutil.rmtree(code_snap_dir, ignore_errors=True)
                self.console.print(
                    f"[dim]코드 스냅샷 삭제: {code_snap_dir.relative_to(self.config.BASE_DIR)} ({code_removed}개)[/dim]",
                    highlight=False
                )
            except Exception as e:
                self.console.print(f"[yellow]코드 스냅샷 삭제 실패({code_snap_dir.name}): {e}[/yellow]", highlight=False)

        return json_deleted, code_removed