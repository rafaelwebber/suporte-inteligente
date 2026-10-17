from flask import Blueprint, g, jsonify, request

from api.decorators import client_ip, require_token_request
from api.services import auth_service, user_service

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")
    data, error, status = auth_service.login(email, password, client_ip())
    if error:
        return jsonify({"error": error}), status
    return jsonify(data), status


@auth_bp.post("/refresh")
def refresh():
    body = request.get_json(silent=True) or {}
    data, error, status = auth_service.refresh(body.get("refresh_token"))
    if error:
        return jsonify({"error": error}), status
    return jsonify(data), status


@auth_bp.post("/logout")
@require_token_request
def logout():
    auth_service.logout(g.current_user.id, client_ip())
    return jsonify({"logged_out": True}), 200


@auth_bp.get("/me")
@require_token_request
def me():
    profile = user_service.get_profile(g.current_user.id)
    return jsonify({"user": {"id": g.current_user.id, "email": g.current_user.email, "profile": profile}}), 200


@auth_bp.post("/password")
@require_token_request
def change_password():
    body = request.get_json(silent=True) or {}
    data, error, status = auth_service.change_password(
        user_id=g.current_user.id,
        email=g.current_user.email,
        current_password=body.get("current_password"),
        new_password=body.get("new_password"),
        ip=client_ip(),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(data), status
