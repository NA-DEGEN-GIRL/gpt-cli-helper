#!/usr/bin/env python3
"""BracketedPaste 테스트 스크립트"""

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

def main():
    bindings = KeyBindings()
    pasted_content = None
    paste_counter = 0

    @bindings.add(Keys.BracketedPaste)
    def handle_paste(event):
        nonlocal pasted_content, paste_counter
        data = event.data

        # 다양한 줄바꿈 문자 처리 (\r\n, \r, \n)
        normalized = data.replace('\r\n', '\n').replace('\r', '\n')
        lines = normalized.split('\n')
        line_count = len(lines)

        print(f"\n[DEBUG] BracketedPaste 감지! 줄 수: {line_count}")
        print(f"[DEBUG] 원본 길이: {len(data)}, 줄바꿈 타입: {'CRLF' if chr(13)+chr(10) in data else ('CR' if chr(13) in data else 'LF')}")

        if line_count >= 5:  # 테스트용으로 5줄로 낮춤
            paste_counter += 1
            pasted_content = data
            preview = lines[0][:30] + "..." if len(lines[0]) > 30 else lines[0]
            collapsed = f"[Pasted #{paste_counter} +{line_count} lines: {preview}]"
            event.current_buffer.insert_text(collapsed)
            print(f"[DEBUG] 압축 표시 적용: {collapsed}")
        else:
            pasted_content = None
            event.current_buffer.insert_text(data)
            print("[DEBUG] 짧은 텍스트, 그대로 삽입")

    session = PromptSession(
        key_bindings=bindings,
        multiline=True
    )

    print("=" * 50)
    print("BracketedPaste 테스트")
    print("5줄 이상 텍스트를 붙여넣어 보세요")
    print("Alt+Enter로 입력 완료")
    print("=" * 50)

    try:
        result = session.prompt("> ")
        print(f"\n[결과] 입력창 텍스트: {result[:100]}...")
        if pasted_content:
            print(f"[결과] 저장된 원본: {len(pasted_content)} chars, {len(pasted_content.split(chr(10)))} lines")
        else:
            print("[결과] 저장된 원본 없음")
    except (KeyboardInterrupt, EOFError):
        print("\n종료")

if __name__ == "__main__":
    main()
