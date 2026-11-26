import json
import logging
import math
import re
import threading
import time

from api.text_utils import keywords, normalize
from .config import SearchError
from .documents import digest, valid_query
from .embeddings import get_embedder
from .storage import check_current, readonly
from .ticket_details import details, evidence_status, field


def fuse(text_ids, vector_ids, k=60):
    """RRF combina posições, não mistura escalas BM25/cosseno."""
    scores = {}
    for ranked in (text_ids, vector_ids):
        for rank, key in enumerate(dict.fromkeys(ranked), 1):
            scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


def explicit_references(query):
    """Números de versão/data não são referências de atendimento."""
    return re.findall(r'\b(ticket|expedicao|producao)\s*#?\s*(\d+)\b', normalize(query))


def ticket_references(turns):
    """Lê IDs antes de transformar as mensagens em palavras-chave."""
    from .dialogue_context import case_observation

    refs = {}
    for turn in reversed(turns):
        for group in re.findall(
            r"\btickets?\s*#?\s*(\d+(?:(?:\s*,\s*|\s+e\s+)#?\s*\d+)*)",
            normalize(case_observation(turn)),
        ):
            for ticket in re.findall(r"\d+", group):
                refs.setdefault(ticket, None)
    return list(refs)


def matches_references(title, body, references):
    if not references:
        return True
    for kind, number in references:
        if kind == 'ticket':
            if re.search(r'\bticket\s+' + re.escape(number) + r'\b', normalize(title)):
                return True
        elif re.search(r'\b' + re.escape(number) + r'\b', body):
            return True
    return False


