# src/gptcli/services/theme.py
from __future__ import annotations
import urwid
from typing import Dict, Tuple, List, Optional
from rich.theme import Theme
import src.constants as constants
        
class ThemeManager:
    """
    Urwid과 Rich의 테마, 팔레트, 문법 하이라이팅 관련 로직을 전담하는 클래스.
    """
    # --- 클래스 레벨 상수: 테마 데이터베이스 ---
    _FG_THEMES = constants.THEME_DATA

    _COLOR_ALIASES = constants.COLOR_ALIASES

    _TRUECOLOR_FALLBACKS = constants.TRUECOLOR_FALLBACKS

    _SIGN_FG = constants.SIGN_FG
    _LNO_OLD_FG = constants.LNO_OLD_FG
    _LNO_NEW_FG = constants.LNO_NEW_FG
    _SEP_FG = constants.SEP_FG
    #_PREVIEW_BG = constants.PREVIEW_BG

    _FG_MAP_DIFF: Dict[str, Dict[str, str]] = {}

    def __init__(self, default_theme: str = 'monokai-ish'):
        self.current_theme_name: str = ""
        self.current_urwid_palette: List[Tuple] = []
        self.set_theme(default_theme)

    @classmethod
    def get_available_themes(cls) -> List[str]:
        """사용 가능한 모든 테마의 이름 목록을 반환합니다."""
        return sorted(cls._FG_THEMES.keys())

    def set_theme(self, theme_name: str):
        if theme_name not in self._FG_THEMES:
            raise KeyError(f"알 수 없는 테마: '{theme_name}'.")
        self.current_theme_name = theme_name
        self.current_urwid_palette = self._generate_urwid_palette()
        theme_map = self._FG_THEMES[theme_name]
        self._FG_MAP_DIFF = {'add': theme_map, 'del': theme_map, 'ctx': theme_map}

    def set_diff_theme(self, kind: str, theme_name: str):
        if kind not in ('add','del','ctx'):
            raise ValueError("Invalid diff kind")
        if theme_name not in self._FG_THEMES:
            raise KeyError("Unknown theme")
        # kind 하나만 변경
        self._FG_MAP_DIFF[kind] = self._FG_THEMES[theme_name]

    def set_global_theme(self, name: str):
        """
        프리뷰(syn_*) 팔레트 + diff 전경색 맵을 모두 동일 테마로 통일.
        """
        self.set_theme(name)

    def _demote_truecolor_to_256(self, spec: str, default: str = 'white') -> str:
        """
        HEX → 256색 근사로 강등
        """
        if spec is None or spec == '':
            return spec or default
        color, attrs = self._split_color_attrs(spec)
        if color and color.startswith('#'):
            color = self._TRUECOLOR_FALLBACKS.get(color.lower(), default)
        out = color or default
        if attrs:
            out += ',' + attrs
        return out
    
    def _normalize_color_spec(self, spec: str) -> str:
        """
        - COLOR_ALIASES 적용
        - 대소문자 정규화
        """
        if spec is None or spec == '':
            return spec
        color, attrs = self._split_color_attrs(spec)
        if not color:
            return spec
        key = color.lower()
        color = self._COLOR_ALIASES.get(key, color)  # 별칭이면 HEX로 치환
        out = color
        if attrs:
            out += ',' + attrs
        return out

    def _mk_attr(self, fg: str, bg: str, fb_bg: str = 'default') -> urwid.AttrSpec:
        """색상 문자열로 urwid.AttrSpec 객체를 생성합니다."""
        fg_norm = self._normalize_color_spec(fg) if fg else fg
        bg_norm = self._normalize_color_spec(bg) if bg else bg
        try:
            return urwid.AttrSpec(fg_norm, bg_norm)
        except Exception:
            # 폴백
            fg_f = self._demote_truecolor_to_256(fg_norm, default='white') if fg_norm else fg_norm
            bg_f = self._demote_truecolor_to_256(bg_norm, default=fb_bg) if bg_norm else fb_bg
        try:
            return urwid.AttrSpec(fg_f, bg_f)
        except Exception:
            return urwid.AttrSpec('white', fb_bg)

    def _bg_for_kind(self, kind: str) -> tuple[str, str]:
        if kind == 'add':
            return (constants.DIFF_ADD_BG, constants.DIFF_ADD_BG_FALLBACK)
        if kind == 'del':
            return (constants.DIFF_DEL_BG, constants.DIFF_DEL_BG_FALLBACK)
        return (constants.DIFF_CTX_BG, constants.DIFF_CTX_BG_FALLBACK)

    def get_fg_map_for_diff(self, kind: str) -> Dict[str, str]:
        if not self._FG_MAP_DIFF:
            base = self._FG_THEMES.get(self.current_theme_name) or self._FG_THEMES.get('monokai-ish')
            self._FG_MAP_DIFF = {'add': base, 'del': base, 'ctx': base}
        return self._FG_MAP_DIFF.get(kind, self._FG_THEMES.get(self.current_theme_name, {}))
    
    @staticmethod
    def _simplify_token_type(tt) -> str:
        # pygments.token을 함수 내부에서 import하여 클래스 로드 시점 의존성 제거
        from pygments.token import (Keyword, String, Number, Comment, Name, Operator, Punctuation, Text, Whitespace)
        # Docstring을 주석으로 처리 (Pygments가 이미 정확히 분류함)
        if tt in String.Doc or tt == String.Doc:
            return 'doc'
        # 멀티라인 문자열도 주석으로 처리하고 싶다면
        if tt == String.Double.Triple or tt == String.Single.Triple:
            return 'com'
        # 일반 주석
        if tt in Comment:
            return 'com'
        # 나머지 문자열은 str로
        if tt in String:
            return 'str'
        # 키워드
        if tt in Keyword or tt in Keyword.Namespace or tt in Keyword.Declaration:
            return 'kw'
        # 숫자
        if tt in Number:
            return 'num'
        # 이름/식별자
        if tt in Name.Function:
            return 'func'
        if tt in Name.Class:
            return 'cls'
        if tt in Name:
            return 'name'
        # 연산자/구두점
        if tt in Operator:
            return 'op'
        if tt in Punctuation:
            return 'punc'
        # 공백/기타
        if tt in (Text, Whitespace):
            return 'text'
        return 'text'

    def get_urwid_palette(self) -> List[Tuple]:
        """현재 테마에 맞는 Urwid 팔레트를 반환합니다."""
        return self.current_urwid_palette

    def get_rich_theme(self) -> Theme:
        """현재 테마에 맞는 Rich 테마 객체를 반환합니다."""
        return Theme({
            "markdown.h1": "bold bright_white",
            "markdown.h2": "bold bright_white",
            "markdown.h3": "bold bright_white",
            "markdown.list": "cyan",
            "markdown.block_quote": "italic #8b949e",
            "markdown.code": "bold white on #484f58",
            "markdown.hr": "yellow",
            "markdown.link": "underline bright_white"
        })

    def apply_to_urwid_loop(self, loop: Optional[urwid.MainLoop]):
        """실행 중인 Urwid MainLoop에 현재 팔레트를 즉시 적용합니다."""
        if not loop:
            return
        try:
            loop.screen.register_palette(self.current_urwid_palette)
            loop.draw_screen()
        except Exception:
            pass

    def _generate_urwid_palette(self) -> List[Tuple]:
        """현재 테마를 기반으로 Urwid 팔레트 목록을 동적으로 생성합니다."""
        theme = self._FG_THEMES.get(self.current_theme_name, self._FG_THEMES['monokai'])
        
        # 팔레트 구성
        palette = [
            ('key', 'yellow', 'black'),
            ('info', 'dark gray', 'black'),
            ('myfocus', 'black', 'light gray'),
            ('info_bg', '', 'dark gray'), 
            ('header', 'white', 'black'),
            ('preview', 'default', constants.PREVIEW_BG),
            ('preview_border', 'dark gray', constants.PREVIEW_BG),
            ('syn_lno', 'dark gray', constants.PREVIEW_BG),

            # ▼ restore 전용 가독성 개선 팔레트
            ('list_row', 'default', 'default'),
            ('list_focus', 'default', 'dark gray'),
            ('muted', 'dark gray', 'default'),
            ('row_title', 'white,bold', 'default'),
            ('badge', 'black', 'dark cyan'),
            ('badge_focus', 'white', 'dark blue'),

            ('muted_focus', 'light gray', 'dark gray'),
            ('row_title_focus', 'white,bold', 'dark gray'),
        ]
        
        # 프리뷰 문법 하이라이팅 (syn_*)
        syn_keys = ['text', 'kw', 'str', 'num', 'com', 'doc', 'name', 'func', 'cls', 'op', 'punc']
        for key in syn_keys:
            fg = self._color_for_palette(theme.get(key, 'white'))
            palette.append((f'syn_{key}', fg, constants.PREVIEW_BG))
        
        # Diff 문법 하이라이팅 (diff_code_*)
        for key in syn_keys:
            fg = self._color_for_palette(theme.get(key, 'white'))
            palette.append((f'diff_code_{key}', fg, 'default'))

        # Diff 고정 색상
        palette.extend([
            ('diff_file_old', 'light red,bold', 'black'),
            ('diff_file_new', 'light green,bold', 'black'),
            ('diff_hunk', 'yellow', 'black'),
            ('diff_meta', 'dark gray', 'black'),
            ('diff_lno', 'dark gray', 'default'),
            ('diff_add_bg', 'white', 'dark green'),
            ('diff_del_bg', 'white', 'dark red'),
        ])

        return palette

    # --- Private Helper Methods (기존 전역 함수들을 클래스 메서드로 변환) ---
    @staticmethod
    def _split_color_attrs(spec: str) -> Tuple[str, str]:
        """
        'light cyan,bold' → ('light cyan', 'bold')
        '#fffacd' → ('#fffacd', '')
        'default' → ('default', '')
        """
        if spec is None: return None, ''
        s = str(spec).strip()
        if not s: return '', ''
        parts = [p.strip() for p in s.split(',') if p.strip()]
        color = parts[0] if parts else ''
        attrs = ','.join(parts[1:]) if len(parts) > 1 else ''
        return color, attrs

    def _color_for_palette(self, spec: str, default: str = 'white') -> str:
        if not spec: return default
        color, attrs = self._split_color_attrs(spec)
        if color:
            color_lower = color.lower()
            color = self._COLOR_ALIASES.get(color_lower, color)
            if color.startswith('#'):
                color = self._TRUECOLOR_FALLBACKS.get(color.lower(), default)
        out = color or default
        if attrs: out += ',' + attrs
        return out
