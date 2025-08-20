from __future__ import annotations
import json, base64, io, os, re, mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from rich.console import Console
from PIL import Image
import src.constants as constants

class Utils:
    """
    특정 클래스에 속하지 않는 순수 유틸리티 함수들을 모아놓은 정적 클래스.
    모든 메서드는 의존성을 인자로 주입받습니다.
    """
    _VENDOR_SPECIFIC_OFFSET = constants.VENDOR_SPECIFIC_OFFSET

    @staticmethod
    def _load_json(path: Path, default: Any = None) -> Any:
        """JSON 파일을 안전하게 읽어옵니다. 실패 시 기본값을 반환합니다."""
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return default or {}
        return default or {}
    
    @staticmethod
    def _save_json(path: Path, data: Any) -> bool:
        """JSON 데이터를 파일에 안전하게 저장합니다."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except IOError:
            return False

    @staticmethod
    def _read_plain_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"[파일 읽기 실패: {e}]"

    @staticmethod
    def _encode_base64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    @staticmethod
    def get_system_prompt_content(mode: str) -> str:
        return constants.PROMPT_TEMPLATES.get(mode,constants.PROMPT_TEMPLATES["dev"]).strip()

    @staticmethod
    def _parse_backticks(line: str) -> Optional[tuple[int, str]]:
        """
        주어진 라인이 코드 블록 구분자인지 확인하고, 백틱 개수와 언어 태그를 반환합니다.
        """
        stripped_line = line.strip()
        if not stripped_line.startswith('`'):
            return None

        count = 0
        for char in stripped_line:
            if char == '`':
                count += 1
            else:
                break

        # 최소 3개 이상이어야 유효한 구분자로 간주
        if count < 3:
            return None

        # 구분자 뒤에 다른 문자가 있다면 백틱이 아니므로 유효하지 않음
        if len(stripped_line) > count and stripped_line[count] == '`':
            return None

        language = stripped_line[count:].strip()
        return count, language

    @staticmethod
    def optimize_image_for_api(path: Path, console: Console, max_dimension: int = 1024, quality: int = 85) -> str:
        """
        이미지를 API에 적합하게 최적화
        - 크기 축소
        - JPEG 압축
        - base64 인코딩
        """
        try:
            with Image.open(path) as img:
                # EXIF 회전 정보 적용
                img = img.convert('RGB')
                
                # 크기 조정 (비율 유지)
                if max(img.size) > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                
                # 메모리 버퍼에 JPEG로 저장
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=quality, optimize=True)
                
                # base64 인코딩
                return base64.b64encode(buffer.getvalue()).decode('utf-8')
                
        except Exception as e:
            console.print(f"[yellow]이미지 최적화 실패 ({path.name}): {e}[/yellow]")
            return Utils._encode_base64(path)

    @staticmethod
    def prepare_content_part(path: Path, console: Console, token_estimator: 'TokenEstimator', optimize_images: bool = True) -> Dict[str, Any]:
        """파일을 API 요청용 컨텐츠로 변환"""
        if path.suffix.lower() in constants.IMG_EXTS:
            # 이미지 크기 확인
            file_size_mb = path.stat().st_size / (1024 * 1024)
            
            if file_size_mb > 20:  # 20MB 이상
                return {
                    "type": "text",
                    "text": f"[오류: {path.name} 이미지가 너무 큽니다 ({file_size_mb:.1f}MB). 20MB 이하로 줄여주세요.]"
                }
            
            # 이미지 최적화
            if optimize_images and file_size_mb > 1:  # 1MB 이상이면 압축
                console.print(f"[dim]이미지 최적화 중: {path.name} ({file_size_mb:.1f}MB)...[/dim]")
                base64_data = Utils.optimize_image_for_api(path, console)
                estimated_tokens = token_estimator.estimate_image_tokens(base64_data, detail="auto")
            else:
                base64_data = Utils._encode_base64(path)
                estimated_tokens = token_estimator.estimate_image_tokens(path, detail="auto")
            
            
            if estimated_tokens > 10000:
                console.print(f"[yellow]경고: {path.name}이 약 {estimated_tokens:,} 토큰을 사용합니다.[/yellow]")
            
            data_url = f"data:{mimetypes.guess_type(path)[0] or 'image/jpeg'};base64,{base64_data}"
            return {
                "type": "image_url",
                "image_url": {
                    "url": data_url, 
                    "detail": "auto", 
                    "image_name": path.name, # 내부 참조용
                }
            }
        
        elif path.suffix.lower() == constants.PDF_EXT:
            estimated_tokens = token_estimator.estimate_pdf_tokens(path)
            console.print(f"[dim]PDF 토큰: 약 {estimated_tokens:,}개[/dim]")

            # PDF는 그대로 (일부 모델만 지원)
            data_url = f"data:application/pdf;base64,{Utils._encode_base64(path)}"
            return {
                "type": "file",
                "file": {"filename": path.name, "file_data": data_url},
            }
        
        # 텍스트 파일
        text = Utils._read_plain_file(path)
        tokens = token_estimator.count_text_tokens(text)
        console.print(f"[dim]텍스트 토큰: {tokens:,}개[/dim]", highlight=False)
        return {
            "type": "text",
            "text": f"\n\n[파일: {path}]\n```\n{text}\n```",
        }

    @staticmethod
    def _count_message_tokens_with_estimator(msg: Dict[str, Any], te: 'TokenEstimator') -> int:
        total = 6  # 메시지 구조 오버헤드
        content = msg.get("content", "")
        
        if isinstance(content, str):
            total += te.count_text_tokens(content)
            return total
        
        if isinstance(content, list):
            for part in content:
                ptype = part.get("type")
                if ptype == "text":
                    total += te.count_text_tokens(part.get("text", ""))
                elif ptype == "image_url":
                    image_url = part.get("image_url", {})
                    url = image_url.get("url", "")
                    detail = image_url.get("detail", "auto")
                    if isinstance(url, str) and "base64," in url:
                        try:
                            b64 = url.split("base64,", 1)[1]
                            total += te.estimate_image_tokens(b64, detail=detail)
                        except Exception:
                            total += 1105
                    else:
                        total += 85
                elif ptype == "file":
                    file_data = part.get("file", {})
                    data_url = file_data.get("file_data", "")
                    filename = file_data.get("filename", "")
                    if isinstance(filename, str) and filename.lower().endswith(".pdf") and "base64," in data_url:
                        try:
                            b64 = data_url.split("base64,", 1)[1]
                            pdf_bytes = base64.b64decode(b64)
                            total += int(len(pdf_bytes) / 1024 * 3)
                        except Exception:
                            total += 1000
                    else:
                        total += 500
        return total
    
    @staticmethod
    def trim_messages_by_tokens(
        messages: List[Dict[str, Any]],
        model_name: str,
        model_context_limit: int,
        system_prompt_text: str,
        token_estimator: 'TokenEstimator',
        console: Console,
        reserve_for_completion: int = 4096,
        trim_ratio: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """컨텍스트 한계에 맞춰 메시지 목록을 트리밍"""
        te = token_estimator
        trim_ratio = float(trim_ratio) if trim_ratio is not None else float(constants.CONTEXT_TRIM_RATIO)

        sys_tokens = te.count_text_tokens(system_prompt_text or "")
        if sys_tokens >= model_context_limit:
            console.print("[red]시스템 프롬프트가 모델 컨텍스트 한계를 초과합니다.[/red]",highlight=False)
            return []

        # 벤더별 추가 오프셋
        vendor_offset = 0
        clean_model_name = model_name.lower()
        for vendor, offset in Utils._VENDOR_SPECIFIC_OFFSET.items():
            if vendor in clean_model_name:
                vendor_offset = offset
                console.print(f"[dim]벤더별 오프셋 적용({vendor}): -{vendor_offset:,} 토큰[/dim]", highlight=False)
                break

        available_for_prompt = model_context_limit - sys_tokens - reserve_for_completion - vendor_offset

        if available_for_prompt <= 0:
            console.print("[red]예약 공간과 오프셋만으로 컨텍스트가 가득 찼습니다.[/red]",highlight=False)
            return []

        prompt_budget = int(available_for_prompt * trim_ratio)

        # 메시지별 토큰 산출
        per_message = [(m, Utils._count_message_tokens_with_estimator(m, te)) for m in messages]

        trimmed: List[Dict[str, Any]] = []
        used = 0
        for m, t in reversed(per_message):
            if used + t > prompt_budget:
                break
            trimmed.append(m)
            used += t
        trimmed.reverse()

        if not trimmed and messages:
            last = messages[-1]
            if isinstance(last.get("content"), list):
                text_parts = [p for p in last["content"] if p.get("type") == "text"]
                minimal = {"role": last.get("role", "user"), "content": text_parts[0]["text"] if text_parts else ""}
                if Utils._count_message_tokens_with_estimator(minimal, te) <= prompt_budget:
                    console.print("[yellow]최신 메시지의 첨부를 제거하여 텍스트만 전송합니다.[/yellow]")
                    return [minimal]
            console.print("[red]컨텍스트 한계로 인해 메시지를 전송할 수 없습니다. 입력을 줄여주세요.[/red]")
            return []

        if len(trimmed) < len(messages):
            removed = len(messages) - len(trimmed)
            console.print(
                f"[dim]컨텍스트 트리밍: {removed}개 제거 | "
                f"[dim]최신 메시지: {len(trimmed)}개 사용 | "
                f"사용:{used:,}/{prompt_budget:,} (총 프롬프트 여유:{available_for_prompt:,} | "
                f"ratio:{trim_ratio:.2f})[/dim]",
                highlight=False
            )
        else:
            # 트리밍이 발생하지 않아도 로그 출력
            console.print(
                f"[dim]컨텍스트 사용:{used:,}/{prompt_budget:,} "
                f"(sys:{sys_tokens:,} | reserve:{reserve_for_completion:,} | ratio:{trim_ratio:.2f} | offset:{vendor_offset:,})[/dim]",
                highlight=False
            )
            
        return trimmed

    @staticmethod
    def extract_code_blocks(markdown: str) -> List[Tuple[str, str]]:
        """
        State-machine 기반으로 마크다운에서 코드 블록을 추출합니다.
        ask_stream의 실시간 파싱 로직과 동일한 원리로, 정규식보다 안정적입니다.
        """
        blocks = []
        lines = markdown.split('\n')
        
        in_code_block = False
        outer_delimiter_len = 0
        nesting_depth = 0
        code_buffer: List[str] = []
        language = ""
        
        for line in lines:
            delimiter_info = Utils._parse_backticks(line)

            # 코드 블록 시작 
            if not in_code_block:
                if delimiter_info:
                    in_code_block = True
                    outer_delimiter_len, language = delimiter_info
                    nesting_depth = 0
                    code_buffer = []
                
            # 코드 블록 종료 
            else:
                is_matching_delimiter = delimiter_info and delimiter_info[0] == outer_delimiter_len

                if is_matching_delimiter:
                    # 같은 길이의 백틱 구분자. 중첩 여부 판단.
                    if delimiter_info[1]: # 언어 태그가 있으면 중첩 시작
                        nesting_depth += 1
                    else: # 언어 태그가 없으면 중첩 종료
                        nesting_depth -= 1

                if nesting_depth < 0:
                    # 최종 블록 종료
                    blocks.append((language, "\n".join(code_buffer)))
                    in_code_block = False
                else:
                    code_buffer.append(line)

        # 파일 끝까지 코드 블록이 닫히지 않은 엣지 케이스 처리
        if in_code_block and code_buffer:
            blocks.append((language, "\n".join(code_buffer)))
            
        return blocks
    
    @staticmethod
    def convert_to_placeholder_message(msg: Dict) -> Dict:
        """
        메시지의 첨부파일을 플레이스홀더로 변환합니다.
        원본을 수정하지 않고 새로운 딕셔너리를 반환합니다.
        """
        import copy
        
        # 깊은 복사로 원본 보호
        new_msg = copy.deepcopy(msg)
        
        if isinstance(new_msg.get("content"), str):
            return new_msg
        
        text_content = ""
        attachments_info = []
        
        cnt = 0
        for part in new_msg.get("content", []):
            
            if part.get("type") == "text":
                # 0 부분은 파일 첨부가 아님
                if cnt == 0:
                    text_content = part.get("text","")
                else:
                    attachment_name = part.get("text","").split("\n```")[0].strip().split(":")[1].strip().replace("]","")
                    attachments_info.append(f"{attachment_name}")    

            elif part.get("type") == "image_url":
                image_name = part.get("image_url", {}).get("image_name", "이미지")
                attachments_info.append(f"📷 {image_name}")

            elif part.get("type") == "file":
                filename = part.get("file", {}).get("filename", "파일")
                attachments_info.append(f"📄 {filename}")

            cnt += 1
        
        if attachments_info:
            attachment_summary = "[첨부: " + ", ".join(attachments_info) + "]"
            new_msg["content"] = text_content + "\n" + attachment_summary

        else:
            new_msg["content"] = text_content
        
        return new_msg
    
    @staticmethod
    def get_last_assistant_message(messages: List[Dict[str, Any]]) -> Optional[str]:
        """
        대화 기록에서 가장 최근의 'assistant' 역할을 가진 메시지 내용을 찾아 반환합니다.
        없으면 None을 반환합니다.
        """
        for message in reversed(messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                # content가 문자열인지 확인 후 반환
                if isinstance(content, str):
                    return content
        return None