class SearchService:
    def __init__(self, settings, embedder_factory=get_embedder):
        self.settings = settings
        self.embedder_factory = embedder_factory
        self._cache_key = None
        self._vectors = None
        self._ids = None
        self._lock = threading.Lock()

    def status(self):
        with readonly(self.settings.source) as source, readonly(self.settings.index) as index:
            info = check_current(source, index)
            return {k: info[k] for k in ("documents", "chunks", "mode", "created_at", "access")}

    def _vector_search(self, index, info, query, count):
        import numpy as np
        embedder = self.embedder_factory(self.settings)
        if embedder.key != info["model"] or embedder.dimension != info["dimension"]:
            raise SearchError("Modelo local diferente do modelo do índice. Refaça a indexação.")
        query_vector = np.asarray(embedder.encode([query], query=True)[0], dtype="<f4")
        if query_vector.shape != (info["dimension"],) or not np.isfinite(query_vector).all():
            raise SearchError("Vetor da consulta inválido.")
        cache_key = (info["source_signature"], info["model"], info["created_at"], info["chunks"])
        with self._lock:
            if cache_key != self._cache_key:
                rows = index.execute("SELECT id,vector FROM chunks ORDER BY id").fetchall()
                if any(r[1] is None or len(r[1]) != info["dimension"] * 4 for r in rows):
                    raise SearchError("Índice vetorial incompleto. Refaça a indexação.")
                vectors = np.stack([np.frombuffer(r[1], dtype="<f4") for r in rows]) if rows else np.empty((0, info["dimension"]), dtype="<f4")
                if not np.isfinite(vectors).all():
                    raise SearchError("Índice contém vetores inválidos.")
                self._vectors, self._ids = vectors, [r[0] for r in rows]
                self._cache_key = cache_key
            vectors, ids = self._vectors, self._ids
        similarities = vectors @ query_vector
        ranked = np.argsort(-similarities, kind="stable")
        return [(ids[int(i)], float(similarities[i])) for i in ranked[:count] if similarities[i] >= self.settings.min_similarity]

    def search(self, query, limit=8, *, prefer_titles=False):
        query = valid_query(query)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise SearchError("Limite de resultados deve ficar entre 1 e 20.", 400)
        if not math.isfinite(self.settings.min_similarity) or not 0 <= self.settings.min_similarity <= 1:
            raise SearchError("Limite de similaridade configurado fora do intervalo permitido.")
        terms = keywords(query, limit=30)
        references = explicit_references(query)
        if not terms:
            raise SearchError("Descreva o problema com mais detalhes.", 400)
        started = time.monotonic()
        with readonly(self.settings.source) as source, readonly(self.settings.index) as index:
            info = check_current(source, index)
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            if references:
                # Aplicar antes do LIMIT evita que termos genéricos expulsem a referência.
                reference_expression = ' OR '.join('"' + number + '"' for _, number in references)
                expression = '(' + expression + ') AND (' + reference_expression + ')'
            textual = [r[0] for r in index.execute("SELECT rowid FROM search_fts WHERE search_fts MATCH ? ORDER BY bm25(search_fts,2.0,1.0),rowid LIMIT 80", (expression,))]
            focused = []
            if prefer_titles and not references:
                # Uma correspondência de frase no título preserva a função relatada
                # mesmo quando palavras do esclarecimento aparecem em muitos documentos.
                phrases = [f'title:"{first} {second}"' for first, second in zip(terms, terms[1:])
                           if not first.isdigit() and not second.isdigit()]
                if phrases:
                    focused = [r[0] for r in index.execute(
                        """SELECT rowid FROM search_fts WHERE search_fts MATCH ?
                           ORDER BY bm25(search_fts,4.0,0.0),rowid LIMIT 40""",
                        (" OR ".join(phrases),),
                    )]
            vectors, warnings = [], []
            if info["mode"] == "hybrid":
                try:
                    vectors = self._vector_search(index, info, query, 80)
                except (SearchError, ImportError, OSError, RuntimeError) as exc:
                    logging.getLogger(__name__).warning("Busca vetorial indisponível: %s", type(exc).__name__)
                    warnings.append("A pesquisa por significado está indisponível nesta consulta. Foram usados somente os termos informados.")
                    # Falha operacional não expõe caminhos ou detalhes internos ao navegador.
            else:
                warnings.append("A pesquisa por significado ainda não foi preparada. Foram usados somente os termos informados.")
            vector_ids = [r[0] for r in vectors]
            vector_scores = dict(vectors)
            ranked = fuse(fuse(textual, focused) if focused else textual, vector_ids)
            documents, results = set(), []
            for identifier in ranked:
                row = index.execute("""SELECT c.*,d.package_id,d.source_id,d.metadata,d.origin,d.fingerprint
                    FROM chunks c JOIN documents d ON d.key=c.document_key WHERE c.id=?""", (identifier,)).fetchone()
                if not matches_references(row['title'], row['text'], references):
                    continue
                if row["document_key"] in documents:
                    continue
                documents.add(row["document_key"])
                via = (["textual"] if identifier in textual or identifier in focused else []) + (["vector"] if identifier in vector_scores else [])
                original = source.execute(
                    "SELECT payload FROM active_records WHERE package_id=? AND id=? AND fingerprint=?",
                    (row["package_id"], row["source_id"], row["fingerprint"]),
                ).fetchone()
                payload = json.loads(original["payload"]) if original else {}
                data = payload.get("dados", {})
                status = evidence_status(payload.get("tipo"), data)
                ticket = data.get("ticket", data.get("ticket_origem"))
                ticket_reference = str(ticket).strip() if isinstance(ticket, (str, int)) and not isinstance(ticket, bool) else ""
                results.append({"document_key": row["document_key"], "chunk_key": row["key"], "source_id": row["source_id"],
                                "package_id": row["package_id"], "title": row["title"], "excerpt": row["text"],
                                "position": row["position"], "start": row["start"], "end": row["end"],
                                "metadata": json.loads(row["metadata"]), "origin": json.loads(row["origin"]),
                                "matched_by": via, "evidence_status": status,
                                "has_solution": status in ("solucao_documentada", "solucao_candidata", "acao_com_resultado"),
                                "ticket_reference": ticket_reference})
                if len(results) == limit:
                    break
        return {"query": query, "mode": "hybrid" if info["mode"] == "hybrid" and not warnings else "textual",
                "results": results, "warnings": warnings, "generated_answer": None, "generation_status": "pending",
                "source_count": info["documents"], "seconds": round(time.monotonic() - started, 3)}

    def document(self, key, *, include_details=False):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise SearchError("Referência inválida.", 400)
        with readonly(self.settings.source) as source, readonly(self.settings.index) as index:
            check_current(source, index)
            row = index.execute("SELECT * FROM documents WHERE key=?", (key,)).fetchone()
            if row is None:
                raise SearchError("Fonte não encontrada no acervo ativo.", 404)
            document = {k: json.loads(row[k]) if k in ("metadata", "origin") else row[k]
                        for k in ("key", "source_id", "package_id", "title", "body", "metadata", "origin")}
            if include_details:
                original = source.execute(
                    "SELECT payload FROM active_records WHERE package_id=? AND id=? AND fingerprint=?",
                    (row["package_id"], row["source_id"], row["fingerprint"]),
                ).fetchone()
                if original:
                    payload = json.loads(original["payload"])
                    detail = details(payload.get("tipo"), payload.get("dados", {}))
                    if detail:
                        detail["interactions"] = self.ticket_interactions(source, row["package_id"], detail["ticket"])
                        document["ticket_details"] = detail
            return document

    @staticmethod
    def ticket_interactions(source, package, ticket, limit=20):
        if not ticket:
            return []
        rows = source.execute("""SELECT id, payload FROM active_records
            WHERE package_id=? AND kind='conhecimento' AND searchable=1 AND id GLOB 'interacao:*'
              AND json_extract(payload,'$.metadados.disponivel_pesquisa')=1
              AND CAST(json_extract(payload,'$.dados.ticket') AS TEXT)=?
            ORDER BY json_extract(payload,'$.dados.data_original') IS NULL,
                     json_extract(payload,'$.dados.data_original'), id LIMIT ?""",
            (package, str(ticket), limit))
        return [{"reference": row["id"], "date": field(data, "data_original"), "text": field(data, "texto")}
                for row in rows for data in [json.loads(row["payload"])["dados"]]]

    def group_tickets(self, result, references):
        """Prepara um candidato por atendimento ou documento para a triagem."""
        records = result["results"]
        ranked = {}
        related = {}
        with readonly(self.settings.source) as source, readonly(self.settings.index) as index:
            check_current(source, index)
            for ticket in references:
                rows = index.execute("""SELECT DISTINCT d.key, d.package_id, d.source_id, d.title
                    FROM search_fts JOIN chunks c ON c.id=search_fts.rowid
                    JOIN documents d ON d.key=c.document_key
                    WHERE search_fts MATCH ?""", (f'title:"ticket {ticket}"',))
                for row in rows:
                    if re.match(r"ticket\s+" + re.escape(ticket) + r"\b", normalize(row["title"])):
                        key = (row["package_id"], ticket)
                        ranked.setdefault(key, None)
                        if row["source_id"].startswith("caso_ticket:"):
                            related[key] = row["key"]
                        else:
                            related.setdefault(key, row["key"])
            for item in records:
                ticket = item.get("ticket_reference")
                key = (item["package_id"], ticket) if ticket else ("", item["document_key"])
                if ranked.get(key) is None or item["source_id"].startswith("caso_ticket:"):
                    ranked[key] = item

            cards = []
            for key, item in ranked.items():
                package, ticket = key
                if package:
                    primary = related.get(key, digest(package + "\0caso_ticket:" + ticket))
                    if item is None or item["document_key"] != primary:
                        row = index.execute("SELECT * FROM documents WHERE key=?", (primary,)).fetchone()
                        if row:
                            raw = source.execute("""SELECT payload FROM active_records
                                WHERE package_id=? AND id=? AND fingerprint=?""",
                                (package, row["source_id"], row["fingerprint"])).fetchone()
                            payload = json.loads(raw["payload"]) if raw else {}
                            status = evidence_status(payload.get("tipo"), payload.get("dados", {}))
                            item = {"document_key": row["key"], "source_id": row["source_id"],
                                    "package_id": package, "title": row["title"], "excerpt": row["body"][:700],
                                    "metadata": json.loads(row["metadata"]), "origin": json.loads(row["origin"]),
                                    "evidence_status": status, "ticket_reference": ticket}
                if item:
                    cards.append(item)
        return {**result, "results": cards, "matched_records": records, "explicit_tickets": references}

    def open_ticket_histories(self, cards, query, references=(), *, selected=False, case_inputs=None):
        """Abre o histórico dos candidatos aprofundados, preservando a ordem da seleção."""
        from .case_cards import sample_sources
        from .investigation import history_excerpt

        histories = []
        with readonly(self.settings.source) as source, readonly(self.settings.index) as index:
            check_current(source, index)
            request_terms = set(keywords("proposta orçamento cotação"))
            wants_request = bool(request_terms.intersection(keywords(query)))
            opened = 0
            for card in cards:
                ticket = card.get("ticket_reference")
                package = card["package_id"]
                if not ticket or (references and ticket not in references):
                    continue
                if not (references or selected or wants_request) and request_terms.intersection(keywords(card["title"])):
                    continue
                if opened >= 4:
                    break
                start = len(histories)
                candidates = []
                indexed_records = []
                for interaction in self.ticket_interactions(source, package, ticket, limit=-1):
                    reference = interaction["reference"]
                    document_key = digest(package + "\0" + reference)
                    indexed = index.execute("SELECT fingerprint FROM documents WHERE key=?", (document_key,)).fetchone()
                    if not indexed:
                        continue
                    if case_inputs is not None:
                        indexed_records.append({"reference": reference, "document_key": document_key,
                                                "fingerprint": indexed["fingerprint"],
                                                "kind": "interacao", "text": interaction["text"]})
                    passage, score = history_excerpt(interaction["text"], query)
                    if passage:
                        candidates.append((score, reference, passage, document_key))
                for _, reference, passage, document_key in sorted(candidates, reverse=True)[:2]:
                    histories.append({"document_key": document_key, "source_id": reference,
                                      "package_id": package, "ticket_reference": ticket, "excerpt": passage})
                if indexed_records:
                    primary = index.execute("SELECT source_id,body,fingerprint FROM documents WHERE key=?",
                                            (card["document_key"],)).fetchone()
                    records = indexed_records
                    if primary and primary["source_id"].startswith("caso_ticket:"):
                        records = [{"reference": primary["source_id"], "document_key": card["document_key"],
                                    "fingerprint": primary["fingerprint"], "kind": "relato",
                                    "text": primary["body"].split("\n\n", 1)[0]}, *records]
                    revision = digest(json.dumps([package, ticket, [(r["reference"], r["fingerprint"]) for r in records]]))
                    sample = sample_sources(records)
                    if any(record["kind"] == "interacao" for record in sample):
                        case_inputs.append({"package_id": package, "ticket": ticket, "revision": revision,
                                            "available_interactions": len(indexed_records), "sources": sample})
                opened += int(len(histories) > start)
        return histories

    def evidence(self, result, selected_keys=None):
        """Fichas sob demanda a partir da projeção autorizada, sem reindexar o acervo."""
        from .dialogue_context import GENERIC, technical_terms, symptom_text, projected_sections
        candidates = []
        query_terms = set(keywords(result.get("query", "")))
        query_terms = {term for term in query_terms if term not in GENERIC or term == "unit"}
        topics = set(result.get("topic_terms", []))
        references = explicit_references(result.get('query', ''))
        requests = set(keywords("proposta orçamento inclusão incluir adicionar melhoria instalação implementação engenharia"))
        wants_request = bool(query_terms & requests)
        if selected_keys is None:
            records = result.get("matched_records", result["results"])[:20]
        else:
            by_key = {card["document_key"]: card for card in result["results"]}
            records = [by_key[key] for key in selected_keys]
        for rank, item in enumerate(records):
            doc = self.document(item["document_key"])
            sections = projected_sections(doc["body"])
            kind = doc['metadata'].get('tipo_registro')
            if kind in ('artigo', 'interacao', 'caso_tecnico'):
                # O trecho recuperado pode estar longe do início do documento.
                # Para curadoria, recuperar blocos inteiros conserva escopo e qualificações.
                label = {'artigo': 'Conteúdo documental', 'interacao': 'Registro da interação',
                         'caso_tecnico': 'Conhecimento estruturado'}[kind]
                excerpt = item['excerpt']
                if kind == 'caso_tecnico':
                    blocks = sections.get(label, '').split('\n\n')
                    occurrence_refs = [(kind, number) for kind, number in references if kind != 'ticket']
                    if occurrence_refs:
                        blocks = [b for b in blocks if matches_references('', b, occurrence_refs)]
                    def claim_score(block):
                        nature = re.search(r'\| Natureza: ([^|]+)', block)
                        nature_match = bool(nature and set(keywords(nature.group(1))) & query_terms)
                        return len(set(keywords(block, limit=1000)) & query_terms) + int(nature_match)
                    ranked = sorted(blocks, key=claim_score, reverse=True)
                    excerpt = ranked[0] if ranked else ''
                relevant = set(keywords(item['title'] + ' ' + excerpt, limit=1000))
                if selected_keys is None and topics and not topics.intersection(relevant):
                    continue
                if selected_keys is None and query_terms and not query_terms.intersection(relevant):
                    continue
                content = excerpt if excerpt.startswith(label + ': ') else label + ': ' + excerpt
                bounded = content + '\n\nLimites: ' + sections.get('Limites', '')
                score = len(query_terms & relevant) / max(1, len(query_terms)) * 10 + 1 / (rank + 1)
                score += {"acao_com_resultado": 2, "acao_registrada": 1, "documentacao": .5}.get(item.get("evidence_status"), 0)
                candidates.append((score, technical_terms(excerpt) - query_terms,
                                   {**item, 'excerpt': bounded, 'source_body': doc["body"]}))
                continue
            symptom = symptom_text(sections.get("Problema relatado", ""))
            signature = technical_terms(item.get("title", "") + " " + symptom) - query_terms
            concrete = bool(technical_terms(symptom) - query_terms)
            relevant = set(keywords(item.get("title", "") + " " + symptom, limit=1000))
            if selected_keys is None and topics and not topics.intersection(relevant):
                continue
            title_terms = set(keywords(item.get("title", "")))
            if selected_keys is None and not wants_request and title_terms.intersection(keywords("proposta orçamento")):
                continue
            if selected_keys is None and query_terms and not query_terms.intersection(relevant):
                continue
            coverage = len(query_terms & relevant) / max(1, len(query_terms))
            evidence_bonus = {"solucao_documentada": 1,
                              "solucao_candidata": .25, "evidencia_parcial": .25}.get(item.get("evidence_status"), 0)
            score = coverage * 10 + concrete * 2 + evidence_bonus + 1 / (rank + 1)
            # Reservas por campo: uma descrição longa não pode expulsar ação/resultado.
            quotas = {"Área técnica": 80, "Problema normalizado": 140,
                      "Problema relatado": 200, "Diagnóstico documentado": 180,
                      "Solução documentada": 360, "Solução candidata": 200,
                      "Resultado documentado": 180, "Pendências": 140,
                      "Versão da ocorrência histórica": 80, "Versão corrigida informada": 80,
                      "Aplicabilidade": 180}
            parts = []
            for label, value in sections.items():
                size = quotas.get(label, 180)
                parts.append(label + ": " + value[:size] + (" [trecho reduzido]" if len(value) > size else ""))
            candidates.append((score, signature, {**item, "excerpt": "\n\n".join(parts), "source_body": doc["body"]}))
        if selected_keys is not None:
            return {**result, "results": [item for _, _, item in candidates], "model_shortlist": True}
        # Diversidade local: evita gastar os três lugares com a mesma descrição.
        selected, signatures = [], []
        while candidates and len(selected) < 3:
            def adjusted(candidate):
                score, signature, _ = candidate
                similarity = max((len(signature & previous) / max(1, len(signature | previous))
                                  for previous in signatures), default=0)
                return score - 2 * similarity
            best = max(range(len(candidates)), key=lambda i: adjusted(candidates[i]))
            _, signature, item = candidates.pop(best)
            selected.append(item)
            signatures.append(signature)
        return {**result, "results": selected}
