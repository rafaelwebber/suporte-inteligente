from typing import Any

from google.cloud.firestore import Query
from google.cloud.firestore_v1 import FieldFilter

from api.decorators import utc_now
from api.extensions import get_db, get_doc, to_dict
from api.services import audit_service


def list_teams() -> list[dict[str, Any]]:
    docs = (
        get_db()
        .collection("teams")
        .order_by("created_at", direction=Query.DESCENDING)
        .stream()
    )
    return [to_dict(doc) for doc in docs]


def _name_taken(name: str, except_id: str | None = None) -> bool:
    found = get_db().collection("teams").where(filter=FieldFilter("name", "==", name)).limit(2).get()
    return any(doc.id != except_id for doc in found)


def create_team(
    *,
    name: str,
    actor_id: str,
    ip: str | None,
) -> tuple[dict[str, Any] | None, str | None, int]:
    name = (name or "").strip()
    if not name:
        return None, "name é obrigatório.", 400
    if _name_taken(name):
        return None, "Já existe uma equipe com esse nome.", 409

    payload = {"name": name, "created_by": actor_id, "created_at": utc_now()}
    try:
        _, ref = get_db().collection("teams").add(payload)
    except Exception as exc:
        return None, f"Falha ao criar equipe: {exc}", 400

    team = to_dict(ref.get())
    audit_service.log_action(
        actor_id=actor_id,
        action="team_created",
        resource_type="team",
        resource_id=team["id"],
        details={"name": name},
        ip_address=ip,
    )
    return team, None, 201


def rename_team(
    *, team_id: str, name: str, actor_id: str, ip: str | None
) -> tuple[dict[str, Any] | None, str | None, int]:
    existing = get_doc("teams", team_id)
    if not existing:
        return None, "Equipe não encontrada.", 404
    name = (name or "").strip()
    if not name:
        return None, "name é obrigatório.", 400
    if _name_taken(name, except_id=team_id):
        return None, "Já existe uma equipe com esse nome.", 409

    old_name = existing.get("name")
    existing.reference.update({"name": name, "updated_at": utc_now()})
    audit_service.log_action(
        actor_id=actor_id,
        action="team_renamed",
        resource_type="team",
        resource_id=team_id,
        details={"from": old_name, "to": name},
        ip_address=ip,
    )
    return to_dict(existing.reference.get()), None, 200


def _in_use(collection: str, team_id: str) -> bool:
    return bool(
        get_db().collection(collection).where(filter=FieldFilter("team_id", "==", team_id)).limit(1).get()
    )


def delete_team(*, team_id: str, actor_id: str, ip: str | None) -> tuple[bool, str | None, int]:
    existing = get_doc("teams", team_id)
    if not existing:
        return False, "Equipe não encontrada.", 404
    if _in_use("profiles", team_id):
        return False, "A equipe tem usuários. Mova-os para outra equipe antes de excluir.", 409
    if _in_use("tickets", team_id):
        return False, "A equipe tem tickets vinculados e não pode ser excluída.", 409

    existing.reference.delete()
    audit_service.log_action(
        actor_id=actor_id,
        action="team_deleted",
        resource_type="team",
        resource_id=team_id,
        details={"name": existing.get("name")},
        ip_address=ip,
    )
    return True, None, 200
