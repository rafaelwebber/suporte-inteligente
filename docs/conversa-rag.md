# Conversa de esclarecimento — piloto

Na página principal ou em `acervo.html`, descreva o problema e envie ao
assistente. O servidor pesquisa no acervo local e usa o provedor de IA
configurado para comparar os candidatos e investigar até quatro deles.
O assistente pode perguntar, orientar com citações ou declarar evidência
insuficiente. Cada resposta do usuário refaz a busca com o contexto.
Para recuperar os candidatos, usa somente o conteúdo informado pelo usuário:
as legendas do histórico (“pergunta”, “resposta do usuário”) não entram na busca.
Pedidos como “mostre opções” e “quais situações parecidas existem nos tickets”
também são retirados. Sem assunto pesquisável, o assistente pede contexto sem
executar uma busca com essas instruções. Componentes reconhecidos no relato
inicial restringem as fichas ao assunto; propostas/orçamentos não entram como
sintomas, exceto quando o pedido trata explicitamente desse tipo de solicitação.
Respostas sim/não usam também a pergunta correspondente. Neste acervo industrial,
“Unity” é expandido para “Unit”, preservando o relato original da sessão.

Exemplo: “Problema de comunicação com PLC”. Se os casos recuperados diferem
entre configuração inicial e alteração de versão, o assistente pode perguntar
se a comunicação nunca funcionou ou parou após uma mudança. A pergunta é
gerada a partir das evidências e não representa diagnóstico confirmado.

## Como testar

1. Execute `python run.py` e entre com um usuário do Firebase.
2. Atualize a página com Ctrl+F5 para carregar o JavaScript novo.
3. Descreva um problema geral e responda à pergunta quando houver.
   Quando existirem alternativas apoiadas nas fontes, elas aparecem como botões.
   Escolher uma envia a resposta e refaz a busca. “Nenhuma dessas / escrever outra
   resposta” permite descrever um caso diferente sem enviar automaticamente.
4. A conversa mostra a orientação, a conferência sugerida ou a pergunta de
   esclarecimento. Abra “Fontes e casos relacionados” para consultar os resultados
   do acervo. As fontes citadas também ficam acessíveis junto à resposta.
   Continue na mesma investigação para informar o resultado de uma verificação.
5. Use “Nova investigação” para mudar de problema. O envio sempre pesquisa o
   acervo local e também solicita orientação quando o provedor estiver disponível.

## Como usar GPT no piloto

Obtenha uma chave da API da OpenAI no projeto que fará o piloto. Preencha
estas variáveis no `.env` do servidor:

```dotenv
KNOWLEDGE_LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6.2-sol
```

Reinicie com `python iniciar_piloto.py` e abra uma nova investigação. A seção
“Informações da busca” identifica o provedor. Para voltar ao Gemini, selecione
`KNOWLEDGE_LLM_PROVIDER=gemini` e use a configuração descrita em [Gemini](gemini.md).
O envio pode fazer chamadas externas; o aviso aparece na tela.
Sem ativação ou se houver falha, os tickets locais permanecem disponíveis e um
aviso explica o ocorrido. O piloto não executa procedimentos nem altera tickets.

## Classificar uma amostra de tickets

Na conversa, a aplicação prepara fichas automáticas apenas para os tickets
aprofundados que ainda não têm ficha para a versão atual do histórico. Envia
uma amostra de trechos ao provedor configurado, exige citações literais e mantém
as fichas no banco SQLite de conversas. Mudanças nas fontes ou no modelo fazem
a aplicação gerar outra ficha na próxima consulta ao ticket.

As fichas distinguem hipóteses, ações planejadas e ações executadas. Um resultado
só entra quando está vinculado a uma ação do mesmo atendimento. Se a amostra não
trouxer essa evidência, o resultado fica como não confirmado nos trechos. A
classificação fica pendente de revisão. Se o provedor falhar nessa etapa, a
conversa pode continuar com os trechos recuperados diretamente do acervo.

O detalhe do ticket apresenta os campos documentados, as interações disponíveis
e a cobertura do histórico. Registros técnicos curados exibem suas afirmações,
qualificações e trechos de origem. As interações brutas continuam pesquisáveis
como contexto. A conversa exige ao menos uma citação técnica de apoio por
parágrafo de orientação. Um teste planejado ou uma hipótese curada pode ajudar
a esclarecer o relato.

Para testar a classificação em lote pequeno, use os bancos do piloto no
PowerShell:

```powershell
$env:KNOWLEDGE_DATABASE = 'instance/piloto.sqlite3'
$env:KNOWLEDGE_SEARCH_DATABASE = 'instance/piloto-busca.sqlite3'
.\.venv\Scripts\python.exe -m knowledge_search.classify --tickets 10404 17122 20436 --saida instance/amostra-rag.json
# Revise os trechos no arquivo e retire informações que não podem ir ao serviço externo.
.\.venv\Scripts\python.exe -m knowledge_search.classify --entrada instance/amostra-rag.json --saida instance/classificacao-rag.json
```

A classificação usa o Gemini configurado no servidor. O arquivo de saída
registra a categoria, a referência da fonte e o trecho literal validado pelo
código. Categorias ausentes ficam em `not_identified`, considerando apenas o
histórico disponível na amostra. Os achados ficam com revisão pendente e podem
ser confrontados com os casos já curados. A ferramenta grava o relatório em
arquivo separado para revisão técnica.

## Contexto e consumo

