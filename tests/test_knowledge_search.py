import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from knowledge_import.storage import connect
from knowledge_search.__main__ import main
from knowledge_search.config import SearchError, Settings
from knowledge_search.documents import chunks, project
from knowledge_search.embeddings import LocalEmbedder, verified
from knowledge_search.service import SearchService, fuse
from knowledge_search.storage import build, metadata, readonly


def record(identifier, title, body, searchable=True, **data):
    payload = {"tipo": "caso_ticket", "id": identifier, "dados": {"assunto": title, "problema_relatado": body, **data},
               "metadados": {"disponivel_pesquisa": searchable, "uso_na_recomendacao": "hipotese_para_investigacao", "apto_recomendacao_automatica": False},
               "origem": {"arquivo_relativo": "casos.jsonl", "registro_id": identifier}}
    raw = json.dumps(payload, ensure_ascii=False)
    return {"package_id": "fixture", "kind": "conhecimento", "id": identifier, "payload": raw,
            "fingerprint": hashlib.sha256(raw.encode()).hexdigest(), "searchable": int(searchable)}


def source_fixture(path, records, snapshot="s1"):
    with closing(connect(path)) as db, db:
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", (snapshot, "fixture", snapshot, "hash", "now", "{}"))
        for r in records:
            db.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?,?)", tuple(r[k] for k in ("package_id", "id", "fingerprint", "kind", "payload", "searchable")))
            db.execute("INSERT INTO membership VALUES (?,?,?,?)", (snapshot, "fixture", r["id"], r["fingerprint"]))
        db.execute("INSERT INTO active_packages VALUES (?,?) ON CONFLICT(package_id) DO UPDATE SET snapshot_id=excluded.snapshot_id", ("fixture", snapshot))


