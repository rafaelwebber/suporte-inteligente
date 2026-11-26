"""Agregados de uso da RAG sem persistir o texto das consultas."""
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


def _source_key(item):
    ticket = str(item.get("ticket_reference") or "")
    package = str(item.get("package_id") or "")
    if ticket:
        return f"ticket:{package}:{ticket}"
    return f"document:{item.get('document_key') or item.get('source_id') or item.get('title') or 'unknown'}"


def _source_type(item):
    if item.get("ticket_reference"):
        return "ticket"
    kind = (item.get("metadata") or {}).get("tipo_registro")
    return "article" if kind == "artigo" else "document"


def _source(item):
    title = re.sub(r"</?b>", "", str(item.get("title") or "")).strip()
    return {
        "source_key": _source_key(item),
        "source_type": _source_type(item),
        "label": title or (f"Ticket #{item['ticket_reference']}" if item.get("ticket_reference") else str(item.get("source_id") or "Documento")),
        "ticket_reference": str(item.get("ticket_reference") or ""),
        "package_id": str(item.get("package_id") or ""),
    }


def _citations(response, by_key, by_ticket):
    answer = response.get("generated_answer") or {}
    paragraphs = list(answer.get("paragraphs") or [])
    paragraphs.extend(answer.get("historical_findings") or [])
    cited = {}
    for paragraph in paragraphs:
        for citation in paragraph.get("citations") or []:
            key = citation.get("document_key")
            package = str(citation.get("package_id") or "")
            ticket = str(citation.get("ticket_reference") or "")
            item = by_key.get(key) or by_ticket.get((package, ticket))
            if item:
                cited[item["source_key"]] = item
            else:
                fallback = {**citation, "title": f"Ticket #{ticket}" if ticket else citation.get("source_id")}
                normalized = _source(fallback)
                cited[normalized["source_key"]] = normalized
    return list(cited.values())


