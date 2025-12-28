# src/gptcli/services/summarization.py
"""
자동 요약 기반 컨텍스트 압축 서비스.

Claude Code 스타일의 "unlimited context through automatic summarization" 구현:
1. 컨텍스트 사용률이 임계값(80%) 초과 시 자동 요약 트리거
2. 오래된 대화를 요약으로 대체하여 핵심 정보 보존
3. 요약본도 너무 길어지면 재요약 가능 (최대 3레벨)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

import src.constants as constants

if TYPE_CHECKING:
    from src.gptcli.services.tokens import TokenEstimator
    from src.gptcli.services.ai_stream import AIStreamParser


@dataclass
class SummaryMetadata:
    """요약 메타데이터"""
    created_at: str                      # 요약 생성 시간
    summarized_message_count: int        # 요약된 메시지 수
    summarized_token_count: int          # 요약 전 토큰 수
    summary_token_count: int             # 요약 후 토큰 수
    compression_ratio: float             # 압축률 (summary/original)
    model_used: str                      # 요약에 사용된 모델
    summary_level: int = 1               # 요약 레벨 (재요약 시 증가)


@dataclass
class SummaryMessage:
    """
    요약 메시지 구조.
    일반 메시지와 구분하기 위한 특별한 구조체.
    """
    role: str = "assistant"
    content: str = ""
    is_summary: bool = True
    metadata: Optional[SummaryMetadata] = None

    def to_dict(self) -> Dict[str, Any]:
        """API 전송 및 저장용 딕셔너리 변환"""
        base = {"role": self.role, "content": self.content}
        if self.is_summary:
            base["_summary_meta"] = {
                "is_summary": True,
                "metadata": asdict(self.metadata) if self.metadata else None
            }
        return base

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional['SummaryMessage']:
        """딕셔너리에서 요약 메시지 복원"""
        meta = data.get("_summary_meta")
        if not meta or not meta.get("is_summary"):
            return None
        metadata = None
        if meta.get("metadata"):
            metadata = SummaryMetadata(**meta["metadata"])
        return cls(
            role=data.get("role", "assistant"),
            content=data.get("content", ""),
            is_summary=True,
            metadata=metadata
        )


class SummarizationService:
    """
    자동 요약 서비스.

    주요 기능:
    1. check_and_summarize(): 컨텍스트 임계값 확인 및 자동 요약
    2. summarize_messages(): 메시지 목록을 요약으로 변환
    3. manual_summarize(): 수동 요약 트리거 (/summarize 명령용)
    """

    # 요약 프롬프트 템플릿
    SUMMARY_SYSTEM_PROMPT = """당신은 대화 내용을 정확하고 간결하게 요약하는 전문가입니다.

주어진 대화 내용을 다음 기준에 따라 요약해주세요:

**요약 원칙:**
1. 핵심 정보만 보존: 구체적인 코드 변경사항, 결정된 사항, 중요한 컨텍스트
2. 시간순 구조 유지: 대화 흐름이 파악되도록
3. 코드/파일 정보 보존: 언급된 파일명, 함수명, 변수명 등은 반드시 유지
4. 불필요한 인사말, 반복, 설명 제거
5. 사용자의 원래 요청과 AI의 최종 응답의 핵심만 추출

**출력 형식:**
## 주요 논의 사항
- (핵심 포인트 1)
- (핵심 포인트 2)
...

## 결정/변경된 사항
- (구체적 결정사항이나 코드 변경)

## 중요 컨텍스트
- (이후 대화에 필요한 배경 정보)

위 형식에 맞춰 간결하게 요약하세요. 총 길이는 원본의 20-30% 이하를 목표로 합니다."""

    RESUMMARIZE_PROMPT = """기존 요약과 새로운 대화 내용을 통합하여 더 간결한 요약을 생성하세요.

**주의사항:**
- 기존 요약의 핵심 정보는 반드시 유지
- 중복되는 내용은 제거
- 최신 정보를 우선시
- 총 길이는 원본의 50% 이하로 압축

