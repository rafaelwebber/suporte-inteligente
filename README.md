# Plataforma de apoio ao suporte

Todo time de suporte conhece a sensação: chega um ticket novo e alguém lembra "isso já aconteceu antes, mas como foi que a gente resolveu mesmo?". Este projeto nasceu para responder essa pergunta rápido.

A plataforma reúne o histórico para apoiar técnicos em novos atendimentos. O acervo documental local inclui tickets com e sem solução, artigos, interações e casos curados com evidências. A pesquisa de tickets de sucesso continua disponível como um fluxo separado.

## Fluxo original de tickets de sucesso

1. **Os tickets entram na base.** Eles podem ser cadastrados na mão pelo painel ou importados de um CSV exportado de outra ferramenta de atendimento.
2. **A plataforma separa o que deu certo.** Um ticket só entra na base de conhecimento se foi resolvido, tem a solução descrita, nunca foi reaberto e não recebeu nota baixa do cliente (a nota mínima é configurável, o padrão é 4 de 5).
3. **O atendente pergunta.** Na tela "Qual sua dúvida?" ele descreve o problema. O texto é limpo (minúsculas, sem acentos, sem palavras sem significado como "de", "para", "cliente", com plurais reduzidos ao singular) e vira uma lista de palavras-chave.
4. **A plataforma busca e ordena.** Os tickets de sucesso que têm essas palavras são pontuados pela quantidade de termos em comum, com peso extra quando a palavra aparece no título. Em caso de empate, ganha o ticket que mais ajudou outros atendentes.
5. **O time ensina a plataforma.** Em cada sugestão o atendente marca se ajudou ou não. Esses votos ajudam a ordenar as próximas buscas e alimentam as estatísticas de quanto as sugestões estão acertando.

Esse ciclo de sugestão e feedback é a base para evoluir a busca com modelos de machine learning e deep learning: o histórico de perguntas, resultados e votos fica todo registrado e pode ser usado para treinar e avaliar um modelo no futuro.

## Cuidados com os dados

- **Dados pessoais são mascarados** antes de gravar qualquer coisa: e-mails, CPFs, CNPJs, números de cartão e telefones viram `[EMAIL]`, `[CPF]`, `[CNPJ]`, `[CARTAO]` e `[TELEFONE]`.
- **Login protegido contra força bruta**, com bloqueio temporário depois de várias tentativas erradas (por e-mail e IP e também por IP).
- **Dois perfis de acesso**: o atendente vê os tickets da própria equipe e a base de conhecimento, e o administrador gerencia usuários, equipes, importações e estatísticas.
- **Auditoria**: logins, alterações de tickets, usuários e equipes ficam registrados com quem fez, quando e de qual IP.
- O navegador não acessa o banco diretamente; tudo passa pela API.

## Tecnologias

- **Back-end:** Python com Flask
- **Banco e autenticação:** Firebase (Firestore e Firebase Auth)
- **Front-end:** HTML, CSS e JavaScript puro, servidos pelo próprio Flask

## Estrutura

```
api/
  __init__.py        cria o app Flask e registra as rotas
  config.py          configurações lidas do .env
  extensions.py      conexão com o Firebase
  decorators.py      verificação de token e de permissão
  text_utils.py      limpeza de texto, palavras-chave e mascaramento de dados
  routes/            endpoints (auth, users, teams, tickets, suggestions, audit)
  services/          regras de negócio de cada parte
models/
  firestore.rules    regras de segurança e descrição das coleções
index.html           tela de login
app.html, app.js,
style.css            painel do atendente e do administrador
run.py               ponto de entrada
```

## Como rodar

1. Crie um projeto no Firebase com Firestore e autenticação por e-mail e senha ativados.
2. Baixe a chave da conta de serviço (Admin SDK) e salve na raiz do projeto como `firebase-credentials.json`.
3. Crie um arquivo `.env` na raiz:

   ```
   FLASK_SECRET_KEY=uma-chave-secreta
   FLASK_DEBUG=true
   FIREBASE_CREDENTIALS=firebase-credentials.json
   FIREBASE_WEB_API_KEY=chave-web-do-firebase
   ```

   Também dá para ajustar `LOGIN_MAX_ATTEMPTS`, `LOGIN_MAX_ATTEMPTS_PER_IP`, `LOGIN_LOCK_MINUTES`, `PASSWORD_MIN_LENGTH`, `TICKET_SUCCESS_MIN_CSAT`, `SUGGESTIONS_LIMIT` e `MAX_UPLOAD_MB`.

4. Instale as dependências e rode:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python run.py
   ```

5. Abra `http://127.0.0.1:5000` no navegador.

