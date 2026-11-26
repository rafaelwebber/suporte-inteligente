"""Valida estrutura, referências, hashes e trechos. Não comprova verdade semântica."""
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate(case):
    schema = load(ROOT / 'schemas/caso_ticket.schema.json')
    Draft202012Validator.check_schema(schema)
    try:
        Draft202012Validator(schema).validate(case)
    except ValidationError as exc:
        raise ValueError('Caso curado incompatível com o contrato 0.2.0: ' + '.'.join(map(str, exc.absolute_path))) from exc
    groups = ['fontes', 'evidencias', 'contexto', 'ocorrencias', 'eventos', 'afirmacoes', 'relacoes', 'pendencias']
    entities = [case] + [x for group in groups for x in case[group]]
    ids = [x['id'] for x in entities]
    if len(set(ids)) != len(ids):
        raise ValueError('Identificador duplicado')
    if any(not x.startswith(case['id'] + ':') for x in ids[1:]):
        raise ValueError('Identificador fora do namespace do caso')
    sources = {s['id']: s for s in case['fontes']}
    evidence = {e['id']: e for e in case['evidencias']}
    occurrences = {o['id'] for o in case['ocorrencias']}
    claims = {a['id']: a for a in case['afirmacoes']}
    context = {c['id'] for c in case['contexto']}
    for source in sources.values():
        if hashlib.sha256(source['texto'].encode()).hexdigest() != source['sha256_texto']:
            raise ValueError('Hash do texto divergente')
    for e in evidence.values():
        s = sources[e['fonte_id']]
        if e['revisao_fonte'] != s['revisao']:
            raise ValueError('Revisão da evidência divergente')
        if not 0 <= e['inicio'] < e['fim'] <= len(s['texto']):
            raise ValueError('Offsets fora do texto')
        if s['texto'][e['inicio']:e['fim']] != e['trecho']:
            raise ValueError('Trecho não corresponde à fonte')
    for group in groups:
        for entity in case[group]:
            if not set(entity.get('evidencia_ids', [])) <= evidence.keys():
                raise ValueError('Evidência inexistente')
    for a in claims.values():
        if not set(a['escopo']['ocorrencia_ids']) <= occurrences:
            raise ValueError('Ocorrência inexistente')
        if not set(a['contexto_ids']) <= context:
            raise ValueError('Contexto inexistente')
        if a['qualificacao'] == 'confirmado_pelo_usuario':
            if not any(sources[evidence[e]['fonte_id']]['tipo'] == 'esclarecimento' for e in a['evidencia_ids']):
                raise ValueError('Confirmação sem esclarecimento como evidência')
    for event in case['eventos']:
        if event['ocorrencia_id'] not in occurrences:
            raise ValueError('Evento sem ocorrência')
        if not any(event['horario_original'] in evidence[e]['trecho'] for e in event['evidencia_ids']):
            raise ValueError('Horário sem evidência literal')
    for r in case['relacoes']:
        if r['origem_id'] not in claims or r['destino_id'] not in claims.keys() | occurrences:
            raise ValueError('Extremidade inválida da relação')
        a = claims[r['origem_id']]
        if r['tipo'] == 'hipotese_semelhante_a' and (a['qualificacao'] != 'hipotese' or r['qualificacao'] != 'hipotese'):
            raise ValueError('Relação hipotética promovida indevidamente')
        if r['tipo'] == 'resultado_apos_acao':
            if a['natureza'] != 'resultado' or claims.get(r['destino_id'], {}).get('natureza') not in ['acao', 'correcao']:
                raise ValueError('Resultado deve apontar para ação ou correção')
    for p in case['pendencias']:
        if p['resolucao_afirmacao_id'] is not None and p['resolucao_afirmacao_id'] not in claims:
            raise ValueError('Resolução inexistente')


def load_curated(path):
    from knowledge_import.validation import parse_json
    from .pipeline import sanitized, sanitize
    case = parse_json(Path(path).read_bytes())
    validate(case)
    result = sanitized(case)
    sources = {s['id']: s for s in result['fontes']}
    for source in sources.values():
        source['sha256_texto'] = hashlib.sha256(source['texto'].encode()).hexdigest()
    for evidence in result['evidencias']:
        text = sources[evidence['fonte_id']]['texto']
        quote = evidence['trecho']
        start = text.find(quote)
        if not quote or start < 0:
            raise ValueError('Mascaramento impede rastrear evidência; revisão necessária.')
        evidence['inicio'], evidence['fim'] = start, start + len(quote)
    validate(result)
    return result
