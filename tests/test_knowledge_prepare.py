"""Integração com dados sintéticos: CSV → catálogo → banco → recuperação."""
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_import.storage import import_package
from knowledge_prepare.pipeline import prepare, PreparationError, REQUIRED, sanitize
from knowledge_prepare.curated import load_curated
from knowledge_search.config import Settings
from knowledge_search.storage import build
from knowledge_search.service import SearchService
from knowledge_search.generation import context_excerpt


def case_fixture():
    text = 'Possivelmente falha no agrupamento do PLC. Contato: tecnico@example.com'
    return {
        'schema_versao': '0.2.0', 'id': 'caso:1', 'revisao': 1, 'tipo': 'caso_ticket',
        'ticket_origem': '1', 'titulo': 'Agrupamento PLC', 'publico': 'tecnicos_internos',
        'cobertura': {'historico_completo': False, 'interacoes_declaradas_csv': 2, 'limitacoes': ['Histórico parcial.']},
        'proveniencia': {'sistema_origem': None, 'referencia_v0_1': 'externo.json',
                        'sha256_referencia_v0_1': '0' * 64, 'estado': 'exemplo_curado_nao_homologado'},
        'fontes': [{'id': 'caso:1:fonte:1', 'tipo': 'texto_chat', 'revisao': 1, 'texto': text,
                    'sha256_texto': hashlib.sha256(text.encode()).hexdigest(), 'localizador': 'chat sintético',
                    'chave_interacao_origem': None, 'data_original': None, 'autor_original': None,
                    'disponibilidade_original': 'chat', 'limites': []}],
        'evidencias': [{'id': 'caso:1:evidencia:1', 'fonte_id': 'caso:1:fonte:1', 'revisao_fonte': 1,
                        'inicio': 0, 'fim': len(text), 'trecho': text}],
        'contexto': [], 'ocorrencias': [], 'eventos': [], 'relacoes': [], 'pendencias': [],
        'afirmacoes': [{'id': 'caso:1:afirmacao:1', 'natureza': 'analise', 'enunciado': 'Possível falha no agrupamento PLC.',
                        'qualificacao': 'hipotese', 'execucao': 'nao_se_aplica',
                        'escopo': {'estado': 'ocorrencias_nao_identificadas', 'ocorrencia_ids': []},
                        'evidencia_ids': ['caso:1:evidencia:1'], 'contexto_ids': [], 'revisao': 'pendente',
                        'metodo_extracao': 'curadoria_assistida', 'limites': ['Não atribuir a uma expedição específica.']}]}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'entrada'; self.input.mkdir()
        rows = {
            'tickets': [{'ticket': '1', 'assunto': 'PLC', 'descricao': 'Carregamento incompleto', 'tot_interacao': '2'},
                        {'ticket': '2', 'assunto': 'Sem solução', 'descricao': 'Análise pendente', 'tot_interacao': '0'}],
            'wiki': [{'cod_artigo': '3', 'titulo': 'Manual válvula', 'texto': 'Procedimento exclusivo valvulaZeta.'},
                     {'cod_artigo': '4', 'titulo': 'Sem texto', 'texto': ''}],
            'tecnicos': [{'login': 'tecnico_teste'}],
            'ticket_artigo': [{'ticket': '1', 'artigo': '3'}],
            'ticket_tag': [{'ticket': '1', 'tag': 'automacao'}],
            'ticket_referencia': [{'ticket': '1', 'ticket_citado': '999', 'interacao': '10'}]}
        for name, values in rows.items():
            self.csv(name, values)
        self.interactions = self.root / 'interacoes.csv'
        self.csv('interacoes', [{'interacao': '10', 'ticket': '1', 'texto': 'Análise de pressão no motorOmega; senha="segredo"', 'data': '2026-01-01 10:00'}], self.interactions)

    def csv(self, name, rows, path=None):
        with (path or self.input / (name + '.csv')).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else sorted(REQUIRED[name]), delimiter=';')
            writer.writeheader(); writer.writerows(rows)

    def run_prepare(self, name='saida', **kwargs):
        output = self.root / name
        report = prepare(self.input, output, **kwargs)
        records = [json.loads(line) for line in (output / 'conhecimento.jsonl').read_text(encoding='utf-8').splitlines()]
        return output, report, {r['id']: r for r in records}

    def test_full_flow_preserves_unsolved_and_recovers_articles_interactions(self):
        output, report, records = self.run_prepare(interactions=self.interactions)
        self.assertIn('caso_ticket:2', records)
        self.assertIn('referencia_ticket:999', records)
        self.assertFalse(records['artigo:4']['metadados']['disponivel_pesquisa'])
        self.assertNotIn('solucao_documentada', records['caso_ticket:1']['dados'])
        self.assertEqual(1, report['cobertura']['diferenca_de_contagem'])
        self.assertNotIn('segredo', records['interacao:10']['dados']['texto'])
        self.assertIn('Análise de pressão', records['interacao:10']['dados']['texto'])
        settings = Settings(self.root / 'source.db', self.root / 'index.db', self.root / 'model')
        catalog = output / 'catalogo_importacao.json'
        self.assertEqual('ativado', import_package(catalog, settings.source)['estado'])
        self.assertEqual('sem_alteracoes', import_package(catalog, settings.source)['estado'])
        build(settings, None)
        service = SearchService(settings)
        for query, identifier in [('valvulaZeta', 'artigo:3'), ('motorOmega', 'interacao:10')]:
            result = service.evidence(service.search(query))
            self.assertEqual(identifier, result['results'][0]['source_id'])
            self.assertFalse(result['results'][0]['has_solution'])
            self.assertIn('Limites:', result['results'][0]['excerpt'])

    def test_deterministic_and_no_overwrite(self):
        first, report, _ = self.run_prepare('a')
        second, report2, _ = self.run_prepare('b')
        self.assertEqual(report['pacote_versao'], report2['pacote_versao'])
        for name in ('conhecimento.jsonl', 'fontes.jsonl', 'vinculos.jsonl', 'catalogo_importacao.json'):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        with self.assertRaises(PreparationError): self.run_prepare('a')

    def test_duplicate_ticket_rejects_atomically(self):
        row = {'ticket': '1', 'assunto': 'x', 'descricao': 'y', 'tot_interacao': '0'}
        self.csv('tickets', [row, row])
        with self.assertRaises(PreparationError): self.run_prepare()
        self.assertFalse((self.root / 'saida').exists())

    def test_orphan_interaction_rejected(self):
        self.csv('interacoes', [{'interacao': '10', 'ticket': '999', 'texto': 'x', 'data': ''}], self.interactions)
        with self.assertRaises(PreparationError): self.run_prepare(interactions=self.interactions)

    def test_legacy_is_explicitly_partial(self):
        from knowledge_prepare.pipeline import envelope
        legacy = self.root / 'legacy.jsonl'
        r = envelope('evidencia_00', 'x', {'tipo': 'interacao', 'evidencia_id': 'interacao_10',
                       'ticket_origem': '1', 'texto': 'Relato parcial'}, 'origem.jsonl', 'x')
        legacy.write_text(json.dumps(r) + '\n')
        _, report, records = self.run_prepare(legacy=legacy)
        self.assertEqual('derivada', report['cobertura']['origem_interacoes'])
        self.assertTrue(records['interacao:10']['dados']['origem_derivada'])
        self.assertIn('não é exportação bruta completa', records['caso_ticket:1']['dados']['pendencias'])

    def test_masking_and_curated_evidence_integrity(self):
        self.assertNotIn('secret', sanitize('<password>secret</password>\nsenha="secret"'))
        folder = self.root / 'casos'; folder.mkdir()
        path = folder / 'ticket_1.json'; case = case_fixture()
        path.write_text(json.dumps(case))
        normalized = load_curated(path)
        self.assertNotIn('tecnico@example.com', json.dumps(normalized))
        self.assertEqual('hipotese', normalized['afirmacoes'][0]['qualificacao'])
        output, _, records = self.run_prepare(curated=folder)
        settings = Settings(self.root / 'source.db', self.root / 'index.db', self.root / 'model')
        import_package(output / 'catalogo_importacao.json', settings.source); build(settings, None)
        service = SearchService(settings)
        result = service.evidence(service.search('agrupamento'))
        excerpt = result['results'][0]['excerpt']
        self.assertIn('hipotese', excerpt)
        self.assertIn('Não atribuir a uma expedição específica', excerpt)
        bad = copy.deepcopy(case); bad['evidencias'][0]['trecho'] = 'inventado'
        path.write_text(json.dumps(bad))
        with self.assertRaises(ValueError): load_curated(path)

    def test_curated_context_is_not_silently_truncated(self):
        item = {'metadata': {'tipo_registro': 'caso_tecnico'}, 'excerpt': 'Fato incompleto ' * 500}
        excerpt = context_excerpt(item, 2400)
        self.assertNotIn('Fato incompleto', excerpt)
        self.assertIn('não há evidência suficiente', excerpt)


if __name__ == '__main__':
    unittest.main()