## Importando tickets por CSV

O CSV precisa ter pelo menos as colunas de título e descrição. Os nomes das colunas são reconhecidos em português ou inglês (por exemplo `titulo` ou `title`, `descricao` ou `description`, `solucao`, `status`, `nota`, `equipe`, `categoria`, `produto`), e o separador pode ser vírgula, ponto e vírgula ou tab. Tickets com o mesmo ID externo não são duplicados ao importar de novo.

## Importando o pacote de conhecimento JSON/JSONL

Para preparar novas exportações CSV, consulte [Preparação da base para técnicos internos](docs/preparacao-base.md).
Para avaliar a recuperação dos casos piloto e seus qualificadores, consulte
[Validação do acervo preparado](docs/validacao-acervo.md).
O comando `python -m knowledge_prepare --help` apresenta as entradas. Ele produz
um pacote validado, com relatório de cobertura, sem modificar o banco da aplicação.

O item 3 dispõe de um importador local separado para o pacote normalizado da
`DataSetBase`. Ele valida o catálogo, integridade, registros e vínculos, mantém
histórico e ativa a nova versão somente após a carga completa. A preparação é
armazenada em SQLite. A primeira entrega do item 6 mantém esse armazenamento
local e integra o conteúdo à busca descrita ao final deste documento.

```powershell
py -3 -m knowledge_import validar
py -3 -m knowledge_import importar --relatorio instance\carga-001.json
py -3 -m knowledge_import status
```

Consulte [o guia de importação e atualização](docs/importacao-conhecimento.md) para
o contrato de entrada, reimportação, erros, histórico e testes.

## Organização e dados locais

O projeto mantém `api/`, `models/`, `knowledge_import/`, `tests/`, `docs/`,
o pacote ativo em `DataSetBase/` e o banco local em `instance/`.
Os materiais históricos de preparação foram arquivados fora do repositório.
Consulte [a organização do repositório](docs/organizacao-repositorio.md) para os
caminhos, a finalidade de cada pasta e a recuperação das origens.

## Avaliação de busca e resposta

A avaliação histórica mantém 35 cenários sintéticos de busca e triagem (removida do aplicativo), com resultados
esperados, métricas e relatórios HTML/JSON. A execução é local, sem Firebase e
sem alterar os atendimentos:

```powershell
.\.venv\Scripts\python.exe -m evaluation
```

O terminal informa o relatório criado em `instance/evaluation/`. Na medição
inicial, 33 de 35 cenários passaram; duas buscas por paráfrase não encontraram
as fontes esperadas. O comando retorna `1` quando há falhas de qualidade.
Consulte [o guia de avaliação](docs/avaliacao.md) para metas, revisão dos cenários,
comparação entre execuções e limites da cobertura. Para avaliar a busca local
adicionada no item 6, use também `python -m evaluation.hybrid`.

## Busca híbrida local — item 6, primeira entrega

A integração do acervo importado está disponível no campo principal **Qual sua dúvida?**. Usa SQLite FTS5 e embeddings locais em CPU,
com fontes e avisos de revisão. A pesquisa de tickets existente é separada.
Não requer PostgreSQL ou chave de API para pesquisar. Para habilitar a orientação
por IA, consulte [como usar GPT no piloto](docs/conversa-rag.md#como-usar-gpt-no-piloto)
ou [como configurar o Gemini](docs/gemini.md).

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m knowledge_search preparar-modelo
.\.venv\Scripts\python.exe -m knowledge_search indexar
```

O banco `instance/knowledge.sqlite3` deve existir; caso necessário, execute
`python -m knowledge_import importar` antes de indexar. Somente a preparação do
modelo usa internet, para baixar pesos públicos. Depois, consultas e embeddings
da busca são locais. O login Firebase continua exigindo conexão.

As conversas e os eventos usados pelas estatísticas são persistidos em SQLite,
por padrão em `instance/conversations.sqlite3`. O caminho pode ser definido por
`KNOWLEDGE_CONVERSATION_DATABASE`. A tabela analítica guarda métricas agregadas e
fontes recuperadas, sem armazenar o texto das mensagens.

Consulte [o guia de instalação, atualização e limites](docs/busca-local.md).

## Conversa para esclarecer o problema

O piloto de conversa usa a busca local e fichas de até três tickets para fazer
perguntas quando faltar contexto, refinar a pesquisa e orientar com citações.
Inclui contagem antes da geração, limite de entrada e sessões privadas por usuário.
Veja [como testar a conversa e seus limites](docs/conversa-rag.md).
