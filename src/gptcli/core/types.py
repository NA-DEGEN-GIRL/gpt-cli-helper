# src/gptcli/core/types.py
from __future__ import annotations
from typing import Any, Dict, List, Protocol, TypedDict, runtime_checkable

# ─────────────────────────────────────────────────────────────
# Chat API 호환 콘텐츠 파트(텍스트/이미지/PDF 등)를 위한 TypedDict 정의
# ─────────────────────────────────────────────────────────────

class ContentText(TypedDict, total=False):
    type: str          # "text"
    text: str

class ContentImageUrl(TypedDict, total=False):
    type: str          # "image_url"
    image_url: Dict[str, Any]  # {"url": "data:image/...;base64,...", "detail": "auto", ...}

class ContentFile(TypedDict, total=False):
    type: str          # "file"
    file: Dict[str, Any]       # {"filename": "...", "file_data": "data:application/pdf;base64,..."}

ContentPart = ContentText | ContentImageUrl | ContentFile

class ChatMessage(TypedDict, total=False):
    role: str                              # "user" | "assistant" | "system"
    content: str | List[ContentPart]       # OpenAI 스타일