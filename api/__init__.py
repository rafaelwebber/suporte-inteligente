from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from api.config import DEFAULT_SECRET_KEY, Config

FRONTEND_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = FRONTEND_DIR / "assets"
FRONTEND_FILES = {"index.html", "app.html", "app.js", "style.css"}


def create_app() -> Flask:
    if not Config.DEBUG and Config.SECRET_KEY == DEFAULT_SECRET_KEY:
        raise RuntimeError("Defina FLASK_SECRET_KEY no .env antes de rodar fora do modo debug.")

    app = Flask(__name__)
    app.config.from_object(Config)

    if Config.TRUSTED_PROXIES > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=Config.TRUSTED_PROXIES, x_proto=Config.TRUSTED_PROXIES)
    if Config.CORS_ORIGINS:
        CORS(app, origins=Config.CORS_ORIGINS)

    from api.routes.audit import audit_bp
    from api.routes.auth import auth_bp
    from api.routes.suggestions import suggestions_bp
    from api.routes.teams import teams_bp
    from api.routes.tickets import tickets_bp
    from api.routes.users import users_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(suggestions_bp)
    app.register_blueprint(audit_bp)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "Arquivo muito grande."}), 413

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def frontend_index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/assets/<path:filename>")
    def frontend_asset(filename: str):
        target = (ASSETS_DIR / filename).resolve()
        if not str(target).startswith(str(ASSETS_DIR.resolve()) + "/") or not target.is_file():
            abort(404)
        return send_from_directory(ASSETS_DIR, filename)

    @app.get("/<path:filename>")
    def frontend_file(filename: str):
        if filename not in FRONTEND_FILES:
            abort(404)
        return send_from_directory(FRONTEND_DIR, filename)

    return app
