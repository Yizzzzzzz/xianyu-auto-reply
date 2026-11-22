"""
AI 服务商 API Key 对称加解密与脱敏工具

使用标准对称加密 Fernet（AES-128-CBC + HMAC-SHA256）。
密钥通过参数传入，不在此模块直接读取全局配置，便于单测与隔离。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def _get_fernet_instance(fernet_key: str | bytes) -> Fernet:
    """获取 Fernet 实例，校验密钥有效性"""
    if not fernet_key:
        raise ValueError("未配置 AI 服务商加密密钥，请在配置或环境变量中设置 AI_PROVIDER_ENCRYPTION_KEY")
    if isinstance(fernet_key, str):
        fernet_key = fernet_key.strip().encode("utf-8")
    try:
        return Fernet(fernet_key)
    except Exception:
        raise ValueError("AI 服务商加密密钥格式无效，必须为有效的 Fernet 密钥") from None


def encrypt_api_key(plaintext: str, fernet_key: str | bytes) -> str:
    """使用 Fernet 加密 API Key 明文

    Args:
        plaintext: 明文 API Key。
        fernet_key: Fernet 密钥（str 或 bytes）。

    Returns:
        str: Fernet 加密后的密文字符串。
    """
    if not plaintext:
        return ""
    fernet = _get_fernet_instance(fernet_key)
    encrypted_bytes = fernet.encrypt(plaintext.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_api_key(ciphertext: str, fernet_key: str | bytes) -> str:
    """使用 Fernet 解密 API Key 密文

    Args:
        ciphertext: Fernet 密文字符串。
        fernet_key: Fernet 密钥（str 或 bytes）。

    Returns:
        str: 解密后的明文 API Key。
    """
    if not ciphertext:
        return ""
    fernet = _get_fernet_instance(fernet_key)
    try:
        decrypted_bytes = fernet.decrypt(ciphertext.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken:
        raise ValueError("解密 AI 服务商 API Key 失败，请检查加密密钥是否正确") from None
    except Exception:
        raise ValueError("解密 AI 服务商 API Key 失败，密文格式无效") from None


def mask_api_key(key: str) -> str:
    """对已解密的明文 API Key 进行脱敏掩码

    规则：
    - 长度 >= 8：保留前 4 位 + '****' + 后 4 位
    - 长度 < 8：返回 '****'

    Args:
        key: 明文 API Key。

    Returns:
        str: 脱敏掩码字符串。
    """
    if not key:
        return ""
    if len(key) >= 8:
        return f"{key[:4]}****{key[-4:]}"
    return "****"
