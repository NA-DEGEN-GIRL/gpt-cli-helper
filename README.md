# GPT CLI Chat Assistant

이 프로젝트는 OpenAI GPT API를 기반으로 한 터미널 기반 챗봇 도구입니다.  
개발 보조에 특화되어 있으며, 파일 첨부, 코드 비교, 세션 유지, 프롬프트 즐겨찾기 등 다양한 기능을 제공합니다.

---

## ✨ 주요 기능

- GPT-4o 기반 CLI 대화
- 코드 파일 첨부 및 자동 diff 분석 (`/diffme`)
- GPT 역할 전환 지원 (`/mode dev` / `general`)
- 모델 전환 가능 (`/model gpt-4` 등)
- 세션별 기록 및 자동 저장
- 응답 Markdown + 코드 자동 저장
- 프롬프트 즐겨찾기 (`/savefav`, `/usefav`)
- 명령어 자동완성 (tab 지원)
- 클립보드 복사 (`--copy`, 기본 비활성화)

---

## 💻 설치 방법

1. 파이썬 가상환경 생성 (선택)

```bash
python3 -m venv ~/.venvs/gptcli
source ~/.venvs/gptcli/bin/activate
```

2. 의존 패키지 설치
```bash
pip install -r requirements.txt
```

3. .env 파일 생성
```bash
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

4. 전역 명령 설정 (gptcli)
```bash
chmod +x gpt_cli_helper.py
sudo ln -s /full/path/to/gpt_cli_helper.py /usr/local/bin/gptcli
```

