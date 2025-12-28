
# src/gptcli/commands/handler.py
from __future__ import annotations

# ── stdlib
import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 3rd-party
import urwid
import pyperclip
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.box import ROUNDED

# ── local
import src.constants as constants
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.sessions import SessionService
from src.gptcli.ui.file_selector import FileSelector
from src.gptcli.ui.diff_view import CodeDiffer
from src.gptcli.utils.common import Utils
from src.gptcli.models.model_searcher import ModelSearcher

class CommandHandler:
    """
    '/'로 시작하는 모든 명령어를 처리하는 전담 클래스.
    메인 애플리케이션(GPTCLI)의 인스턴스를 주입받아 그 상태에 접근하고 수정합니다.
    """
    def __init__(self, app: 'GPTCLI', config: 'ConfigManager', sessions: 'SessionService'):
        """
        Args:
            app ('GPTCLI'): 메인 애플리케이션 인스턴스.
            config ('ConfigManager'): 설정 및 파일 I/O 관리자 인스턴스.
        """
        self.app = app
        self.console = self.app.console
        self.config = config
        self.theme_manager = app.theme_manager
        self.sessions = sessions
        self.differ_ref: Dict[str, CodeDiffer | None] = {"inst": None}

    def _app_state(self) -> Dict[str, Any]:  # [추가]
        return {
            "current_session_name": getattr(self.app, "current_session_name", "default"),
            "messages": getattr(self.app, "messages", []),
            "model": getattr(self.app, "model", ""),
            "model_context": getattr(self.app, "model_context", 0),
            "usage_history": getattr(self.app, "usage_history", []),
            "mode": getattr(self.app, "mode", "dev"),
        }

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
    
    def handle_unknown(self, args: List[str]) -> None:
        self.console.print("[yellow]알 수 없는 명령어입니다. '/commands'로 전체 목록을 확인하세요.[/yellow]",highlight=False)
    
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
            return self.sessions.get_backup_json_path(name)

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
            ok = self.sessions.snapshot_single(current, self._app_state(), reason="switch_session")  # [변경]
            if ok:
                self.sessions.delete_live_session_file(current)   # [변경]
                self.sessions.remove_session_code_files(current)  # [변경]
            else:
                self.console.print(
                    "[yellow]경고: 스냅샷 실패로 live/코드 파일을 삭제하지 않았습니다.[/yellow]",
                    highlight=False
                )

        # 2) 대상 복원(스냅샷 우선)
        if self.sessions.has_backup(target):
            if self.sessions.restore_single(target):
                self._load_session_into_app(target)
                try:
                    self.config.save_current_session_name(self.app.current_session_name)
                except Exception:
                    pass
                self.console.print(f"[green]세션 전환 완료 → '{target}' (스냅샷 복원)[/green]", highlight=False)
            else:
                tpath = self.config.get_session_path(target)
                if tpath.exists():
                    self._load_session_into_app(target)
                    self.sessions.snapshot_single(target, self._app_state(), reason="migrate-live-to-snapshot")  # [변경]
                    self.console.print(
                        f"[yellow]스냅샷 손상 → live 로드 후 스냅샷 생성: '{target}'[/yellow]", highlight=False
                    )
                else:
                    self.config.save_session(target, [], self.app.default_model, self.app.default_context_length, [],
                                             mode=getattr(self.app, "mode", "dev"))
                    self._load_session_into_app(target)
                    self.console.print(
                        f"[yellow]스냅샷 손상 → 빈 세션 생성: '{target}'[/yellow]", highlight=False
                    )
        else:
            # 스냅샷 없음 → 라이브 확인
            tpath = self.config.get_session_path(target)
            if tpath.exists():
                self._load_session_into_app(target)
                self.sessions.snapshot_single(target, self._app_state(), reason="migrate-live-to-snapshot")  # [변경]
                self.console.print(
                    f"[green]세션 전환 완료 → '{target}' (live 로드·스냅샷 생성)[/green]", highlight=False
                )
            else:
                self.config.save_session(
                    target, [], self.app.default_model, self.app.default_context_length, [],
                    mode=getattr(self.app, "mode", "dev"),
                )
                self._load_session_into_app(target)
                self.console.print(f"[green]새 세션 생성 → '{target}'[/green]", highlight=False)

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
        reason = "manual"
        if args:
            reason = " ".join(args).strip() or "manual"
        sess = getattr(self.app, "current_session_name", "default")
        ok = self.sessions.snapshot_single(sess, self._app_state(), reason=reason)  # [변경]
        if ok:
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
            self.app.token_estimator.update_model(new_model)
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

        if hard:
            js_del, code_del = self.sessions.delete_single_snapshot(sess)  # [변경]
            self.console.print(
                f"[yellow]하드 리셋: 기존 스냅샷 제거(JSON:{'O' if js_del else 'X'}, code:{code_del}개)[/yellow]",
                highlight=False
            )
        elif not no_snapshot:
            ok = self.sessions.snapshot_single(sess, self._app_state(), reason="reset")  # [변경]
            if not ok:
                self.console.print(
                    "[yellow]경고: 스냅샷 실패(/restore로 되돌리기 불가할 수 있음). 초기화를 계속합니다.[/yellow]",
                    highlight=False
                )

        # 라이브 초기화
        self.app.messages = []
        self.app.usage_history = []
        self.config.save_session(
            sess,
            [],
            getattr(self.app, "model", ""),
            getattr(self.app, "model_context", 0),
            [],
            mode=getattr(self.app, "mode", "dev"),
        )
        removed_live_codes = self.sessions.remove_session_code_files(sess)     # [변경]

        mode_str = "HARD" if hard else ("NO-SNAPSHOT" if no_snapshot else "SOFT")
        self.console.print(
            f"[green]세션 '{sess}' 초기화 완료[/green] (mode: {mode_str}, codes removed: {removed_live_codes})",
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

    def handle_tools(self, args: List[str]) -> None:
        """Tool 모드를 토글합니다."""
        self.app.tool_mode_enabled = not self.app.tool_mode_enabled
        status = "[green]활성화[/green]" if self.app.tool_mode_enabled else "[yellow]비활성화[/yellow]"
        self.console.print(f"Tool 모드가 {status}되었습니다.", highlight=False)

        if self.app.tool_mode_enabled:
            self.console.print(
                "[dim]AI가 Read/Write/Edit/Bash/Grep/Glob 도구를 사용하여 "
                "파일을 읽고, 수정하고, 명령을 실행할 수 있습니다.[/dim]",
                highlight=False
            )
            # 현재 신뢰 수준 표시
            trust_status = self.app.tool_loop.get_trust_status()
            self.console.print(f"[dim]현재 {trust_status}[/dim]", highlight=False)
        else:
            self.console.print(
                "[dim]AI는 텍스트 응답만 생성합니다. 파일 수정이나 명령 실행은 불가능합니다.[/dim]",
                highlight=False
            )

    def handle_trust(self, args: List[str]) -> None:
        """Tool 신뢰 수준을 설정합니다."""
        from src.gptcli.tools.permission import TrustLevel

        valid_levels = {
            "full": TrustLevel.FULL,
            "read_only": TrustLevel.READ_ONLY,
            "none": TrustLevel.NONE,
        }

        if not args:
            # 현재 상태 표시
            current = self.app.tool_loop.get_trust_status()
            self.console.print(f"현재 {current}", highlight=False)
            self.console.print("\n[yellow]사용법: /trust <full|read_only|none>[/yellow]", highlight=False)
            self.console.print("[dim]  full      - 모든 Tool 자동 실행[/dim]", highlight=False)
            self.console.print("[dim]  read_only - Read/Grep/Glob만 자동 허용[/dim]", highlight=False)
            self.console.print("[dim]  none      - 모든 Tool 실행 전 확인[/dim]", highlight=False)
            return

        level_name = args[0].lower()

        if level_name not in valid_levels:
            self.console.print(
                f"[red]알 수 없는 신뢰 수준: {level_name}[/red]\n"
                f"[yellow]사용 가능: full, read_only, none[/yellow]",
                highlight=False
            )
            return

        self.app.tool_loop.set_trust_level(valid_levels[level_name])

    def handle_toolforce(self, args: List[str]) -> None:
        """Tool 강제 모드를 토글합니다."""
        current = self.app.tool_loop.force_mode
        self.app.tool_loop.set_force_mode(not current)

    def handle_summarize(self, args: List[str]) -> None:
        """
        수동으로 컨텍스트 요약을 실행합니다.

        사용법:
            /summarize           → 요약 시도
            /summarize --force   → 메시지 수/레벨 제한 무시하고 강제 요약
        """
        force = "--force" in args or "-f" in args

        if not self.app.messages:
            self.console.print("[yellow]요약할 대화 내용이 없습니다.[/yellow]", highlight=False)
            return

        # 현재 상태 표시
        from src.gptcli.utils.common import Utils
        system_prompt = Utils.get_system_prompt_content(self.app.mode)
        system_tokens = self.app.token_estimator.count_text_tokens(system_prompt)

        reserve = 32000 if self.app.model_context >= 200000 else (16000 if self.app.model_context >= 128000 else 4096)

        used, available, ratio = self.app.summarization_service.calculate_context_usage(
            self.app.messages,
            self.app.model_context,
            system_tokens,
            reserve,
            0
        )

        self.console.print(
            f"[cyan]현재 컨텍스트: {used:,}/{available:,} 토큰 ({ratio:.1%})[/cyan]",
            highlight=False
        )
        self.console.print(
            f"[dim]메시지 수: {len(self.app.messages)}개[/dim]",
            highlight=False
        )

        # 요약 수행
        new_messages, was_summarized = self.app.summarization_service.manual_summarize(
            self.app.messages,
            self.app.model,
            force=force
        )

        if was_summarized:
            self.app.messages = new_messages
            # 세션 저장
            self.config.save_session(
                self.app.current_session_name,
                self.app.messages,
                self.app.model,
                self.app.model_context,
                self.app.usage_history,
                mode=self.app.mode
            )
            self.console.print("[green]요약이 세션에 저장되었습니다.[/green]", highlight=False)
        else:
            self.console.print("[dim]요약이 수행되지 않았습니다.[/dim]", highlight=False)

    def handle_show_summary(self, args: List[str]) -> None:
        """
        현재 세션의 요약 정보를 표시합니다.
        """
        from rich.table import Table

        summary_info = self.app.summarization_service.get_summary_info(self.app.messages)

        if not summary_info:
            self.console.print("[yellow]현재 세션에 요약이 없습니다.[/yellow]", highlight=False)
            self.console.print("[dim]'/summarize' 명령으로 수동 요약을 실행할 수 있습니다.[/dim]", highlight=False)
            return

        # 요약 내용 표시
        self.console.print(Panel(
            summary_info["content"],
            title="[cyan]📋 현재 요약 내용[/cyan]",
            border_style="cyan"
        ), highlight=False)

        # 메타데이터 표시
        meta = summary_info.get("metadata")
        if meta:
            table = Table(title="요약 메타데이터", box=ROUNDED)
            table.add_column("항목", style="cyan")
            table.add_column("값", style="white")

            table.add_row("생성 시간", meta.get("created_at", "N/A"))
            table.add_row("요약된 메시지 수", str(meta.get("summarized_message_count", 0)))
            table.add_row("원본 토큰", f"{meta.get('summarized_token_count', 0):,}")
            table.add_row("요약 토큰", f"{meta.get('summary_token_count', 0):,}")
            table.add_row("압축률", f"{meta.get('compression_ratio', 0):.1%}")
            table.add_row("요약 모델", meta.get("model_used", "N/A").split("/")[-1])
            table.add_row("요약 레벨", str(meta.get("summary_level", 1)))

            self.console.print(table, highlight=False)

        # 요약 히스토리 표시
        history = self.app.summarization_service.summary_history
        if len(history) > 1:
            self.console.print(f"\n[dim]이 세션에서 총 {len(history)}회 요약 수행됨[/dim]", highlight=False)