- Fichas são extraídas sob demanda dos documentos já indexados: problema,
  diagnóstico, solução documentada/candidata, resultado, versão e condições.
  Campos ausentes não são inventados. Não há resumo por IA na importação e
  não é necessário reindexar. Campos longos têm cortes identificados.
- O contexto da sessão preserva o relato inicial, pares de pergunta/resposta
  e, quando disponível, um trecho da orientação anterior. Detalhes longos podem
  ficar fora do contexto enviado, que tem até 2.000 caracteres.
- A consulta textual evita usar ausências explícitas como termos positivos. A
  IA recebe a conversa com as negações e correções do usuário para avaliar quais
  condições ainda se aplicam.
- A triagem prepara até quatro fichas e pode aprofundar até duas interações
  por ticket selecionado. Títulos com correspondência forte com o relato
  têm prioridade para leitura. O mascaramento por padrões não substitui revisão
  de confidencialidade do texto livre.
- A seleção local examina até vinte candidatos e prioriza a relação com o assunto,
  sintomas concretos e diversidade. Alternativas genéricas que só repetem o relato
  são descartadas. Se a IA se abstiver ou fizer uma pergunta aberta e as fichas
  contiverem alternativas concretas, a interface pode usar opções extraídas dessas
  descrições, sem chamar o provedor novamente e respeitando o limite de perguntas.
- A resposta final exige pelo menos uma citação de diagnóstico, ação ou resultado
  documentado na ficha. Citar apenas o relato de um problema ou uma solicitação
  de serviço não libera uma orientação final. Essa verificação estrutural não
  substitui a avaliação semântica nem a revisão técnica da orientação.
- Antes de gerar, a API de contagem estima os tokens do pedido. Não há um teto
  interno fixo de entrada; pedidos acima do contexto aceito pelo provedor podem
  ser rejeitados pelo próprio provedor. A saída tem limite de 1.800 tokens,
  incluindo o JSON. A tela informa a contagem de entrada e, quando retornado,
  o total da geração. Esses números não são valores monetários nem cotas diárias.
- Cada envio com candidatos pode fazer uma contagem e uma geração. A busca e
  as fichas são locais. Sem candidatos, a primeira pergunta básica é local.
  Se o Gemini se abstiver no primeiro turno, o servidor também faz uma pergunta
  básica sobre o sintoma, sem uma chamada adicional, em vez de encerrar o relato.
  Se houver sintomas distintos nas fichas, essa pergunta local também pode oferecer
  alternativas extraídas desses sintomas. Não são geradas alternativas sem fontes.
- São permitidas até três perguntas de esclarecimento; após isso, o modelo
  precisa orientar com evidências ou declarar insuficiência. Limite de seis
  mensagens do usuário por investigação. Não há promessa de diagnóstico.

## Sessões e API

`POST /knowledge/conversation` exige autenticação e aceita somente:

```json
{
  "message": "Parou depois de atualizar o supervisório",
  "request_id": "UUID novo por mensagem",
  "session_id": "UUID retornado na primeira resposta; omitir ao começar"
}
```

O retorno preserva os resultados da busca e acrescenta `session_id`, `turn`,
`messages`, `decision`, `follow_up`, `generated_answer`, `generation_status` e
`usage`. Na conversa, o Gemini recebe um catálogo de trechos identificados
(`F1:E1`, etc.) e seleciona esses IDs. O servidor monta as citações com o texto
original, dispensando a cópia literal pelo modelo. IDs inexistentes são rejeitados.
O texto das fichas não é duplicado no pedido. A rota antiga `/knowledge/answer`
continua conferindo as citações literais retornadas pelo modelo.
Conferir a presença da citação não comprova que a conclusão é sustentada.

Sessões ficam no SQLite `instance/conversations.sqlite3`, isoladas pelo UID do
Firebase, com bloqueio de processamento simultâneo. Repetir a última mensagem
com o mesmo `request_id` retorna a resposta armazenada sem gerar outra vez.
Para integração externa, preservar esse ID em tentativas após falha de rede.
Falhas técnicas aparecem separadas de evidência insuficiente. O botão “Tentar
novamente” faz uma nova chamada somente ao ser acionado. Repetir a mesma mensagem
após falha técnica mantém o turno original, sem acrescentar uma resposta fictícia
do usuário ou gastar outro dos seis turnos. Uma falha técnica não libera uma
resposta inválida nem relaxa a verificação de referências.

Registros inativos por 24 horas são removidos na próxima operação. A sessão
atual fica apenas na memória da página; atualizar/fechar a página inicia outra
investigação. “Nova investigação” limpa a tela, sem apagar imediatamente o
registro local. As rotas antigas de busca e resposta continuam disponíveis.

## Verificação

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check acervo.js
node tests/test_conversation_ui.cjs
```

Os testes de conversa verificam isolamento, repetição idempotente, falhas do
provedor, orçamento, perguntas repetidas, recuperação de solução no final de
documentos longos e preservação das referências. As respostas do provedor nos
testes automatizados são simuladas; homologação da utilidade exige casos reais.

Também foi exercitado o fluxo completo da API com o Gemini real, autenticação
simulada e dois tickets fictícios: a primeira mensagem gerou esclarecimento
(712 tokens de entrada; 760 no total); a resposta sobre atualização gerou
orientação citando o ticket compatível (772 de entrada; 899 no total). Nenhum
conteúdo real do acervo foi enviado nesse teste. Esses valores são exemplos,
não estimativas para todos os atendimentos. O controlador da tela foi testado
em Node; a inspeção visual em navegador ficou indisponível no ambiente.
