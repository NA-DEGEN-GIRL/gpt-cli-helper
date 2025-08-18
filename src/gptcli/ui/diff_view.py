# src/gptcli/ui/diff_view.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional, Set
from pathlib import Path
import urwid, difflib, threading, re
from pygments import lex as pyg_lex
from pygments.lexers import guess_lexer_for_filename, TextLexer
from rich.console import Console
from src.gptcli.services.theme import ThemeManager
from src.gptcli.services.config import ConfigManager
import src.constants as constants

class DiffListBox(urwid.ListBox):
    """마우스 이벤트를 안전하게 처리하는 diff 전용 ListBox"""
    
    def mouse_event(self, size, event, button, col, row, focus):
        """마우스 이벤트 처리"""
        # 휠 스크롤만 처리
        if button == 4:  # 휠 업
            self.keypress(size, 'up')
            return True
        elif button == 5:  # 휠 다운
            self.keypress(size, 'down')
            return True
        
        # 클릭은 무시 (header/footer 깜빡임 방지)
        return True

class CodeDiffer:
    def __init__(self, attached_files: List[str], session_name: str, messages: List[Dict],
                 theme_manager: 'ThemeManager', config: 'ConfigManager', console: 'Console'):
        # 입력 데이터
        self.attached_files = [Path(p) for p in attached_files]
        self.session_name = session_name
        self.messages = messages
        self.theme_manager = theme_manager
        self.config = config
        self.console = console

        # 상태
        self.expanded_items: Set[str] = set()
        self.selected_for_diff: List[Dict] = []
        self.previewing_item_id: Optional[str] = None
        self.preview_offset = 0
        self.preview_lines_per_page = 30

        self._visible_preview_lines: Optional[int] = None

        self._lexer_cache: Dict[str, Any] = {}

        # 표시/리스트 구성
        self.display_items: List[Dict] = []
        self.response_files: Dict[int, List[Path]] = self._scan_response_files()

        self.list_walker = urwid.SimpleFocusListWalker([])
        self.listbox = urwid.ListBox(self.list_walker)
        self.preview_text = urwid.Text("", wrap='clip')                         # flow
        self.preview_body = urwid.AttrMap(self.preview_text, {None: 'preview'})         # 배경 스타일
        self.preview_filler = urwid.Filler(self.preview_body, valign='top')     # flow → box
        self.preview_adapted = urwid.BoxAdapter(self.preview_filler, 1)         # 고정 높이(초기 1줄)
        self.preview_box = urwid.LineBox(self.preview_adapted, title="Preview") # 테두리(+2줄)
        self.preview_widget = urwid.AttrMap(self.preview_box, {None:'preview_border'}) # 외곽 스타일

        self._visible_preview_lines: Optional[int] = None  # 동적 가시 줄수 캐시
        self.main_pile = urwid.Pile([self.listbox])
        
        self.default_footer_text = "↑/↓:이동 | Enter:확장/프리뷰 | Space:선택 | D:Diff | Q:종료 | PgUp/Dn:스크롤"
        self.footer = urwid.AttrMap(urwid.Text(self.default_footer_text), 'header')
        self.footer_timer: Optional[threading.Timer] = None # 활성 타이머 추적

        self.frame = urwid.Frame(self.main_pile, footer=self.footer)
        self.main_loop: Optional[urwid.MainLoop] = None

        # diff 뷰 복귀/복원용
        self._old_input_filter = None
        self._old_unhandled_input = None
        self._old_widget = None

        self.h_offset = 0  # diff 뷰 가로 오프셋
        self.preview_h_offset = 0  # preview 뷰 가로 오프셋
        self.max_line_length = 0  # 현재 보이는 줄 중 최대 길이

        self.context_lines = 3 # 기본 문맥 줄 수
        self.show_full_diff = False # 전체 보기 모드 토글 상태
        
    def _get_lexer_for_path(self, path: Path) -> Any:
        key = path.suffix.lower()
        if key in self._lexer_cache:
            return self._lexer_cache[key]
        try:
            lexer = guess_lexer_for_filename(path.name, "")
        except Exception:
            lexer = TextLexer()
        self._lexer_cache[key] = lexer
        return lexer

    def _lex_file_by_lines(self, file_path: Path, lexer=None) -> Dict[int, List[Tuple]]:
        """
        파일 전체를 한 번에 렉싱한 후, 줄별로 토큰을 분리합니다.
        이렇게 하면 멀티라인 docstring이 String.Doc으로 올바르게 인식됩니다.
        
        Returns:
            Dict[int, List[Tuple]]: {줄번호(0-based): [(token_type, value), ...]}
        """
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return {}
        
        if lexer is None:
            try:
                lexer = self._get_lexer_for_path(file_path)
            except Exception:
                lexer = TextLexer()
        
        # 전체 파일을 한 번에 렉싱
        tokens = list(pyg_lex(content, lexer))
        
        # 줄별로 토큰 분리
        line_tokens = {}
        current_line = 0
        current_line_tokens = []
        
        for ttype, value in tokens:
            if not value:
                continue
            
            # 멀티라인 값 처리
            if '\n' in value:
                lines = value.split('\n')
                
                # 첫 번째 줄 부분
                if lines[0]:
                    current_line_tokens.append((ttype, lines[0]))
                
                # 첫 줄 저장
                if current_line_tokens:
                    line_tokens[current_line] = current_line_tokens
                
                # 중간 줄들
                for i in range(1, len(lines) - 1):
                    current_line += 1
                    if lines[i]:
                        line_tokens[current_line] = [(ttype, lines[i])]
                    else:
                        line_tokens[current_line] = []
                
                # 마지막 줄 시작
                current_line += 1
                current_line_tokens = []
                if lines[-1]:
                    current_line_tokens.append((ttype, lines[-1]))
            else:
                # 단일 라인 토큰
                current_line_tokens.append((ttype, value))
        
        # 마지막 줄 저장
        if current_line_tokens:
            line_tokens[current_line] = current_line_tokens
        
        return line_tokens
    
    def _get_cols(self) -> int:
        try:
            if self.main_loop:
                cols, _ = self.main_loop.screen.get_cols_rows()
                return cols
        except Exception:
            pass
        return 120 # safe fallback

    def _calc_preview_visible_lines(self) -> int:
        """
        터미널 rows에 맞춰 프리뷰 본문(코드) 가시 줄 수를 계산.
        LineBox 테두리 2줄을 제외하고 반환.
        """
        try:
            cols, rows = self.main_loop.screen.get_cols_rows()
        except Exception:
            rows = 24  # 폴백

        min_list_rows = 6   # 목록이 최소 확보할 줄 수
        footer_rows = 1     # Frame footer 가정
        safety = 1

        # 프리뷰에 할당 가능한 총 높이(테두리 포함)
        available_total = max(0, rows - (min_list_rows + footer_rows + safety))
        # 본문 라인 수 = 총 높이 - 2(위/아래 테두리)
        visible_lines = max(1, available_total - 2)
        # 유저 설정보다 크지 않게 제한
        return min(self.preview_lines_per_page, visible_lines)

    def _ensure_preview_in_pile(self, adapted_widget: urwid.Widget) -> None:
        """
        Pile 맨 아래에 프리뷰 박스를 넣되, 이미 있으면 교체한다.
        """
        if len(self.main_pile.contents) == 1:
            # 아직 프리뷰가 없으면 추가
            self.main_pile.contents.append((adapted_widget, self.main_pile.options('pack')))
        else:
            # 이미 프리뷰가 있으면 위젯/옵션을 교체
            self.main_pile.contents[-1] = (adapted_widget, self.main_pile.options('pack'))

    # ─────────────────────────────────────────────
    # (추가) 프리뷰 스크롤 헬퍼
    # ─────────────────────────────────────────────
    def _scroll_preview(self, key: str) -> None:
        if not self.previewing_item_id:
            return
        try:
            item_data = next(item for item in self.display_items if item.get('id') == self.previewing_item_id)
            content = item_data['path'].read_text(encoding='utf-8', errors='ignore').splitlines()
            total_lines = len(content)
            max_offset = max(0, total_lines - self.preview_lines_per_page)

            if key == 'page down':
                self.preview_offset = min(max_offset, self.preview_offset + self.preview_lines_per_page)
            elif key == 'page up':
                self.preview_offset = max(0, self.preview_offset - self.preview_lines_per_page)

            self._update_preview()
        except StopIteration:
            pass
        except IOError:
            pass

    # 임시 메시지를 표시하고 원래대로 복원하는 헬퍼 메서드
    def _show_temporary_footer(self, message: str, duration: float = 2.0):
        # 기존 타이머가 있으면 제거
        if self.footer_timer is not None:
            self.main_loop.remove_alarm(self.footer_timer)
            self.footer_timer = None

        # 새 메시지 표시
        self.footer.original_widget.set_text(message)
        # draw_screen()은 set_alarm_in 콜백에서 호출되므로 여기서 필요 없음
        
        # 지정된 시간 후에 원래 푸터로 복원하는 알람 설정
        self.footer_timer = self.main_loop.set_alarm_in(
            sec=duration,
            callback=self._restore_default_footer
        )

    # 기본 푸터 메시지로 복원하는 메서드
    def _restore_default_footer(self, loop, user_data=None):
        self.footer.original_widget.set_text(self.default_footer_text)
        # 알람 ID 정리
        self.footer_timer = None
        # 화면 갱신은 루프가 자동으로 처리하므로 draw_screen() 호출 불필요

    def handle_selection(self, item):
        is_in_list = any(s['id'] == item['id'] for s in self.selected_for_diff)
        if not is_in_list:
            if len(self.selected_for_diff) >= 2:
                self._show_temporary_footer("[!] 2개 이상 선택할 수 없습니다.")
                return
            if item.get('source') == 'local':
                self.selected_for_diff = [s for s in self.selected_for_diff if s.get('source') != 'local']
            self.selected_for_diff.append(item)
        else:
            self.selected_for_diff = [s for s in self.selected_for_diff if s['id'] != item['id']]
        self._show_temporary_footer(f" {len(self.selected_for_diff)}/2 선택됨. 'd' 키를 눌러 diff를 실행하세요.")

    def _scan_response_files(self) -> Dict[int, List[Path]]:
        if not self.config.CODE_OUTPUT_DIR.is_dir(): return {}
        pattern = re.compile(rf"codeblock_{re.escape(self.session_name)}_(\d+)_.*")
        msg_files: Dict[int, List[Path]] = {}
        for p in self.config.CODE_OUTPUT_DIR.glob(f"codeblock_{self.session_name}_*"):
            match = pattern.match(p.name)
            if match:
                msg_id = int(match.group(1))
                if msg_id not in msg_files: msg_files[msg_id] = []
                msg_files[msg_id].append(p)
        return {k: sorted(v) for k, v in sorted(msg_files.items(), reverse=True)}
    
    def _render_all(self, keep_focus: bool = True):
        pos = 0
        if keep_focus:
            try:
                pos = self.listbox.focus_position
            except IndexError:
                pos = 0

        self.display_items = []
        widgets = []

        # 로컬 파일 섹션
        if self.attached_files:
            section_id = "local_files"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(
                urwid.AttrMap(
                    urwid.SelectableIcon(f"{arrow} Current Local Files ({len(self.attached_files)})"),
                    'header', 'header'
                )
            )
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in self.attached_files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"local_{p.name}"
                    widgets.append(
                        urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus')
                    )
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "local"})

        # response 파일 섹션
        for msg_id, files in self.response_files.items():
            section_id = f"response_{msg_id}"
            arrow = "▼" if section_id in self.expanded_items else "▶"
            widgets.append(
                urwid.AttrMap(
                    urwid.SelectableIcon(f"{arrow} Response #{msg_id}"),
                    'header', 'header'
                )
            )
            self.display_items.append({"id": section_id, "type": "section"})
            if section_id in self.expanded_items:
                for p in files:
                    checked = "✔" if any(s.get("path") == p for s in self.selected_for_diff) else " "
                    item_id = f"response_{msg_id}_{p.name}"
                    widgets.append(
                        urwid.AttrMap(urwid.SelectableIcon(f"  [{checked}] {p.name}"), '', 'myfocus')
                    )
                    self.display_items.append({"id": item_id, "type": "file", "path": p, "source": "response", "msg_id": msg_id})

        if not widgets:
            placeholder = urwid.Text(("info", "No files found.\n- 첨부 파일을 추가하거나\n- 코드 블록이 저장된 디렉터리(gpt_codes 또는 gpt_outputs/codes)를 확인하세요."))
            widgets.append(urwid.AttrMap(placeholder, None, focus_map='myfocus'))
            self.display_items.append({"id": "placeholder", "type": "placeholder"})

        self.list_walker[:] = widgets
        if widgets:
            self.listbox.focus_position = min(pos, len(widgets) - 1)

        self._update_preview()

    def _render_preview(self):
        """파일 프리뷰를 렌더링 (가로 스크롤 지원)"""
        item_id = self.previewing_item_id
        is_previewing = len(self.main_pile.contents) > 1

        if not item_id:
            if is_previewing:
                self.main_pile.contents.pop()
            return

        item_data = next((it for it in self.display_items if it.get('id') == item_id), None)
        if not (item_data and item_data['type'] == 'file'):
            if is_previewing:
                self.main_pile.contents.pop()
            return

        try:
            file_path = item_data['path']
            all_lines = file_path.read_text(encoding='utf-8', errors='ignore').splitlines()
            total = len(all_lines)

            # 1) 가시 줄 수 산정
            visible_lines = self._calc_preview_visible_lines()
            self._visible_preview_lines = visible_lines

            # 2) 세로 오프셋 보정
            max_offset = max(0, total - visible_lines)
            if self.preview_offset > max_offset:
                self.preview_offset = max_offset

            start = self.preview_offset
            end = min(start + visible_lines, total)

            # 3) 최대 줄 길이 계산 (가로 스크롤 범위 결정용)
            visible_line_texts = all_lines[start:end]
            self.max_line_length = max(len(line.expandtabs(4)) for line in visible_line_texts) if visible_line_texts else 0

            # 4) 제목 갱신 (가로 스크롤 정보 포함)
            info = f" [{start+1}-{end}/{total}]"
            if self.preview_h_offset > 0:
                info += f" [H:{self.preview_h_offset}]"
            if self.max_line_length > 100:
                info += " [←→]"
            self.preview_box.set_title(f"Preview: {file_path.name}{info}")

            # 5) 전체 파일 렉싱 (한 번만)
            line_tokens_dict = self._lex_file_by_lines(file_path)
            
            # 6) 테마 가져오기
            preview_theme_name = self.theme_manager.current_theme_name
            preview_theme = self.theme_manager._FG_THEMES.get(preview_theme_name, {})
            
            markup = []
            digits = max(2, len(str(total)))
            
            for idx in range(start, end):
                # 줄번호
                lno_attr = self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black')
                markup.append((lno_attr, f"{idx+1:>{digits}} │ "))
                
                # 가로 오프셋 적용을 위한 코드 재구성
                line_text = all_lines[idx].expandtabs(4)
                
                # 가로 오프셋 적용
                if self.preview_h_offset > 0:
                    # 왼쪽에 더 있음을 표시
                    if line_text and self.preview_h_offset < len(line_text):
                        markup.append((self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black'), "←"))
                        # 오프셋만큼 잘라냄
                        visible_text = line_text[self.preview_h_offset:]
                    else:
                        visible_text = ""
                else:
                    visible_text = line_text
                
                # 토큰화된 렌더링
                if idx in line_tokens_dict:
                    # 오프셋이 적용된 visible_text를 다시 토큰화해야 함
                    # 하지만 이미 토큰화된 데이터가 있으므로, 위치 기반으로 처리
                    accumulated_pos = 0
                    for ttype, value in line_tokens_dict[idx]:
                        token_start = accumulated_pos
                        token_end = accumulated_pos + len(value)
                        
                        # 토큰이 가시 영역에 포함되는지 확인
                        if token_end > self.preview_h_offset:
                            # 토큰의 가시 부분만 추출
                            if token_start < self.preview_h_offset:
                                # 토큰의 일부가 잘림
                                visible_value = value[self.preview_h_offset - token_start:]
                            else:
                                # 토큰 전체가 보임
                                visible_value = value
                            
                            base = self.theme_manager._simplify_token_type(ttype)
                            fg_color = preview_theme.get(base, 'white')
                            attr = self.theme_manager._mk_attr(fg_color, constants.PREVIEW_BG, 'black')
                            markup.append((attr, visible_value))
                        
                        accumulated_pos = token_end
                else:
                    # 토큰 정보가 없으면 일반 텍스트로
                    if visible_text:
                        attr = self.theme_manager._mk_attr('light gray', constants.PREVIEW_BG, 'black')
                        markup.append((attr, visible_text))
                
                # 오른쪽에 더 있음을 표시
                if self.preview_h_offset + len(visible_text) < len(line_text):
                    markup.append((self.theme_manager._mk_attr('dark gray', constants.PREVIEW_BG, 'black'), "→"))
                
                # 줄바꿈 추가
                if idx < end - 1:
                    markup.append('\n')

            # 7) 마크업 적용
            self.preview_text.set_text(markup)

            # 8) 높이 조정
            self.preview_adapted.height = visible_lines

            # 9) Pile에 추가/교체
            if not is_previewing:
                self.main_pile.contents.append((self.preview_widget, self.main_pile.options('pack')))
            else:
                self.main_pile.contents[-1] = (self.preview_widget, self.main_pile.options('pack'))

        except Exception as e:
            error_attr = self.theme_manager._mk_attr('light red', constants.PREVIEW_BG, 'black')
            self.preview_text.set_text([(error_attr, f"Preview error: {e}")])
            self.preview_adapted.height = max(1, self._visible_preview_lines or 1)
            if not is_previewing:
                self.main_pile.contents.append((self.preview_widget, self.main_pile.options('pack')))
            else:
                self.main_pile.contents[-1] = (self.preview_widget, self.main_pile.options('pack'))

    # _update_preview 메서드도 수정 (단순히 _render_preview 호출)
    def _update_preview(self):
        """프리뷰 업데이트 - _render_preview를 호출"""
        self._render_preview()  # item 파라미터는 사용하지 않으므로 None 전달

    def _input_filter(self, keys, raw):
        try:
            if self.main_loop and self.main_loop.widget is self.frame:
                out = []
                for k in keys:
                    # 마우스 휠
                    if isinstance(k, tuple) and len(k) >= 2 and self.previewing_item_id:
                        ev, btn = k[0], k[1]
                        if ev == 'mouse press' and btn in (4, 5):
                            try:
                                it = next(x for x in self.display_items if x.get('id') == self.previewing_item_id)
                                total = len(it['path'].read_text(encoding='utf-8', errors='ignore').splitlines())
                                lines_per_page = self._visible_preview_lines or self.preview_lines_per_page
                                max_off = max(0, total - lines_per_page)
                                step = max(1, lines_per_page // 2)
                                if btn == 4:
                                    self.preview_offset = max(0, self.preview_offset - step)
                                else:
                                    self.preview_offset = min(max_off, self.preview_offset + step)
                                self._update_preview()
                                try: self.main_loop.draw_screen()
                                except Exception: pass
                            except Exception:
                                pass
                            continue  # 소비

                    if isinstance(k, str) and self.previewing_item_id:
                        kl = k.lower()
                        handled = False
                        if kl in ('page up', 'page down', 'home', 'end'):
                            try:
                                it = next(x for x in self.display_items if x.get('id') == self.previewing_item_id)
                                total = len(it['path'].read_text(encoding='utf-8', errors='ignore').splitlines())
                                lines_per_page = self._visible_preview_lines or self.preview_lines_per_page
                                max_off = max(0, total - lines_per_page)

                                if   kl == 'page up':
                                    self.preview_offset = max(0, self.preview_offset - lines_per_page)
                                elif kl == 'page down':
                                    self.preview_offset = min(max_off, self.preview_offset + lines_per_page)
                                elif kl == 'home':
                                    self.preview_offset = 0
                                elif kl == 'end':
                                    self.preview_offset = max_off

                                self._update_preview()
                                try: self.main_loop.draw_screen()
                                except Exception: pass
                                handled = True
                            except Exception:
                                handled = False

                        if handled:
                            continue  # 소비

                    out.append(k)
                return out
        except Exception:
            return keys
        return keys
    
    def handle_input(self, key):
        if not isinstance(key, str):
            return
        
        # preview 가로 스크롤 처리
        if self.previewing_item_id:
            handled = False
            
            if key == 'right':
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset += 10
                    self._update_preview()
                    handled = True
            
            elif key == 'left':
                if self.preview_h_offset > 0:
                    self.preview_h_offset = max(0, self.preview_h_offset - 10)
                    self._update_preview()
                    handled = True
            
            elif key == 'shift right':
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset = min(self.max_line_length - 40, self.preview_h_offset + 30)
                    self._update_preview()
                    handled = True
            
            elif key == 'shift left':
                if self.preview_h_offset > 0:
                    self.preview_h_offset = max(0, self.preview_h_offset - 30)
                    self._update_preview()
                    handled = True
            
            elif key in ('home', 'g'):
                if self.preview_h_offset > 0:
                    self.preview_h_offset = 0
                    self._update_preview()
                    handled = True
            
            elif key in ('end', 'G'):
                if self.preview_h_offset < self.max_line_length - 40:
                    self.preview_h_offset = max(0, self.max_line_length - 40)
                    self._update_preview()
                    handled = True
            
            if handled:
                return

        try:
            pos = self.listbox.focus_position
            item = self.display_items[pos]
        except IndexError:
            # 프레임으로 전달
            self.frame.keypress(self.main_loop.screen_size, key)
            return

        if key.lower() == 'q':
            raise urwid.ExitMainLoop()

        elif key == 'enter':
            if item['type'] == 'section':
                # 섹션 확장/축소
                if item['id'] in self.expanded_items:
                    self.expanded_items.remove(item['id'])
                else:
                    self.expanded_items.add(item['id'])
            elif item['type'] == 'file':
                item_id = item['id']
                self.previewing_item_id = None if self.previewing_item_id == item_id else item_id
                self.preview_offset = 0
            self._render_all()

        elif key == ' ':
            if item['type'] == 'file':
                self.handle_selection(item)
                self._render_all(keep_focus=True)

        elif key.lower() == 'd':
            if len(self.selected_for_diff) == 2:
                self._show_diff_view()
            else:
                message = f"[!] 2개 항목을 선택해야 diff가 가능합니다. (현재 {len(self.selected_for_diff)}개 선택됨)"
                self._show_temporary_footer(message)
        else:
            # 기본 처리는 프레임으로
            self.frame.keypress(self.main_loop.screen_size, key)

    def _build_diff_line_widget(
        self,
        kind: str,
        code_line: str,
        old_no: Optional[int],
        new_no: Optional[int],
        digits_old: int,
        digits_new: int,
        line_tokens: Optional[List[Tuple]] = None,  # 사전 렉싱된 토큰
        h_offset: int = 0
    ) -> urwid.Text:
        """
        사전 렉싱된 토큰 정보를 활용한 diff 라인 렌더링
        """

        bg, fb_bg = self.theme_manager._bg_for_kind(kind)
        fgmap = self.theme_manager.get_fg_map_for_diff(kind) or self.theme_manager.get_fg_map_for_diff('ctx')
        
        sign_char = '+' if kind == 'add' else '-' if kind == 'del' else ' '
        old_s = f"{old_no}" if old_no is not None else ""
        new_s = f"{new_no}" if new_no is not None else ""

        parts: List[Tuple[urwid.AttrSpec, str]] = []
        
        # 구터는 항상 표시 (스크롤 영향 없음)
        parts.append((self.theme_manager._mk_attr(self.theme_manager._SIGN_FG[kind], bg, fb_bg), f"{sign_char} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._LNO_OLD_FG, bg, fb_bg), f"{old_s:>{digits_old}} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._LNO_NEW_FG, bg, fb_bg), f"{new_s:>{digits_new}} "))
        parts.append((self.theme_manager._mk_attr(self.theme_manager._SEP_FG, bg, fb_bg), "│ "))
        
        # 코드 부분에 가로 오프셋 적용
        safe = code_line.expandtabs(4).replace('\n','').replace('\r','')
        
        # 오프셋 적용
        if h_offset > 0:
            if h_offset < len(safe):
                safe = safe[h_offset:]
            else:
                safe = ""
        
        # 스크롤 표시
        if h_offset > 0 and safe:
            parts.append((self.theme_manager._mk_attr('dark gray', bg, fb_bg), "←"))
        
        # ✅ 핵심: 사전 렉싱된 토큰 사용
        if line_tokens:
            # 토큰 정보가 있으면 정확한 하이라이팅
            accumulated_pos = 0
            for ttype, value in line_tokens:
                token_start = accumulated_pos
                token_end = accumulated_pos + len(value)
                
                # 오프셋 적용된 가시 영역 체크
                if token_end > h_offset:
                    if token_start < h_offset:
                        # 토큰이 잘림
                        visible_value = value[h_offset - token_start:]
                    else:
                        # 전체 보임
                        visible_value = value
                    base = self.theme_manager._simplify_token_type(ttype)
                    #self.console.print(fgmap.get(base, 'white'), base)
                    #time.sleep(1)
                    parts.append((self.theme_manager._mk_attr(fgmap.get(base, 'white'), bg, fb_bg), visible_value))
                
                accumulated_pos = token_end
        else:
            # 토큰 정보가 없으면 기존 방식 (줄 단위 렉싱)
            if safe:
                try:
                    lexer = TextLexer()
                    for ttype, value in pyg_lex(safe, lexer):
                        if not value:
                            continue
                        v = value.replace('\n','').replace('\r','')
                        if not v:
                            continue
                        base = self.theme_manager._simplify_token_type(ttype)
                        parts.append((self.theme_manager._mk_attr(fgmap.get(base, 'white'), bg, fb_bg), v))
                except:
                    parts.append((self.theme_manager._mk_attr('white', bg, fb_bg), safe))
        
        # 오른쪽 스크롤 표시
        original_len = len(code_line.expandtabs(4))
        visible_len = len(safe)
        if h_offset + visible_len < original_len:
            parts.append((self.theme_manager._mk_attr('dark gray', bg, fb_bg), "→"))
        
        # 패딩
        padding = ' ' * 200
        parts.append((self.theme_manager._mk_attr('default', bg, fb_bg), padding))

        return urwid.Text(parts, wrap='clip')

    def _show_diff_view(self):
        import re
        if len(self.selected_for_diff) != 2:
            return

        item1, item2 = self.selected_for_diff
        # old/new 결정(응답 순서 기준)
        if item1.get("source") == "local" or item1.get("msg_id", 0) < item2.get("msg_id", 0):
            old_item, new_item = item1, item2
        else:
            old_item, new_item = item2, item1

        try:
            old_text = old_item['path'].read_text(encoding='utf-8', errors='ignore')
            new_text = new_item['path'].read_text(encoding='utf-8', errors='ignore')
            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
        except Exception as e:
            self.footer.original_widget.set_text(f"Error: {e}")
            return

        # ✅ 핵심: 전체 파일을 먼저 렉싱하여 토큰 정보 획득
        try:
            old_lexer = self._get_lexer_for_path(old_item['path'])
        except Exception:
            old_lexer = TextLexer()
        
        try:
            new_lexer = self._get_lexer_for_path(new_item['path'])
        except Exception:
            new_lexer = TextLexer()
        
        # 전체 파일 렉싱하여 줄별 토큰 매핑 생성
        old_line_tokens = self._lex_file_by_lines(old_item['path'], old_lexer)
        new_line_tokens = self._lex_file_by_lines(new_item['path'], new_lexer)

        if self.show_full_diff:
            context_lines = max(len(old_lines), len(new_lines))
        else:
            context_lines = self.context_lines

        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{old_item['path'].name}",
            tofile=f"b/{new_item['path'].name}",
            lineterm='',
            n=context_lines
        ))
        if not diff:
            self.footer.original_widget.set_text("두 파일이 동일합니다.")
            return

        # 필요한 변수들
        digits_old = max(2, len(str(len(old_lines))))
        digits_new = max(2, len(str(len(new_lines))))

        # 가로 스크롤 상태
        h_offset_ref = {'value': 0}
        max_line_len = 0
        
        # diff 라인에서 최대 길이 계산
        for line in diff:
            if line and not line.startswith('@@'):
                content = line[1:] if line[0] in '+-' else line
                max_line_len = max(max_line_len, len(content.expandtabs(4)))

        # diff 위젯 생성 함수 수정
        def generate_diff_widgets(h_offset: int) -> List[urwid.Widget]:
            widgets: List[urwid.Widget] = []
            
            # 파일 헤더
            widgets.append(urwid.Text(('diff_file_old', f"--- a/{old_item['path'].name}"), wrap='clip'))
            widgets.append(urwid.Text(('diff_file_new', f"+++ b/{new_item['path'].name}"), wrap='clip'))
            
            # 헝크 파서
            old_ln = None
            new_ln = None
            hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
            
            i = 0
            while i < len(diff):
                line = diff[i]

                # 파일 헤더 스킵
                if line.startswith('---') and i == 0:
                    i += 1; continue
                if line.startswith('+++') and i == 1:
                    i += 1; continue

                # 헝크 헤더
                if line.startswith('@@'):
                    m = hunk_re.match(line)
                    if m:
                        old_ln = int(m.group(1))
                        new_ln = int(m.group(3))
                    widgets.append(urwid.Text(('diff_hunk', line), wrap='clip'))
                    i += 1
                    continue

                # ✅ 수정된 emit_kind: 토큰 정보 활용
                def emit_kind(kind: str, old_no: Optional[int], new_no: Optional[int], content: str):
                    # 원본 파일에서 해당 라인의 토큰 정보 가져오기
                    line_tokens = None
                    if kind == 'del' and old_no is not None:
                        # 삭제된 라인은 old 파일의 토큰 정보 사용
                        line_tokens = old_line_tokens.get(old_no - 1)  # 0-based index
                    elif kind == 'add' and new_no is not None:
                        # 추가된 라인은 new 파일의 토큰 정보 사용
                        line_tokens = new_line_tokens.get(new_no - 1)  # 0-based index
                    elif kind == 'ctx':
                        # context 라인은 둘 다 같으므로 new 파일 사용
                        if new_no is not None:
                            line_tokens = new_line_tokens.get(new_no - 1)
                    
                    widgets.append(
                        self._build_diff_line_widget(
                            kind=kind,
                            code_line=content,
                            old_no=old_no,
                            new_no=new_no,
                            digits_old=digits_old,
                            digits_new=digits_new,
                            line_tokens=line_tokens,  # 토큰 정보 전달
                            h_offset=h_offset,
                        )
                    )

                # '-' 다음이 '+' 페어
                if line.startswith('-') and i + 1 < len(diff) and diff[i+1].startswith('+'):
                    old_line = line[1:]
                    new_line = diff[i+1][1:]
                    emit_kind('del', old_ln, None, old_line)
                    emit_kind('add', None, new_ln, new_line)
                    if old_ln is not None: old_ln += 1
                    if new_ln is not None: new_ln += 1
                    i += 2
                    continue

                # 단일 라인
                if line.startswith('-'):
                    emit_kind('del', old_ln, None, line[1:])
                    if old_ln is not None: old_ln += 1
                elif line.startswith('+'):
                    emit_kind('add', None, new_ln, line[1:])
                    if new_ln is not None: new_ln += 1
                elif line.startswith(('---','+++')):
                    widgets.append(urwid.Text(('diff_meta', line), wrap='clip'))
                else:
                    content = line[1:] if line.startswith(' ') else line
                    emit_kind('ctx', old_ln, new_ln, content)
                    if old_ln is not None: old_ln += 1
                    if new_ln is not None: new_ln += 1

                i += 1
            
            return widgets

        # 초기 위젯 생성
        diff_walker = urwid.SimpleFocusListWalker(generate_diff_widgets(0))
        diff_listbox = DiffListBox(diff_walker)

        header = urwid.AttrMap(
            urwid.Text(f"Diff: {old_item['path'].name} → {new_item['path'].name}", wrap='clip'),
            'header'
        )
        
        # footer 업데이트 함수
        def update_footer():
            scroll_info = ""
            if h_offset_ref['value'] > 0:
                scroll_info = f" [H:{h_offset_ref['value']}]"
            if max_line_len > 100:
                scroll_info += f" [←→: 가로스크롤]"
            context_info = f" [+/-/F: 문맥({self.context_lines})]" if not self.show_full_diff else " [문맥: 전체]"
            footer_text = f"PgUp/Dn: 스크롤 | Home/End: 처음/끝 | ←→: 가로 | Q: 닫기{scroll_info}{context_info}"
            return urwid.AttrMap(urwid.Text(footer_text, wrap='clip'), 'header')
        
        diff_footer = update_footer()
        diff_frame = urwid.Frame(diff_listbox, header=header, footer=diff_footer)

        # 기존 상태 백업
        self._old_widget = self.main_loop.widget
        self._old_unhandled_input = self.main_loop.unhandled_input
        self._old_input_filter = self.main_loop.input_filter
        self.main_loop.widget = diff_frame

        # ✅ 핵심: diff 뷰 재생성 함수
        def regenerate_diff_view():
            # 현재 포커스 위치 저장
            try:
                current_focus = diff_listbox.focus_position
            except:
                current_focus = 0
            
            # 위젯 리스트 재생성
            new_widgets = generate_diff_widgets(h_offset_ref['value'])
            diff_walker[:] = new_widgets
            
            # 포커스 복원
            if new_widgets:
                diff_listbox.focus_position = min(current_focus, len(new_widgets) - 1)
            
            # footer 업데이트
            diff_frame.footer = update_footer()
            
            # 화면 다시 그리기
            self.main_loop.draw_screen()

        # 키 처리
        def diff_unhandled(key):
            if isinstance(key, str):
                if key.lower() == 'q':
                    # 원래 상태 복원
                    self.main_loop.widget = self._old_widget
                    self.main_loop.unhandled_input = self._old_unhandled_input
                    self.main_loop.input_filter = self._old_input_filter
                    try:
                        self.main_loop.draw_screen()
                    except Exception:
                        pass

                elif key == '+':
                    self.context_lines = min(self.context_lines + 2, 99)
                    self.show_full_diff = False
                    regenerate_diff_view()

                elif key == '-':
                    self.context_lines = max(0, self.context_lines - 2)
                    self.show_full_diff = False
                    regenerate_diff_view()

                elif key == 'f':
                    self.show_full_diff = not self.show_full_diff
                    regenerate_diff_view()
                
                # ✅ 가로 스크롤 처리
                elif key == 'right':
                    if h_offset_ref['value'] < max_line_len - 40:  # 여유 40자
                        h_offset_ref['value'] += 10
                        regenerate_diff_view()
                
                elif key == 'left':
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = max(0, h_offset_ref['value'] - 10)
                        regenerate_diff_view()
                
                elif key == 'shift right':  # 빠른 스크롤
                    if h_offset_ref['value'] < max_line_len - 40:
                        h_offset_ref['value'] = min(max_line_len - 40, h_offset_ref['value'] + 30)
                        regenerate_diff_view()
                
                elif key == 'shift left':  # 빠른 스크롤
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = max(0, h_offset_ref['value'] - 30)
                        regenerate_diff_view()
                
                elif key in ('home', 'g'):  # 줄 시작으로
                    if h_offset_ref['value'] > 0:
                        h_offset_ref['value'] = 0
                        regenerate_diff_view()
                
                elif key in ('end', 'G'):  # 줄 끝으로
                    if h_offset_ref['value'] < max_line_len - 40:
                        h_offset_ref['value'] = max_line_len - 40
                        regenerate_diff_view()

        self.main_loop.unhandled_input = diff_unhandled


    def start(self):
        self._render_all(keep_focus=False)

        if len(self.list_walker) > 0:
            try:
                self.listbox.focus_position = 0
            except IndexError:
                pass

        screen = urwid.raw_display.Screen()
        # 마우스 트래킹 활성화 (가능한 터미널에서 휠 이벤트 수신)
        try:
            screen.set_terminal_properties(colors=256)
            screen.set_mouse_tracking()
            
        except Exception as e:
            self.console.print(f"[yellow]경고: 터미널 속성 설정 실패 ({e})[/yellow]")

        palette = self.theme_manager.get_urwid_palette()
        self.main_loop = urwid.MainLoop(
            self.frame,
            palette=palette,
            screen=screen,
            unhandled_input=self.handle_input,
            input_filter=self._input_filter
        )
        self.theme_manager.apply_to_urwid_loop(self.main_loop)
        self.main_loop.run()
