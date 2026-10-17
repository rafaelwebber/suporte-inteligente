import csv
import hashlib
import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from google.cloud.firestore import Query
from google.cloud.firestore_v1 import FieldFilter

from api.config import Config
from api.decorators import ROLE_ADMIN, utc_now
from api.extensions import get_db, get_doc, to_dict
from api.services import audit_service
from api.text_utils import anonymize, keywords, normalize

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUSES = {STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_RESOLVED}

TEXT_FIELDS = ("title", "description", "category", "product", "resolution")
INTERNAL_FIELDS = ("keywords", "title_keywords")
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


# ---------------------------------------------------------------- regras

def is_successful(ticket: dict[str, Any]) -> bool:
    """Critério de êxito: resolvido, com solução descrita, nunca reaberto e sem nota ruim do cliente."""
    satisfaction = ticket.get("satisfaction")
    return (
        ticket.get("status") == STATUS_RESOLVED
        and bool((ticket.get("resolution") or "").strip())
        and (ticket.get("reopen_count") or 0) == 0
        and (satisfaction is None or satisfaction >= Config.TICKET_SUCCESS_MIN_CSAT)
    )


def _derived_fields(ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "successful": is_successful(ticket),
        "keywords": keywords(
            ticket.get("title"),
            ticket.get("description"),
            ticket.get("category"),
            ticket.get("product"),
            " ".join(ticket.get("tags") or []),
        ),
        "title_keywords": keywords(ticket.get("title")),
    }


def _clean_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return anonymize(text) or None


def _clean_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    if not isinstance(value, list):
        return []
    return sorted({str(tag).strip().lower() for tag in value if str(tag).strip()})[:20]


def _clean_satisfaction(value: Any) -> tuple[int | None, str | None]:
    if value in (None, ""):
        return None, None
    try:
        number = round(float(str(value).replace(",", ".")))
    except ValueError:
        return None, "Nota de satisfação deve ser um número de 1 a 5."
    if not 1 <= number <= 5:
        return None, "Nota de satisfação deve ser um número de 1 a 5."
    return number, None


def public(snapshot) -> dict[str, Any]:
    ticket = to_dict(snapshot)
    for field in INTERNAL_FIELDS:
        ticket.pop(field, None)
    ticket.setdefault("helpful_count", 0)
    ticket.setdefault("not_helpful_count", 0)
    return ticket


def can_read(ticket: dict[str, Any], user) -> bool:
    """Atendente vê os tickets da própria equipe e qualquer ticket da base de conhecimento (com êxito)."""
    return user.role == ROLE_ADMIN or ticket.get("team_id") == user.team_id or bool(ticket.get("successful"))


def can_write(ticket: dict[str, Any], user) -> bool:
    return user.role == ROLE_ADMIN or ticket.get("team_id") == user.team_id


# ---------------------------------------------------------------- CRUD

