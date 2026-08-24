"""终端编码与 JSON 输出回归测试。"""

import io
import json

from social_database.output import format_json, safe_print


def test_json_is_ascii_safe_and_round_trips_unicode():
    payload = {"value": "中文²"}

    rendered = format_json(payload)

    assert rendered.isascii()
    assert "\\u00b2" in rendered
    assert json.loads(rendered) == payload


def test_text_escapes_characters_the_stream_cannot_encode():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")

    safe_print("中文²", file=stream)
    stream.flush()
    rendered = raw.getvalue().decode("ascii")

    assert rendered.rstrip("\r\n") == "\\u4e2d\\u6587\\xb2"
    assert rendered.endswith(("\n", "\r\n"))
    stream.detach()