위의 출력 형식을 따르세요."""

    def __init__(
        self,
        console: Console,
        token_estimator: 'TokenEstimator',
        parser: 'AIStreamParser',
        config: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            console: Rich Console 인스턴스
            token_estimator: 토큰 추정기
            parser: API 호출용 파서
            config: 설정 오버라이드 (threshold, keep_recent 등)
        """
        self.console = console
        self.token_estimator = token_estimator
        self.parser = parser

        # 설정값 로드
        config = config or {}
        self.threshold = config.get("threshold", constants.SUMMARIZATION_THRESHOLD)
        self.min_messages = config.get("min_messages", constants.MIN_MESSAGES_TO_SUMMARIZE)
        self.keep_recent = config.get("keep_recent", constants.KEEP_RECENT_MESSAGES)
        self.max_levels = config.get("max_levels", constants.MAX_SUMMARY_LEVELS)

        # 요약 히스토리 (세션별)
        self.summary_history: List[SummaryMetadata] = []

    def calculate_context_usage(
        self,
        messages: List[Dict[str, Any]],
        model_context_limit: int,
        system_prompt_tokens: int,
        reserve_for_completion: int,
        tools_tokens: int = 0
    ) -> Tuple[int, int, float]:
        """
        현재 컨텍스트 사용량을 계산합니다.

        Returns:
            (used_tokens, available_tokens, usage_ratio)
        """
        from src.gptcli.utils.common import Utils

        used = sum(
            Utils._count_message_tokens_with_estimator(m, self.token_estimator)
            for m in messages
        )

        available = model_context_limit - system_prompt_tokens - reserve_for_completion - tools_tokens
        ratio = used / available if available > 0 else 1.0

        return used, available, ratio

    def should_summarize(
        self,
        messages: List[Dict[str, Any]],
        model_context_limit: int,
        system_prompt_tokens: int,
        reserve_for_completion: int,
        tools_tokens: int = 0
    ) -> Tuple[bool, float, str]:
        """
        요약이 필요한지 판단합니다.

        Returns:
            (should_summarize, current_ratio, reason)
        """
        used, available, ratio = self.calculate_context_usage(
            messages, model_context_limit, system_prompt_tokens,
            reserve_for_completion, tools_tokens
        )

        # 메시지 수 확인
        if len(messages) < self.min_messages:
            return False, ratio, f"메시지 수 부족 ({len(messages)} < {self.min_messages})"

        # 임계값 확인
        if ratio < self.threshold:
            return False, ratio, f"임계값 미달 ({ratio:.1%} < {self.threshold:.0%})"

        # 요약할 대상 확인 (최근 N개 제외)
        summarizable = len(messages) - self.keep_recent
        if summarizable < 2:
            return False, ratio, "요약할 메시지가 충분하지 않음"

        return True, ratio, f"임계값 초과 ({ratio:.1%} >= {self.threshold:.0%})"

    def _prepare_messages_for_summary(
        self,
        messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        요약할 메시지와 보존할 메시지를 분리합니다.

        Returns:
            (to_summarize, to_keep)
        """
        if len(messages) <= self.keep_recent:
            return [], messages

        split_point = len(messages) - self.keep_recent
        to_summarize = messages[:split_point]
        to_keep = messages[split_point:]

        return to_summarize, to_keep

    def _format_messages_for_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """메시지 목록을 요약 프롬프트용 텍스트로 변환"""
        lines = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 리스트 형식 content 처리 (첨부파일 포함)
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        text_parts.append("[이미지 첨부]")
                    elif part.get("type") == "file":
                        fname = part.get("file", {}).get("filename", "파일")
                        text_parts.append(f"[파일 첨부: {fname}]")
                content = "\n".join(text_parts)

            # 이미 요약된 메시지 표시
            if msg.get("_summary_meta", {}).get("is_summary"):
                lines.append(f"[메시지 {i+1}] {role.upper()} (기존 요약):\n{content}\n")
            else:
                lines.append(f"[메시지 {i+1}] {role.upper()}:\n{content}\n")

        return "\n---\n".join(lines)

    # 일부 API (Gemini 등)는 요청 본문 크기 제한이 있음
    # 청크당 최대 토큰 (안전 마진 포함)
    CHUNK_TOKEN_LIMIT: int = 25000

    def summarize_messages(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        is_resummarize: bool = False
    ) -> Optional[Tuple[str, int, int]]:
        """
        메시지 목록을 요약합니다.

        요청이 너무 크면 (413 에러 방지) 청크 분할 요약을 수행합니다.

        Args:
            messages: 요약할 메시지 목록
            model: 요약에 사용할 모델
            is_resummarize: 재요약 여부 (기존 요약 + 새 메시지)

        Returns:
            (summary_text, original_tokens, summary_tokens) 또는 None
        """
        from src.gptcli.utils.common import Utils

        if not messages:
            return None

        # 원본 토큰 수 계산
        original_tokens = sum(
            Utils._count_message_tokens_with_estimator(m, self.token_estimator)
            for m in messages
        )

        # 청크 분할 필요 여부 확인
        if original_tokens > self.CHUNK_TOKEN_LIMIT:
            self.console.print(
                f"[yellow]⚠ 요약 대상이 큽니다 ({original_tokens:,}tk). 청크 분할 요약 실행...[/yellow]",
                highlight=False
            )
            return self._chunked_summarize(messages, model, original_tokens)

        # 단일 요약 (기존 로직)
        return self._single_summarize(messages, model, is_resummarize, original_tokens)

    def _single_summarize(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        is_resummarize: bool,
        original_tokens: int
    ) -> Optional[Tuple[str, int, int]]:
        """단일 API 호출로 요약을 수행합니다."""
        # 요약 프롬프트 준비
        formatted_content = self._format_messages_for_prompt(messages)
        system_prompt = self.RESUMMARIZE_PROMPT if is_resummarize else self.SUMMARY_SYSTEM_PROMPT

        # 로딩 패널 표시
        self.console.print(
            Panel.fit(
                f"[dim]요약 중... ({len(messages)}개 메시지, ~{original_tokens:,} 토큰)[/dim]",
                title="[yellow]📝 컨텍스트 압축[/yellow]",
                border_style="yellow"
            ),
            highlight=False
        )

        try:
            # API 호출 (스트리밍 없이 간단하게)
            result = self.parser.stream_and_parse(
                system_prompt={"role": "system", "content": system_prompt},
                final_messages=[{
                    "role": "user",
                    "content": f"다음 대화를 요약해주세요:\n\n{formatted_content}"
                }],
                model=model,
                pretty_print=False,  # 패널로 간단히
                tools=None
            )

            if result is None:
                self.console.print("[red]요약 생성 실패[/red]", highlight=False)
                return None

            summary_text, _, _ = result
            summary_tokens = self.token_estimator.count_text_tokens(summary_text)

            compression = summary_tokens / original_tokens if original_tokens > 0 else 0
            self.console.print(
                f"[green]✅ 요약 완료:[/green] {original_tokens:,} → {summary_tokens:,} 토큰 "
                f"([cyan]{compression:.1%}[/cyan] 압축)",
                highlight=False
            )

            return summary_text, original_tokens, summary_tokens

        except Exception as e:
            self.console.print(f"[red]요약 중 오류 발생: {e}[/red]", highlight=False)
            return None

    def _chunked_summarize(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        original_tokens: int
    ) -> Optional[Tuple[str, int, int]]:
        """
        메시지를 청크로 분할하여 순차적으로 요약합니다.

        각 청크를 개별 요약 → 중간 요약들을 최종 통합 요약
        """
        from src.gptcli.utils.common import Utils

        # 1. 메시지를 청크로 분할
        chunks: List[List[Dict[str, Any]]] = []
        current_chunk: List[Dict[str, Any]] = []
        current_tokens = 0

        for msg in messages:
            msg_tokens = Utils._count_message_tokens_with_estimator(msg, self.token_estimator)

            # 청크 크기 초과 시 새 청크 시작
            if current_tokens + msg_tokens > self.CHUNK_TOKEN_LIMIT and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0

            current_chunk.append(msg)
            current_tokens += msg_tokens

        # 마지막 청크 추가
        if current_chunk:
            chunks.append(current_chunk)

        self.console.print(
            f"[cyan]📦 {len(chunks)}개 청크로 분할 요약 시작[/cyan]",
            highlight=False
        )

        # 2. 각 청크 요약
        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            chunk_tokens = sum(
                Utils._count_message_tokens_with_estimator(m, self.token_estimator)
                for m in chunk
            )
            self.console.print(
                f"[dim]  청크 {i}/{len(chunks)} 요약 중... ({len(chunk)}개, ~{chunk_tokens:,}tk)[/dim]",
                highlight=False
            )

            formatted = self._format_messages_for_prompt(chunk)

            try:
                result = self.parser.stream_and_parse(
                    system_prompt={"role": "system", "content": self.SUMMARY_SYSTEM_PROMPT},
                    final_messages=[{
                        "role": "user",
                        "content": f"다음 대화를 요약해주세요:\n\n{formatted}"
                    }],
                    model=model,
                    pretty_print=False,
                    tools=None
                )

                if result and result[0]:
                    chunk_summaries.append(f"[파트 {i}]\n{result[0]}")
                else:
                    self.console.print(f"[yellow]청크 {i} 요약 실패, 건너뜀[/yellow]", highlight=False)

            except Exception as e:
                self.console.print(f"[yellow]청크 {i} 오류: {e}[/yellow]", highlight=False)

        if not chunk_summaries:
            self.console.print("[red]모든 청크 요약 실패[/red]", highlight=False)
            return None

        # 3. 청크 요약들을 최종 통합 (청크가 2개 이상일 때만)
        if len(chunk_summaries) == 1:
            final_summary = chunk_summaries[0].replace("[파트 1]\n", "")
        else:
            self.console.print(
                f"[dim]  최종 통합 요약 중... ({len(chunk_summaries)}개 파트)[/dim]",
                highlight=False
            )

            combined = "\n\n---\n\n".join(chunk_summaries)

            try:
                result = self.parser.stream_and_parse(
                    system_prompt={"role": "system", "content": self.RESUMMARIZE_PROMPT},
                    final_messages=[{
                        "role": "user",
                        "content": f"다음 분할 요약들을 하나로 통합해주세요:\n\n{combined}"
                    }],
                    model=model,
                    pretty_print=False,
                    tools=None
                )

                if result and result[0]:
                    final_summary = result[0]
                else:
                    # 통합 실패 시 청크 요약들을 그냥 이어붙임
                    final_summary = combined

            except Exception as e:
                self.console.print(f"[yellow]통합 요약 오류: {e}[/yellow]", highlight=False)
                final_summary = combined

        summary_tokens = self.token_estimator.count_text_tokens(final_summary)
        compression = summary_tokens / original_tokens if original_tokens > 0 else 0

        self.console.print(
            f"[green]✅ 청크 분할 요약 완료:[/green] {original_tokens:,} → {summary_tokens:,} 토큰 "
            f"([cyan]{compression:.1%}[/cyan] 압축)",
            highlight=False
        )

        return final_summary, original_tokens, summary_tokens

    def check_and_summarize(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        model_context_limit: int,
        system_prompt_tokens: int,
        reserve_for_completion: int,
        tools_tokens: int = 0
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        컨텍스트 임계값을 확인하고 필요시 자동 요약을 수행합니다.

        이 메서드는 _handle_chat_message()에서 trim_messages_by_tokens() 전에 호출됩니다.

        Args:
            messages: 현재 메시지 목록
            model: 사용 중인 모델
            model_context_limit: 모델 컨텍스트 한계
            system_prompt_tokens: 시스템 프롬프트 토큰 수
            reserve_for_completion: 응답 예약 토큰
            tools_tokens: Tool 스키마 토큰 수

        Returns:
            (updated_messages, was_summarized)
        """
        should, ratio, reason = self.should_summarize(
            messages, model_context_limit, system_prompt_tokens,
            reserve_for_completion, tools_tokens
        )

        if not should:
            # 디버그용 (필요시 주석 해제)
            # self.console.print(f"[dim]요약 건너뜀: {reason}[/dim]", highlight=False)
            return messages, False

        self.console.print(
            f"\n[yellow]⚠️ 컨텍스트 사용률 {ratio:.1%} - 자동 요약 시작[/yellow]",
            highlight=False
        )

        # 요약 대상과 보존 대상 분리
        to_summarize, to_keep = self._prepare_messages_for_summary(messages)

        if not to_summarize:
            return messages, False

        # 기존 요약이 있는지 확인
        has_existing_summary = any(
            m.get("_summary_meta", {}).get("is_summary")
            for m in to_summarize
        )

        # 요약 레벨 확인 (최대 레벨 초과 시 경고)
        current_level = self._get_current_summary_level(to_summarize)
        if current_level >= self.max_levels:
            self.console.print(
                f"[yellow]최대 요약 레벨({self.max_levels}) 도달. 기존 트리밍으로 진행합니다.[/yellow]",
                highlight=False
            )
            return messages, False

        # 요약 수행
        result = self.summarize_messages(
            to_summarize,
            model,
            is_resummarize=has_existing_summary
        )

        if result is None:
            self.console.print(
                "[yellow]요약 실패, 기존 트리밍 방식으로 진행[/yellow]",
                highlight=False
            )
            return messages, False

        summary_text, original_tokens, summary_tokens = result

        # 요약 메시지 생성
        metadata = SummaryMetadata(
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            summarized_message_count=len(to_summarize),
            summarized_token_count=original_tokens,
            summary_token_count=summary_tokens,
            compression_ratio=summary_tokens / original_tokens if original_tokens > 0 else 0,
            model_used=model,
            summary_level=current_level + 1
        )

        summary_msg = SummaryMessage(
            role="assistant",
            content=f"[이전 대화 요약]\n\n{summary_text}",
            is_summary=True,
            metadata=metadata
        )

        # 히스토리에 추가
        self.summary_history.append(metadata)

        # 새 메시지 목록 구성: [요약] + [보존된 최근 메시지]
        new_messages = [summary_msg.to_dict()] + to_keep

        self.console.print(
            f"[green]✅ 컨텍스트 압축 완료:[/green] {len(messages)} → {len(new_messages)} 메시지\n",
            highlight=False
        )

        return new_messages, True

    def _get_current_summary_level(self, messages: List[Dict[str, Any]]) -> int:
        """현재 메시지에서 최대 요약 레벨을 반환합니다."""
        max_level = 0
        for msg in messages:
            meta = msg.get("_summary_meta", {})
            if meta.get("is_summary") and meta.get("metadata"):
                level = meta["metadata"].get("summary_level", 1)
                max_level = max(max_level, level)
        return max_level

    def get_summary_info(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        현재 메시지에서 요약 정보를 추출합니다 (/show_summary 명령용)
        """
        for msg in messages:
            summary = SummaryMessage.from_dict(msg)
            if summary:
                return {
                    "content": summary.content,
                    "metadata": asdict(summary.metadata) if summary.metadata else None
                }
        return None

    def manual_summarize(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        force: bool = False
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        수동 요약 (/summarize 명령용)

        Args:
            messages: 현재 메시지 목록
            model: 사용할 모델
            force: 임계값/메시지 수 무시하고 강제 요약

        Returns:
            (updated_messages, was_summarized)
        """
        if len(messages) < self.min_messages and not force:
            self.console.print(
                f"[yellow]요약할 메시지가 충분하지 않습니다 "
                f"({len(messages)} < {self.min_messages})[/yellow]",
                highlight=False
            )
            self.console.print(
                "[dim]강제 요약: /summarize --force[/dim]",
                highlight=False
            )
            return messages, False

        to_summarize, to_keep = self._prepare_messages_for_summary(messages)

        if not to_summarize:
            self.console.print("[yellow]요약할 메시지가 없습니다[/yellow]", highlight=False)
            return messages, False

        # 기존 요약 확인
        has_existing_summary = any(
            m.get("_summary_meta", {}).get("is_summary")
            for m in to_summarize
        )

        # 요약 레벨 확인
        current_level = self._get_current_summary_level(to_summarize)
        if current_level >= self.max_levels and not force:
            self.console.print(
                f"[yellow]최대 요약 레벨({self.max_levels}) 도달.[/yellow]",
                highlight=False
            )
            self.console.print(
                "[dim]강제 요약: /summarize --force[/dim]",
                highlight=False
            )
            return messages, False

        result = self.summarize_messages(
            to_summarize,
            model,
            is_resummarize=has_existing_summary
        )

        if result is None:
            return messages, False

        summary_text, original_tokens, summary_tokens = result

        metadata = SummaryMetadata(
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            summarized_message_count=len(to_summarize),
            summarized_token_count=original_tokens,
            summary_token_count=summary_tokens,
            compression_ratio=summary_tokens / original_tokens if original_tokens > 0 else 0,
            model_used=model,
            summary_level=current_level + 1
        )

        summary_msg = SummaryMessage(
            role="assistant",
            content=f"[이전 대화 요약]\n\n{summary_text}",
            is_summary=True,
            metadata=metadata
        )

        self.summary_history.append(metadata)

        new_messages = [summary_msg.to_dict()] + to_keep

        return new_messages, True
