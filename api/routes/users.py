from flask import Blueprint, g, jsonify, request

from api.decorators import client_ip, require_admin, require_token_request
from api.services import user_service

users_bp = Blueprint("users", __name__, url_prefix="/users")

EDITABLE_FIELDS = ("full_name", "team_id", "role", "disabled")


@users_bp.get("")
@require_token_request
@require_admin
def list_users():
    users = user_service.list_users()
    return jsonify({"users": users}), 200


@users_bp.post("")
@require_token_request
@require_admin
def create_user():
    body = request.get_json(silent=True) or {}
    data, error, status = user_service.create_user(
        email=body.get("email"),
        password=body.get("password"),
        full_name=body.get("full_name"),
        team_id=body.get("team_id"),
        role=body.get("role"),
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"user": data}), status


@users_bp.patch("/<user_id>")
@require_token_request
@require_admin
def update_user(user_id: str):
    body = request.get_json(silent=True) or {}
    data, error, status = user_service.update_user(
        user_id=user_id,
        changes={k: body[k] for k in EDITABLE_FIELDS if k in body},
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"user": data}), status


@users_bp.post("/<user_id>/password")
@require_token_request
@require_admin
def reset_password(user_id: str):
    body = request.get_json(silent=True) or {}
    ok, error, status = user_service.reset_password(
        user_id=user_id,
        password=body.get("password"),
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"updated": ok}), status


@users_bp.delete("/<user_id>")
@require_token_request
@require_admin
def delete_user(user_id: str):
    ok, error, status = user_service.delete_user(
        user_id=user_id,
        actor_id=str(g.current_user.id),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"deleted": ok, "user_id": user_id}), status
