# src/gptcli/ui/completion.py
from __future__ import annotations
from typing import Optional, List
from pathlib import Path
from prompt_toolkit.completion import Completer, WordCompleter, FuzzyCompleter, Completion
from prompt_toolkit.document import Document
from src.gptcli.services.config import ConfigManager
from src.gptcli.services.theme import ThemeManager

class PathCompleterWrapper(Completer):
    """
    PathCompleter를 /files 명령어에 맞게 감싸는 최종 완성 버전.
    스페이스로 구분된 여러 파일 입력을 완벽하게 지원합니다.
    """
    def __init__(self, command_prefix: str, path_completer: Completer, config: 'ConfigManager'):
        self.command_prefix = command_prefix
        self.path_completer = path_completer
        self.config = config

    def get_completions(self, document: Document, complete_event):
        # 1. 사용자가 "/files " 뒤에 있는지 확인합니다.
        #    커서 위치가 명령어 길이보다 짧으면 자동 완성을 시도하지 않습니다.
        if document.cursor_position < len(self.command_prefix):
            return

        # 2. 커서 바로 앞의 '단어'를 가져옵니다. 이것이 핵심입니다.
        # WORD=True 인자는 슬래시(/)나 점(.)을 단어의 일부로 인식하게 합니다.
        # 예: "/files main.py src/co" -> "src/co"
        word_before_cursor = document.get_word_before_cursor(WORD=True)

        # 3. 만약 단어가 없다면 (예: "/files main.py ") 아무것도 하지 않습니다.
        #if not word_before_cursor:
        #    return

        # 4. '현재 단어'만을 내용으로 하는 가상의 Document 객체를 만듭니다.
        doc_for_path = Document(
            text=word_before_cursor,
            cursor_position=len(word_before_cursor)
        )

        # 5. PathCompleter 제안을 받아 '무시 규칙'으로 후처리 필터링합니다.
        #    여기서 핵심은 comp.text를 '현재 단어의 디렉터리 컨텍스트'에 맞춰 절대경로로 복원하는 것입니다.
        spec = self.config.get_ignore_spec()
        
        for comp in self.path_completer.get_completions(doc_for_path, complete_event):
            try:
                # comp.start_position을 반영하여 "적용 후 최종 단어"를 구성
                start_pos = getattr(comp, "start_position", 0) or 0
                base = word_before_cursor or ""
                # 음수면 좌측으로 그만큼 잘라낸 뒤 comp.text로 대체
                cut = len(base) + start_pos if start_pos < 0 else len(base)
                final_word = (base[:cut] + (comp.text or "")).strip()

                # 최종 단어 → 절대 경로
                cand_path = Path(final_word).expanduser()
                if cand_path.is_absolute():
                    p_full = cand_path.resolve()
                else:
                    p_full = (self.config.BASE_DIR / cand_path).resolve()
                # 실제 존재하는 후보만 필터링(미존재 조각은 통과시켜 타이핑 진행 가능)
                if p_full.exists() and self.config.is_ignored(p_full, spec):
                    continue  # 무시 대상은 자동완성에서 숨김
            except Exception:
                # 문제가 있어도 자동완성 전체를 막지 않음
                pass
            yield comp

class AttachedFileCompleter(Completer):
    """
    오직 '첨부된 파일 목록' 내에서만 경로 자동완성을 수행하는 전문 Completer.
    WordCompleter가 겪는 경로 관련 '단어' 인식 문제를 완벽히 해결합니다.
    """
    def __init__(self, attached_relative_paths: List[str]):
        self.attached_paths = sorted(list(set(attached_relative_paths)))

    def get_completions(self, document: Document, complete_event):
        # 1. 사용자가 현재 입력 중인 '단어'를 가져옵니다. (WORD=True로 경로 문자 인식)
        word_before_cursor = document.get_word_before_cursor(WORD=True)
        
        if not word_before_cursor:
            return

        # 2. 첨부된 모든 경로 중에서, 현재 입력한 단어로 '시작하는' 경로를 모두 찾습니다.
        for path in self.attached_paths:
            if path.startswith(word_before_cursor):
                # 3. 찾은 경로를 Completion 객체로 만들어 반환합니다.
                #    start_position=-len(word_before_cursor) 는
                #    '현재 입력 중인 단어 전체를 이 completion으로 교체하라'는 의미입니다.
                #    이것이 이 문제 해결의 핵심입니다.
                yield Completion(
                    path,
                    start_position=-len(word_before_cursor),
                    display_meta="[첨부됨]"
                )

