"""Não chama APIs, não infere soluções e não ativa o banco da aplicação."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from knowledge_import.validation import canonical, digest, validate_record, SNAPSHOT_MODE
from knowledge_import.storage import import_package
from knowledge_search.text import clean_text

VERSION = '1.0.0'
SOURCES = ('tickets', 'wiki', 'tecnicos', 'ticket_artigo', 'ticket_tag', 'ticket_referencia')
REQUIRED = {'tickets': {'ticket', 'assunto', 'descricao', 'tot_interacao'},
            'wiki': {'cod_artigo', 'titulo', 'texto'}, 'tecnicos': {'login'},
            'ticket_artigo': {'ticket', 'artigo'}, 'ticket_tag': {'ticket', 'tag'},
            'ticket_referencia': {'ticket', 'ticket_citado', 'interacao'}}
csv.field_size_limit(64 * 1024 * 1024)


class PreparationError(ValueError):
    pass


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sanitize(text):
    # Tratar formatos de credenciais antes do mascaramento genérico existente.
    text = re.sub(r'(?is)(<(?:password|senha|pwd|token|api[_-]?key)\b[^>]*>).*?(</(?:password|senha|pwd|token|api[_-]?key)\s*>)', r'\1[REMOVIDO]\2', text)
    text = re.sub(r'''(?im)(\b(?:password|senha|pwd|token|api[_-]?key)\b\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^;\r\n<>]+)''', r'\1[REMOVIDO]', text)
    return clean_text(text)


def sanitized(value):
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {k: sanitized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    return value


def read_csv(path, required):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, delimiter=';')
        columns = reader.fieldnames or []
        if len(set(columns)) != len(columns) or not required <= set(columns):
            raise PreparationError(f'Cabeçalho inválido em {path.name}; obrigatórios: {sorted(required)}')
        for number, row in enumerate(reader, 1):
            if None in row or any(v is None for v in row.values()):
                raise PreparationError(f'Quantidade de campos inválida em {path.name}, registro {number}')
            yield number, row


def envelope(kind, key, data, source, origin_key, searchable=False, group='Tickets', review='importado_sem_extracao_semantica'):
    record = {'id': f'{kind}:{key}', 'tipo': kind, 'grupo_conhecimento': group, 'id_origem': str(key),
              'origem': {'arquivo_relativo': source, 'registro_id': str(origin_key)},
              'metadados': {'cliente_referencia': None, 'versao_referencia': None, 'estado_revisao': review,
                            'permissao_de_acesso_requerida': True, 'apto_recomendacao_automatica': False,
                            'indexacao': 'nao_executada', 'disponivel_pesquisa': bool(searchable),
                            'uso_na_recomendacao': 'fonte_documentada_preservar_qualificacoes'},
              'dados': data, 'conteudo_sha256': digest(data)}
    validate_record(record, 'conhecimento')
    return record


def prepare(input_dir, output, *, package='suporte_base_csv', version=None, legacy=None,
            interactions=None, interaction_columns=None, curated=None):
    input_dir, output = Path(input_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise PreparationError('Saída já existe; use um diretório novo.')
    if output.is_relative_to(input_dir):
        raise PreparationError('Saída deve ficar fora da pasta de entrada.')
    if legacy and interactions:
        raise PreparationError('Escolha CSV bruto ou evidências derivadas, nunca ambos na mesma carga.')
    if interaction_columns and not interactions:
        raise PreparationError('Mapeamento de colunas exige CSV de interações.')
    paths = {}
    for name in SOURCES:
        found = sorted(p for p in input_dir.rglob('*.csv') if re.sub(r' \(\d+\)$', '', p.stem) == name)
        if len(found) != 1:
            raise PreparationError(f'Esperado um CSV de {name}; encontrados {len(found)}.')
        paths[name] = found[0]
    if interactions:
        paths['interacoes'] = Path(interactions).resolve()
    if legacy:
        paths['interacoes_derivadas'] = Path(legacy).resolve()
    curated_paths = sorted(Path(curated).glob('ticket_*.json')) if curated else []
    if curated and not curated_paths:
        raise PreparationError('Pasta de curadoria sem arquivos ticket_*.json.')
    for p in curated_paths:
        paths['curado_' + p.stem] = p
    manifest = {name: {'arquivo': p.name, 'sha256': file_hash(p)} for name, p in paths.items()}
    project = Path(__file__).resolve().parents[1]
    dependencies = ['knowledge_prepare/pipeline.py', 'knowledge_prepare/curated.py',
                    'knowledge_prepare/schemas/caso_ticket.schema.json', 'knowledge_search/text.py',
                    'api/text_utils.py', 'knowledge_import/validation.py']
    settings = {'pipeline': VERSION, 'codigo_sha256': {p: file_hash(project / p) for p in dependencies},
                'colunas_interacoes': interaction_columns}
    version = version or ('preparacao-' + digest({'fontes': manifest, 'configuracao': settings})[:20])
    report = {'pipeline': settings, 'fontes': manifest, 'contagens': {}, 'pendencias': [],
              'mascaramento': 'por_padroes_nao_equivale_a_anonimizacao_completa',
              'llm_executada': False, 'busca_executada': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'pacote'; stage.mkdir()
        counts = Counter(); ids = set(); ticket_rows = {}; article_ids = set()
        interactions_count = Counter(); legacy_count = 0
        entities = (stage / 'conhecimento.jsonl').open('w', encoding='utf-8')
        links = (stage / 'vinculos.jsonl').open('w', encoding='utf-8')
        sources = []
        def emit(record):
            if record['id'] in ids:
                raise PreparationError('Identificador duplicado: ' + record['id'])
            ids.add(record['id']); counts[record['tipo']] += 1
            entities.write(canonical(record) + '\n')
        def link(origin, target, relation, source, condition=''):
            key = digest([origin, target, relation, source])
            record = {'id': 'vinculo:' + key, 'origem_id': origin, 'destino_id': target,
                      'relacao': relation, 'estado': 'documentado_na_fonte', 'origem_vinculo': source,
                      'prova_de_causalidade': False, 'condicao_documentada': condition}
            # Repetições equivalentes não geram arestas duplicadas.
            if record['id'] in ids:
                return
            ids.add(record['id']); counts['vinculos'] += 1
            links.write(canonical(record) + '\n')
        def reference(kind, key):
            if not key:
                raise PreparationError('Associação sem identificador de destino.')
            identifier = f'{kind}:{key}'
            if identifier not in ids:
                emit(envelope(kind, key, {'referencia': key, 'disponibilidade': 'fora_do_recorte'}, 'referencias_externas', key))
            return identifier
        try:
            for name, path in paths.items():
                sources.append({'id': 'fonte:' + name, 'tipo': 'csv' if path.suffix.lower() == '.csv' else 'derivado',
                                'arquivo_relativo': path.name, 'disponibilidade': 'origem_local_separada',
                                'acesso_automatico_ao_caminho': False, 'sha256': manifest[name]['sha256']})
            for number, raw in read_csv(paths['tickets'], REQUIRED['tickets']):
                key = raw['ticket'].strip()
                if not key or key in ticket_rows:
                    raise PreparationError('Ticket sem chave ou duplicado.')
                try:
                    declared = int(raw['tot_interacao'])
                    if declared < 0: raise ValueError()
                except ValueError as exc:
                    raise PreparationError('Total de interações inválido no ticket ' + key) from exc
                ticket_rows[key] = (declared, raw)
            # Contar e validar interações antes de produzir os pais.
            staged_interactions = Path(tmp) / 'interacoes.jsonl'
            with staged_interactions.open('w', encoding='utf-8') as stream:
                seen = set()
                def interaction(key, ticket, text, date, source, source_number, original_hash):
                    if not key or key in seen or ticket not in ticket_rows:
                        raise PreparationError('Interação duplicada, sem chave ou com ticket ausente: ' + str(key))
                    seen.add(key); interactions_count[ticket] += 1
                    payload = {'interacao': key, 'ticket': ticket, 'texto': sanitize(text), 'data_original': date or None,
                               'registro_origem': source_number, 'sha256_registro_origem': original_hash,
                               'origem_derivada': bool(legacy)}
                    stream.write(canonical(envelope('interacao', key, payload, source, key, bool(text.strip()), review='interacao_preservada_sem_inferencia')) + '\n')
                if legacy:
                    with paths['interacoes_derivadas'].open(encoding='utf-8') as original:
                        for number, line in enumerate(original, 1):
                            from knowledge_import.validation import parse_json
                            r = parse_json(line.encode()); validate_record(r, 'conhecimento')
                            d = r['dados']
                            if d.get('tipo') != 'interacao': continue
                            legacy_count += 1
                            interaction(str(d['evidencia_id']).removeprefix('interacao_'), str(d['ticket_origem']), d['texto'],
                                        d.get('data_ou_revisao'), paths['interacoes_derivadas'].name, number, digest(r))
                if interactions:
                    mapping = interaction_columns or {'id': 'interacao', 'ticket': 'ticket', 'texto': 'texto', 'data': 'data'}
                    if set(mapping) != {'id', 'ticket', 'texto', 'data'} or len(set(mapping.values())) != 4:
                        raise PreparationError('Mapeamento exige id, ticket, texto, data para quatro colunas diferentes.')
                    for number, raw in read_csv(paths['interacoes'], set(mapping.values())):
                        interaction(raw[mapping['id']].strip(), raw[mapping['ticket']].strip(), raw[mapping['texto']],
                                    raw[mapping['data']], paths['interacoes'].name, number, digest(raw))
            for key, (declared, raw) in ticket_rows.items():
                found = interactions_count[key]
                coverage = 'contagem_confere_nao_comprova_integridade' if found == declared else 'contagem_divergente'
                data = {'ticket': key, 'assunto': sanitize(raw['assunto']), 'problema_relatado': sanitize(raw['descricao']),
                        'total_interacoes_declarado': declared, 'total_interacoes_disponivel': found,
                        'cobertura_interacoes': coverage, 'interacoes_origem_derivada': bool(legacy),
                        'pendencias': f'Interações disponíveis: {found}; declaradas: {declared}. ' +
                                     ('Fonte derivada do pacote anterior; não é exportação bruta completa.' if legacy else 'Conferir integridade na fonte.'),
                        'campos_origem': sanitized(raw), 'sha256_registro_origem': digest(raw)}
                emit(envelope('caso_ticket', key, data, paths['tickets'].name, key, True))
                link('caso_ticket:' + key, 'fonte:tickets', 'proveniente_de', 'csv')
            with staged_interactions.open(encoding='utf-8') as stream:
                for line in stream:
                    r = json.loads(line); emit(r)
                    link(r['id'], 'caso_ticket:' + r['dados']['ticket'], 'pertence_ao_ticket', 'origem_interacao')
            for _, raw in read_csv(paths['wiki'], REQUIRED['wiki']):
                key = raw['cod_artigo'].strip()
                if not key or key in article_ids: raise PreparationError('Artigo sem chave ou duplicado.')
                article_ids.add(key)
                data = {**sanitized(raw), 'artigo': key, 'sha256_registro_origem': digest(raw),
                        'limites': 'Texto da exportação; diagramas e anexos não foram reconstruídos. Versão do software não inferida.'}
                emit(envelope('artigo', key, data, paths['wiki'].name, key, bool(raw['texto'].strip() or raw.get('resumo', '').strip()), group='Processos'))
                link('artigo:' + key, 'fonte:wiki', 'proveniente_de', 'csv')
            for _, raw in read_csv(paths['tecnicos'], REQUIRED['tecnicos']):
                key = raw['login'].strip()
                if not key: raise PreparationError('Técnico sem identificador.')
                emit(envelope('tecnico', key, sanitized(raw), paths['tecnicos'].name, key))
            for name in ('ticket_tag', 'ticket_artigo', 'ticket_referencia'):
                for number, raw in read_csv(paths[name], REQUIRED[name]):
                    ticket = raw['ticket'].strip()
                    if ticket not in ticket_rows: raise PreparationError('Associação com ticket ausente em ' + name)
                    if name == 'ticket_tag':
                        key = digest(raw); emit(envelope('ticket_tag', key, sanitized(raw), paths[name].name, str(number)))
                        target, relation = 'ticket_tag:' + key, 'possui_tag'
                    elif name == 'ticket_artigo':
                        article = raw['artigo'].strip()
                        target = 'artigo:' + article if article in article_ids else reference('referencia_artigo', article)
                        relation = 'vincula_artigo'
                    else:
                        dest = raw['ticket_citado'].strip()
                        target = 'caso_ticket:' + dest if dest in ticket_rows else reference('referencia_ticket', dest)
                        relation = 'cita_ticket'
                    link('caso_ticket:' + ticket, target, relation, name + ':' + str(number), canonical(sanitized(raw)))
            for key, (_, raw) in ticket_rows.items():
                dest = raw.get('ticket_relacionado', '').strip()
                if dest:
                    target = 'caso_ticket:' + dest if dest in ticket_rows else reference('referencia_ticket', dest)
                    link('caso_ticket:' + key, target, 'relacionado_no_cadastro', 'csv')
            for path in curated_paths:
                from .curated import load_curated
                case = load_curated(path)
                ticket = case['ticket_origem']
                if ticket not in ticket_rows: raise PreparationError('Caso curado sem ticket na exportação.')
                emit(envelope('caso_tecnico', ticket, case, path.name, ticket, True, review='curadoria_assistida_preservar_estados'))
                link('caso_tecnico:' + ticket, 'caso_ticket:' + ticket, 'detalha_ticket', 'curadoria_chat')
        finally:
            entities.close(); links.close()
        with (stage / 'fontes.jsonl').open('w', encoding='utf-8') as stream:
            for r in sources: stream.write(canonical(r) + '\n')
        declared = sum(x[0] for x in ticket_rows.values()); found = sum(interactions_count.values())
        report.update(contagens=dict(counts), cobertura={'tickets': len(ticket_rows), 'interacoes_declaradas': declared,
            'interacoes_disponiveis': found, 'diferenca_de_contagem': declared-found,
            'tickets_com_contagem_divergente': sum(interactions_count[k] != v[0] for k,v in ticket_rows.items()),
            'origem_interacoes': 'derivada' if legacy else 'csv' if interactions else 'ausente'})
        if legacy or not interactions or found != declared:
            report['pendencias'].append('Cobertura das interações não homologada; exportação bruta completa pendente.')
        report['pendencias'].extend(['Revisão de confidencialidade e permissões antes de disponibilizar o acervo.',
                                    'Sem extração por IA em massa; conteúdo preservado não equivale a regra validada.'])
        (stage / 'relatorio_preparacao.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        entries = []
        for name, kind, count in [('fontes.jsonl', 'fontes', len(sources)), ('conhecimento.jsonl', 'conhecimento', sum(v for k,v in counts.items() if k != 'vinculos')), ('vinculos.jsonl','vinculos',counts['vinculos'])]:
            p = stage / name
            entries.append({'arquivo':name,'formato':'jsonl','papel':'registros','destino_sugerido':kind,'importar':True,'registros':count,'bytes':p.stat().st_size,'sha256':file_hash(p)})
        catalog = {'schema_versao':'1.0.0','pacote_id':package,'pacote_versao':version,'modo':SNAPSHOT_MODE,
                   'entidades':entries[1]['registros'],'vinculos':counts['vinculos'],'pendencias_estruturais':0,
                   'cobertura_fontes':report['cobertura'], 'limite_snapshot':'Snapshot completo das fontes declaradas; não afirma completude do sistema de origem.', 'arquivos':entries}
        (stage / 'catalogo_importacao.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        validation = import_package(stage / 'catalogo_importacao.json', Path(tmp) / 'unused.sqlite3', validate_only=True)
        if validation.get('estado') == 'rejeitado':
            raise PreparationError('Importador rejeitou pacote: ' + canonical(validation))
        (stage / 'validacao_importador.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        stage.rename(output)
    return {'saida':str(output),'pacote_versao':version,**report}
