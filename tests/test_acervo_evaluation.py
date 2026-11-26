import tempfile
import unittest
from pathlib import Path

from evaluation.acervo import check_case, write_report


class AcervoEvaluationTests(unittest.TestCase):
    def test_wrong_scope_or_missing_qualification_fails(self):
        case = ('a', 'consulta', 'caso:1', ['hipotese'])
        result = check_case(case, {'ids': ['caso:1'], 'evidence': [
            {'source_id': 'caso:2', 'excerpt': 'hipotese'},
            {'source_id': 'caso:1', 'excerpt': 'causa confirmada'}]})
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['qualificadores_preservados'])

    def test_negative_requires_empty_search_and_empty_evidence(self):
        case = ('n', 'sem cobertura', None, [])
        self.assertTrue(check_case(case, {'ids': [], 'evidence': []})['passed'])
        self.assertFalse(check_case(case, {'ids': ['x'], 'evidence': []})['passed'])

    def test_report_escapes_content_and_refuses_overwrite(self):
        case = check_case(('x', '<script>teste</script>', None, []), {'ids': [], 'evidence': []})
        report = {'cases': [case], 'status': {'mode': 'textual'}, 'passed': 1, 'total': 1}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'report'
            write_report(report, output)
            self.assertNotIn('<script>', (output / 'relatorio.html').read_text())
            with self.assertRaises(FileExistsError):
                write_report(report, output)
