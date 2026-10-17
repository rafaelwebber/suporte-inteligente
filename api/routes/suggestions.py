from flask import Blueprint, g, jsonify, request

from api.decorators import require_admin, require_token_request
from api.services import suggestion_service

suggestions_bp = Blueprint("suggestions", __name__, url_prefix="/suggestions")


@suggestions_bp.post("")
@require_token_request
def suggest():
    body = request.get_json(silent=True) or {}
    data, error, status = suggestion_service.suggest(text=body.get("query"), user=g.current_user)
    if error:
        return jsonify({"error": error}), status
    return jsonify(data), status


@suggestions_bp.post("/<query_id>/feedback")
@require_token_request
def feedback(query_id: str):
    body = request.get_json(silent=True) or {}
    ok, error, status = suggestion_service.give_feedback(
        query_id=query_id,
        ticket_id=body.get("ticket_id"),
        helpful=body.get("helpful"),
        comment=body.get("comment"),
        user=g.current_user,
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"saved": ok}), status


@suggestions_bp.get("/stats")
@require_token_request
@require_admin
def stats():
    return jsonify(suggestion_service.stats()), 200
