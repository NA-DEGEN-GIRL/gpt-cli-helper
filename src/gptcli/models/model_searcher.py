# src/gptcli/models/model_searcher.py
from __future__ import annotations
import requests, urwid
from typing import Any, Dict, List, Optional, Set, Tuple
from rich.console import Console
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.theme import ThemeManager
import src.constants as constants

class ModelSearcher:
    """OpenRouter 모델을 검색하고 TUI를 통해 선택하여 `ai_models.txt`를 업데이트합니다."""
    
    API_URL = constants.API_URL
    
    def __init__(self, config: 'ConfigManager', theme_manager: 'ThemeManager', console: 'Console'):
        self.config = config
        self.theme_manager = theme_manager
        self.console = console

        self.all_models_map: Dict[str, Dict[str, Any]] = {}
        self.selected_ids: Set[str] = set()
        self.expanded_ids: Set[str] = set()
        self.display_models: List[Dict[str, Any]] = []

    def _fetch_all_models(self) -> bool:
        try:
            with self.console.status("[cyan]OpenRouter에서 모델 목록을 가져오는 중...", spinner="dots"):
                response = requests.get(self.API_URL, timeout=10)
                response.raise_for_status()
            self.all_models_map = {m['id']: m for m in response.json().get("data", [])}
            return True if self.all_models_map else False
        except requests.RequestException as e:
            self.console.print(f"[red]API 실패: {e}[/red]"); return False

    def _get_existing_model_ids(self) -> Set[str]:
        if not self.config.MODELS_FILE.exists(): return set()
        try:
            lines = self.config.MODELS_FILE.read_text(encoding="utf-8").splitlines()
            return {line.strip().split()[0] for line in lines if line.strip() and not line.strip().startswith("#")}
        except Exception: return set()

    def _save_models(self):
        try:
            final_ids = sorted(list(self.selected_ids))
            with self.config.MODELS_FILE.open("w", encoding="utf-8") as f:
                f.write("# OpenRouter.ai Models (gpt-cli auto-generated)\n\n")
                for model_id in final_ids:
                    model_data = self.all_models_map.get(model_id, {})
                    #f.write(f"{model_id} {model_data.get('context_length', 0)}\n")
                    context_len = model_data.get('context_length') or constants.DEFAULT_CONTEXT_LENGTH
                    f.write(f"{model_id} {context_len}\n")
            self.console.print(f"[green]성공: {len(final_ids)}개 모델로 '{self.config.MODELS_FILE}' 업데이트 완료.[/green]")
        except Exception as e:
            self.console.print(f"[red]저장 실패: {e}[/red]")
            
    class Collapsible(urwid.Pile):
        def __init__(self, model_data, is_expanded, is_selected, in_existing):
            self.model_id = model_data.get('id', 'N/A')
            
            checked = "✔" if is_selected else " "
            arrow = "▼" if is_expanded else "▶"
            context_length = model_data.get("context_length")
            context_str = f"[CTX: {context_length:,}]" if context_length else "[CTX: N/A]"

            
            style = "key" if in_existing else "default"
            
            # 첫 번째 라인: 포커스 시 배경색이 변경되도록 AttrMap으로 감쌈
            line1_cols = urwid.Columns([
                ('pack', urwid.Text(f"[{checked}] {arrow}")),
                ('weight', 1, urwid.Text(self.model_id)),
                ('pack', urwid.Text(context_str)),
            ], dividechars=1)
            line1_wrapped = urwid.AttrMap(line1_cols, style, focus_map='myfocus')

            widget_list = [line1_wrapped]
            
            if is_expanded:
                desc_text = model_data.get('description') or "No description available."
                desc_content = urwid.Text(desc_text, wrap='space')
                padded_content = urwid.Padding(desc_content, left=4, right=2)
                desc_box = urwid.LineBox(padded_content, tlcorner=' ', tline=' ', lline=' ', 
                                         trcorner=' ', blcorner=' ', rline=' ', bline=' ', brcorner=' ')
                # 설명 부분은 포커스와 상관없이 항상 동일한 배경
                widget_list.append(urwid.AttrMap(desc_box, 'info_bg'))

            super().__init__(widget_list)

        def selectable(self) -> bool:
            return True

    def start(self, keywords: List[str]):
        if not self._fetch_all_models(): return

        existing_ids = self._get_existing_model_ids()
        self.selected_ids = existing_ids.copy()

        model_ids_to_display = set(self.all_models_map.keys()) if not keywords else existing_ids.copy()
        if keywords:
            for mid, mdata in self.all_models_map.items():
                search_text = f"{mid} {mdata.get('name', '')}".lower()
                if any(kw.lower() in search_text for kw in keywords):
                    model_ids_to_display.add(mid)

        self.display_models = [self.all_models_map[mid] for mid in sorted(list(model_ids_to_display)) if mid in self.all_models_map]
        self.display_models.sort(key=lambda m: (m['id'] not in existing_ids, m.get('id', '')))

        list_walker = urwid.SimpleFocusListWalker([])
        listbox = urwid.ListBox(list_walker)

        def refresh_list():
            try:
                current_focus_pos = listbox.focus_position
            except IndexError:
                current_focus_pos = 0 # 리스트가 비어있으면 0으로 초기화

            widgets = [
                self.Collapsible(m, m['id'] in self.expanded_ids, m['id'] in self.selected_ids, m['id'] in existing_ids)
                for m in self.display_models
            ]
            list_walker[:] = widgets #! .contents가 아니라 슬라이싱으로 할당
            
            if widgets:
                listbox.focus_position = min(current_focus_pos, len(widgets) - 1)
        
        refresh_list()
        listbox.focus_position = 0

        def keypress(key: str):
            if isinstance(key, tuple): return
            try:
                model_widget = list_walker[listbox.focus_position]
                model_id = model_widget.model_id
            except (IndexError, AttributeError): return

            if key == 'enter':
                self.expanded_ids.symmetric_difference_update({model_id})
            elif key == ' ':
                self.selected_ids.symmetric_difference_update({model_id})
            elif key.lower() == 'a':
                self.selected_ids.update(m['id'] for m in self.display_models)
            elif key.lower() == 'n':
                for m in self.display_models:
                    self.selected_ids.discard(m['id'])
        
        help_text = urwid.Text([ "명령어: ", ("key", "Enter"),":설명 ", ("key", "Space"),":선택 ", ("key", "A/N"),":전체선택/해제 ", ("key", "S"),":저장 ", ("key", "Q"),":취소" ])
        header_title = lambda: f"{len(self.display_models)}개 모델 표시됨 (전체 {len(self.selected_ids)}개 선택됨)"
        header = urwid.Pile([urwid.Text(header_title()), help_text, urwid.Divider()])
        frame = urwid.Frame(listbox, header=header)
        
        save_triggered = False
        def exit_handler(key):
            nonlocal save_triggered
            if isinstance(key, tuple): return
            if key.lower() == 's':
                save_triggered = True; raise urwid.ExitMainLoop()
            elif key.lower() == 'q':
                raise urwid.ExitMainLoop()
            
            keypress(key)
            refresh_list()
            #header.widget_list[0].set_text(header_title())
            header.contents[0][0].set_text(header_title())
        
        screen = urwid.raw_display.Screen()
        palette = self.theme_manager.get_urwid_palette()
        main_loop = urwid.MainLoop(frame, palette=palette, screen=screen, unhandled_input=exit_handler)
        
        main_loop.run()
        #finally: screen.clear()

        if save_triggered:
            self._save_models()
        else:
            self.console.print("[dim]모델 선택이 취소되었습니다.[/dim]")
