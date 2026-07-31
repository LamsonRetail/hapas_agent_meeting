"""
Mã hóa token khi lưu (Fernet, đối xứng có xác thực).

Token nằm trong SQLite trên máy local. Mất file .db = cả công ty enroll
lại (V2_LONGTERM §2), nên file được mã hóa để backup/copy an toàn — key
để ở .env, tách khỏi file .db.

Sinh key một lần:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
rồi đặt vào V2_FERNET_KEY trong .env.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from . import config


class CryptoError(RuntimeError):
    pass


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        if not config.FERNET_KEY:
            raise CryptoError(
                "Thiếu V2_FERNET_KEY trong .env.\n"
                "Sinh key: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        try:
            _fernet = Fernet(config.FERNET_KEY.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise CryptoError(f"V2_FERNET_KEY không hợp lệ: {exc}") from exc
    return _fernet


def encrypt(plaintext: str) -> str:
    """Mã hóa chuỗi -> token base64 để lưu vào DB."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Giải mã token base64 từ DB. Ném CryptoError nếu key sai/hỏng."""
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CryptoError(
            "Giải mã token thất bại — sai V2_FERNET_KEY hoặc dữ liệu hỏng."
        ) from exc
