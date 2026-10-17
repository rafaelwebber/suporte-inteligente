from typing import Any

from google.cloud.firestore import Query

from api.config import Config
from api.decorators import ROLE_AGENT, ROLES, utc_now
from api.extensions import get_auth, get_db, get_doc, revoke_sessions, to_dict
from api.services import audit_service


def validate_password(password: str | None) -> str | None:
    if not password or len(password) < Config.PASSWORD_MIN_LENGTH:
        return f"A senha precisa ter pelo menos {Config.PASSWORD_MIN_LENGTH} caracteres."
    return None


def _team_summary(team_id: str | None, cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Equivalente ao join profiles -> teams(id, name) do Postgres."""
    if cache is not None and team_id in cache:
        return cache[team_id]
    snapshot = get_doc("teams", team_id)
    summary = {"id": snapshot.id, "name": snapshot.get("name")} if snapshot else None
    if cache is not None:
        cache[team_id] = summary
    return summary


def _with_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    profile.setdefault("role", ROLE_AGENT)
    profile.setdefault("disabled", False)
    return profile


def get_profile(user_id: str) -> dict[str, Any] | None:
    snapshot = get_doc("profiles", user_id)
    if not snapshot:
        return None
    profile = _with_defaults(to_dict(snapshot))
    profile["teams"] = _team_summary(profile.get("team_id"))
    return profile


def list_users() -> list[dict[str, Any]]:
    docs = (
        get_db()
        .collection("profiles")
        .order_by("created_at", direction=Query.DESCENDING)
        .stream()
    )
    cache: dict[str, Any] = {}
    users = []
    for doc in docs:
        profile = _with_defaults(to_dict(doc))
        profile["teams"] = _team_summary(profile.get("team_id"), cache)
        users.append(profile)
    return users


def create_user(
    *,
    email: str,
    password: str,
    full_name: str | None,
    team_id: str,
    role: str | None,
    actor_id: str,
    ip: str | None,
) -> tuple[dict[str, Any] | None, str | None, int]:
    email = (email or "").strip().lower()
    role = role or ROLE_AGENT
    if not email or not password or not team_id:
        return None, "email, password e team_id são obrigatórios.", 400
    if role not in ROLES:
        return None, "Perfil inválido.", 400
    error = validate_password(password)
    if error:
        return None, error, 400

    if not get_doc("teams", team_id):
        return None, "Equipe não encontrada.", 404

    auth = get_auth()
    try:
        user = auth.create_user(
            email=email,
            password=password,
            display_name=full_name or None,
            email_verified=True,
        )
    except Exception as exc:
        return None, f"Falha ao criar usuário no Auth: {exc}", 400

    now = utc_now()
    profile_payload = {
        "email": email,
        "full_name": full_name,
        "team_id": team_id,
        "role": role,
        "disabled": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        get_db().collection("profiles").document(user.uid).set(profile_payload)
    except Exception as exc:
        try:
            auth.delete_user(user.uid)
        except Exception:
            pass
        return None, f"Falha ao criar profile: {exc}", 400

    profile = get_profile(user.uid)
    audit_service.log_action(
        actor_id=actor_id,
        action="user_created",
        resource_type="user",
        resource_id=user.uid,
        details={"email": email, "team_id": team_id, "role": role},
        ip_address=ip,
    )
    return profile, None, 201


def update_user(
    *,
    user_id: str,
    changes: dict[str, Any],
    actor_id: str,
    ip: str | None,
) -> tuple[dict[str, Any] | None, str | None, int]:
    existing = get_doc("profiles", user_id)
    if not existing:
        return None, "Usuário não encontrado.", 404

    updates: dict[str, Any] = {}
    if "full_name" in changes:
        updates["full_name"] = (changes["full_name"] or "").strip() or None
    if "team_id" in changes:
        if not get_doc("teams", changes["team_id"]):
            return None, "Equipe não encontrada.", 404
        updates["team_id"] = changes["team_id"]
    if "role" in changes:
        if changes["role"] not in ROLES:
            return None, "Perfil inválido.", 400
        if user_id == actor_id and changes["role"] != existing.get("role"):
            return None, "Não é permitido alterar o próprio perfil de acesso.", 400
        updates["role"] = changes["role"]
    if "disabled" in changes:
        if user_id == actor_id and changes["disabled"]:
            return None, "Não é permitido desativar o próprio usuário.", 400
        updates["disabled"] = bool(changes["disabled"])
    if not updates:
        return None, "Nada para atualizar.", 400

    auth = get_auth()
    try:
        auth_updates: dict[str, Any] = {}
        if "full_name" in updates:
            auth_updates["display_name"] = updates["full_name"]
        if "disabled" in updates:
            auth_updates["disabled"] = updates["disabled"]
        if auth_updates:
            auth.update_user(user_id, **auth_updates)
        if updates.get("disabled"):
            revoke_sessions(user_id)
    except Exception as exc:
        return None, f"Falha ao atualizar usuário no Auth: {exc}", 400

    updates["updated_at"] = utc_now()
    existing.reference.update(updates)

    audit_service.log_action(
        actor_id=actor_id,
        action="user_updated",
        resource_type="user",
        resource_id=user_id,
        details={k: v for k, v in updates.items() if k != "updated_at"},
        ip_address=ip,
    )
    return get_profile(user_id), None, 200


def reset_password(
    *, user_id: str, password: str, actor_id: str, ip: str | None
) -> tuple[bool, str | None, int]:
    existing = get_doc("profiles", user_id)
    if not existing:
        return False, "Usuário não encontrado.", 404
    error = validate_password(password)
    if error:
        return False, error, 400

    try:
        auth = get_auth()
        auth.update_user(user_id, password=password)
        revoke_sessions(user_id)
    except Exception as exc:
        return False, f"Falha ao redefinir senha: {exc}", 400

    audit_service.log_action(
        actor_id=actor_id,
        action="user_password_reset",
        resource_type="user",
        resource_id=user_id,
        details={"email": existing.get("email")},
        ip_address=ip,
    )
    return True, None, 200


def delete_user(
    *,
    user_id: str,
    actor_id: str,
    ip: str | None,
) -> tuple[bool, str | None, int]:
    if not user_id:
        return False, "user_id obrigatório.", 400
    if user_id == actor_id:
        return False, "Não é permitido excluir o próprio usuário.", 400

    existing = get_doc("profiles", user_id)
    if not existing:
        return False, "Usuário não encontrado.", 404

    email = existing.get("email")

    try:
        get_auth().delete_user(user_id)
    except Exception as exc:
        return False, f"Falha ao remover usuário no Auth: {exc}", 400

    try:
        existing.reference.delete()
    except Exception as exc:
        return False, f"Usuário removido do Auth, mas falha ao remover profile: {exc}", 500

    audit_service.log_action(
        actor_id=actor_id,
        action="user_deleted",
        resource_type="user",
        resource_id=user_id,
        details={"email": email},
        ip_address=ip,
    )
    return True, None, 200
