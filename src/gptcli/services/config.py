# src/gptcli/services/config.py
from __future__ import annotations
import json, base64, time, shutil, os, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Set
from pathspec import PathSpec
import src.constants as constants
from src.gptcli.utils.common import Utils

class ConfigManager:
    """
    설정 파일, 경로, 세션, 즐겨찾기 등 모든 파일 시스템 I/O를 관리하는 클래스.
    이 클래스의 인스턴스는 애플리케이션 설정의 단일 진실 공급원이 됩니다.
    """
    def __init__(self, base_dir: Optional[Path] = None, config_dir: Optional[Path] = None):
        """
        모든 경로를 초기화하고 필요한 디렉터리와 기본 설정 파일을 생성합니다.

        Args:
            base_dir (Optional[Path]): 프로젝트의 루트 디렉터리. 기본값은 현재 작업 디렉터리.
            config_dir (Optional[Path]): ai_models.txt 등이 위치한 설정 디렉터리.
                                         기본값은 '~/codes/gpt_cli'.
        """
        # --- 경로 정의 ---
        self.BASE_DIR = Path(base_dir) if base_dir else Path.cwd()
        self.CONFIG_DIR = (config_dir or Path.home() / "codes" / "gpt_cli").resolve()
        
        self.SESSION_DIR = self.BASE_DIR / ".gpt_sessions"
        self.SESSION_BACKUP_DIR = self.SESSION_DIR / "backups"

        self.MD_OUTPUT_DIR = self.BASE_DIR / "gpt_markdowns"
        self.CODE_OUTPUT_DIR = self.BASE_DIR / "gpt_codes"

        # --- 파일 경로 정의 ---
        self.PROMPT_HISTORY_FILE = self.BASE_DIR / ".gpt_prompt_history.txt"
        self.FAVORITES_FILE = self.BASE_DIR / ".gpt_favorites.json"
        self.IGNORE_FILE = self.BASE_DIR / ".gptignore"
        self.MODELS_FILE = self.BASE_DIR / "ai_models.txt"
        self.DEFAULT_IGNORE_FILE = self.BASE_DIR / ".gptignore"
        
        # 현재 세션 포인터 파일(.gpt_session)
        self.CURRENT_SESSION_FILE = self.BASE_DIR / ".gpt_session"

        # --- 자동 초기화 ---
        self._initialize_directories()
        self._create_default_ignore_file_if_not_exists()
        self._create_default_ai_models_files_if_not_exists()

    def _initialize_directories(self):
        """애플리케이션에 필요한 모든 디렉터리가 존재하는지 확인하고 없으면 생성합니다."""
        dirs_to_create = [
            self.SESSION_DIR,
            self.SESSION_BACKUP_DIR,
            self.MD_OUTPUT_DIR,
            self.CODE_OUTPUT_DIR
        ]
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)
    
    def _create_default_ai_models_files_if_not_exists(self):
        if self.MODELS_FILE.exists():
            return
        default_ai_models = """
anthropic/claude-opus-4.1 200000
anthropic/claude-sonnet-4 200000
google/gemini-2.5-flash 1048576
google/gemini-2.5-pro 1048576
openai/gpt-5 400000
openai/gpt-5-mini 400000
qwen/qwen3-235b-a22b-2507 262144
qwen/qwen3-235b-a22b-thinking-2507 262144
qwen/qwen3-coder 262144
x-ai/grok-4 256000
"""
        try:
            self.MODELS_FILE.write_text(default_ai_models.strip(), encoding="utf-8")
        except Exception as e:
            print(f"[경고] ai_models.txt 파일을 생성하지 못했습니다: {e}")

    def _create_default_ignore_file_if_not_exists(self):
        """.gptignore 파일이 없으면, 합리적인 기본값으로 생성합니다."""
        if self.DEFAULT_IGNORE_FILE.exists():
            return

        default_patterns = """
# 이 파일은 모든 프로젝트에 공통으로 적용되는 전역 gptcli 무시 규칙입니다.
# 사용자가 자유롭게 수정할 수 있습니다.

# --- 일반적인 무시 목록 ---
.DS_Store
.env
*.pyc
*.swp

# --- Python 관련 ---
__pycache__/
.venv/
venv/
env/

# --- 버전 관리 및 IDE 설정 ---
.git/
.vscode/
.idea/

# --- 이 앱 자체의 파일들 ---
.gpt_sessions/
.gpt_prompt_history.txt
.gpt_favorites.json
.gptignore
__init__.py
"""
        try:
            self.DEFAULT_IGNORE_FILE.write_text(default_patterns.strip(), encoding="utf-8")
        except Exception as e:
            print(f"[경고] 전역 기본 무시 파일을 생성하지 못했습니다: {e}")

    def _backup_root_dir(self) -> Path:
        """
        세션 백업 루트(.gpt_sessions/backups) 경로.
        프로젝트에 SESSION_BACKUP_DIR 속성이 있으면 그것을, 없으면 기본값을 사용합니다.
        """
        root = getattr(self, "SESSION_BACKUP_DIR", None)
        if root is None:
            root = Path(self.SESSION_DIR) / "backups"
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        return root
    
    # --- Current Session I/O ---
    def load_current_session_name(self) -> Optional[str]:
        """
        .gpt_session 파일에서 현재 세션명을 읽어 반환합니다.
        파일이 없거나 읽을 수 없으면 None.
        """
        try:
            if self.CURRENT_SESSION_FILE.exists():
                name = self.CURRENT_SESSION_FILE.read_text(encoding="utf-8").strip()
                return name or None
        except Exception:
            return None
        return None

    def save_current_session_name(self, name: str) -> None:
        """
        .gpt_session 파일에 현재 세션명을 저장합니다. 실패해도 흐름을 막지 않습니다.
        """
        try:
            self.CURRENT_SESSION_FILE.write_text(str(name).strip(), encoding="utf-8")
        except Exception:
            pass

    # --- Session Management ---
    def get_session_path(self, name: str) -> Path:
        """세션 이름에 해당하는 파일 경로를 반환합니다."""
        return self.SESSION_DIR / f"session_{name}.json"

    def load_session(self, name: str) -> Dict[str, Any]:
        """지정된 이름의 세션 데이터를 로드합니다."""
        default_data = {"messages": [], "model": "openai/gpt-5", "context_length": 200000, "usage_history": [], "mode":"dev"}
        path = self.get_session_path(name)
        data = Utils._load_json(path, default_data)
        
        # 레거시 형식 (message 리스트만 있던 경우) 호환
        if isinstance(data, list):
            return {"messages": data, **{k:v for k,v in default_data.items() if k != 'messages'}}
        
        # 키가 없는 경우 기본값으로 채워줌
        for key, value in default_data.items():
            data.setdefault(key, value)
            
        return data

    def save_session(
        self,
        name: str,
        msgs: List[Dict],
        model: str,
        context_length: int,
        usage_history: List[Dict],
        mode: Optional[str] = None,  # ← [추가]
    ) -> None:
        """세션 데이터를 파일에 저장합니다."""
        path = self.get_session_path(name)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        mode_to_save = (mode if mode is not None else existing.get("mode") or "dev")

        data = {
            "messages": msgs,
            "model": model,
            "context_length": context_length,
            "usage_history": usage_history or [],
            "mode": mode_to_save,
            "total_usage": {
                "total_prompt_tokens": sum(u.get("prompt_tokens", 0) for u in (usage_history or [])),
                "total_completion_tokens": sum(u.get("completion_tokens", 0) for u in (usage_history or [])),
                "total_tokens": sum(u.get("total_tokens", 0) for u in (usage_history or [])),
            },
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        Utils._save_json(path, data)

    def _session_name_from_backup_json(self, path: Path) -> Optional[str]:
        """
        백업 스냅샷 JSON에서 '원본 세션명'을 복원합니다.
        우선순위: backup_meta.session > name > 파일명(session_<slug>.json)
        """
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            meta = data.get("backup_meta") or {}
            name = meta.get("session") or data.get("name")
            if not name:
                # 파일명에서 복원
                stem = path.stem  # e.g. session_foo
                if stem.startswith("session_"):
                    name = stem[len("session_"):]
                else:
                    name = stem
            name = str(name).strip()
            return name or None
        except Exception:
            # JSON 파싱 실패 시 파일명 기반 폴백
            try:
                stem = path.stem
                return stem[len("session_"):] if stem.startswith("session_") else stem
            except Exception:
                return None


    def get_session_names(
        self,
        include_backups: bool = True,
        exclude_current: Optional[str] = None,
    ) -> List[str]:
        """
        세션 이름 목록을 반환합니다.
        - 라이브: .gpt_sessions/session_*.json
        - (옵션) 백업: .gpt_sessions/backups/session_*.json
        - 중복 제거: 라이브에 있으면 1회만 노출
        - 현재 세션(exclude_current)이면 목록에서 제외
        """
        names_live: Set[str] = set()
        names_backup: Set[str] = set()

        # 1) 라이브 세션 수집
        sess_dir = Path(self.SESSION_DIR)
        if sess_dir.exists():
            for f in sess_dir.glob("session_*.json"):
                if f.is_file():
                    stem = f.stem  # session_<name>
                    name = stem[len("session_"):] if stem.startswith("session_") else stem
                    name = name.strip()
                    if name:
                        names_live.add(name)

        # 2) 백업 세션 수집
        if include_backups:
            bdir = self._backup_root_dir()
            if bdir.exists():
                for bj in bdir.glob("session_*.json"):
                    if bj.is_file():
                        name = self._session_name_from_backup_json(bj)
                        if not name:
                            continue
                        # 라이브에 이미 있으면 굳이 추가하지 않음(중복 제거)
                        if name not in names_live:
                            names_backup.add(name)

        # 3) 합치고, 현재 세션 제외
        all_names: Set[str] = names_live.union(names_backup)
        if exclude_current:
            all_names.discard(exclude_current)

        return sorted(all_names)


    # --- Favorites Management ---
    
    def load_favorites(self) -> Dict[str, str]:
        """즐겨찾기 데이터를 로드합니다."""
        return Utils._load_json(self.FAVORITES_FILE, {})

    def save_favorite(self, name: str, prompt: str) -> None:
        """새로운 즐겨찾기를 저장합니다."""
        favs = self.load_favorites()
        favs[name] = prompt
        Utils._save_json(self.FAVORITES_FILE, favs)

    # --- Ignore File Management ---

    def get_ignore_spec(self) -> Optional[PathSpec]:
        """전역 및 프로젝트 .gptignore 파일을 결합하여 PathSpec 객체를 생성합니다."""
        default_patterns = []
        if self.DEFAULT_IGNORE_FILE.exists():
            default_patterns = self.DEFAULT_IGNORE_FILE.read_text('utf-8').splitlines()

        user_patterns = []
        if self.IGNORE_FILE.exists():
            user_patterns = self.IGNORE_FILE.read_text('utf-8').splitlines()
        
        # 순서를 보존하며 중복을 제거 (dict.fromkeys 트릭)
        combined_patterns = list(dict.fromkeys(default_patterns + user_patterns))
        
        final_patterns = [p.strip() for p in combined_patterns if p.strip() and not p.strip().startswith("#")]
        
        return PathSpec.from_lines("gitwildmatch", final_patterns) if final_patterns else None

    def is_ignored(self, path: Path, spec: Optional[PathSpec]) -> bool:
        """주어진 경로가 ignore spec에 의해 무시되어야 하는지 확인합니다."""
        if not spec:
            return False
        
        try:
            relative_path_str = path.relative_to(self.BASE_DIR).as_posix()
        except ValueError:
            # BASE_DIR 외부에 있는 경로는 무시 규칙의 대상이 아님
            return False

        if path.is_dir() and not relative_path_str.endswith('/'):
            relative_path_str += '/'
            
        return spec.match_file(relative_path_str)

    @staticmethod
    def read_plain_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"[파일 읽기 실패: {e}]"

    @staticmethod
    def encode_base64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    def save_code_blocks(self, blocks: Sequence[Tuple[str, str]], session_name: str, msg_id: int) -> List[Path]:
        """AI가 생성한 코드 블록을 파일로 저장합니다."""
        self.CODE_OUTPUT_DIR.mkdir(exist_ok=True)
        saved: List[Path] = []
        ext_map = {
            # 스크립팅 & 프로그래밍 언어
            "python": "py", "py": "py",
            "javascript": "js", "js": "js",
            "typescript": "ts", "ts": "ts",
            "bash": "sh", "sh": "sh", "shell": "sh",
            "java": "java",
            "c": "c",
            "cpp": "cpp", "c++": "cpp",
            "go": "go",
            "rust": "rs", "rs": "rs",
            "ruby": "rb", "rb": "rb",
            "php": "php",
            "sql": "sql",
            
            # 마크업 & 데이터 형식
            "html": "html",
            "css": "css",
            "scss": "scss",
            "json": "json",
            "xml": "xml",
            "yaml": "yml", "yml": "yml",
            "markdown": "md", "md": "md",
            
            # 기타
            "text": "txt", "plaintext": "txt",
            "diff": "diff",
        }
        
        for i, (lang, code) in enumerate(blocks, 1):
            ext = ext_map.get(lang.lower() if lang else "text", "txt")
            
            # 기본 파일 경로 생성
            base_p = self.CODE_OUTPUT_DIR / f"codeblock_{session_name}_{msg_id}_{i}.{ext}"
            p = base_p
            
            # 파일이 이미 존재하면 이름에 숫자 추가
            cnt = 1
            while p.exists():
                p = self.CODE_OUTPUT_DIR / f"codeblock_{session_name}_{msg_id}_{i}_{cnt}.{ext}"
                cnt += 1
            
            p.write_text(code, encoding="utf-8")
            saved.append(p)
        return saved
