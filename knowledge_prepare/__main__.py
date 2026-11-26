"""Preparação reproduzível; não altera o banco ativo da plataforma."""
import argparse
import json
import sys

from .pipeline import prepare


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entrada', required=True)
    parser.add_argument('--saida', required=True, help='Diretório novo, fora da entrada')
    parser.add_argument('--pacote', default='suporte_base_csv')
    parser.add_argument('--versao')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--interacoes-csv')
    source.add_argument('--interacoes-derivadas', help='JSONL de evidências do pacote anterior; cobertura parcial')
    parser.add_argument('--colunas-interacoes', help='JSON com id, ticket, texto e data mapeados aos cabeçalhos CSV')
    parser.add_argument('--casos-curados', help='Pasta opcional com ticket_*.json no contrato 0.2.0')
    args = parser.parse_args(argv)
    try:
        result = prepare(args.entrada, args.saida, package=args.pacote, version=args.versao,
                         legacy=args.interacoes_derivadas, interactions=args.interacoes_csv,
                         interaction_columns=json.loads(args.colunas_interacoes) if args.colunas_interacoes else None,
                         curated=args.casos_curados)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'estado': 'erro', 'mensagem': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
