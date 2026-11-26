"""Classificação conservadora e projeção do histórico para consulta local."""

from .text import clean_text


def field(data, name):
    value = data.get(name)
    return clean_text(value.strip()) if isinstance(value, str) and value.strip() else None


def evidence_status(kind, data):
    if kind == "artigo":
        return "documentacao"
    if kind == "interacao":
        return "interacao_nao_classificada"
    if kind == "caso_tecnico":
        claims = {claim["id"]: claim for claim in data.get("afirmacoes", [])}
        actions = {key for key, claim in claims.items()
                   if claim["natureza"] in ("acao", "correcao")
                   and claim["execucao"] == "realizada_conforme_relato"
                   and claim["qualificacao"] != "hipotese" and claim["evidencia_ids"]}
        for link in data.get("relacoes", []):
            outcome = claims.get(link["origem_id"], {})
            if (link["tipo"] == "resultado_apos_acao" and link["destino_id"] in actions
                    and link["qualificacao"] in ("documentada", "confirmada_pelo_usuario")
                    and outcome.get("natureza") == "resultado" and outcome.get("evidencia_ids")
                    and outcome.get("qualificacao") != "hipotese" and link["evidencia_ids"]):
                return "acao_com_resultado"
        return "acao_registrada" if actions else "caso_em_revisao"
    if field(data, "solucao_documentada"):
        return "solucao_documentada"
    if field(data, "solucao_candidata"):
        return "solucao_candidata"
    if field(data, "diagnostico_documentado") or field(data, "resultado_documentado"):
        return "evidencia_parcial"
    return "sem_solucao_identificada"


def details(kind, data):
    if kind not in ("caso_ticket", "caso_tecnico", "interacao"):
        return None
    context_fields = ("area_tecnica", "versao_cliente", "versao_corrigida", "aplicabilidade_ao_caso_atual")
    result = {
        "ticket": data.get("ticket", data.get("ticket_origem")),
        "evidence_status": evidence_status(kind, data),
        "problem": field(data, "problema_relatado") or field(data, "problema_normalizado"),
        "diagnosis": field(data, "diagnostico_documentado"),
        "action": field(data, "solucao_documentada"),
        "candidate": field(data, "solucao_candidata"),
        "outcome": field(data, "resultado_documentado"),
        "context": [value for key in context_fields if (value := field(data, key))],
        "declared_interactions": data.get("total_interacoes_declarado"),
        "available_interactions": data.get("total_interacoes_disponivel"),
        "derived_history": data.get("interacoes_origem_derivada") is True,
    }
    if kind == "caso_tecnico":
        evidence = {item["id"]: item for item in data["evidencias"]}
        sources = {item["id"]: item for item in data["fontes"]}
        result["context"] = [clean_text(f"{item['campo']}: {item['valor']}") for item in data["contexto"]]
        result["claims"] = [{
            "statement": field(claim, "enunciado"),
            "kind": claim["natureza"], "qualification": claim["qualificacao"],
            "execution": claim["execucao"], "review": claim["revisao"],
            "evidence": [{
                "reference": key,
                "source": sources[evidence[key]["fonte_id"]]["tipo"],
                "quote": field(evidence[key], "trecho"),
            } for key in claim["evidencia_ids"]],
        } for claim in data["afirmacoes"]]
    return result
