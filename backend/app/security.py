"""Authentication primitives.

Passwords are bcrypt-hashed. Access tokens are HS256 JWTs minted and verified
with the gateway engine's own crypto, so the dashboard and tool calls share one
audited token machinery.
"""

from __future__ import annotations

import time

import bcrypt
from mcp_gateway.auth import OAuth2Verifier, encode_jwt_hs256
from mcp_gateway.identity import ClientIdentity

from .config import AuthConfig


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def mint_access_token(auth: AuthConfig, *, subject: str, roles: list[str]) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "roles": roles,
        "iss": auth.issuer,
        "aud": auth.audience,
        "iat": now,
        "exp": now + auth.access_token_ttl_minutes * 60,
    }
    return encode_jwt_hs256(payload, auth.jwt_secret)


def verify_access_token(auth: AuthConfig, token: str) -> ClientIdentity | None:
    verifier = OAuth2Verifier(
        algorithm="HS256",
        secret=auth.jwt_secret,
        issuer=auth.issuer,
        audience=auth.audience,
    )
    identity, _error = verifier.verify(token)
    return identity