class FixtureEmbedder:
    """Vetores controlados para testar ranking e persistência; não é modelo de produção."""
    key = "fixture-v1"
    dimension = 3

    def fits(self, text):
        return len(text) < 700

    def encode(self, texts, query=False):
        vectors = []
        for text in texts:
            if any(t in text.casefold() for t in ("comunicação", "conectividade", "plc")):
                vectors.append([0, 1, 0])
            elif any(t in text.casefold() for t in ("redler", "velocidade", "transportador")):
                vectors.append([1, 0, 0])
            else:
                vectors.append([0, 0, 1])
        return np.asarray(vectors, dtype="<f4")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(self.root / "knowledge.sqlite3", self.root / "search.sqlite3", self.root / "model")
        self.records = [record("redler", "Zero speed no redler", "Ausência de velocidade durante o transporte.", cliente="NOME_PRIVADO", autores_interacoes="AUTOR_PRIVADO"),
                        record("plc", "Perda de comunicação PLC", "Canal de comunicação com valores congelados."),
                        record("blocked", "Segredo exclusivo", "não publicar", False)]
        source_fixture(self.settings.source, self.records)
        self.embedder = FixtureEmbedder()
        self.service = SearchService(self.settings, lambda _: self.embedder)

    def test_explicit_ticket_excludes_other_tickets_and_numeric_prefixes(self):
        records = [record('target', 'PLC', 'Comunicação pendente', ticket='42'),
                   record('prefix', 'PLC', 'Comunicação pendente', ticket='420'),
                   record('other', 'PLC', 'Comunicação pendente; versão 42', ticket='99')]
        source_fixture(self.settings.source, records, 'refs')
        build(self.settings, self.embedder)
        found = self.service.search('Ticket #42 comunicação PLC')['results']
        self.assertEqual(['target'], [r['source_id'] for r in found])
        self.assertEqual([], self.service.search('Ticket #99999 comunicação PLC')['results'])

    def test_explicit_expedition_filters_vector_candidates_too(self):
        records = [record('target', 'PLC', 'Expedição 1234: comunicação interrompida'),
                   record('other', 'PLC', 'Expedição 12345: comunicação interrompida')]
        source_fixture(self.settings.source, records, 'refs')
        build(self.settings, self.embedder)
        found = self.service.search('Expedição 1234 causa comunicação PLC')['results']
        self.assertEqual(['target'], [r['source_id'] for r in found])

    def test_versions_and_dates_are_not_explicit_references(self):
        from knowledge_search.service import explicit_references
        self.assertEqual([], explicit_references('versão 42 de 2026-11-21'))
        self.assertEqual([('producao', '42')], explicit_references('produção 42'))

    def test_projection_uses_only_authorized_explicit_fields(self):
        doc = project(self.records[0])
        self.assertNotIn("NOME_PRIVADO", doc["body"])
        self.assertNotIn("AUTOR_PRIVADO", doc["body"])
        self.assertIsNone(project(self.records[2]))
        unknown = copy.deepcopy(self.records[0])
        data = json.loads(unknown["payload"]); data["tipo"] = "unreviewed"; unknown["payload"] = json.dumps(data)
        self.assertIsNone(project(unknown))
        self.assertFalse(doc["metadata"]["apto_recomendacao_automatica"])

    def test_chunks_preserve_references_cover_full_text_and_fit_model(self):
        doc = project(record("long", "Título", "Texto muito longo com palavras e códigos. " * 200))
        pieces = list(chunks(doc, self.embedder))
        coverage = set()
        for piece in pieces:
            self.assertEqual(doc["body"][piece["start"]:piece["end"]], piece["text"])
            self.assertTrue(self.embedder.fits(piece["embedding_text"]))
            coverage.update(range(piece["start"], piece["end"]))
        self.assertEqual(set(range(len(doc["body"]))), coverage)

    def test_hybrid_finds_semantic_only_and_references_original_projection(self):
        report = build(self.settings, self.embedder)
        self.assertEqual(2, report["documents"])
        result = self.service.search("conectividade controlador")
        self.assertEqual("hybrid", result["mode"])
        self.assertEqual("plc", result["results"][0]["source_id"])
        self.assertEqual(["vector"], result["results"][0]["matched_by"])
        self.assertIsNone(result["generated_answer"])
        found = result["results"][0]
        doc = self.service.document(found["document_key"])
        self.assertEqual(found["excerpt"], doc["body"][found["start"]:found["end"]])

    def test_textual_mode_and_missing_model_are_explicit(self):
        build(self.settings)
        self.assertEqual("textual", self.service.search("redler")["mode"])
        self.assertTrue(self.service.search("redler")["warnings"])
        build(self.settings, self.embedder)
        def unavailable(_):
            raise SearchError("modelo ausente")
        result = SearchService(self.settings, unavailable).search("redler")
        self.assertEqual("textual", result["mode"])
        self.assertTrue(result["results"])

    def test_ticket_reference_and_solution_use_complete_active_record(self):
        records = [
            record("empty", "Agendamento", "Produção parada.", ticket="11370", solucao_documentada="  "),
            record("solved", "Agendamento", "Produção parada. " * 150, ticket="11371",
                   solucao_documentada="Ação documentada no final do registro."),
            record("candidate", "Agendamento", "Produção parada.", solucao_candidata="Hipótese a verificar."),
        ]
        source_fixture(self.settings.source, records, "s2")
        build(self.settings)
        found = {r["source_id"]: r for r in self.service.search("Agendamento")["results"]}
        self.assertFalse(found["empty"]["has_solution"])
        self.assertEqual("11370", found["empty"]["ticket_reference"])
        self.assertTrue(found["solved"]["has_solution"])
        self.assertNotIn("Ação documentada", found["solved"]["excerpt"])
        self.assertTrue(found["candidate"]["has_solution"])
        self.assertEqual("", found["candidate"]["ticket_reference"])

    def test_reuses_unchanged_embeddings_and_updates_only_changed_content(self):
        first = build(self.settings, self.embedder)
        second = build(self.settings, self.embedder)
        self.assertEqual(0, second["embedded"])
        self.assertEqual(first["chunks"], second["reused"])
        changed = [self.records[0], record("plc", "Perda de comunicação PLC", "Nova descrição do problema de comunicação.")]
        source_fixture(self.settings.source, changed, "s2")
        third = build(self.settings, self.embedder)
        self.assertEqual(1, third["embedded"])
        self.assertEqual(1, third["reused"])
        result = self.service.search("conectividade")
        self.assertIn("Nova descrição", result["results"][0]["excerpt"])

    def test_stale_index_fails_closed_and_removes_old_sources(self):
        build(self.settings, self.embedder)
        key = self.service.search("redler")["results"][0]["document_key"]
        source_fixture(self.settings.source, [self.records[1]], "s2")
        with self.assertRaisesRegex(SearchError, "acervo mudou"):
            self.service.search("redler")
        with self.assertRaises(SearchError):
            self.service.document(key)
        build(self.settings, self.embedder)
        self.assertEqual([], self.service.search("redler")["results"])
        with self.assertRaises(SearchError) as exc:
            self.service.document(key)
        self.assertEqual(404, exc.exception.status)

    def test_failed_build_does_not_replace_working_index(self):
        build(self.settings, self.embedder)
        bad = FixtureEmbedder(); bad.key = "new-model"
        with patch.object(bad, "encode", side_effect=RuntimeError("simulated")):
            with self.assertRaises(RuntimeError):
                build(self.settings, bad)
        self.assertEqual("plc", self.service.search("conectividade")["results"][0]["source_id"])

    def test_change_during_build_is_not_published(self):
        build(self.settings, self.embedder)
        changing = FixtureEmbedder(); changing.key = "new-model"
        original = changing.encode
        def change(texts, query=False):
            source_fixture(self.settings.source, [], "s2")
            return original(texts, query)
        with patch.object(changing, "encode", side_effect=change):
            with self.assertRaisesRegex(SearchError, "durante a indexação"):
                build(self.settings, changing)
        with readonly(self.settings.index) as index:
            self.assertEqual("fixture-v1", metadata(index)["model"])

    def test_empty_source_is_valid_and_does_not_return_old_vectors(self):
        build(self.settings, self.embedder)
        self.service.search("redler")
        source_fixture(self.settings.source, [], "s2")
        build(self.settings, self.embedder)
        self.assertEqual([], self.service.search("redler")["results"])

    def test_rrf_deduplicates_and_uses_rank_not_raw_scores(self):
        self.assertEqual([2, 1, 3], fuse([1, 2, 2], [2, 3]))

    def test_index_does_not_overwrite_source_or_unrelated_database(self):
        with self.assertRaises(SearchError):
            build(replace(self.settings, index=self.settings.source))
        unrelated = self.root / "unrelated.sqlite3"
        with closing(sqlite3.connect(unrelated)) as db, db:
            db.execute("CREATE TABLE important(value)")
        with self.assertRaises(SearchError):
            build(replace(self.settings, index=unrelated))

    def test_query_validation_and_fts_metacharacters(self):
        build(self.settings)
        for value in (None, [], {}, "x", "*", "a" * 2001):
            with self.subTest(value=str(value)[:30]), self.assertRaises(SearchError):
                self.service.search(value)
        self.assertIsInstance(self.service.search('redler " OR * NOT :')['results'], list)
        self.assertEqual([], self.service.search("qx999 ultrassonica")["results"])

    def test_source_and_search_never_connect_to_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network")):
            build(self.settings, self.embedder)
            self.assertTrue(self.service.search("redler")["results"])

    def test_model_integrity_checks_size_and_hash(self):
        path = self.root / "file"
        path.write_bytes(b"safe")
        spec = ("file", 4, hashlib.sha256(b"safe").hexdigest())
        self.assertTrue(verified(path, spec))
        path.write_bytes(b"evil")
        self.assertFalse(verified(path, spec))

    def test_inference_failure_becomes_controlled_search_error(self):
        embedder = LocalEmbedder.__new__(LocalEmbedder)
        embedder.np = np
        embedder.lock = threading.Lock()
        encoded = SimpleNamespace(ids=[0, 2], attention_mask=[1, 1], type_ids=[0, 0])
        embedder.tokenizer = SimpleNamespace(encode_batch=lambda _: [encoded])
        embedder.input_names = {"input_ids", "attention_mask"}
        def fail(*args):
            raise Exception("runtime internal details")
        embedder.session = SimpleNamespace(run=fail)
        with self.assertRaisesRegex(SearchError, "Não foi possível calcular"):
            embedder.encode(["redler"], query=True)

    def test_cli_missing_model_and_textual_index(self):
        args = ["--banco", str(self.settings.source), "--indice", str(self.settings.index), "--modelo-dir", str(self.settings.model_dir)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(1, main(["indexar", *args]))
            self.assertEqual(0, main(["indexar", "--somente-texto", *args]))
            self.assertEqual(0, main(["buscar", "--consulta", "redler", *args]))


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        SearchTests.setUp(self)
        from api import create_app
        from api.config import Config
        with patch.object(Config, "SECRET_KEY", "test-secret"), patch.object(Config, "DEBUG", False):
            self.app = create_app()
        self.app.config.update(TESTING=True, KNOWLEDGE_SEARCH_SETTINGS=self.settings,
                               KNOWLEDGE_LLM_PROVIDER="gemini")
        from knowledge_search.generation import GenerationSettings
        self.app.config["GEMINI_SETTINGS"] = GenerationSettings()
        build(self.settings, self.embedder)
        self.app.extensions["knowledge_search"] = self.service
        self.client = self.app.test_client()
        auth = SimpleNamespace(verify_id_token=lambda token, **_: {"uid": token})
        for name, value in (("get_auth", auth), ("get_doc", {"role": "agent", "team_id": "test"})):
            p = patch("api.decorators." + name, return_value=value); p.start(); self.addCleanup(p.stop)
        self.headers = {"Authorization": "Bearer alice"}

    def test_api_authentication_shared_acervo(self):
        self.assertEqual(401, self.client.post("/knowledge/search", json={"query": "redler"}).status_code)
        self.assertEqual(401, self.client.get("/knowledge/status").status_code)
        found = self.client.post("/knowledge/search", json={"query": "redler"}, headers=self.headers)
        self.assertEqual(200, found.status_code)
        key = found.json["results"][0]["document_key"]
        self.assertEqual(200, self.client.get("/knowledge/documents/" + key, headers={"Authorization": "Bearer bob"}).status_code)
        self.assertEqual(401, self.client.get("/knowledge/documents/" + key).status_code)

    def test_api_invalid_json_and_no_static_database_exposure(self):
        for data in ([], {"query": []}, {"query": "redler", "extra": 1}, {"query": "redler", "limit": True}):
            self.assertEqual(400, self.client.post("/knowledge/search", json=data, headers=self.headers).status_code)
        for path in ("/instance/search.sqlite3", "/instance/models/multilingual-e5-small/model.onnx"):
            self.assertEqual(404, self.client.get(path).status_code)
        for path in ("/acervo.html", "/acervo.js", "/acervo.css"):
            response = self.client.get(path); self.assertEqual(200, response.status_code); response.close()

    def test_retired_triage_is_unavailable(self):
        for path in ("/triagem.html", "/triagem.js", "/triagem.css", "/triage/models", "/triage/sessions"):
            self.assertEqual(404, self.client.get(path, headers=self.headers).status_code)
        # Sem a API, resta apenas a rota genérica de arquivos, que aceita GET.
        self.assertEqual(405, self.client.post("/triage/sessions", json={}, headers=self.headers).status_code)
        self.assertFalse(any(rule.rule.startswith("/triage") for rule in self.app.url_map.iter_rules()))
        self.assertEqual(400, self.client.post("/knowledge/search", json={"query": "redler", "session_id": "old"}, headers=self.headers).status_code)


if __name__ == "__main__":
    unittest.main()
