from datetime import timedelta
from typing import Any
from urllib.parse import quote

import requests

from api.config import Config
from api.decorators import utc_now
from api.extensions import get_auth, get_db, revoke_sessions
from api.services import audit_service, user_service

SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"


CREDENTIAL_ERRORS = {
    "INVALID_LOGIN_CREDENTIALS",
    "EMAIL_NOT_FOUND",
    "INVALID_PASSWORD",
    "INVALID_EMAIL",
    "MISSING_PASSWORD",
    "USER_DISABLED",
}

REFRESH_ERRORS = {
    "TOKEN_EXPIRED",
    "USER_DISABLED",
    "USER_NOT_FOUND",
    "INVALID_REFRESH_TOKEN",
    "MISSING_REFRESH_TOKEN",
    "INVALID_GRANT_TYPE",
}


class FirebaseAuthUnavailable(Exception):
    pass


class FirebaseRateLimited(Exception):
    pass


# Bloqueio por e-mail+IP (protege a conta sem deixar um terceiro bloqueá-la de outro IP)
# e por IP sozinho, com limite maior (barra força bruta em vários e-mails a partir do mesmo IP).
def _limits(email: str, ip: str) -> list[tuple[str, int]]:
    return [
        (f"email_ip:{email.lower().strip()}|{ip}", Config.LOGIN_MAX_ATTEMPTS),
        (f"ip:{ip}", Config.LOGIN_MAX_ATTEMPTS_PER_IP),
    ]


def _attempt_ref(identifier: str):
    return get_db().collection("login_attempts").document(quote(identifier, safe=""))


def _get_attempt(identifier: str) -> dict[str, Any] | None:
    snapshot = _attempt_ref(identifier).get()
    return snapshot.to_dict() if snapshot.exists else None


def is_locked(email: str, ip: str) -> tuple[bool, str | None]:
    now = utc_now()
    for identifier, _ in _limits(email, ip):
        row = _get_attempt(identifier)
        locked_until = row.get("locked_until") if row else None
        if locked_until and locked_until > now:
            return True, locked_until.isoformat()
    return False, None


def register_failed_login(email: str, ip: str) -> None:
    now = utc_now()
    window = timedelta(minutes=Config.LOGIN_LOCK_MINUTES)

    for identifier, max_attempts in _limits(email, ip):
        row = _get_attempt(identifier)
        expired = not row or (row.get("updated_at") and row["updated_at"] < now - window)
        failed = 1 if expired else row.get("failed_count", 0) + 1
        _attempt_ref(identifier).set(
            {
                "identifier": identifier,
                "failed_count": failed,
                "updated_at": now,
                "locked_until": now + window if failed >= max_attempts else None,
            }
        )


def reset_login_attempts(email: str, ip: str) -> None:
    identifier, _ = _limits(email, ip)[0]
    _attempt_ref(identifier).set(
        {
            "identifier": identifier,
            "failed_count": 0,
            "locked_until": None,
            "updated_at": utc_now(),
        }
    )


def _firebase_error(response: requests.Response) -> str:
    data = response.json() if response.content else {}
    return ((data.get("error") or {}).get("message") or f"HTTP {response.status_code}").split(" ")[0]


