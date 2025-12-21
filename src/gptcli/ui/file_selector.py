# src/gptcli/ui/file_selector.py
from __future__ import annotations
from typing import List, Tuple, Set
from pathlib import Path
import urwid
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.theme import ThemeManager

class FileSelector:
    def __init__(self, config: 'ConfigManager', theme_manager: 'ThemeManager') -> None:
        """
        Args:
            config (ConfigManager): 경로 및 무시 규칙을 제공하는 설정 관리자.
        """
        self.config = config
        self.theme_manager = theme_manager
        self.spec = self.config.get_ignore_spec()
        self.items: List[Tuple[Path, bool]] = []  # (path, is_dir)
        self.selected: set[Path] = set()
        self.expanded: set[Path] = set()

    def refresh(self) -> None:
        self.items.clear()
        def visit_dir(path: Path, depth: int):
            path = path.resolve()
            # [변경] 전역 is_ignored 대신 self.config.is_ignored 사용
            if depth > 0 and self.config.is_ignored(path, self.spec):
                return
            
            self.items.append((path, True))
            
            if path in self.expanded:
                try:
                    children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
                    for child in children:
                        if child.is_dir():
                            visit_dir(child, depth + 1)
                        elif child.is_file():
                            # [변경] 전역 is_ignored 대신 self.config.is_ignored 사용
                            if self.config.is_ignored(child, self.spec):
                                continue
                            # 확장자/휴리스틱 필터 제거, ignore만 통과하면 추가
                            #if child.suffix.lower() in (*constants.PLAIN_EXTS, *constants.IMG_EXTS, constants.PDF_EXT):
                            self.items.append((child.resolve(), False))
                except Exception:
                    pass

        # [변경] 전역 BASE_DIR 대신 self.config.BASE_DIR 사용
        visit_dir(self.config.BASE_DIR, 0)
    
    def get_all_files_in_dir(self, folder: Path) -> set[Path]:                                       
        """주어진 폴더 내 모든 하위 파일을 무시 규칙을 적용하여 반환합니다."""
        result = set()
        if self.config.is_ignored(folder, self.spec):
            return result
        try:
            for entry in folder.iterdir():                                                           
                if self.config.is_ignored(entry, self.spec):
                    continue
                if entry.is_dir():                                                                   
                    result.update(self.get_all_files_in_dir(entry))                                       
                elif entry.is_file():                                                                
                    #if entry.suffix.lower() in (*constants.PLAIN_EXTS, *constants.IMG_EXTS, constants.PDF_EXT):                    
                    result.add(entry.resolve())
        except Exception:                                                                            
            pass                                                                                     
        return result
    
    def folder_all_selected(self, folder: Path) -> bool:                                             
        """해당 폴더의 모든 허용 파일이 선택되었는지 확인합니다."""
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files) and all_files.issubset(self.selected)                                 
                                                                                                    
    def folder_partial_selected(self, folder: Path) -> bool:                                         
        """해당 폴더의 파일 중 일부만 선택되었는지 확인합니다."""
        all_files = self.get_all_files_in_dir(folder)                                                
        return bool(all_files & self.selected) and not all_files.issubset(self.selected)             
                                                                                                        
    # TUI
    def start(self) -> List[str]:
        """TUI를 시작하고 사용자가 선택한 파일 경로 목록을 반환합니다."""
        self.refresh()

        def mkwidget(data: Tuple[Path, bool]) -> urwid.Widget:                                           
            path, is_dir = data                                                                          
            try:
                relative_path = path.relative_to(self.config.BASE_DIR)
                depth = len(relative_path.parts) - (0 if is_dir or path == self.config.BASE_DIR else 1)
            except ValueError:
                depth = 0 # BASE_DIR 외부에 있는 경우
            indent = "  " * depth                                                                        
                                                                                                        
            # 선택 상태 결정: 부분선택(폴더) 고려                                                        
            if is_dir:                                                                                   
                if self.folder_all_selected(path):                                                       
                    checked = "✔"                                                                        
                elif self.folder_partial_selected(path):                                                 
                    checked = "−"  # 또는 "*" 등                                                         
                else:                                                                                    
                    checked = " "                                                                        
                arrow = "▼" if path in self.expanded else "▶"                                            
                label = f"{indent}{arrow} [{checked}] {path.name}/"                                      
            else:                                                                                        
                checked = "✔" if path in self.selected else " "                                          
                label = f"{indent}  [{checked}] {path.name}"                                             
            return urwid.AttrMap(urwid.SelectableIcon(label, 0), None, focus_map='myfocus') 

        walker = urwid.SimpleFocusListWalker([mkwidget(i) for i in self.items])
        
        def refresh_list() -> None:
            walker[:] = [mkwidget(i) for i in self.items]

        def keypress(key: str) -> None:
            if isinstance(key, tuple) and len(key) >= 4:
                event_type, button, col, row = key[:4]
                if event_type == 'mouse press':
                    if button == 4:  # 마우스 휠 업
                        # 위로 스크롤 (ListBox focus 이동)
                        if listbox.focus_position > 0:
                            listbox.focus_position -= 1
                        return
                    elif button == 5:  # 마우스 휠 다운
                        # 아래로 스크롤
                        if listbox.focus_position < len(self.items) - 1:
                            listbox.focus_position += 1
                        return
                return
            
            idx = listbox.focus_position
            if key == " ":
                tgt, is_dir = self.items[idx]
                tgt = tgt.resolve()
                if is_dir:
                    files_in_dir = self.get_all_files_in_dir(tgt)
                    if files_in_dir.issubset(self.selected):                                                 
                        # 이미 전체 선택되어 있었으니 전체 해제                                              
                        self.selected -= files_in_dir                                                        
                        self.selected.discard(tgt)                                                           
                    else:                                                                                    
                        # 전체 선택 아님, 모두 추가                                                          
                        self.selected |= files_in_dir                                                        
                        self.selected.add(tgt)
                else:
                    self.selected.symmetric_difference_update({tgt})
                refresh_list()
            elif key == "enter":
                tgt, is_dir = self.items[idx]
                tgt = tgt.resolve()
                if is_dir:
                    if tgt in self.expanded:
                        self.expanded.remove(tgt)
                    else:
                        self.expanded.add(tgt)
                    self.refresh()
                    refresh_list()
            elif key.lower() == "a":
                # 전체 트리에서 모든 파일(노출 여부와 관계 없이!)을 재귀 선택
                self.selected = self.get_all_files_in_dir(self.config.BASE_DIR)
                refresh_list()
            elif key.lower() == "n":
                self.selected.clear()
                refresh_list()
            elif key.lower() == "s":
                raise urwid.ExitMainLoop()
            elif key.lower() == "q":
                self.selected.clear()
                raise urwid.ExitMainLoop()

        listbox = urwid.ListBox(walker)
        help_text = urwid.Text([
            "명령어: ",
            ("key", "Space"), ":선택  ",
            ("key", "Enter"), ":펼침  ", 
            ("key", "A"), ":전체선택  ",
            ("key", "N"), ":해제  ",
            ("key", "S"), ":완료  ",
            ("key", "Q"), ":취소\n",
            ("info", f"현재 위치: {self.config.BASE_DIR}")
        ])
        
        header = urwid.Pile([
            help_text,
            urwid.Divider(),
        ])

        frame = urwid.Frame(listbox, header=header)
        palette = self.theme_manager.get_urwid_palette()
        urwid.MainLoop(                                                                                  
            frame,                                                                                       
            palette=palette,
            unhandled_input=keypress,                                                                    
        ).run() 
        return [str(p) for p in sorted(self.selected) if p.is_file()]
