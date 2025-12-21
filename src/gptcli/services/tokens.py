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
    def __init__(self, console: Console, model: str = "gpt-4"):
        """
        모델별 토크나이저 초기화
        - gpt-4, gpt-3.5-turbo: cl100k_base
        - older models: p50k_base
        """
        self.console = console
        try:
            self.encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")
    
    def count_text_tokens(self, text: str) -> int:
        """텍스트의 정확한 토큰 수 계산"""
        return len(self.encoder.encode(text))
    
    def calculate_image_tokens(self, width: int, height: int, detail: str = "auto") -> int:
        """
        OpenAI의 공식 이미지 토큰 계산 방식
        
        detail 옵션:
        - "low": 항상 85 토큰 (512x512 이하로 리사이즈)
        - "high": 타일 기반 계산 (더 정확한 분석)
        - "auto": 이미지 크기에 따라 자동 선택
        """
        
        # Low detail: 고정 비용
        if detail == "low":
            return 85
        
        # High detail: 타일 기반 계산
        # 1. 이미지를 2048x2048 이내로 조정
        if width > 2048 or height > 2048:
            ratio = min(2048/width, 2048/height)
            width = int(width * ratio)
            height = int(height * ratio)
        
        # 2. 짧은 변을 768px로 조정
        if min(width, height) > 768:
            if width < height:
                height = int(height * 768 / width)
                width = 768
            else:
                width = int(width * 768 / height)
                height = 768
        
        # 3. 512x512 타일로 나누기
        tiles_x = math.ceil(width / 512)
        tiles_y = math.ceil(height / 512)
        total_tiles = tiles_x * tiles_y
        
        # 4. 토큰 계산: 베이스(85) + 타일당 170
        return 85 + (170 * total_tiles)
    
    def estimate_image_tokens(self, image_input: Union[Path, str], detail: str = "auto") -> int:
        """이미지 파일 또는 base64 문자열의 토큰 추정"""
        try:
            # base64 문자열인 경우
            if isinstance(image_input, str):
                # base64 문자열에서 이미지 디코드
                try:
                    # data:image/...;base64, 접두사 제거
                    if image_input.startswith('data:'):
                        image_input = image_input.split(',')[1]
                    
                    image_data = base64.b64decode(image_input)
                    img = Image.open(io.BytesIO(image_data))
                    width, height = img.size
                    
                    # base64는 보통 고화질로 처리
                    if detail == "auto":
                        detail = "high"
                    
                    return self.calculate_image_tokens(width, height, detail)
                except Exception:
                    # base64 디코딩 실패 시 길이 기반 추정
                    return len(image_input) // 4
            
            # Path 객체인 경우 (기존 로직)
            elif isinstance(image_input, Path):
                with Image.open(image_input) as img:
                    width, height = img.size
                    
                    # 파일 크기 기반 detail 자동 선택
                    if detail == "auto":
                        file_size_mb = image_input.stat().st_size / (1024 * 1024)
                        detail = "low" if file_size_mb < 0.5 else "high"
                    
                    return self.calculate_image_tokens(width, height, detail)
            else:
                raise ValueError(f"지원하지 않는 입력 타입: {type(image_input)}")
                
        except Exception as e:
            self.console.print(f"[yellow]이미지 토큰 추정 실패: {e}[/yellow]")
            # 폴백: 기본값 반환
            return 1105  # GPT-4V 평균 토큰 수
    
    def estimate_pdf_tokens(self, pdf_path: Path) -> int:
        """
        PDF 토큰 추정 (대략적)
        일부 모델만 PDF를 직접 지원하며, 
        대부분 텍스트 추출 후 처리
        """
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    text += page.extract_text()
                return self.count_text_tokens(text)
        except Exception as e:
            self.console.print(f"[yellow]PDF 토큰 추정 실패 ({e}). ...[/yellow]")
            # 폴백: base64 크기 기반
            return len(base64.b64encode(pdf_path.read_bytes())) // 4
