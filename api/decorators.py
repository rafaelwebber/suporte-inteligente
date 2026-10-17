from datetime import datetime, timezone
from functools import wraps
from types import SimpleNamespace
from typing import Any

from flask import g, jsonify, request

from api.extensions import get_auth, get_doc

ROLE_ADMIN = "admin"
ROLE_AGENT = "agent"
ROLES = {ROLE_ADMIN, ROLE_AGENT}


def _extract_token() -> str | None:
    token = request.headers.get("token_request")
    if token:
        return token.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_token_request(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "Token de acesso obrigatório."}), 401

        try:
            claims = get_auth().verify_id_token(token, check_revoked=True, clock_skew_seconds=60)
        except Exception:
            return jsonify({"error": "Token inválido ou expirado."}), 401

        profile = get_doc("profiles", claims["uid"])
        if not profile:
            return jsonify({"error": "Usuário sem perfil cadastrado."}), 403

        g.current_user = SimpleNamespace(
            id=claims["uid"],
            email=claims.get("email"),
            role=profile.get("role") or ROLE_AGENT,
            team_id=profile.get("team_id"),
        )
        g.access_token = token
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """Usar abaixo de @require_token_request."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if g.current_user.role != ROLE_ADMIN:
            return jsonify({"error": "Apenas administradores podem fazer isso."}), 403
        return fn(*args, **kwargs)

    return wrapper


def is_admin() -> bool:
    return g.current_user.role == ROLE_ADMIN


def client_ip() -> str:
    # Com TRUSTED_PROXIES > 0 o ProxyFix já troca remote_addr pelo IP real do cliente.
    return request.remote_addr or "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