def _sign_in(email: str, password: str) -> dict[str, Any] | None:
    """Confere e-mail/senha no Firebase Auth. Retorna None se as credenciais não conferem."""
    if not Config.FIREBASE_WEB_API_KEY:
        raise FirebaseAuthUnavailable("FIREBASE_WEB_API_KEY não configurada.")
    try:
        response = requests.post(
            SIGN_IN_URL,
            params={"key": Config.FIREBASE_WEB_API_KEY},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise FirebaseAuthUnavailable(str(exc)) from exc

    if response.ok:
        return response.json()

    message = _firebase_error(response)
    if message in CREDENTIAL_ERRORS:
        return None
    if message == "TOO_MANY_ATTEMPTS_TRY_LATER":
        raise FirebaseRateLimited(message)
    raise FirebaseAuthUnavailable(message)


def _session_payload(user_id: str, email: str, id_token: str, refresh_token: str, expires_in: Any) -> dict[str, Any]:
    try:
        profile = user_service.get_profile(user_id)
    except Exception:
        profile = None
    return {
        "access_token": id_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(expires_in or 3600),
        "user": {"id": user_id, "email": email, "profile": profile},
    }


def login(email: str, password: str, ip: str) -> tuple[dict[str, Any] | None, str | None, int]:
    email = (email or "").strip().lower()
    if not email or not password:
        return None, "Email e senha são obrigatórios.", 400

    locked, until = is_locked(email, ip)
    if locked:
        audit_service.log_action(
            actor_id=None,
            action="login_blocked",
            resource_type="auth",
            details={"email": email, "locked_until": until},
            ip_address=ip,
        )
        return (
            None,
            f"Muitas tentativas falhas. Tente novamente após {until}.",
            429,
        )

    try:
        session = _sign_in(email, password)
    except FirebaseRateLimited:
        return None, "Muitas tentativas no Firebase. Aguarde alguns minutos.", 429
    except FirebaseAuthUnavailable as exc:
        return None, f"Falha ao contatar o Firebase Auth: {exc}", 502

    if not session:
        register_failed_login(email, ip)
        audit_service.log_action(
            actor_id=None,
            action="login_failed",
            resource_type="auth",
            details={"email": email},
            ip_address=ip,
        )
        return None, "Credenciais inválidas.", 401

    user_id = session["localId"]
    reset_login_attempts(email, ip)
    audit_service.log_action(
        actor_id=user_id,
        action="login_success",
        resource_type="auth",
        resource_id=user_id,
        details={"email": email},
        ip_address=ip,
    )
    payload = _session_payload(
        user_id, session.get("email", email), session["idToken"], session["refreshToken"], session.get("expiresIn")
    )
    return payload, None, 200


def refresh(refresh_token: str) -> tuple[dict[str, Any] | None, str | None, int]:
    if not refresh_token:
        return None, "refresh_token é obrigatório.", 400
    if not Config.FIREBASE_WEB_API_KEY:
        return None, "FIREBASE_WEB_API_KEY não configurada.", 502
    try:
        response = requests.post(
            REFRESH_URL,
            params={"key": Config.FIREBASE_WEB_API_KEY},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=10,
        )
    except requests.RequestException as exc:
        return None, f"Falha ao contatar o Firebase Auth: {exc}", 502

    if not response.ok:
        message = _firebase_error(response)
        if message in REFRESH_ERRORS:
            return None, "Sessão expirada. Entre novamente.", 401
        return None, f"Falha ao renovar sessão: {message}", 502

    data = response.json()
    user_id = data["user_id"]
    try:
        email = get_auth().get_user(user_id).email
    except Exception:
        email = None
    payload = _session_payload(user_id, email, data["id_token"], data["refresh_token"], data.get("expires_in"))
    return payload, None, 200


def logout(user_id: str, ip: str) -> None:
    """Revoga os refresh tokens: encerra as sessões do usuário em todos os dispositivos."""
    revoke_sessions(user_id)
    audit_service.log_action(
        actor_id=user_id,
        action="logout",
        resource_type="auth",
        resource_id=user_id,
        ip_address=ip,
    )


def change_password(
    *, user_id: str, email: str, current_password: str, new_password: str, ip: str
) -> tuple[dict[str, Any] | None, str | None, int]:
    if not current_password or not new_password:
        return None, "Senha atual e nova senha são obrigatórias.", 400
    error = user_service.validate_password(new_password)
    if error:
        return None, error, 400

    try:
        if not _sign_in(email, current_password):
            return None, "Senha atual incorreta.", 400
        get_auth().update_user(user_id, password=new_password)
        revoke_sessions(user_id)
        session = _sign_in(email, new_password)
    except FirebaseRateLimited:
        return None, "Muitas tentativas no Firebase. Aguarde alguns minutos.", 429
    except FirebaseAuthUnavailable as exc:
        return None, f"Falha ao contatar o Firebase Auth: {exc}", 502

    audit_service.log_action(
        actor_id=user_id,
        action="password_changed",
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
    )
    if not session:
        return None, "Senha alterada. Entre novamente.", 401
    payload = _session_payload(
        user_id, session.get("email", email), session["idToken"], session["refreshToken"], session.get("expiresIn")
    )
    return payload, None, 200
