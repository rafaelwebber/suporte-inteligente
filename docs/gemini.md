# Respostas com Gemini — item 6

A integração usa a busca híbrida local para recuperar evidências e envia um recorte
ao Gemini para redigir a resposta. Os embeddings e os bancos continuam locais.
Não é necessário reindexar o acervo nem instalar novas dependências para ativá-la.

## Configurar no computador que hospeda a aplicação

Abra o `.env` já existente na raiz do projeto, preservando Firebase e demais
configurações. A chave deve ficar somente nesse servidor, nunca no chat, no
JavaScript, em um relatório ou no Git. Acrescente:

```dotenv
GEMINI_API_KEY=coloque_a_chave_aqui_no_servidor
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_ENABLED=false
```

O modelo é configurável pelo seu identificador na API. O exemplo usa um modelo
listado na [documentação de modelos do Google](https://ai.google.dev/gemini-api/docs/models).
Disponibilidade e cotas dependem do projeto da chave; a integração não garante uso
gratuito. Confira [preços](https://ai.google.dev/gemini-api/docs/pricing) e
[limites de uso](https://ai.google.dev/gemini-api/docs/rate-limits) na sua conta.

Quando o conteúdo estiver autorizado para envio ao Google, altere
`GEMINI_ENABLED=true`, reinicie a aplicação e atualize o navegador com Ctrl+F5.
A presença da chave sozinha não ativa chamadas externas.

## Usar

1. No painel, escreva no campo principal **Qual sua dúvida?** e envie a pesquisa. A triagem também abre esse campo com seu contexto.
2. O servidor pesquisa primeiro no acervo local. Quando o Gemini está ativado, o assistente usa os resultados para pedir esclarecimentos ou elaborar uma orientação com citações.
3. Se o Gemini estiver desativado ou falhar, os resultados locais continuam visíveis nos cards da tela.
4. Use os cards e as evidências da resposta para abrir os registros originais.

O primeiro teste pode ser uma consulta técnica sem informações de cliente.
Quando a integração está ativada e configurada, o envio pode chamar o Gemini.
Se houver falha, limite de uso ou resposta inválida, as fontes continuam disponíveis.
Uma resposta insuficientemente fundamentada pode resultar em abstenção.

## Conteúdo enviado

Cada solicitação envia a pergunta efetiva, de até 2.000 caracteres, e até cinco
trechos técnicos, com no máximo 1.000 caracteres cada. Na triagem, a pergunta
efetiva inclui relato, complemento, versão e alarme, conforme a busca local.
São enviados também os indicadores de revisão/uso desses trechos e instruções
fixas para a redação. Os rótulos F1–F5 são gerados para aquela solicitação.

Não são enviados o banco completo, vetores, anexos, objetos de sessão, campos
administrativos de cliente/autor, títulos, identificadores internos de registros,
caminhos de origem ou tokens Firebase. A projeção e o mascaramento já existentes
removem padrões conhecidos, mas **não garantem anonimização do texto livre**.
Nomes de empresas ou informações específicas podem estar nos relatos e trechos;
revise a autorização do acervo antes de habilitar. As políticas aplicáveis ao
tratamento pelo provedor precisam ser avaliadas para a conta usada.

## Funcionamento e limites

- `POST /knowledge/search` permanece local. `POST /knowledge/answer` exige a mesma
  autenticação e aceita somente `query`, `limit` e `session_id` opcional.
- A rota de resposta recupera as fontes novamente do índice ativo. Não aceita
  trechos fornecidos pelo navegador e verifica o dono da sessão de triagem.
- A chave é enviada ao endpoint fixo do Google por header. Não há redirecionamento,
  ferramentas, navegação web, execução de código ou tentativas automáticas.
- Limites: dois pedidos em andamento por processo, conexão de até cinco segundos,
  espera de leitura de até 45 segundos, 4.096 tokens de saída e 128 KiB de resposta.
  O limite simultâneo não é uma cota diária ou financeira; vários processos
  possuem limites independentes. Configure as cotas no projeto Google.
- A aplicação pede JSON estruturado. Rejeita saídas incompletas, bloqueadas,
  referências desconhecidas, parágrafos sem citações e citações que não existam
  literalmente nos trechos enviados. A verificação não comprova que uma conclusão
  é sustentada pela evidência; a revisão técnica continua necessária.
- O texto retornado é exibido como texto escapado. A aplicação não registra a
  chave, prompts, respostas ou corpo de erros do provedor em logs nem persiste
  as respostas geradas. A página mantém a resposta somente enquanto estiver aberta.
- Sem fontes, chave, modelo válido ou ativação, não ocorre chamada externa.

Contrato: `generated_answer` contém parágrafos com citações conferidas quando
`generation_status=generated`; nos demais estados é `null`, acompanhado de aviso.
`generation_available` informa se a configuração permite solicitar uma resposta.

Referências de implementação: [generateContent](https://ai.google.dev/api/generate-content)
e [saídas estruturadas](https://ai.google.dev/gemini-api/docs/structured-output).

## Verificação desta entrega

Os 96 testes automatizados do projeto passaram. O fluxo da interface foi exercitado
no Edge em larguras de computador e celular: pesquisa sem chamada ao Gemini,
solicitação explícita de resposta, leitura das citações, abertura da fonte,
exibição segura de texto retornado e preservação das fontes ao atingir a cota.
Não foram encontrados erros JavaScript nesse fluxo.

Os testes automatizados usam respostas simuladas do provedor para verificar
sucesso, permissões, projeção do conteúdo, erros HTTP, tempo excedido, cotas,
respostas bloqueadas/incompletas, abstenção e referências inválidas. Nenhuma chave
real foi usada e nenhum conteúdo real foi enviado ao Google nesta verificação.

A avaliação local de recuperação continua separada (`python -m evaluation.hybrid`).
Ela não valida a qualidade da redação do Gemini. A homologação com o modelo e a
chave do ambiente de destino, incluindo pertinência das respostas e comportamento
diante de instruções maliciosas inseridas em documentos, permanece necessária.

Para desativar, use `GEMINI_ENABLED=false` e reinicie o servidor. A pesquisa local
continua funcionando.