class ConditionalCompleter(Completer):
    """
    모든 문제를 해결한, 최종 버전의 '지능형' 자동 완성기.
    /mode <mode> [-s <session>] 문법까지 지원합니다.
    """
    def __init__(self, command_completer: Completer, file_completer: Completer):
        self.command_completer = command_completer
        self.file_completer = file_completer
        self.attached_completer: Optional[Completer] = None
        self.config: Optional[ConfigManager] = None 
        self.theme_manager: Optional[ThemeManager] = None

        self.modes_with_meta = [
            Completion("dev", display_meta="개발/기술 지원 전문가"),
            Completion("general", display_meta="친절하고 박식한 어시스턴트"),
            Completion("teacher", display_meta="코드 구조 분석 아키텍트"),
        ]
        self.mode_completer = WordCompleter(
            words=[c.text for c in self.modes_with_meta], 
            ignore_case=True,
            meta_dict={c.text: c.display_meta for c in self.modes_with_meta}
        )
        self.session_option_completer = WordCompleter(["-s", "--session"], ignore_case=True)
    
    def update_attached_file_completer(self, attached_filenames: List[str], base_dir: Path):
        if attached_filenames:
            try:
                # 1. 자동완성 후보가 될 상대 경로 리스트를 생성합니다.
                relative_paths = [
                    str(Path(p).relative_to(base_dir)) for p in attached_filenames
                ]
                
                # 2. WordCompleter 대신, 우리가 만든 AttachedFileCompleter를 사용합니다.
                self.attached_completer = AttachedFileCompleter(relative_paths)

            except ValueError:
                # BASE_DIR 외부 경로 등 예외 발생 시, 전체 경로를 사용
                self.attached_completer = AttachedFileCompleter(attached_filenames)
            '''
            relative_paths = [
                str(Path(p).relative_to(BASE_DIR)) for p in attached_filenames
            ]
            self.attached_completer =  FuzzyCompleter(
                WordCompleter(relative_paths, ignore_case=True)
            )
            '''
            #self.attached_completer =  FuzzyCompleter(WordCompleter(attached_filenames, ignore_case=True))
        else:
            self.attached_completer = None

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped_text = text.lstrip()
        

        # mode 선택

        if stripped_text.startswith('/theme'):
            words = stripped_text.split()
            # "/theme" 또는 "/theme v" 처럼 테마명 입력 중
            if len(words) <= 1 or (len(words) == 2 and not text.endswith(' ')):
                # 테마 목록 자동완성
                if self.theme_manager:
                    theme_names = self.theme_manager.get_available_themes()
                    theme_completer = FuzzyCompleter(
                        WordCompleter(theme_names, ignore_case=True, 
                                    meta_dict={name: "코드 하이라이트 테마" for name in theme_names})
                    )
                    yield from theme_completer.get_completions(document, complete_event)
                    return
                
        if stripped_text.startswith('/session'):
            words = stripped_text.split()
            # "/session" 또는 "/session <pa" 처럼 세션명 입력 중
            if len(words) <= 1 or (len(words) == 2 and not text.endswith(' ')):
                if self.config:
                    session_names = self.config.get_session_names(include_backups=True,exclude_current=getattr(self.app,"current_session_name",None))
                    session_completer = FuzzyCompleter(
                        WordCompleter(session_names, ignore_case=True)
                    )
                    yield from session_completer.get_completions(document, complete_event)
                    return
            
        if stripped_text.startswith('/mode'):
            words = stripped_text.split()

            # "/mode"만 있거나, "/mode d" 처럼 두 번째 단어 입력 중일 때
            if len(words) < 2 or (len(words) == 2 and words[1] == document.get_word_before_cursor(WORD=True)):
                yield from self.mode_completer.get_completions(document, complete_event)
                return

            # "/mode dev"가 입력되었고, 세 번째 단어("-s")를 입력할 차례일 때
            # IndexError 방지: len(words) >= 2 인 것이 확실한 상황
            if len(words) == 2 and words[1] in ["dev", "general", "teacher"] and text.endswith(" "):
                yield from self.session_option_completer.get_completions(document, complete_event)
                return

            # 위의 어떤 경우에도 해당하지 않으면, 기본적으로 모드 완성기를 보여줌
            yield from self.mode_completer.get_completions(document, complete_event)
            return

        # 경우 1: 경로 완성이 필요한 경우
        if stripped_text.startswith('/files '):
            yield from self.file_completer.get_completions(document, complete_event)

        # 경우 2: 명령어 완성이 필요한 경우
        elif stripped_text.startswith('/') and ' ' not in stripped_text:
            yield from self.command_completer.get_completions(document, complete_event)

        # 경우 3: 그 외 (일반 질문 시 '첨부 파일 이름' 완성 시도)
        else:
            word = document.get_word_before_cursor(WORD=True)
            if word and self.attached_completer:
                yield from self.attached_completer.get_completions(document, complete_event)
            else:
                yield from []
