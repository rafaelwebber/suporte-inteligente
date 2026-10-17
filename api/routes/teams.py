from flask import Blueprint, g, jsonify, request

from api.decorators import client_ip, require_admin, require_token_request
from api.services import team_service

teams_bp = Blueprint("teams", __name__, url_prefix="/teams")


@teams_bp.get("")
@require_token_request
def list_teams():
    teams = team_service.list_teams()
    return jsonify({"teams": teams}), 200


@teams_bp.post("")
@require_token_request
@require_admin
def create_team():
    body = request.get_json(silent=True) or {}
    data, error, status = team_service.create_team(
        name=body.get("name"),
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"team": data}), status


@teams_bp.patch("/<team_id>")
@require_token_request
@require_admin
def rename_team(team_id: str):
    body = request.get_json(silent=True) or {}
    data, error, status = team_service.rename_team(
        team_id=team_id,
        name=body.get("name"),
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"team": data}), status


@teams_bp.delete("/<team_id>")
@require_token_request
@require_admin
def delete_team(team_id: str):
    ok, error, status = team_service.delete_team(
        team_id=team_id,
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"deleted": ok, "team_id": team_id}), status
