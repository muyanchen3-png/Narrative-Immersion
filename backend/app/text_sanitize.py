"""面向终端用户的正文清理（模型思考链等不回显）。"""

from __future__ import annotations

import re


def strip_thinking_blocks(text: str) -> str:
    """去掉各类「思考 / chain-of-thought」片段，只保留对用户可见的正文。"""

    if not text or not isinstance(text, str):
        return text
    s = text
    # MiniMax / 通用 XML 风格
    s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<thinking>[\s\S]*?</thinking>", "", s, flags=re.IGNORECASE)
    # 反斜杠指令块（Ollama / 部分本地模型等）
    s = re.sub(r"\\think[\s\S]*?\\end", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\\think[\s\S]*?\\endthink", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
