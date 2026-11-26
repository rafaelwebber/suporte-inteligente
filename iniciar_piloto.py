"""Inicia a interface local com os bancos piloto e o provedor de IA configurado."""
import os
import runpy
from pathlib import Path

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parent
    for variable, filename in (
        ("KNOWLEDGE_DATABASE", "piloto.sqlite3"),
        ("KNOWLEDGE_SEARCH_DATABASE", "piloto-busca.sqlite3"),
    ):
        path = root / "instance" / filename
        if not path.is_file():
            raise SystemExit(f"Banco piloto não encontrado: {path}")
        os.environ[variable] = str(path)
    load_dotenv(root / ".env")
    os.environ.setdefault("GEMINI_ENABLED", "true")
    os.environ["FLASK_DEBUG"] = "false"
    print("Piloto local: busca no acervo com orientação por IA quando configurada.")
    print("O login continua usando o Firebase configurado no .env.")
    runpy.run_path(str(root / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
