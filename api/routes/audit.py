from flask import Blueprint, jsonify, request

from api.decorators import require_admin, require_token_request
from api.services import audit_service

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")


@audit_bp.get("")
@require_token_request
@require_admin
def list_logs():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"logs": audit_service.list_logs(limit)}), 200
