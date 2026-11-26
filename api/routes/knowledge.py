import sqlite3
import threading
import time

from flask import Blueprint, current_app, g, jsonify, request

from api.decorators import require_admin, require_token_request
from knowledge_search.config import SearchError
from knowledge_search.service import SearchService
from knowledge_search.generation import GeminiGenerator
from knowledge_search.openai_provider import OpenAIGenerator
from knowledge_search.conversation import ConversationStore, reply
from knowledge_search.analytics import record as record_analytics, stats as knowledge_stats
from api.services import team_service

knowledge_bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")
_lock = threading.Lock()


def service():
    # Instância por aplicação: mantém uma única matriz vetorial e modelo por processo.
    with _lock:
        if "knowledge_search" not in current_app.extensions:
            current_app.extensions["knowledge_search"] = SearchService(current_app.config["KNOWLEDGE_SEARCH_SETTINGS"])
        return current_app.extensions["knowledge_search"]


def generation_config():
    provider = current_app.config["KNOWLEDGE_LLM_PROVIDER"]
    if provider == "openai":
        return provider, OpenAIGenerator, current_app.config["OPENAI_SETTINGS"]
    if provider == "gemini":
        return provider, GeminiGenerator, current_app.config["GEMINI_SETTINGS"]
    raise SearchError("KNOWLEDGE_LLM_PROVIDER deve ser openai ou gemini.", 503)


def generator():
    with _lock:
        if "knowledge_generator" not in current_app.extensions:
            _, build, settings = generation_config()
            current_app.extensions["knowledge_generator"] = build(settings)
        return current_app.extensions["knowledge_generator"]


@knowledge_bp.errorhandler(SearchError)
def invalid(error):
    return jsonify(error=str(error)), error.status


@knowledge_bp.errorhandler(sqlite3.Error)
@knowledge_bp.errorhandler(OSError)
def storage_error(error):
    current_app.logger.error("Falha no acervo local: %s", type(error).__name__)
    return jsonify(error="Não foi possível acessar o acervo. Confira a preparação da busca no servidor."), 503


@knowledge_bp.get("/status")
@require_token_request
def status():
    provider, _, settings = generation_config()
    return jsonify({**service().status(), "generation_status": settings.status(),
                    "generation_provider": provider})


@knowledge_bp.post("/search")
@require_token_request
def search():
    return jsonify(retrieve())


def retrieve():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.keys() - {"query", "limit"}:
        raise SearchError("Envie query e, opcionalmente, limit.", 400)
    query = data.get("query", "")
    if not isinstance(query, str) or len(query) > 2000:
        raise SearchError("Consulta inválida; use até 2.000 caracteres.", 400)
    result = service().search(query, data.get("limit", 8))
    provider, _, settings = generation_config()
    result.update(generation_available=settings.status() == "ready", generation_provider=provider)
    return result


@knowledge_bp.post("/answer")
@require_token_request
def answer():
    # Reconsulta a base; não aceita fontes do navegador.
    return jsonify(generator().generate(retrieve()))


@knowledge_bp.post("/conversation")
@require_token_request
def conversation():
    settings = current_app.config["KNOWLEDGE_SEARCH_SETTINGS"]
    path = current_app.config.get("KNOWLEDGE_CONVERSATION_DATABASE", settings.index.parent / "conversations.sqlite3")
    provider, _, _ = generation_config()
    payload = request.get_json(silent=True)
    started = time.perf_counter()
    result = reply(ConversationStore(path), service(), generator(), g.current_user.id, payload)
    try:
        record_analytics(path, request_id=(payload or {}).get("request_id"), session_id=result.get("session_id"),
                         user_id=g.current_user.id, team_id=g.current_user.team_id, response=result,
                         duration_ms=(time.perf_counter() - started) * 1000)
    except Exception:
        current_app.logger.warning("Falha ao registrar metadados das estatisticas da RAG.")
    public_result = {key: value for key, value in result.items() if key != "generation_trace"}
    return jsonify({**public_result, "generation_provider": provider})


def conversation_store():
    settings = current_app.config["KNOWLEDGE_SEARCH_SETTINGS"]
    path = current_app.config.get("KNOWLEDGE_CONVERSATION_DATABASE", settings.index.parent / "conversations.sqlite3")
    return ConversationStore(path)


@knowledge_bp.get("/conversations")
@require_token_request
def recent_conversations():
    return jsonify({"investigations": conversation_store().list_for_owner(g.current_user.id)}), 200


@knowledge_bp.get("/conversations/<session_id>")
@require_token_request
def get_conversation(session_id):
    result = conversation_store().get_for_owner(g.current_user.id, session_id)
    if not result:
        return jsonify({"error": "Investigação não encontrada."}), 404
    return jsonify(result), 200


@knowledge_bp.delete("/conversations/<session_id>")
@require_token_request
def delete_conversation(session_id):
    deleted = conversation_store().delete_for_owner(g.current_user.id, session_id)
    if not deleted:
        return jsonify({"error": "Investigação não encontrada."}), 404
    return jsonify({"deleted": True, "session_id": session_id}), 200


@knowledge_bp.get("/stats")
@require_token_request
@require_admin
def stats():
    settings = current_app.config["KNOWLEDGE_SEARCH_SETTINGS"]
    path = current_app.config.get("KNOWLEDGE_CONVERSATION_DATABASE", settings.index.parent / "conversations.sqlite3")
    try:
        days = int(request.args.get("days", "30"))
    except ValueError as exc:
        raise SearchError("O periodo deve ser 7, 30 ou 90 dias.", 400) from exc
    if days not in (7, 30, 90):
        raise SearchError("O periodo deve ser 7, 30 ou 90 dias.", 400)
    team_id = request.args.get("team_id", "")
    if len(team_id) > 200:
        raise SearchError("Equipe invalida.", 400)
    return jsonify(knowledge_stats(path, days=days, team_id=team_id, teams=team_service.list_teams())), 200


@knowledge_bp.get("/documents/<key>")
@require_token_request
def document(key):
    return jsonify(service().document(key, include_details=True))
