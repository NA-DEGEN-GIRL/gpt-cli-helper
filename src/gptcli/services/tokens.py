from __future__ import annotations
import io, math, base64
from pathlib import Path
from typing import Union
from PIL import Image
import PyPDF2
import tiktoken
from rich.console import Console
#import src.constants as constants

class TokenEstimator:
    """
    토큰 수 추정기.

    주의: tiktoken은 OpenAI의 토큰화 방식을 사용합니다.
    Anthropic, Google 등 다른 벤더는 토큰화 방식이 다르므로,
    추정값은 실제와 차이가 있을 수 있습니다.

    보정 배수는 경험적으로 조정된 값이며, 정확하지 않습니다.
    실제 API 응답의 usage 정보를 참고하세요.
    """
    # 벤더별 토큰 보정 배수 (tiktoken 대비)
    # - OpenAI: 1.0 (tiktoken과 동일)
    # - Anthropic: Claude의 토큰화는 tiktoken과 유사하지만 메시지 구조 오버헤드가 큼
    # - Google: Gemini는 자체 토큰화 사용, 약간 다름
    VENDOR_MULTIPLIERS = {
        "openai": 1.0,
        "anthropic": 1.1,  # 기존 3.0에서 1.1로 수정 (메시지 오버헤드는 별도 계산)
        "google": 1.2,
    }
    DEFAULT_MULTIPLIER = 1.0

    # 메시지당 구조 오버헤드 (role, content 등 JSON 구조)
    # OpenAI 공식 문서: 약 4-7 토큰/메시지, 하지만 실제로는 더 클 수 있음
    MESSAGE_OVERHEAD = 20

    def __init__(self, console: Console, model: str = "gpt-4"):
        self.console = console
        self.model = model
        self._vendor = "openai"
        self._multiplier = self.DEFAULT_MULTIPLIER
        self._init_encoder(model)

    def _init_encoder(self, model: str) -> None:
        self.model = model
        model_lower = model.lower()

        # 벤더 감지 및 배수 설정
        self._vendor = "openai"
        for vendor in self.VENDOR_MULTIPLIERS:
            if vendor in model_lower:
                self._vendor = vendor
                break
        # Claude 모델명만 있는 경우도 anthropic으로 처리
        if "claude" in model_lower:
            self._vendor = "anthropic"

        self._multiplier = self.VENDOR_MULTIPLIERS.get(self._vendor, self.DEFAULT_MULTIPLIER)

        if self._multiplier != 1.0:
            self.console.print(f"[dim]{self._vendor} 모델: 토큰 보정 {self._multiplier}x[/dim]", highlight=False)

        # tiktoken 인코더
        try:
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")

    def update_model(self, model: str) -> None:
        if model != self.model:
            self._init_encoder(model)

    def count_text_tokens(self, text: str) -> int:
        """텍스트의 토큰 수를 추정합니다."""
        base_tokens = len(self.encoder.encode(text))
        return int(base_tokens * self._multiplier)

    def calculate_image_tokens(self, width: int, height: int, detail: str = "auto") -> int:
        """이미지 토큰 수를 계산합니다 (OpenAI 방식)."""
        if detail == "low":
            return 85
        if width > 2048 or height > 2048:
            r = min(2048 / width, 2048 / height)
            width, height = int(width * r), int(height * r)
        if min(width, height) > 768:
            if width < height:
                height = int(height * 768 / width)
                width = 768
            else:
                width = int(width * 768 / height)
                height = 768
        tiles_x = math.ceil(width / 512)
        tiles_y = math.ceil(height / 512)
        base = 85 + 170 * (tiles_x * tiles_y)
        return int(base * self._multiplier)

    def estimate_image_tokens(self, image_input: Union[Path, str], detail: str = "auto") -> int:
        """이미지의 토큰 수를 추정합니다."""
        try:
            from PIL import Image
            if isinstance(image_input, str):
                try:
                    if image_input.startswith('data:'):
                        image_input = image_input.split(',', 1)[1]
                    image_data = base64.b64decode(image_input)
                    img = Image.open(io.BytesIO(image_data))
                    width, height = img.size
                    if detail == "auto":
                        detail = "high"
                    return self.calculate_image_tokens(width, height, detail)
                except Exception:
                    tokens = len(image_input) // 4
                    return int(tokens * self._multiplier)
            elif isinstance(image_input, Path):
                with Image.open(image_input) as img:
                    width, height = img.size
                    if detail == "auto":
                        file_size_mb = image_input.stat().st_size / (1024 * 1024)
                        detail = "low" if file_size_mb < 0.5 else "high"
                    return self.calculate_image_tokens(width, height, detail)
            else:
                raise ValueError(f"지원하지 않는 입력 타입: {type(image_input)}")
        except ImportError:
            base = 1105
            return int(base * self._multiplier)
        except Exception as e:
            self.console.print(f"[yellow]이미지 토큰 추정 실패: {e}[/yellow]", highlight=False)
            base = 1105
            return int(base * self._multiplier)

    def estimate_pdf_tokens(self, pdf_path: Path) -> int:
        """PDF 파일의 토큰 수를 추정합니다."""
        try:
            import PyPDF2
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = "".join(page.extract_text() or "" for page in reader.pages)
                return self.count_text_tokens(text)
        except ImportError:
            file_size_kb = pdf_path.stat().st_size / 1024
            base = int(file_size_kb * 3)
            return int(base * self._multiplier)
        except Exception:
            tokens = len(base64.b64encode(pdf_path.read_bytes())) // 4
            return int(tokens * self._multiplier)
