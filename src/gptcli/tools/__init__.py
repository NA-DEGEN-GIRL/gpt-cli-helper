# src/gptcli/tools/__init__.py
"""
Claude Code 스타일 Tool 시스템 모듈.

이 모듈은 AI가 파일 읽기/쓰기/편집, 명령 실행, 검색 등의 작업을
수행할 수 있도록 하는 도구(Tool) 시스템을 제공합니다.

주요 구성요소:
- schemas: OpenAI Function Calling 형식의 Tool 스키마 정의
- executor: 각 Tool의 실제 실행 로직
- registry: 스키마와 실행기의 매핑 관리
- permission: 권한/신뢰 수준 관리
"""

from .schemas import TOOL_SCHEMAS, get_tool_schema
from .executor import ToolExecutor
from .registry import ToolRegistry
from .permission import TrustLevel, PermissionManager

__all__ = [
    "TOOL_SCHEMAS",
    "get_tool_schema",
    "ToolExecutor",
    "ToolRegistry",
    "TrustLevel",
    "PermissionManager",
]