def list_tickets(
    *, user, status: str | None = None, successful: bool | None = None, team_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    if user.role != ROLE_ADMIN:
        team_id = user.team_id

    query = get_db().collection("tickets")
    filters = {"team_id": team_id, "status": status, "successful": successful}
    active = {k: v for k, v in filters.items() if v is not None}
    for field, value in active.items():
        query = query.where(filter=FieldFilter(field, "==", value))

    if not active:
        docs = query.order_by("created_at", direction=Query.DESCENDING).limit(limit).stream()
        return [public(doc) for doc in docs]

    # Igualdade + order_by exigiria índice composto no Firestore; ordena em memória.
    tickets = [public(doc) for doc in query.stream()]
    tickets.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return tickets[:limit]


def get_ticket(ticket_id: str, user) -> tuple[dict[str, Any] | None, str | None, int]:
    snapshot = get_doc("tickets", ticket_id)
    if not snapshot:
        return None, "Ticket não encontrado.", 404
    ticket = public(snapshot)
    if not can_read(ticket, user):
        return None, "Sem acesso a este ticket.", 403
    return ticket, None, 200


def create_ticket(*, data: dict[str, Any], user, ip: str | None) -> tuple[dict[str, Any] | None, str | None, int]:
    ticket: dict[str, Any] = {field: _clean_text(data.get(field)) for field in TEXT_FIELDS}
    if not ticket["title"] or not ticket["description"]:
        return None, "title e description são obrigatórios.", 400

    team_id = data.get("team_id") if user.role == ROLE_ADMIN and data.get("team_id") else user.team_id
    if not get_doc("teams", team_id):
        return None, "Equipe não encontrada.", 404

    status = data.get("status") or STATUS_OPEN
    if status not in STATUSES:
        return None, "Status inválido.", 400
    if status == STATUS_RESOLVED and not ticket["resolution"]:
        return None, "Informe a solução para marcar o ticket como resolvido.", 400

    satisfaction, error = _clean_satisfaction(data.get("satisfaction"))
    if error:
        return None, error, 400

    now = utc_now()
    ticket.update(
        {
            "tags": _clean_tags(data.get("tags")),
            "team_id": team_id,
            "status": status,
            "satisfaction": satisfaction,
            "reopen_count": 0,
            "helpful_count": 0,
            "not_helpful_count": 0,
            "source": "manual",
            "external_id": None,
            "created_by": user.id,
            "created_at": now,
            "updated_at": now,
            "resolved_by": user.id if status == STATUS_RESOLVED else None,
            "resolved_at": now if status == STATUS_RESOLVED else None,
        }
    )
    ticket.update(_derived_fields(ticket))

    _, ref = get_db().collection("tickets").add(ticket)
    audit_service.log_action(
        actor_id=user.id,
        action="ticket_created",
        resource_type="ticket",
        resource_id=ref.id,
        details={"title": ticket["title"], "status": status},
        ip_address=ip,
    )
    return public(ref.get()), None, 201


def update_ticket(
    *, ticket_id: str, data: dict[str, Any], user, ip: str | None
) -> tuple[dict[str, Any] | None, str | None, int]:
    snapshot = get_doc("tickets", ticket_id)
    if not snapshot:
        return None, "Ticket não encontrado.", 404
    current = snapshot.to_dict()
    if not can_write(current, user):
        return None, "Sem permissão para alterar este ticket.", 403

    updates: dict[str, Any] = {}
    for field in TEXT_FIELDS:
        if field in data:
            updates[field] = _clean_text(data[field])
    if "title" in updates and not updates["title"]:
        return None, "title não pode ficar vazio.", 400
    if "description" in updates and not updates["description"]:
        return None, "description não pode ficar vazio.", 400
    if "tags" in data:
        updates["tags"] = _clean_tags(data["tags"])
    if "satisfaction" in data:
        satisfaction, error = _clean_satisfaction(data["satisfaction"])
        if error:
            return None, error, 400
        updates["satisfaction"] = satisfaction
    if "team_id" in data and data["team_id"] != current.get("team_id"):
        if user.role != ROLE_ADMIN:
            return None, "Apenas administradores podem mover tickets de equipe.", 403
        if not get_doc("teams", data["team_id"]):
            return None, "Equipe não encontrada.", 404
        updates["team_id"] = data["team_id"]

    now = utc_now()
    old_status = current.get("status")
    new_status = data.get("status", old_status)
    if new_status not in STATUSES:
        return None, "Status inválido.", 400
    if new_status != old_status:
        updates["status"] = new_status
        if new_status == STATUS_RESOLVED:
            updates["resolved_at"] = now
            updates["resolved_by"] = user.id
        elif old_status == STATUS_RESOLVED:
            updates["reopen_count"] = (current.get("reopen_count") or 0) + 1
            updates["resolved_at"] = None

    merged = {**current, **updates}
    if merged.get("status") == STATUS_RESOLVED and not merged.get("resolution"):
        return None, "Informe a solução para marcar o ticket como resolvido.", 400
    if not updates:
        return None, "Nada para atualizar.", 400

    updates.update(_derived_fields(merged))
    updates["updated_at"] = now
    snapshot.reference.update(updates)

    audit_service.log_action(
        actor_id=user.id,
        action="ticket_updated",
        resource_type="ticket",
        resource_id=ticket_id,
        details={"fields": sorted(k for k in updates if k not in INTERNAL_FIELDS and k != "updated_at")},
        ip_address=ip,
    )
    return public(snapshot.reference.get()), None, 200


def delete_ticket(*, ticket_id: str, actor_id: str, ip: str | None) -> tuple[bool, str | None, int]:
    snapshot = get_doc("tickets", ticket_id)
    if not snapshot:
        return False, "Ticket não encontrado.", 404
    snapshot.reference.delete()
    audit_service.log_action(
        actor_id=actor_id,
        action="ticket_deleted",
        resource_type="ticket",
        resource_id=ticket_id,
        details={"title": snapshot.get("title")},
        ip_address=ip,
    )
    return True, None, 200


# ---------------------------------------------------------------- importação CSV

COLUMN_ALIASES = {
    "external_id": {"external_id", "id_externo", "id", "numero", "ticket", "ticket_id", "protocolo"},
    "title": {"title", "titulo", "assunto", "subject"},
    "description": {"description", "descricao", "problema", "mensagem"},
    "category": {"category", "categoria"},
    "product": {"product", "produto", "sistema"},
    "tags": {"tags", "etiquetas"},
    "team": {"team", "equipe", "time", "grupo"},
    "status": {"status", "situacao"},
    "resolution": {"resolution", "solucao", "resolucao", "resposta"},
    "satisfaction": {"satisfaction", "nota", "csat", "satisfacao"},
    "reopen_count": {"reopen_count", "reaberturas", "reaberto"},
    "created_at": {"created_at", "aberto_em", "data_abertura", "criado_em"},
    "resolved_at": {"resolved_at", "resolvido_em", "data_resolucao", "fechado_em"},
}

STATUS_ALIASES = {
    STATUS_RESOLVED: {"resolved", "resolvido", "solved", "fechado", "closed", "concluido", "finalizado"},
    STATUS_IN_PROGRESS: {"in_progress", "em andamento", "andamento", "em atendimento", "pending", "pendente"},
    STATUS_OPEN: {"open", "aberto", "novo", "new"},
}

DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_date(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)


def _parse_status(value: str | None, has_resolution: bool) -> str | None:
    value = normalize(value or "")
    if not value:
        return STATUS_RESOLVED if has_resolution else STATUS_OPEN
    for status, aliases in STATUS_ALIASES.items():
        if value in aliases:
            return status
    return None


def _read_csv(content: bytes) -> csv.DictReader:
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    mapping: dict[str, str] = {}
    for header in reader.fieldnames or []:
        normalized = normalize(header).replace(" ", "_")
        for field, aliases in COLUMN_ALIASES.items():
            if normalized in aliases and field not in mapping.values():
                mapping[header] = field
                break
    reader.fieldnames = [mapping.get(h, h) for h in reader.fieldnames or []]
    return reader


def import_csv(
    *, content: bytes, default_team_id: str, actor_id: str, ip: str | None
) -> tuple[dict[str, Any] | None, str | None, int]:
    if not get_doc("teams", default_team_id):
        return None, "Equipe padrão não encontrada.", 404

    db = get_db()
    teams_by_name = {normalize(t.get("name") or ""): t.id for t in db.collection("teams").stream()}

    reader = _read_csv(content)
    if not {"title", "description"} <= set(reader.fieldnames or []):
        return None, "O CSV precisa ter ao menos as colunas titulo e descricao.", 400

    batch = db.batch()
    pending = imported = 0
    errors: list[str] = []
    now = utc_now()

    for line, row in enumerate(reader, start=2):
        title = _clean_text(row.get("title"))
        description = _clean_text(row.get("description"))
        if not title or not description:
            errors.append(f"Linha {line}: titulo e descricao são obrigatórios.")
            continue

        resolution = _clean_text(row.get("resolution"))
        status = _parse_status(row.get("status"), bool(resolution))
        if not status:
            errors.append(f"Linha {line}: status '{row.get('status')}' não reconhecido.")
            continue
        satisfaction, error = _clean_satisfaction(row.get("satisfaction"))
        if error:
            errors.append(f"Linha {line}: {error}")
            continue
        try:
            reopen_count = int(row.get("reopen_count") or 0)
        except ValueError:
            reopen_count = 1 if normalize(row.get("reopen_count") or "") in {"sim", "yes", "true"} else 0

        created_at = _parse_date(row.get("created_at")) or now
        resolved_at = _parse_date(row.get("resolved_at")) if status == STATUS_RESOLVED else None
        external_id = (row.get("external_id") or "").strip() or None

        ticket = {
            "title": title,
            "description": description,
            "category": _clean_text(row.get("category")),
            "product": _clean_text(row.get("product")),
            "resolution": resolution,
            "tags": _clean_tags(row.get("tags") or ""),
            "team_id": teams_by_name.get(normalize(row.get("team") or ""), default_team_id),
            "status": status,
            "satisfaction": satisfaction,
            "reopen_count": reopen_count,
            "source": "import",
            "external_id": external_id,
            "created_by": actor_id,
            "created_at": created_at,
            "updated_at": now,
            "resolved_by": None,
            "resolved_at": resolved_at or (created_at if status == STATUS_RESOLVED else None),
        }
        ticket.update(_derived_fields(ticket))

        collection = db.collection("tickets")
        if external_id:
            doc_id = "imp-" + hashlib.sha1(external_id.encode()).hexdigest()[:24]
            batch.set(collection.document(doc_id), ticket, merge=True)
        else:
            batch.set(collection.document(), ticket)
        pending += 1
        imported += 1
        if pending >= 400:
            batch.commit()
            batch, pending = db.batch(), 0

    if pending:
        batch.commit()

    audit_service.log_action(
        actor_id=actor_id,
        action="tickets_imported",
        resource_type="ticket",
        details={"imported": imported, "errors": len(errors)},
        ip_address=ip,
    )
    return {"imported": imported, "skipped": len(errors), "errors": errors[:50]}, None, 200
