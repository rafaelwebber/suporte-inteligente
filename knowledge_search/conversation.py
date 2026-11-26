"""Sessões locais do piloto; isolamento por usuário e repetição idempotente."""
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from api.text_utils import anonymize
from .case_cards import prepare_cards
from .config import SearchError
from .text import clean_text
from .dialogue_context import case_observation, matching_ticket_cards, retrieval_query, topic_terms
from .generation import KnowledgeGenerator, SHORTLIST_LIMIT
from .service import ticket_references

MAX_TURNS = 6
RETRYABLE = {"rejected", "unavailable", "provider_busy", "rate_limited", "busy"}


def compact(turns):
    """Preserva o relato inicial e os últimos pares pergunta/resposta, sem fatos inferidos."""
    if len(turns) == 1:
        return turns[0]["message"][:1800]
    parts = ["Relato inicial: " + turns[0]["message"][:350]]
    prior = (turns[-1].get("prior_answer") or {}).get("paragraphs", [])
    allowance = (1240 if prior else 1400) // (len(turns) - 1)
    for index, turn in enumerate(turns[1:], 1):
        if index == len(turns) - 1 and prior:
            parts.append("Orientação anterior do assistente: " + prior[0]["text"][:110])
        if turn.get("asked"):
            parts.append("Pergunta anterior: " + turn["asked"][:allowance // 3])
        parts.append("Resposta do usuário: " + case_observation(turn)[:allowance * 2 // 3])
    return "\n".join(parts)[:2000]


class ConversationStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, turns TEXT NOT NULL,
            request_id TEXT, request_message TEXT, response TEXT,
            lease_until REAL NOT NULL DEFAULT 0, updated REAL NOT NULL)""")
        return db

    def begin(self, owner, session_id, request_id, message):
        if session_id is not None and not isinstance(session_id, str):
            raise SearchError("Referência de conversa inválida.", 400)
        for value in (request_id, session_id if session_id is not None else request_id):
            if not isinstance(value, str) or len(value) != 36:
                raise SearchError("Referência de conversa inválida.", 400)
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise SearchError("Referência de conversa inválida.", 400) from exc
        if not isinstance(message, str) or not 2 <= len(message.strip()) <= 1800:
            raise SearchError("Escreva entre 2 e 1.800 caracteres.", 400)
        message = clean_text(anonymize(message.strip()))
        identifier = session_id or request_id
        now = time.time()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            retry = False
            if row is None:
                if session_id:
                    raise SearchError("Conversa não encontrada ou expirada. Inicie uma nova investigação.", 404)
                if db.execute("SELECT 1 FROM conversations WHERE id=?", (identifier,)).fetchone():
                    raise SearchError("Conversa não encontrada.", 404)
                db.execute("INSERT INTO conversations(id,owner,turns,updated) VALUES (?,?,?,?)", (identifier, owner, "[]", now))
                turns = []
            else:
                if row["request_id"] == request_id and row["request_message"] != message:
                    raise SearchError("Use uma nova referência para uma nova mensagem.", 409)
                if row["request_id"] == request_id and row["response"]:
                    return identifier, None, json.loads(row["response"])
                previous = json.loads(row["response"]) if row["response"] else {}
                if row["lease_until"] > now:
                    raise SearchError("Aguarde a resposta em andamento.", 409)
                turns = json.loads(row["turns"])
                retry = (previous.get("generation_status") in RETRYABLE and turns
                         and turns[-1]["message"] == message)
                if len(turns) >= MAX_TURNS and not retry:
                    raise SearchError("Esta investigação atingiu seis mensagens. Inicie uma nova investigação.", 400)
            asked = json.loads(row["response"]).get("follow_up", "") if row and row["response"] else ""
            if not retry:
                turn = {"message": message, "asked": asked}
                prior_answer = previous.get("generated_answer") if row else None
                if prior_answer:
                    turn["prior_answer"] = prior_answer
                turns.append(turn)
            # A classificação sob demanda pode acrescentar uma chamada antes da síntese.
            db.execute("UPDATE conversations SET lease_until=?,updated=? WHERE id=?", (now + 360, now, identifier))
        return identifier, turns, None

    def finish(self, owner, identifier, request_id, turns, response):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE conversations SET turns=?,request_id=?,request_message=?,response=?,
                lease_until=0,updated=? WHERE id=? AND owner=?""",
                       (json.dumps(turns, ensure_ascii=False), request_id, turns[-1]["message"],
                        json.dumps(response, ensure_ascii=False), time.time(), identifier, owner))

    def release(self, owner, identifier):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE conversations SET lease_until=0 WHERE id=? AND owner=?", (identifier, owner))

    def list_for_owner(self, owner):
        with closing(self.connect()) as db:
            rows = db.execute("""SELECT id, turns, updated FROM conversations
                WHERE owner=? ORDER BY updated DESC""", (owner,)).fetchall()
        investigations = []
        for row in rows:
            turns = json.loads(row["turns"] or "[]")
            if not turns:
                continue
            investigations.append({
                "session_id": row["id"],
                "title": turns[0].get("message", "Investigação"),
                "updated_at": row["updated"],
                "turn_count": len(turns),
            })
        return investigations

    def get_for_owner(self, owner, identifier):
        with closing(self.connect()) as db:
            row = db.execute("SELECT turns, response FROM conversations WHERE id=? AND owner=?",
                             (identifier, owner)).fetchone()
        if not row or not row["response"]:
            return None
        data = json.loads(row["response"])
        data["messages"] = json.loads(row["turns"] or "[]")
        data["session_id"] = identifier
        data.pop("generation_trace", None)
        return data

    def delete_for_owner(self, owner, identifier):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT lease_until FROM conversations WHERE id=? AND owner=?",
                             (identifier, owner)).fetchone()
            if not row:
                return False
            if row["lease_until"] > time.time():
                raise SearchError("Não é possível excluir uma investigação em andamento.", 409)
            try:
                db.execute("""DELETE FROM knowledge_event_sources WHERE request_id IN
                    (SELECT request_id FROM knowledge_events WHERE session_id=? AND user_id=?)""",
                    (identifier, owner))
                db.execute("DELETE FROM knowledge_events WHERE session_id=? AND user_id=?", (identifier, owner))
            except sqlite3.OperationalError:
                # As tabelas de métricas podem ainda não ter sido criadas.
                pass
            db.execute("DELETE FROM conversations WHERE id=? AND owner=?", (identifier, owner))
        return True


def reply(store, search, generator, owner, data):
    if not isinstance(data, dict) or data.keys() - {"message", "session_id", "request_id"}:
        raise SearchError("Envie message, request_id e, opcionalmente, session_id.", 400)
    identifier, turns, cached = store.begin(owner, data.get("session_id"), data.get("request_id"), data.get("message"))
    if cached is not None:
        return cached
    try:
        context = compact(turns)
        query = retrieval_query(turns)
        if query:
            result = search.search(query, 20, prefer_titles=True)
        else:
            # Sem assunto pesquisável, pedir contexto sem buscar palavras de instrução.
            result = {"query": "", "mode": "textual", "results": [], "warnings": [],
                      "generated_answer": None, "generation_status": "pending", "source_count": 0}
        result["topic_terms"] = topic_terms(turns)
        references = ticket_references(turns)
        if result["results"] or references:
            result = search.group_tickets(result, references)
        cards = result["results"]
        close_matches = matching_ticket_cards(cards, query) if not references else []
        selected_keys = None
        if not references and len(cards) > SHORTLIST_LIMIT:
            selected_keys, status = generator.shortlist(cards, context)
            result["triage"] = {"status": status, "candidates": len(cards),
                                "selected": len(selected_keys or [])}
            if status == "provider_busy":
                result["warnings"].append("O serviço de IA retornou HTTP 503 na triagem. A busca usou a seleção local.")
            elif selected_keys is None and status not in ("disabled", "not_configured"):
                result["warnings"].append("O serviço de IA não conseguiu comparar todos os resultados. A busca usou a seleção local.")
        elif not references and cards:
            selected_keys = [card["document_key"] for card in cards]
        if selected_keys is not None:
            by_key = {card["document_key"]: card for card in cards}
            to_open = [by_key[key] for key in selected_keys]
        else:
            to_open = cards
        if close_matches:
            ordered = {card["document_key"]: card for card in close_matches}
            for card in to_open:
                ordered.setdefault(card["document_key"], card)
            to_open = list(ordered.values())[:SHORTLIST_LIMIT]
            selected_keys = [card["document_key"] for card in to_open]
            if "triage" in result:
                result["triage"]["selected"] = len(selected_keys)
        evidence = search.evidence(result, selected_keys)
        evidence["comparison_keys"] = [card["document_key"] for card in close_matches]
        case_inputs = [] if isinstance(generator, KnowledgeGenerator) and generator.settings.status() == "ready" else None
        case_trace = {}
        if any(card.get("ticket_reference") for card in to_open):
            evidence["ticket_histories"] = search.open_ticket_histories(
                to_open, query, references, selected=selected_keys is not None, case_inputs=case_inputs,
            )
        if case_inputs:
            evidence["case_cards"], case_trace = prepare_cards(store, generator, case_inputs)
        for field in ("matched_records", "explicit_tickets"):
            result.pop(field, None)
        questions = [t["asked"] for t in turns if t["asked"]]
        decision = generator.converse(evidence, context, questions, len(turns) < MAX_TURNS and len(questions) < 3 and not references)
        trace = decision.pop("generation_trace", {})
        result["generation_trace"] = {
            "selected_sources": [item["source_id"] for item in evidence["results"]],
            "consulted_interactions": [item["source_id"] for item in evidence.get("ticket_histories", [])],
            "case_cards": case_trace,
            **trace,
        }
        result.update(decision, session_id=identifier, turn=len(turns),
                      messages=turns,
                      generation_available=generator.settings.status() == "ready")
        store.finish(owner, identifier, data["request_id"], turns, result)
        return result
    except BaseException:
        store.release(owner, identifier)
        raise