def record(path, *, request_id, session_id, user_id, team_id, response, duration_ms):
    if not isinstance(request_id, str) or not request_id or not isinstance(session_id, str) or not session_id:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    results = response.get("results") or []
    retrieved = {_source(item)["source_key"]: _source(item) for item in results if isinstance(item, dict)}
    by_key = {str(item.get("document_key")): source for item in results
              if isinstance(item, dict) and item.get("document_key")
              for source in [_source(item)]}
    by_ticket = {(str(item.get("package_id") or ""), str(item.get("ticket_reference") or "")): source
                 for item in results if isinstance(item, dict) and item.get("ticket_reference")
                 for source in [_source(item)]}
    cited = _citations(response, by_key, by_ticket)
    decision = response.get("decision") or "insufficient"
    if response.get("generated_answer") and cited:
        outcome = "answered_with_evidence"
    elif decision == "ask":
        outcome = "clarification"
    elif response.get("generated_answer"):
        outcome = "answered"
    elif response.get("generation_status") in {"rejected", "unavailable", "provider_busy", "rate_limited", "busy"}:
        outcome = "generation_failure"
    else:
        outcome = "insufficient"
    usage = response.get("usage") or {}
    sources = [("retrieved", item) for item in retrieved.values()]
    sources.extend(("cited", item) for item in cited)

    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS knowledge_events (
            request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
            team_id TEXT, created_at REAL NOT NULL, is_new INTEGER NOT NULL,
            outcome TEXT NOT NULL, generation_status TEXT, source_count INTEGER NOT NULL,
            input_tokens INTEGER NOT NULL, duration_ms INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS knowledge_event_sources (
            request_id TEXT NOT NULL, source_key TEXT NOT NULL, stage TEXT NOT NULL,
            source_type TEXT NOT NULL, label TEXT NOT NULL, ticket_reference TEXT,
            package_id TEXT, PRIMARY KEY (request_id, source_key, stage))""")
        db.execute("CREATE INDEX IF NOT EXISTS knowledge_events_created_idx ON knowledge_events(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS knowledge_events_team_idx ON knowledge_events(team_id, created_at)")
        inserted = db.execute("""INSERT OR IGNORE INTO knowledge_events
            (request_id,session_id,user_id,team_id,created_at,is_new,outcome,generation_status,source_count,input_tokens,duration_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (request_id, session_id, str(user_id), str(team_id or ""), time.time(),
             int(request_id == session_id), outcome, str(response.get("generation_status") or ""),
             len(retrieved), max(0, int(usage.get("input_tokens") or 0)), max(0, int(duration_ms)))).rowcount
        if inserted:
            db.executemany("""INSERT OR IGNORE INTO knowledge_event_sources
                (request_id,source_key,stage,source_type,label,ticket_reference,package_id)
                VALUES (?,?,?,?,?,?,?)""",
                [(request_id, item["source_key"], stage, item["source_type"], item["label"],
                  item["ticket_reference"], item["package_id"]) for stage, item in sources])


def stats(path, *, days=30, team_id="", teams=()):
    path = Path(path)
    names = {str(team.get("id")): team.get("name") or "Equipe sem nome" for team in teams}
    if not path.exists():
        return _empty(days, names)
    since = time.time() - days * 86400
    with closing(sqlite3.connect(path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        try:
            events = db.execute("SELECT * FROM knowledge_events WHERE created_at >= ? AND (? = '' OR team_id = ?)",
                                (since, team_id, team_id)).fetchall()
            sources = db.execute("""SELECT s.*,e.session_id FROM knowledge_event_sources s
                JOIN knowledge_events e USING(request_id)
                WHERE e.created_at >= ? AND (? = '' OR e.team_id = ?)""",
                (since, team_id, team_id)).fetchall()
        except sqlite3.OperationalError:
            return _empty(days, names)

    event_count = len(events)
    investigations = sum(row["is_new"] for row in events)
    active_users = len({row["user_id"] for row in events})
    answered_with_evidence = sum(row["outcome"] == "answered_with_evidence" for row in events)
    outcome_labels = {
        "answered_with_evidence": "Respondida com evidência",
        "answered": "Respondida",
        "clarification": "Pediu esclarecimento",
        "insufficient": "Sem evidência suficiente",
        "generation_failure": "Falha na geração",
    }
    outcomes = defaultdict(int)
    team_counts = defaultdict(lambda: {"investigations": 0, "users": set()})
    daily = defaultdict(int)
    for row in events:
        outcomes[row["outcome"]] += 1
        team = team_counts[row["team_id"] or ""]
        team["investigations"] += row["is_new"]
        team["users"].add(row["user_id"])
        if row["is_new"]:
            date = datetime.fromtimestamp(row["created_at"], timezone.utc).astimezone(LOCAL_TZ).date().isoformat()
            daily[date] += 1

    teams_list = [{"team_id": key, "name": names.get(key, "Equipe removida" if key else "Sem equipe"),
                   "investigations": value["investigations"], "active_users": len(value["users"])}
                  for key, value in team_counts.items()]
    teams_list.sort(key=lambda item: (-item["investigations"], -item["active_users"], item["name"].casefold()))
    most_active = teams_list[0] if teams_list else None

    grouped = defaultdict(lambda: {"label": "", "ticket_reference": "", "appearances": 0,
                                   "queries": set(), "investigations": set()})
    for row in sources:
        if row["stage"] != "retrieved":
            continue
        item = grouped[(row["source_type"], row["source_key"])]
        item["label"] = row["label"]
        item["ticket_reference"] = row["ticket_reference"] or ""
        item["appearances"] += 1
        item["queries"].add(row["request_id"])
        item["investigations"].add(row["session_id"])
    top = {"ticket": [], "article": [], "cited": []}
    for (kind, key), value in grouped.items():
        item = {"source_key": key, "title": value["label"], "ticket_reference": value["ticket_reference"],
                "appearances": value["appearances"], "queries": len(value["queries"]),
                "investigations": len(value["investigations"])}
        if kind in ("ticket", "article"):
            top[kind].append(item)
    cited_grouped = defaultdict(lambda: {"label": "", "ticket_reference": "", "appearances": 0,
                                         "queries": set(), "investigations": set()})
    for row in sources:
        if row["stage"] != "cited":
            continue
        item = cited_grouped[(row["source_type"], row["source_key"])]
        item["label"] = row["label"]
        item["ticket_reference"] = row["ticket_reference"] or ""
        item["appearances"] += 1
        item["queries"].add(row["request_id"])
        item["investigations"].add(row["session_id"])
    cited_items = [{"source_key": key, "title": value["label"], "ticket_reference": value["ticket_reference"],
                    "appearances": value["appearances"], "queries": len(value["queries"]),
                    "investigations": len(value["investigations"])}
                   for (_, key), value in cited_grouped.items()]
    top["cited"] = cited_items
    for values in top.values():
        values.sort(key=lambda item: (-item["investigations"], -item["queries"], item["title"].casefold()))
        del values[5:]

    start_date = datetime.fromtimestamp(since, timezone.utc).astimezone(LOCAL_TZ).date()
    end_date = datetime.now(LOCAL_TZ).date()
    trend = []
    current = start_date
    while current <= end_date:
        day = current.isoformat()
        trend.append({"date": day, "investigations": daily.get(day, 0)})
        current = current.fromordinal(current.toordinal() + 1)
    return {
        "days": days, "team_id": team_id,
        "summary": {"investigations": investigations, "messages": event_count, "active_users": active_users,
                    "evidence_rate": round(answered_with_evidence / event_count, 3) if event_count else None,
                    "avg_duration_ms": round(sum(row["duration_ms"] for row in events) / event_count) if event_count else None,
                    "input_tokens": sum(row["input_tokens"] for row in events),
                    "most_active_team": most_active},
        "trend": trend, "teams": teams_list,
        "outcomes": [{"key": key, "label": outcome_labels.get(key, key), "count": count}
                     for key, count in sorted(outcomes.items(), key=lambda pair: (-pair[1], pair[0]))],
        "top_tickets": top["ticket"], "top_articles": top["article"], "top_cited": top["cited"],
    }


def _empty(days, names):
    return {"days": days, "team_id": "", "summary": {"investigations": 0, "messages": 0, "active_users": 0,
            "evidence_rate": None, "avg_duration_ms": None, "input_tokens": 0, "most_active_team": None},
            "trend": [], "teams": [], "outcomes": [], "top_tickets": [], "top_articles": [], "top_cited": []}
