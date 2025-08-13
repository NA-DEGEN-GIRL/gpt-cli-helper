# GPT-CLI Pro — 터미널 최적화 AI 개발 동반자

![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**GPT-CLI Pro**는 개발자의 터미널 워크플로우에 완벽하게 통합되도록 설계된, 대화형 AI 클라이언트입니다. OpenRouter의 범용 API를 통해 다양한 최신 언어 모델(Claude 3, GPT-4o, Llama 3 등)을 손쉽게 전환하며 사용할 수 있습니다. 단순히 질문하고 답을 얻는 것을 넘어, 코드 분석, 리뷰, 디버깅, 학습 등 개발의 모든 단계에서 생산성을 극대화하는 데 초점을 맞춘 강력한 기능들을 제공합니다.

---

## ✨ 핵심 기능

-   **지능형 스트리밍 출력**: Rich 라이브러리 기반의 미려한 UI로 AI의 응답을 실시간 렌더링합니다.
    -   **추론(Reasoning) Live**: 일부 모델이 지원하는 "생각 과정"을 별도의 고정 높이 패널에 표시하여, 답변이 생성되는 과정을 투명하게 보여줍니다.
    -   **코드(Code) Live**: 코드 블록은 내용 길이에 따라 패널 높이가 동적으로 조절되며, 설정된 최대 높이를 넘어서면 "...N줄 생략..." 안내와 함께 스크롤 없이도 핵심 내용을 파악할 수 있습니다.
-   **견고한 코드블록 파서**: LLM이 생성하는 다양한 형식의 코드 블록을 정확하게 인식합니다.
    -   들여쓰기가 깊거나(`- `` `), ` `나 `~`를 사용하는 펜스를 모두 지원합니다.
    -   "이것은 ```python 예시입니다"와 같은 문장 속 삼중 백틱(인라인)을 코드로 오인하지 않습니다.
    -   코드 블록 내부에 다른 코드 블록이 중첩된 경우에도 깊이를 추적하여 정확히 파싱합니다.
-   **강력한 파일 첨부 및 관리**:
    -   `.gptignore` 규칙을 존중하는 TUI 파일 선택기(`/all_files`)로 프로젝트 컨텍스트를 안전하고 빠르게 추가합니다.
    -   이미지(.png, .jpg), PDF, 소스 코드 등 다양한 파일을 첨부할 수 있습니다.
-   **TUI 기반 인터페이스**:
    -   **Diff 뷰 (`/diff_code`)**: 두 코드 블록(예: 수정 전/후)을 나란히 비교합니다. 문맥 줄 수를 동적으로 조절하고(+/-), 전체 파일을 보거나(f), 가로로 긴 코드를 스크롤(←/→)하며 리뷰할 수 있습니다. Pygments 기반의 정확한 문법 하이라이팅을 지원합니다.
    -   **모델 선택 및 검색 (`/select_model`, `/search_models`)**: `ai_models.txt` 파일을 기반으로 모델을 쉽게 전환하거나, OpenRouter에서 새로운 모델을 검색하여 목록에 추가할 수 있습니다.
-   **효율적인 컨텍스트 관리**:
    -   **Compact 모드**: 긴 대화에서 과거 메시지의 첨부 파일을 간단한 플레이스홀더(`[첨부: 파일명]`)로 압축하여, 토큰 사용량을 크게 절감합니다.
    -   **컨텍스트 리포트 (`/show_context`)**: 현재 대화의 토큰 사용량을 모델 한계, 시스템 프롬프트, 예약 공간 등과 비교하여 시각적으로 보여줍니다.
-   **안전한 클립보드 복사 (`/copy`)**:
    -   `/copy <번호>` 명령어로 답변의 코드 블록을 즉시 복사합니다.
    -   SSH 원격 접속 환경처럼 클립보드 접근이 실패할 경우, 코드를 터미널에 **순수 텍스트로 다시 출력**해 사용자가 직접 드래그하여 복사할 수 있도록 하는 안전장치(Fallback)가 내장되어 있습니다.

---

## 🚀 설치 및 설정

### 1단계: 소스 코드 다운로드
먼저, 이 저장소(repository)를 로컬 컴퓨터에 복제(clone)하거나 다운로드합니다. 이 폴더 위치는 나중에 전역 명령어로 등록할 때 필요합니다.

```bash
git clone https://github.com/your-username/gpt-cli-pro.git
cd gpt-cli-pro
```

### 2단계: 의존성 설치
`gptcli`는 여러 파이썬 라이브러리를 사용합니다. 아래 명령어로 한 번에 설치할 수 있습니다.

```bash
pip install -r requirements.txt
```

### 3단계: API 키 설정
프로젝트 루트 디렉터리에 `.env` 파일을 생성하고 OpenRouter API 키를 추가합니다. 키는 [OpenRouter 대시보드](https://openrouter.ai/keys)에서 발급받을 수 있습니다.

```env
OPENROUTER_API_KEY="sk-or-..."_
```

### 4단계: 전역 설정 디렉터리
`gptcli`는 사용자의 홈 디렉터리 아래에 `~/.config/gptcli`를 설정 폴더로 사용합니다. 최초 실행 시 아래 파일들이 자동으로 생성됩니다.
-   `~/.config/gptcli/ai_models.txt`: 사용 가능한 AI 모델 목록.
-   `~/.config/gptcli/.gptignore_default`: 모든 프로젝트에 공통으로 적용될 파일/디렉터리 무시 규칙.

---

## ⚙️ 전역 명령어로 사용하기 (어디서든 `gptcli` 실행)

이 스크립트를 매번 `python gptcli.py`로 실행하는 것은 불편합니다. 터미널의 어떤 위치에서든 `gptcli`라는 짧은 명령어로 실행할 수 있도록 설정하세요.

### Linux & macOS

가장 권장되는 방법은 `PATH`에 포함된 디렉터리에 심볼릭 링크를 만드는 것입니다.

1.  **실행 권한 부여:**
    `gptcli.py` 파일에 실행 권한을 줍니다.
    ```bash
    chmod +x gptcli.py
    ```

2.  **심볼릭 링크 생성:**
    `/usr/local/bin`은 대부분의 시스템에서 기본적으로 `PATH`에 포함되어 있습니다.
    ```bash
    # 'gptcli.py'의 전체 경로를 확인하고, 'gptcli'라는 이름의 링크를 생성합니다.
    # 예: /home/user/myprojects/gpt-cli-pro/gptcli.py
    sudo ln -s /path/to/your/gpt-cli-pro/gptcli.py /usr/local/bin/gptcli
    ```

3.  **확인:**
    새 터미널을 열거나 `rehash` (zsh) 또는 `hash -r` (bash)를 실행한 뒤, 아무 디렉터리에서나 아래 명령어를 입력해 보세요.
    ```bash
    gptcli --help 
    ```
    gptcli의 도움말이 나오면 성공입니다.

### Windows

Windows에서는 시스템 환경 변수 `Path`에 스크립트가 있는 폴더를 추가하는 것이 일반적입니다.

1.  **환경 변수 `Path`에 폴더 추가:**
    -   `시스템 속성` -> `고급` -> `환경 변수`로 이동합니다.
    -   `사용자 변수` 또는 `시스템 변수` 목록에서 `Path`를 선택하고 `편집`을 클릭합니다.
    -   `새로 만들기`를 눌러 `gptcli.py`가 있는 폴더의 전체 경로(예: `C:\Projects\gpt-cli-pro`)를 추가합니다.

2.  **확인:**
    새 명령 프롬프트(cmd)나 PowerShell 창을 열고, 아무 디렉터리에서나 아래 명령어를 입력합니다.
    ```powershell
    gptcli.py --help
    ```
    이름을 더 짧게 하고 싶다면, `gptcli.py` 파일의 이름을 `gptcli`로 변경할 수 있습니다. (이 경우, `Path`에 `.PY`가 `PATHEXT` 환경 변수에 포함되어 있어야 확장자 없이 실행됩니다.)

---

## 💡 평상시 사용 워크플로우 예시

`gptcli`는 단순 질의응답을 넘어, 개발자의 일상적인 작업을 돕는 실용적인 도구입니다.

### 시나리오 1: 기존 코드 분석 및 리팩터링 제안 받기

1.  **프로젝트 파일 첨부:**
    -   `gptcli`를 실행하고, 분석하고 싶은 파일들을 첨부합니다.
    -   `/all_files` 명령어로 TUI 파일 선택기를 열어 프로젝트 구조를 보며 파일을 선택하거나,
    -   `/files src/main.py src/utils/` 처럼 특정 파일이나 디렉터리를 직접 지정합니다.
    -   첨부된 파일 목록과 예상 토큰 사용량이 표시됩니다.

2.  **분석 요청:**
    -   `/mode teacher`로 전환하여 코드를 깊이 있게 분석하는 '아키텍트' 페르소나를 활성화합니다.
    -   "첨부된 코드의 전체 구조를 설명하고, `process_data` 함수의 비효율적인 부분을 찾아 리팩터링 방안을 제안해줘." 와 같이 구체적으로 요청합니다.
    -   AI는 첨부된 코드를 기반으로 상세한 분석과 개선된 코드 예시를 제공합니다.

3.  **코드 복사 및 적용:**
    -   제안된 코드 블록이 마음에 들면, `/copy 1` 명령어로 즉시 클립보드에 복사하여 에디터에 붙여넣습니다.

### 시나리오 2: 오류 메시지 디버깅

1.  **오류 로그 및 관련 코드 첨부:**
    -   오류가 발생한 터미널의 스택 트레이스(stack trace)를 복사하여 프롬프트에 붙여넣습니다.
    -   `/files` 명령어로 오류와 관련된 소스 코드 파일을 첨부합니다.

2.  **디버깅 요청:**
    -   "첨부된 `main.py` 코드와 아래 오류 로그를 보고, '배열 인덱스 초과' 에러의 원인이 되는 부분을 찾아 수정해줘." 라고 질문합니다.

3.  **수정 전/후 코드 비교 (Diff):**
    -   AI가 수정된 코드 블록을 제안하면, 기존 코드와 어떻게 다른지 확인하고 싶을 수 있습니다.
    -   이때 `/diff_code` 명령어로 TUI Diff 뷰어를 열어 두 코드의 차이점을 시각적으로 명확하게 비교하고, 변경 사항의 타당성을 검토합니다.

### 시나리오 3: 새로운 기술 학습

1.  **학습 모드 및 질문:**
    -   `/mode general` 또는 `/mode teacher`로 AI의 역할을 설정합니다.
    -   "Python의 `asyncio`와 `threading`의 차이점을 설명하고, 각각 어떤 상황에 사용하는 것이 적합한지 예제 코드와 함께 알려줘." 와 같이 질문합니다.

2.  **즐겨찾기 저장:**
    -   답변 내용이 유용하여 나중에 다시 보고 싶다면, `/savefav asyncio_vs_thread` 명령어로 마지막 질문을 즐겨찾기에 저장합니다.
    -   나중에 `/usefav asyncio_vs_thread`로 똑같은 질문을 다시 하거나, `/favs`로 저장된 목록을 확인할 수 있습니다.

---

## 🛠️ 주요 명령어 목록

| 명령어 | 설명 |
|---|---|
| `/commands` | 전체 명령어 목록을 보여줍니다. |
| `/pretty_print` | Rich 기반의 미려한 출력을 켜고 끕니다. |
| `/raw` | 마지막 AI 응답을 순수 텍스트로 다시 출력합니다. |
| `/select_model` | TUI를 열어 사용 가능한 모델 목록에서 모델을 선택합니다. |
| `/search_models <키워드>` | OpenRouter에서 모델을 검색하여 `ai_models.txt`에 추가합니다. |
| `/all_files` | TUI 파일 선택기를 엽니다. |
| `/files <경로>...` | 지정된 파일이나 디렉터리를 대화에 첨부합니다. |
| `/clearfiles` | 현재 첨부된 모든 파일을 초기화합니다. |
| `/mode <dev\|general\|teacher>` | AI의 페르소나(시스템 프롬프트)를 변경합니다. |
| `/savefav <이름>` | 마지막 질문을 즐겨찾기에 저장합니다. |
| `/usefav <이름>` | 저장된 즐겨찾기 질문을 불러옵니다. |
| `/favs` | 저장된 모든 즐겨찾기 목록을 보여줍니다. |
| `/diff_code` | TUI 코드 비교 뷰어를 엽니다. |
| `/show_context` | 현재 대화의 토큰 사용량에 대한 상세 보고서를 봅니다. |
| `/reset` | 현재 세션을 백업하고 초기화합니다. |
| `/restore` | 백업된 세션 목록에서 선택하여 복원합니다. |
| `/copy <번호>` | 답변의 N번째 코드 블록을 클립보드에 복사합니다. |
| `/exit` | 프로그램을 종료합니다. |

<br>
<hr>
<br>

# GPT-CLI Pro — The Developer’s AI CLI (English Version)

**GPT-CLI Pro** is a conversational AI client meticulously engineered to integrate seamlessly into a developer's terminal workflow. Powered by OpenRouter's universal API, it allows you to effortlessly switch between state-of-the-art language models like Claude 3, GPT-4o, and Llama 3. It transcends simple Q&A, offering a suite of powerful features focused on maximizing developer productivity across all stages of development—from code analysis and review to debugging and learning.

---

## ✨ Core Features

-   **Intelligent Streaming Output**: Renders AI responses in real-time with a beautiful UI powered by the Rich library.
    -   **Reasoning Live**: Displays the "thought process" of supported models in a separate, fixed-height panel, offering transparency into how answers are generated.
    -   **Code Live**: Code block panels dynamically adjust their height to fit the content, capping at a maximum height with a "...N lines omitted..." indicator to keep the view clean.
-   **Robust Code Block Parser**: Accurately recognizes a wide variety of code block formats generated by LLMs.
    -   Supports deeply indented fences (e.g., inside lists), and fences using both backticks (```) and tildes (~~~).
    -   Intelligently avoids misinterpreting inline triple-backticks as code blocks.
    -   Manages nested code blocks by tracking depth, ensuring correct parsing.
-   **Powerful File Attachments**:
    -   Attach project context safely and quickly using a TUI file selector (`/all_files`) that respects `.gptignore` rules.
    -   Supports various file types, including images (.png, .jpg), PDFs, and source code.
-   **TUI-based Interfaces**:
    -   **Diff Viewer (`/diff_code`)**: Visually compare two code blocks (e.g., before and after a change). Dynamically adjust context lines (+/-), toggle a full-file view (f), and scroll horizontally (←/→) through long lines. Features accurate, Pygments-based syntax highlighting.
    -   **Model Selection & Search (`/select_model`, `/search_models`)**: Easily switch models from your `ai_models.txt` list or discover and add new ones from OpenRouter.
-   **Efficient Context Management**:
    -   **Compact Mode**: Drastically reduces token usage in long conversations by compressing file attachments in past messages into simple placeholders (`[Attachment: filename]`).
    -   **Context Report (`/show_context`)**: Visually breaks down token usage against the model's limit, detailing the cost of the system prompt, reserved space, and attachments.
-   **Safe Clipboard Copy (`/copy`)**:
    -   Instantly copy code from responses using the `/copy <number>` command.
    -   Includes a built-in fallback for environments where clipboard access fails (like SSH sessions), reprinting the code as raw text for easy manual selection and copying.

---

## 🚀 Installation and Setup

### Step 1: Clone the Repository
First, clone or download this repository to your local machine. You will need this path to register it as a global command.
```bash
git clone https://github.com/your-username/gpt-cli-pro.git
cd gpt-cli-pro
```

### Step 2: Install Dependencies
`gptcli` uses several Python libraries. Install them all at once with this command:
```bash
pip install -r requirements.txt
```

### Step 3: Set API Key
Create a `.env` file in the project root directory and add your OpenRouter API key. You can get a key from the [OpenRouter Dashboard](https://openrouter.ai/keys).
```env
OPENROUTER_API_KEY="sk-or-..."
```

### Step 4: Global Configuration Directory
`gptcli` uses a configuration folder at `~/.config/gptcli` in your home directory. The following files are auto-generated on the first run:
-   `~/.config/gptcli/ai_models.txt`: An editable list of available AI models.
-   `~/.config/gptcli/.gptignore_default`: Global ignore rules for files/directories.

---

## ⚙️ Using as a Global Command (Run `gptcli` Anywhere)

Running the script with `python gptcli.py` every time is inconvenient. Set it up as a global command so you can run `gptcli` from any directory in your terminal.

### Linux & macOS

The recommended method is to create a symbolic link in a directory that is in your system's `PATH`.

1.  **Grant Execute Permissions:**
    Make the `gptcli.py` file executable.
    ```bash
    chmod +x gptcli.py
    ```

2.  **Create Symbolic Link:**
    `/usr/local/bin` is included in the `PATH` on most systems by default.
    ```bash
    # Create a link named 'gptcli' pointing to the full path of your script.
    # Example path: /home/user/projects/gpt-cli-pro/gptcli.py
    sudo ln -s /path/to/your/gpt-cli-pro/gptcli.py /usr/local/bin/gptcli
    ```

3.  **Verify:**
    Open a new terminal session or run `rehash` (zsh) / `hash -r` (bash), then type the following command from any directory:
    ```bash
    gptcli --help 
    ```
    If the help message appears, the setup was successful.

### Windows

The standard method is to add the script's folder to the system's `Path` environment variable.

1.  **Add Folder to Path:**
    -   Go to `System Properties` -> `Advanced` -> `Environment Variables`.
    -   Under `System variables` or `User variables`, find `Path`, select it, and click `Edit`.
    -   Click `New` and add the full path to the folder containing `gptcli.py` (e.g., `C:\Projects\gpt-cli-pro`).

2.  **Verify:**
    Open a new Command Prompt or PowerShell window and run the following command from any directory:
    ```powershell
    gptcli.py --help
    ```
    To run it without the `.py` extension, ensure `.PY` is included in your `PATHEXT` environment variable and rename
    `gptcli.py` to `gptcli`.

---

## 💡 Common Workflows

`gptcli` is a practical tool designed to assist with daily developer tasks.

### Scenario 1: Analyzing Existing Code and Getting Refactoring Suggestions

1.  **Attach Project Files:**
    -   Launch `gptcli` and attach the files you want to analyze.
    -   Use `/all_files` to open the TUI file picker or `/files src/main.py src/utils/` to specify paths directly.
2.  **Request Analysis:**
    -   Switch to an expert persona with `/mode teacher`.
    -   Prompt: "Analyze the attached code, explain the overall architecture, and suggest a more efficient way to refactor the `process_data` function."
3.  **Copy & Apply:**
    -   Use `/copy 1` to instantly copy the suggested code block to your clipboard and paste it into your editor.

### Scenario 2: Debugging an Error

1.  **Provide Context:**
    -   Paste the stack trace from your terminal directly into the prompt.
    -   Attach the relevant source code file(s) with `/files`.
2.  **Ask for a Fix:**
    -   Prompt: "Based on the attached `main.py` and the error log below, find and fix the 'index out of bounds' error."
3.  **Diff the Changes:**
    -   When the AI provides a fixed code block, use `/diff_code` to open the TUI and visually compare the original code with the suggested fix to validate the changes.

### Scenario 3: Learning a New Technology

1.  **Set the Mode and Ask:**
    -   Use `/mode teacher` to set the AI's role.
    -   Prompt: "Explain the difference between `asyncio` and `threading` in Python, and provide code examples for when to use each."
2.  **Save as Favorite:**
    -   If the answer is useful, save it for later with `/savefav asyncio_vs_thread`.
    -   You can ask the same question again with `/usefav asyncio_vs_thread` or view your list with `/favs`.

---

## 🛠️ Command Reference

| Command | Description |
|---|---|
| `/commands` | Show the list of all available commands. |
| `/pretty_print` | Toggle the beautiful Rich-based output on/off. |
| `/raw` | Reprint the last AI response as raw text. |
| `/select_model` | Open a TUI to select a different AI model. |
| `/search_models <keyword>`| Search for models on OpenRouter and add them to `ai_models.txt`. |
| `/all_files` | Open the TUI file selector. |
| `/files <path>...`| Attach specified files or directories to the conversation. |
| `/clearfiles` | Clear all currently attached files. |
| `/mode <dev\|general\|teacher>`| Change the AI's persona (system prompt). |
| `/savefav <name>` | Save the last prompt as a favorite. |
| `/usefav <name>` | Use a saved favorite prompt. |
| `/favs` | List all saved favorites. |
| `/diff_code` | Open the TUI code comparison viewer. |
| `/show_context` | View a detailed report of current token usage. |
| `/reset` | Reset the current session after backing it up. |
| `/restore` | Restore a session from a backup. |
| `/copy <n>` | Copy the nth code block from the last response. |
| `/exit` | Exit the program. |