# Plataforma de apoio ao suporte

Todo time de suporte conhece a sensação: chega um ticket novo e alguém lembra "isso já aconteceu antes, mas como foi que a gente resolveu mesmo?". Este projeto nasceu para responder essa pergunta rápido.

A ideia é simples: cada ticket resolvido com sucesso vira conhecimento. Quando um atendente recebe um problema novo, ele descreve o que o cliente relatou e a plataforma devolve os tickets parecidos que já foram resolvidos, junto com a solução que funcionou. Assim o time não precisa reinventar a resposta toda vez e o cliente é atendido mais rápido.

## Como funciona

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
