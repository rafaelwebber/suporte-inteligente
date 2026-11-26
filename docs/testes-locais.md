# Testes locais da base preparada

Os pacotes fornecidos em `../Base` foram integrados à cópia local baseada no
commit `4dc09e1`, usando `continuacao_base/alteracoes-completas.patch`.
Os dados estão em `instance/base-preparada`, fora do Git. O importador verificou
contagens, hashes e relações antes de ativar o snapshot no banco piloto.

## Arquivos do piloto

- Base: `instance/piloto.sqlite3`.
- Índice textual: `instance/piloto-busca.sqlite3`.
- Resultado da importação: `instance/importacao-local.json`.
- Resultado da indexação: `instance/indexacao-local.json`.
- Avaliação do acervo: `instance/avaliacao-local-001/relatorio.html`.
- Testes de software: `instance/testes-local-final.txt`.

O catálogo declara 11.000 tickets e 62.667 interações disponíveis, de 103.241
declaradas na origem. O histórico é parcial. A importação não confirma a verdade
das conclusões técnicas; a avaliação textual também não mede respostas de IA.

## Resultado desta execução

- Importação concluída: 75.509 entidades e 77.540 vínculos.
- Índice textual: 74.456 documentos e 84.625 trechos, sem embeddings.
- Avaliação do acervo: 8 de 8 cenários atendidos.
- Preparador: 7 testes aprovados, incluindo preservação de acentos no Windows.
- Teste da interface em Node: aprovado.
- Descoberta da suíte Python: 126 entradas, 112 aprovadas e 14 erros por
  dependências ausentes. Uma dessas entradas representa o módulo de avaliação
  que não pôde ser importado; portanto nem todos os testes foram descobertos.
- Respostas reais de IA e login na interface: não executados.

O preparador foi corrigido para ler e gravar JSON explicitamente em UTF-8;
antes dependia da codificação padrão do Windows em alguns pontos.

## Completar as dependências

O ambiente `.venv` foi criado, mas a instalação não terminou porque o processo
não conseguiu resolver o endereço do servidor de pacotes. No PowerShell, na raiz
do projeto, com acesso à internet:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/test_conversation_ui.cjs
```

Os testes iniciais de importação e busca foram executados com o Python global
3.14.2. Ele possui parte das dependências, mas faltam Firebase Admin,
Google Cloud Firestore e tokenizers. Não confundir os testes locais com uma
aprovação da suíte completa.

## Repetir a avaliação e pesquisar

Escolha uma pasta de relatório nova a cada avaliação:

```powershell
.\.venv\Scripts\python.exe -m evaluation.acervo --banco instance\piloto.sqlite3 --indice instance\piloto-busca.sqlite3 --saida instance\avaliacao-local-002
.\.venv\Scripts\python.exe -m knowledge_search buscar --banco instance\piloto.sqlite3 --indice instance\piloto-busca.sqlite3 --consulta "Ticket #17122 quem salva primeiro"
```

A busca textual não precisa de Firebase ou chave de IA. Para esses comandos,
enquanto a instalação da `.venv` estiver pendente, o Python global pode ser usado
com `python` no lugar de `.\.venv\Scripts\python.exe`.

## Interface com o banco piloto

O caminho de `FIREBASE_CREDENTIALS` no `.env` precisa apontar para um arquivo
local existente de credenciais do projeto Firebase. Na conferência inicial,
o arquivo indicado não foi encontrado. Preserve as chaves apenas no `.env` e
nos arquivos privados correspondentes.

Depois de instalar as dependências e corrigir esse caminho:

```powershell
.\.venv\Scripts\python.exe iniciar_piloto.py
```

Abra `http://127.0.0.1:5000`. O iniciador seleciona os dois bancos piloto para
esse processo e desativa o modo de depuração. A orientação usa o Gemini quando
a chave e o modelo estão configurados. Para testar somente a busca local, defina
`GEMINI_ENABLED=false` no terminal antes de iniciar o piloto.
O login usa o Firebase já configurado; operações de usuários e tickets continuam
usando esse Firebase. Apenas os bancos locais de conhecimento são separados.

Para a etapa posterior de respostas com IA, confira `docs/gemini.md` e
`docs/conversa-rag.md`. O `.env` existente já indica geração habilitada, mas
nenhuma chamada real ao provedor faz parte desta preparação. O iniciador piloto
a mantém desativada. Embeddings também precisam de `tokenizers` e dos pesos
baixados por `python -m knowledge_search preparar-modelo`, seguidos de uma
indexação sem `--somente-texto`.
