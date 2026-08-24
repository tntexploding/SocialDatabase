"""跨终端编码稳定的文本与 JSON 输出。"""

import json
import sys
from typing import Any, TextIO


def format_json(payload: Any) -> str:
    """返回仅含 ASCII 字节表示、语义无损的标准 JSON。"""

    return json.dumps(payload, ensure_ascii=True, indent=2)


def safe_print(value: object = "", *, file: TextIO | None = None) -> None:
    """打印文本，并把当前终端无法编码的字符转换为可见转义。"""

    stream = file if file is not None else sys.stdout
    text = str(value)
    encoding = getattr(stream, "encoding", None)
    if encoding:
        text = text.encode(
            encoding,
            errors="backslashreplace",
        ).decode(encoding)
    print(text, file=stream)
