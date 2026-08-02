from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.reader_chat.worker import _build_messages

pytestmark = pytest.mark.unit


def test_build_messages_keeps_recent_dialogue_as_non_evidence_framing() -> None:
    messages, *_ = _build_messages(
        SimpleNamespace(
            system_prompt="reader system",
            max_input_tokens=8_000,
            max_output_tokens=2_000,
        ),
        {
            "user_body": "那之后发生了什么？",
            "allowed_evidence_ids": ["selection:1"],
            "evidence": [],
            "dialogue": [
                {"role": "user", "body": "主角为什么离开？", "sequence": 1},
                {"role": "assistant", "body": "原文显示他选择了离开。", "sequence": 2},
            ],
            "manifest_checksum": "a" * 64,
        },
    )

    payload = json.loads(
        messages[1]["content"].split("UNTRUSTED_DATA_BEGIN\n", 1)[1]
        .split("\nUNTRUSTED_DATA_END", 1)[0]
    )
    assert payload["conversational_framing_not_evidence"][0]["body"] == (
        "主角为什么离开？"
    )
    assert payload["conversational_framing_not_evidence"][1]["role"] == "assistant"
