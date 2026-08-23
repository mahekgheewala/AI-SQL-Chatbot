import os
import hashlib
import hmac
from passlib.context import CryptContext
from cryptography.fernet import Fernet
from utils.config_loader import init_env

init_env()

# Bcrypt Context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Fernet Context — fail fast on a missing/insecure key. A randomly generated
# key on every restart would silently invalidate all previously encrypted
# PostgreSQL passwords, so that insecure fallback is removed.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "").strip()
if not ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY is not configured. Set a strong Fernet key in backend/.env "
        "before starting the server. A random key must NOT be generated at startup "
        "because it would make all stored encrypted credentials undecryptable."
    )

fernet = Fernet(ENCRYPTION_KEY.encode())

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def encrypt_data(data: str) -> str:
    if not data:
        return ""
    return fernet.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    if not encrypted_data:
        return ""
    return fernet.decrypt(encrypted_data.encode()).decode()

# ─── Token hashing (refresh / password-reset / email-verification tokens) ─────
# Raw tokens are never stored in the database; only a SHA-256 digest is kept.
# SHA-256 (instead of bcrypt) avoids bcrypt's 72-byte truncation issue with
# long JWT-style tokens and is cheap to verify.
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def verify_token(token: str, token_hash: str) -> bool:
    if not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)
