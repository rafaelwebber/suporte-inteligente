"""Avalia recuperação no acervo importado; não chama LLM nem altera os bancos."""
import argparse
import csv
import hashlib
import html
import json
from dataclasses import replace
from pathlib import Path

from knowledge_search.config import Settings
from knowledge_search.service import SearchService
from knowledge_search.storage import readonly, metadata

# Expectativas de desenvolvimento baseadas nos dois atendimentos fornecidos.
# Não são um conjunto independente de homologação nem rótulos exaustivos.
CASES = [
    ('precedencia', 'Ticket #17122 quem salva primeiro', 'caso_tecnico:17122', ['quem salva primeiro', 'confirmado_pelo_usuario']),
    ('testes', 'Ticket #17122 testes realizados', 'caso_tecnico:17122', ['Natureza: teste', 'planejada', 'Resultados da execução dos testes não fornecidos']),
    ('decks', 'DataManager silos paralelos mesmo deck', 'caso_tecnico:20436', ['decks diferentes', 'mesmo deck']),
    ('hipotese', 'Expedição 4798309 causa confirmada', 'caso_tecnico:20436', ['Qualificação: hipotese', '4798309']),
    ('cronologia', 'Expedição 4798552 caixa adicionada depois', 'caso_tecnico:20436', ['4798552', 'caixa 3']),
    ('implantacao', 'Ticket #20436 versão implantada', 'caso_tecnico:20436', ['Versão do software não fornecida', 'Implantação no cliente não documentada']),
    ('plc', 'Ticket #20436 mesmo deck DB PLC', 'caso_tecnico:20436', ['mesmo deck']),
    ('sem_cobertura', 'zzqxnonexistent987', None, []),
]


def check_case(case, observation):
    identifier, query, target, required = case
    ids = observation['ids']
    evidence = observation['evidence']
    excerpts = '\n'.join(r['excerpt'] for r in evidence if r['source_id'] == target)
    checks = {'alvo_top5': target in ids[:5] if target else not ids,
              'alvo_evidencias': any(r['source_id'] == target for r in evidence) if target else not evidence,
              'qualificadores_preservados': all(t.casefold() in excerpts.casefold() for t in required)}
    return {'id': identifier, 'query': query, 'target': target, 'required': required,
            'ids': ids, 'evidence': evidence, 'checks': checks, 'passed': all(checks.values())}


def evaluate(service):
    service.status()
    with readonly(service.settings.index) as index:
        info = metadata(index)
    observations = []
    for case in CASES:
        result = service.search(case[1], 20)
        selected = service.evidence(result)
        observations.append(check_case(case, {'ids': [r['source_id'] for r in result['results']],
            'evidence': [{'source_id': r['source_id'], 'excerpt': r['excerpt']} for r in selected['results']]}))
    return {'suite': 'acervo_desenvolvimento_v1', 'status': service.status(),
            'indice': {key: info.get(key) for key in ('source_signature', 'projection', 'model', 'created_at')},
            'expectativas_sha256': hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest(),
            'codigo_sha256': {name: hashlib.sha256((Path(__file__).parents[1] / name).read_bytes()).hexdigest()
                             for name in ['knowledge_search/service.py', 'evaluation/acervo.py']},
            'llm_executada': False, 'homologacao_independente': False,
            'passed': sum(r['passed'] for r in observations), 'total': len(observations), 'cases': observations}


def write_report(report, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'relatorio.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = ''
    for r in report['cases']:
        failures = ', '.join(k for k, v in r['checks'].items() if not v) or 'Verificações atendidas'
        rows += '<tr><td>' + html.escape(r['query']) + '</td><td>' + ('Passou' if r['passed'] else 'Revisar') + '</td><td>' + html.escape(failures) + '</td></tr>'
    page = '''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Avaliação do acervo</title>
<style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#193044}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ccc;text-align:left}</style>
<h1>Avaliação da recuperação do acervo</h1><p>Casos de desenvolvimento derivados dos tickets 17122 e 20436. Não houve geração por LLM. As verificações abaixo medem recuperação de fontes e preservação de qualificadores, não correção de uma resposta gerada.</p>'''
    page += f'<p><strong>{report["passed"]}/{report["total"]} cenários atendidos.</strong> Modo: {html.escape(report["status"]["mode"])}.</p>'
    page += '<table><tr><th>Consulta</th><th>Resultado</th><th>Verificação</th></tr>' + rows + '</table><p>Os rótulos são parciais e foram usados durante o desenvolvimento. Não representam precisão geral, taxa de resolução de tickets nem homologação independente.</p><p><a href="relatorio.json">Evidências e hashes</a> · <a href="revisao-humana.csv">Planilha de revisão humana</a></p></html>'
    (output / 'relatorio.html').write_text(page, encoding='utf-8')
    with (output / 'revisao-humana.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(['cenario', 'consulta', 'fontes_pertinentes', 'escopo_correto', 'observacoes', 'revisor'])
        for r in report['cases']:
            writer.writerow([r['id'], r['query'], '', '', '', ''])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--banco', type=Path, required=True)
    parser.add_argument('--indice', type=Path, required=True)
    parser.add_argument('--saida', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.saida.exists():
        parser.error('Use uma pasta nova para preservar a avaliação anterior.')
    settings = replace(Settings.from_env(), source=args.banco, index=args.indice)
    report = evaluate(SearchService(settings))
    write_report(report, args.saida)
    print(json.dumps({'aprovados': report['passed'], 'total': report['total'], 'saida': str(args.saida)}, ensure_ascii=False))
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
