from typing import Any

from google.cloud.firestore import Increment, Query
from google.cloud.firestore_v1 import FieldFilter

from api.config import Config
from api.decorators import ROLE_ADMIN, utc_now
from api.extensions import get_db, get_doc
from api.services import ticket_service
from api.text_utils import anonymize, keywords

MAX_QUERY_TERMS = 30
MAX_CANDIDATES = 500


def _rank(query_terms: list[str], limit: int) -> list[tuple[Any, float]]:
    candidates = (
        get_db()
        .collection("tickets")
        .where(filter=FieldFilter("keywords", "array_contains_any", query_terms))
        .limit(MAX_CANDIDATES)
        .stream()
    )
    terms = set(query_terms)
    scored = []
    for doc in candidates:
        data = doc.to_dict()
        if not data.get("successful"):
            continue
        matched = terms & set(data.get("keywords") or [])
        in_title = terms & set(data.get("title_keywords") or [])
        score = (len(matched) + len(in_title)) / (2 * len(terms))
        votes = (data.get("helpful_count") or 0) - (data.get("not_helpful_count") or 0)
        scored.append((doc, round(score, 3), votes))

    scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return [(doc, score) for doc, score, _ in scored[:limit]]


def suggest(*, text: str, user) -> tuple[dict[str, Any] | None, str | None, int]:
    text = anonymize((text or "").strip())
    if not text:
        return None, "Descreva o problema do cliente.", 400
    terms = keywords(text, limit=MAX_QUERY_TERMS)
    if not terms:
        return None, "Descreva o problema com mais detalhes.", 400

    ranked = _rank(terms, Config.SUGGESTIONS_LIMIT)
    results = []
    for doc, score in ranked:
        ticket = ticket_service.public(doc)
        results.append(
            {
                "ticket_id": ticket["id"],
                "score": score,
                "title": ticket.get("title"),
                "category": ticket.get("category"),
                "product": ticket.get("product"),
                "resolution": ticket.get("resolution"),
                "resolved_at": ticket.get("resolved_at"),
                "helpful_count": ticket.get("helpful_count", 0),
                "not_helpful_count": ticket.get("not_helpful_count", 0),
            }
        )

    _, ref = get_db().collection("suggestion_queries").add(
        {
            "user_id": user.id,
            "team_id": user.team_id,
            "query": text,
            "terms": terms,
            "results": [{"ticket_id": r["ticket_id"], "score": r["score"]} for r in results],
            "feedback": {},
            "has_feedback": False,
            "helpful": False,
            "created_at": utc_now(),
        }
    )
    return {"query_id": ref.id, "results": results}, None, 200


def give_feedback(
    *, query_id: str, ticket_id: str, helpful: Any, comment: str | None, user
) -> tuple[bool, str | None, int]:
    if not isinstance(helpful, bool):
        return False, "helpful deve ser true ou false.", 400
    snapshot = get_doc("suggestion_queries", query_id)
    if not snapshot:
        return False, "Consulta não encontrada.", 404
    query = snapshot.to_dict()
    if query.get("user_id") != user.id and user.role != ROLE_ADMIN:
        return False, "Sem acesso a esta consulta.", 403
    if ticket_id not in {r["ticket_id"] for r in query.get("results") or []}:
        return False, "Este ticket não faz parte das sugestões desta consulta.", 400

    feedback = dict(query.get("feedback") or {})
    previous = feedback.get(ticket_id, {}).get("helpful")
    feedback[ticket_id] = {
        "helpful": helpful,
        "comment": anonymize((comment or "").strip()) or None,
        "at": utc_now(),
    }
    snapshot.reference.update(
        {
            "feedback": feedback,
            "has_feedback": True,
            "helpful": any(f.get("helpful") for f in feedback.values()),
        }
    )

    if previous != helpful:
        ticket = get_doc("tickets", ticket_id)
        if ticket:
            counters = {"helpful_count" if helpful else "not_helpful_count": Increment(1)}
            if previous is not None:
                counters["helpful_count" if previous else "not_helpful_count"] = Increment(-1)
            ticket.reference.update(counters)
    return True, None, 200


def _count(query) -> int:
    return int(query.count().get()[0][0].value)


def stats() -> dict[str, Any]:
    queries = get_db().collection("suggestion_queries")
    total = _count(queries)
    with_feedback = _count(queries.where(filter=FieldFilter("has_feedback", "==", True)))
    helpful = _count(queries.where(filter=FieldFilter("helpful", "==", True)))
    tickets = get_db().collection("tickets")

    top = (
        tickets.order_by("helpful_count", direction=Query.DESCENDING).limit(5).stream()
    )
    return {
        "queries": total,
        "with_feedback": with_feedback,
        "helpful": helpful,
        "helpful_rate": round(helpful / with_feedback, 3) if with_feedback else None,
        "knowledge_base": _count(tickets.where(filter=FieldFilter("successful", "==", True))),
        "tickets": _count(tickets),
        "top_tickets": [
            {"id": doc.id, "title": doc.get("title"), "helpful_count": doc.get("helpful_count")}
            for doc in top
            if (doc.to_dict().get("helpful_count") or 0) > 0
        ],
    }
