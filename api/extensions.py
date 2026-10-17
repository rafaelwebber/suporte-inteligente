import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import firebase_admin
import requests
from firebase_admin import auth, credentials, firestore
from google.cloud.firestore import Client, DocumentSnapshot

from api.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_db: Client | None = None


def init_firebase() -> None:
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    if not Config.FIREBASE_CREDENTIALS:
        raise RuntimeError("FIREBASE_CREDENTIALS é obrigatório.")
    cred_path = Path(Config.FIREBASE_CREDENTIALS)
    if not cred_path.is_absolute():
        cred_path = PROJECT_ROOT / cred_path
    if not cred_path.is_file():
        raise RuntimeError(f"Arquivo de credenciais do Firebase não encontrado: {cred_path}")

    firebase_admin.initialize_app(credentials.Certificate(str(cred_path)))


def get_db() -> Client:
    global _db
    if _db is None:
        init_firebase()
        _db = firestore.client()
    return _db


def get_auth():
    init_firebase()
    return auth


def _google_now() -> int:
    try:
        response = requests.head("https://www.googleapis.com", timeout=5)
        return int(parsedate_to_datetime(response.headers["Date"]).timestamp())
    except Exception:
        return int(time.time())


def revoke_sessions(uid: str) -> None:
    """Encerra as sessões do usuário em todos os dispositivos.

    Não usa auth.revoke_refresh_tokens porque ele carimba a revogação com o relógio local:
    se a máquina estiver adiantada, os logins feitos logo depois também nascem revogados.
    """
    get_auth().update_user(uid, valid_since=_google_now())


def get_doc(collection: str, doc_id: str | None) -> DocumentSnapshot | None:
    """Busca um documento pelo id; ids inválidos (vazios, com "/") contam como inexistentes."""
    if not doc_id:
        return None
    try:
        snapshot = get_db().collection(collection).document(doc_id).get()
    except ValueError:
        return None
    return snapshot if snapshot.exists else None


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def to_dict(snapshot: DocumentSnapshot) -> dict[str, Any]:
    """Documento do Firestore -> dict JSON-serializável (com "id" e datas em ISO 8601)."""
    return {"id": snapshot.id, **_serialize(snapshot.to_dict() or {})}
