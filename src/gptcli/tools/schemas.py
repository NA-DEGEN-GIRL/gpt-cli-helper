# src/gptcli/tools/schemas.py
"""
OpenAI Function Calling 형식의 Tool 스키마 정의.

각 Tool은 다음 형식을 따릅니다:
{
    "type": "function",
    "function": {
        "name": "ToolName",
        "description": "Tool 설명",
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }
}
"""
from typing import Any, Dict, List, Optional

# ============================================================================
# Tool 스키마 정의
# ============================================================================

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    # ─────────────────────────────────────────────
    # Read: 파일 읽기
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": (
                "파일 내용을 읽습니다. 라인 번호와 함께 출력됩니다 (cat -n 형식). "
                "offset과 limit으로 특정 범위만 읽을 수 있습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "읽을 파일의 절대 또는 상대 경로"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "시작 라인 번호 (1부터 시작, 기본값: 1)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "읽을 최대 라인 수 (기본값: 2000)"
                    }
                },
                "required": ["file_path"]
            }
        }
    },

    # ─────────────────────────────────────────────
    # Write: 파일 쓰기 (전체 덮어쓰기)
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": (
                "파일에 내용을 씁니다. 기존 파일이 있으면 덮어씁니다. "
                "부모 디렉터리가 없으면 자동 생성됩니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "쓸 파일의 절대 또는 상대 경로"
                    },
                    "content": {
                        "type": "string",
                        "description": "파일에 쓸 내용"
                    }
                },
                "required": ["file_path", "content"]
            }
        }
    },

    # ─────────────────────────────────────────────
    # Edit: 문자열 치환 기반 편집
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": (
                "파일에서 old_string을 new_string으로 치환합니다. "
                "old_string은 파일에서 유일해야 합니다 (여러 번 매칭되면 오류). "
                "정확한 들여쓰기를 유지해야 합니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "편집할 파일의 절대 또는 상대 경로"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "찾아서 바꿀 문자열 (유일해야 함)"
                    },
                    "new_string": {
                        "type": "string",
                        "description": "대체할 새 문자열"
                    }
                },
                "required": ["file_path", "old_string", "new_string"]
            }
        }
    },

    # ─────────────────────────────────────────────
    # Bash: 셸 명령 실행
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "셸 명령을 실행합니다. stdout과 stderr를 반환합니다. "
                "위험한 명령(rm -rf / 등)은 차단될 수 있습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "실행할 셸 명령"
                    },
                    "description": {
                        "type": "string",
                        "description": "명령에 대한 간단한 설명 (5-10단어)"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "타임아웃 (초, 기본값: 120)"
                    }
                },
                "required": ["command"]
            }
        }
    },

    # ─────────────────────────────────────────────
    # Grep: 패턴 검색
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": (
                "파일에서 정규식 패턴을 검색합니다. ripgrep 사용 (없으면 grep 폴백). "
                "output_mode로 출력 형식을 지정할 수 있습니다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "검색할 정규식 패턴"
                    },
                    "path": {
                        "type": "string",
                        "description": "검색할 파일 또는 디렉터리 경로 (기본값: 현재 디렉터리)"
                    },
                    "glob": {
                        "type": "string",
                        "description": "파일 필터 glob 패턴 (예: '*.py')"
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["files_with_matches", "content", "count"],
                        "description": "출력 모드: files_with_matches(기본), content, count"
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "대소문자 무시 여부 (기본값: false)"
                    }
                },
                "required": ["pattern"]
            }
        }
    },

    # ─────────────────────────────────────────────
    # Glob: 파일 패턴 매칭
    # ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": (
                "glob 패턴으로 파일을 검색합니다. "
                "결과는 수정 시간 기준으로 정렬됩니다 (최신 순)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 패턴 (예: '**/*.py', 'src/**/*.ts')"
                    },
                    "path": {
                        "type": "string",
                        "description": "검색 시작 디렉터리 (기본값: 현재 디렉터리)"
                    }
                },
                "required": ["pattern"]
            }
        }
    }
]


def get_tool_schema(name: str) -> Optional[Dict[str, Any]]:
    """이름으로 특정 Tool 스키마를 가져옵니다."""
    for schema in TOOL_SCHEMAS:
        if schema["function"]["name"] == name:
            return schema
    return None


def get_tool_names() -> List[str]:
    """모든 Tool 이름 목록을 반환합니다."""
    return [schema["function"]["name"] for schema in TOOL_SCHEMAS]


def estimate_tool_schemas_tokens() -> int:
    """
    Tool 스키마의 대략적인 토큰 수를 추정합니다.

    OpenAI/Anthropic API는 tool 스키마를 JSON으로 직렬화하여 전송하므로,
    이 토큰 수가 컨텍스트에서 차지하는 공간을 미리 계산해야 합니다.

    Returns:
        추정 토큰 수 (약 3,500 토큰 for 6 tools)
    """
    import json
    # Tool 스키마를 JSON으로 직렬화
    schema_json = json.dumps(TOOL_SCHEMAS, ensure_ascii=False)
    # 대략적인 토큰 추정: 4글자 ≈ 1토큰 (보수적으로 3글자로 계산)
    estimated_tokens = len(schema_json) // 3
    # 안전 마진 20% 추가
    return int(estimated_tokens * 1.2)
