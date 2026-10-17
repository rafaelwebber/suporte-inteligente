from flask import Blueprint, g, jsonify, request

from api.decorators import client_ip, require_admin, require_token_request
from api.services import ticket_service

tickets_bp = Blueprint("tickets", __name__, url_prefix="/tickets")


def _bool_arg(name: str) -> bool | None:
    value = request.args.get(name)
    if value is None or value == "":
        return None
    return value.lower() in ("1", "true", "yes", "sim")


@tickets_bp.get("")
@require_token_request
def list_tickets():
    tickets = ticket_service.list_tickets(
        user=g.current_user,
        status=request.args.get("status") or None,
        successful=_bool_arg("successful"),
        team_id=request.args.get("team_id") or None,
        limit=request.args.get("limit", default=100, type=int),
    )
    return jsonify({"tickets": tickets}), 200


@tickets_bp.get("/<ticket_id>")
@require_token_request
def get_ticket(ticket_id: str):
    data, error, status = ticket_service.get_ticket(ticket_id, g.current_user)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"ticket": data}), status


@tickets_bp.post("")
@require_token_request
def create_ticket():
    body = request.get_json(silent=True) or {}
    data, error, status = ticket_service.create_ticket(data=body, user=g.current_user, ip=client_ip())
    if error:
        return jsonify({"error": error}), status
    return jsonify({"ticket": data}), status


@tickets_bp.patch("/<ticket_id>")
@require_token_request
def update_ticket(ticket_id: str):
    body = request.get_json(silent=True) or {}
    data, error, status = ticket_service.update_ticket(
        ticket_id=ticket_id, data=body, user=g.current_user, ip=client_ip()
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"ticket": data}), status


@tickets_bp.delete("/<ticket_id>")
@require_token_request
@require_admin
def delete_ticket(ticket_id: str):
    ok, error, status = ticket_service.delete_ticket(
        ticket_id=ticket_id, actor_id=g.current_user.id, ip=client_ip()
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"deleted": ok, "ticket_id": ticket_id}), status


@tickets_bp.post("/import")
@require_token_request
@require_admin
def import_tickets():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Envie o arquivo CSV no campo 'file'."}), 400
    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "O arquivo precisa ser .csv."}), 400
    data, error, status = ticket_service.import_csv(
        content=file.read(),
        default_team_id=request.form.get("team_id", ""),
        actor_id=g.current_user.id,
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(data), status
