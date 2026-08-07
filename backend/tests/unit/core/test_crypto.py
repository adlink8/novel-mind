"""Unit tests for app.core.crypto decrypt edge branches."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.core.crypto import decrypt_text, encrypt_text


def test_decrypt_none_returns_none():
    assert decrypt_text(None) is None


def test_encrypt_none_returns_none():
    assert encrypt_text(None) is None


def test_decrypt_corrupted_prefixed_ciphertext_raises():
    # Token carries the enc:v1: prefix but is not decryptable → ValueError.
    with pytest.raises(ValueError):
        decrypt_text("enc:v1:not-a-valid-fernet-token")


def test_decrypt_unprefixed_plaintext_returns_as_is():
    # InvalidToken on a value WITHOUT the prefix → legacy plaintext compatibility.
    assert decrypt_text("legacy-plaintext-key") == "legacy-plaintext-key"


def test_decrypt_prefixed_ciphertext_roundtrip():
    token = encrypt_text("secret-value")
    assert token.startswith("enc:v1:")
    assert decrypt_text(token) == "secret-value"
