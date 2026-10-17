from typing import Any

from google.cloud.firestore import Query

from api.decorators import utc_now
from api.extensions import get_db, to_dict


def log_action(
    *,
    actor_id: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> dict[str, Any] | None:
    payload = {
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details or {},
        "ip_address": ip_address,
        "created_at": utc_now(),
    }
    _, ref = get_db().collection("audit_logs").add(payload)
    return to_dict(ref.get())


def list_logs(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    docs = (
        get_db()
        .collection("audit_logs")
        .order_by("created_at", direction=Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    logs = [to_dict(doc) for doc in docs]

    actor_ids = {log["actor_id"] for log in logs if log.get("actor_id")}
    refs = [get_db().collection("profiles").document(uid) for uid in actor_ids]
    emails = {snap.id: snap.get("email") for snap in get_db().get_all(refs) if snap.exists} if refs else {}
    for log in logs:
        log["actor_email"] = emails.get(log.get("actor_id"))
    return logs
