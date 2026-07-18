import os
from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = "crypto_key.txt"


def _get_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    return key


_fernet = Fernet(_get_key())


def encrypt(plaintext):
    """Returns an encrypted string, or None if plaintext is empty/None."""
    if not plaintext:
        return None
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext):
    """Returns the decrypted string, or None if ciphertext is empty/None/invalid."""
    if not ciphertext:
        return None
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
