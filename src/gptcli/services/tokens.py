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
    # Anthropic은 tiktoken 대비 약 3.0배 토큰 사용 (경험적 보정)
    ANTHROPIC_MULTIPLIER = 3.0

    def __init__(self, console: Console, model: str = "gpt-4"):
        self.console = console
        self.model = model
        self._is_anthropic = False
        self._init_encoder(model)

    def _init_encoder(self, model: str) -> None:
        self.model = model
        model_lower = model.lower()
        
        # Anthropic 모델 감지
        self._is_anthropic = any(k in model_lower for k in ("anthropic", "claude"))
        
        if self._is_anthropic:
            self.console.print(f"[dim]Anthropic 모델 감지: 토큰 보정 계수 {self.ANTHROPIC_MULTIPLIER}x 적용[/dim]", highlight=False)
        
        # tiktoken 인코더
        try:
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")

    def update_model(self, model: str) -> None:
        if model != self.model:
            self._init_encoder(model)

    def count_text_tokens(self, text: str) -> int:
        base_tokens = len(self.encoder.encode(text))
        if self._is_anthropic:
            return int(base_tokens * self.ANTHROPIC_MULTIPLIER)
        return base_tokens

    def calculate_image_tokens(self, width: int, height: int, detail: str = "auto") -> int:
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
        if self._is_anthropic:
            return int(base * self.ANTHROPIC_MULTIPLIER)
        return base

    def estimate_image_tokens(self, image_input: Union[Path, str], detail: str = "auto") -> int:
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
                    return int(tokens * self.ANTHROPIC_MULTIPLIER) if self._is_anthropic else tokens
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
            return int(base * self.ANTHROPIC_MULTIPLIER) if self._is_anthropic else base
        except Exception as e:
            self.console.print(f"[yellow]이미지 토큰 추정 실패: {e}[/yellow]", highlight=False)
            base = 1105
            return int(base * self.ANTHROPIC_MULTIPLIER) if self._is_anthropic else base

    def estimate_pdf_tokens(self, pdf_path: Path) -> int:
        try:
            import PyPDF2
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = "".join(page.extract_text() or "" for page in reader.pages)
                return self.count_text_tokens(text)
        except ImportError:
            file_size_kb = pdf_path.stat().st_size / 1024
            base = int(file_size_kb * 3)
            return int(base * self.ANTHROPIC_MULTIPLIER) if self._is_anthropic else base
        except Exception:
            tokens = len(base64.b64encode(pdf_path.read_bytes())) // 4
            return int(tokens * self.ANTHROPIC_MULTIPLIER) if self._is_anthropic else tokens
