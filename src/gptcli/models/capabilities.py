# src/gptcli/models/capabilities.py
"""
모델 기능 확인 유틸리티.

OpenRouter API에서 모델의 지원 기능(tool calling 등)을 확인합니다.
"""
from __future__ import annotations

import requests
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set

import src.constants as constants


# 캐시된 모델 정보 (메모리 캐시)
_MODEL_CACHE: Dict[str, Dict[str, Any]] = {}


def _fetch_models_if_needed() -> bool:
    """필요한 경우 모델 목록을 가져옵니다."""
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return True

    try:
        response = requests.get(constants.API_URL, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        _MODEL_CACHE = {m['id']: m for m in data}
        return True
    except Exception:
        return False


def get_model_info(model_id: str) -> Optional[Dict[str, Any]]:
    """
    특정 모델의 정보를 가져옵니다.

    Args:
        model_id: 모델 ID (예: "anthropic/claude-opus-4")

    Returns:
        모델 정보 딕셔너리 또는 None
    """
    # :online 접미사 제거
    clean_id = model_id.replace(":online", "")

    _fetch_models_if_needed()
    return _MODEL_CACHE.get(clean_id)


def supports_tools(model_id: str) -> bool:
    """
    모델이 function calling / tool use를 지원하는지 확인합니다.

    OpenRouter API의 supported_parameters에 'tools'가 포함되어 있는지 확인합니다.

    Args:
        model_id: 모델 ID

    Returns:
        True if model supports tools, False otherwise
    """
    model_info = get_model_info(model_id)
    if not model_info:
        # 정보를 가져올 수 없으면 일단 지원한다고 가정 (사용자가 시도해볼 수 있도록)
        return True

    supported_params = model_info.get("supported_parameters", [])
    return "tools" in supported_params


def get_supported_parameters(model_id: str) -> List[str]:
    """
    모델이 지원하는 파라미터 목록을 반환합니다.

    Args:
        model_id: 모델 ID

    Returns:
        지원 파라미터 목록
    """
    model_info = get_model_info(model_id)
    if not model_info:
        return []

    return model_info.get("supported_parameters", [])


def get_model_context_length(model_id: str) -> int:
    """
    모델의 컨텍스트 길이를 반환합니다.

    Args:
        model_id: 모델 ID

    Returns:
        컨텍스트 길이 (기본값: constants.DEFAULT_CONTEXT_LENGTH)
    """
    model_info = get_model_info(model_id)
    if not model_info:
        return constants.DEFAULT_CONTEXT_LENGTH

    return model_info.get("context_length", constants.DEFAULT_CONTEXT_LENGTH)


def supports_reasoning(model_id: str) -> bool:
    """
    모델이 reasoning (extended thinking) 파라미터를 지원하는지 확인합니다.

    현재 reasoning을 지원하는 모델:
    - Anthropic Claude 모델 (claude-3.5-sonnet, claude-opus-4, claude-sonnet-4 등)

    지원하지 않는 모델:
    - OpenAI GPT 모델 (gpt-4, gpt-5 등)
    - Google Gemini 모델
    - 기타 모델

    Args:
        model_id: 모델 ID (예: "anthropic/claude-opus-4")

    Returns:
        True if model supports reasoning parameter
    """
    model_lower = model_id.lower()

    # Anthropic Claude 모델만 reasoning 지원
    # OpenRouter에서 anthropic/ 접두사로 시작하는 모델
    if "anthropic/" in model_lower or "claude" in model_lower:
        return True

    # OpenAI, Google, 기타 모델은 지원하지 않음
    return False


def get_models_with_tool_support() -> List[str]:
    """
    Tool calling을 지원하는 모든 모델 ID 목록을 반환합니다.

    Returns:
        Tool 지원 모델 ID 목록
    """
    _fetch_models_if_needed()

    return [
        model_id
        for model_id, info in _MODEL_CACHE.items()
        if "tools" in info.get("supported_parameters", [])
    ]


def clear_cache() -> None:
    """모델 캐시를 초기화합니다."""
    global _MODEL_CACHE
    _MODEL_CACHE = {}
