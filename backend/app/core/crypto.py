"""
加密工具模块

提供 Fernet 对称加密功能，用于保护敏感数据（如 API Key）。

使用方式:
  from app.core.crypto import encrypt_text, decrypt_text
  encrypted = encrypt_text("sk-xxx")
  original = decrypt_text(encrypted)

密钥管理:
  - 数据加密使用独立的 NOVELMIND_ENCRYPTION_KEY
  - previous_encryption_keys 支持密钥轮换期间读取旧密文
  - 无前缀值按历史明文兼容，下一次更新时自动写为版本化密文
"""

import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings

_PREFIX = "enc:v1:"


def _derive_fernet_key(secret: str) -> bytes:
    """
    从应用密钥派生 Fernet 密钥。

    Fernet 要求 32 字节 base64 编码的密钥。
    使用 SHA-256 哈希后将结果 base64 编码。
    """
    key_hash = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(key_hash)


def _create_keyring() -> MultiFernet:
    secrets = [settings.encryption_key]
    secrets.extend(
        value.strip()
        for value in settings.previous_encryption_keys.split(",")
        if value.strip()
    )
    # The previous implementation derived encryption from secret_key.
    secrets.append(settings.secret_key)
    unique_secrets = list(dict.fromkeys(secrets))
    return MultiFernet(
        [Fernet(_derive_fernet_key(secret)) for secret in unique_secrets]
    )


_keyring = _create_keyring()


def encrypt_text(plaintext: str | None) -> str | None:
    """
    加密明文文本。

    Args:
        plaintext: 要加密的字符串，为 None 时直接返回 None

    Returns:
        加密后的 base64 编码字符串，或 None
    """
    if plaintext is None:
        return None
    token = _keyring.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt_text(ciphertext: str | None) -> str | None:
    """
    解密加密文本。

    Args:
        ciphertext: 加密后的 base64 编码字符串，为 None 时直接返回 None

    Returns:
        解密后的原始字符串，或 None

    Raises:
        ValueError: 密钥无效或密文被篡改时
    """
    if ciphertext is None:
        return None
    token = ciphertext.removeprefix(_PREFIX)
    try:
        return _keyring.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        if not ciphertext.startswith(_PREFIX):
            return ciphertext
        raise ValueError("解密失败：密钥无效或密文已损坏")
