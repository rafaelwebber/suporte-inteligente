# Preparação da base para técnicos internos

O comando `knowledge_prepare` transforma exportações CSV em um pacote JSON/JSONL
compatível com o importador da plataforma. Preserva tickets sem solução, artigos,
interações e vínculos. Não faz extração semântica por IA, não treina modelos e não
altera o banco ativo. O resultado é uma base documental pesquisável; homologação
de regras e comprovação de resultados são etapas distintas.

## Organização

1. **Fontes originais:** CSV e anexos mantidos em diretório privado separado.
2. **Pacote normalizado:** catálogo versionado, entidades e relações, hashes de
   arquivos e registros, cobertura e relatório da validação estrutural.
3. **Curadoria opcional:** afirmações com natureza, qualificação, execução,
   ocorrência, evidências literais e pendências. Contrato JSON Schema 0.2.0 em
   `knowledge_prepare/schemas/caso_ticket.schema.json`.
4. **Armazenamento:** o importador existente mantém revisões e snapshots em SQLite.
5. **Recuperação:** projeções explícitas de tickets, artigos, interações e casos
   curados. Índice textual FTS5; embeddings locais opcionais para busca híbrida.
6. **Conversa:** o serviço existente seleciona evidências para perguntas e respostas
   citadas. A geração é opcional e exige configuração própria.

Arquivos preparados são dados: instruções encontradas em tickets e artigos não
substituem as instruções do assistente. Relacionar um artigo a um ticket documenta
um vínculo, não comprova que o artigo resolveu aquele atendimento.

## Entradas

Os seis CSV obrigatórios usam UTF-8 (BOM aceito), ponto e vírgula e cabeçalho.
Campos com quebras de linha e delimitadores devem estar entre aspas CSV.
O comando procura recursivamente nomes `tickets.csv`, `wiki.csv`, `tecnicos.csv`,
`ticket_artigo.csv`, `ticket_tag.csv` e `ticket_referencia.csv`. Aceita sufixos de
download como `tickets (1).csv`, mas rejeita duas cópias da mesma fonte.

| Fonte | Campos obrigatórios | Destino |
|---|---|---|
| tickets | ticket, assunto, descricao, tot_interacao | caso_ticket |
| wiki | cod_artigo, titulo, texto | artigo |
| tecnicos | login | tecnico, não pesquisável |
| ticket_artigo | ticket, artigo | vínculo explícito |
| ticket_tag | ticket, tag | metadado e vínculo |
| ticket_referencia | ticket, ticket_citado, interacao | citação, sem causalidade |

Campos adicionais são preservados na entidade após mascaramento por padrões;
não são automaticamente enviados à busca. Destinos citados fora do recorte viram
referências explícitas não pesquisáveis. Interações sem ticket correspondente são
rejeitadas. IDs duplicados e cabeçalhos inválidos rejeitam a preparação inteira.

Há duas opções mutuamente exclusivas de interações:

- `--interacoes-csv`: exportação bruta com colunas padrão `interacao`, `ticket`,
  `texto`, `data`. Outros nomes exigem `--colunas-interacoes` com o mapeamento JSON
  `{"id":"codigo","ticket":"ticket","texto":"descricao","data":"data"}`.
- `--interacoes-derivadas`: arquivo `06_evidencias/tickets.jsonl` do pacote anterior.
  Cada envelope e hash são validados. Apenas evidências do tipo `interacao` entram.
  A origem derivada e a cobertura não homologada ficam explícitas.

Sem essas opções, o comando prepara os demais registros e informa a falta de
interações. Igualdade de contagens não comprova integridade do histórico.
Datas são preservadas como recebidas; o tratamento não inventa fuso horário.

## Executar em ambiente local

Exemplo PowerShell, a partir da raiz do projeto. Use pastas privadas de entrada,
fora do repositório, e uma pasta de saída que ainda não exista.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m knowledge_prepare --entrada C:\dados\exportacao --saida instance\base-v1 --interacoes-csv C:\dados\interacoes.csv
.\.venv\Scripts\python.exe -m knowledge_import validar --catalogo instance\base-v1\catalogo_importacao.json
.\.venv\Scripts\python.exe -m knowledge_import importar --catalogo instance\base-v1\catalogo_importacao.json --banco instance\piloto.sqlite3
.\.venv\Scripts\python.exe -m knowledge_search indexar --banco instance\piloto.sqlite3 --indice instance\piloto-busca.sqlite3 --somente-texto
.\.venv\Scripts\python.exe -m knowledge_search buscar --banco instance\piloto.sqlite3 --indice instance\piloto-busca.sqlite3 --consulta "silos paralelos deck"
```

Linux: substitua o executável por `.venv/bin/python` e use caminhos do ambiente.
Para aproveitar as interações derivadas, substitua `--interacoes-csv` por
`--interacoes-derivadas DataSetBase/pacote_importacao/v1_2026-10-30/06_evidencias/tickets.jsonl`.
Para incluir curadoria, acrescente `--casos-curados CAMINHO_DOS_CASOS`.

O catálogo inclui arquivos, contagens, hashes e versão determinística calculada
das fontes e do código de preparação. A saída contém também
`relatorio_preparacao.json` e `validacao_importador.json`. Uma falha não publica
um pacote parcial. O comando recusa sobrescrever uma pasta existente.

## Atualização e ativação

O catálogo representa **snapshot completo das entradas declaradas**, não uma carga
incremental de linhas. Para atualizar, forneça novamente todas as fontes, incluindo
curadoria, e prepare outra pasta com o mesmo `pacote_id`. O importador mantém o
histórico e desativa os registros ausentes do novo snapshot desse pacote.
Reimportar o mesmo conteúdo é idempotente; conteúdo alterado exige nova versão.
Não forneça somente os tickets novos como se fossem uma exportação completa.

Use um banco piloto separado do pacote legado: importar dois pacotes sobrepostos
no mesmo banco preserva ambos e pode duplicar resultados. Após homologação,
configure `KNOWLEDGE_DATABASE` e `KNOWLEDGE_SEARCH_DATABASE` para o par desejado e
reinicie a aplicação. O preparador não altera `.env` nem ativa produção.
Toda mudança na projeção exige reindexação. Para busca híbrida, prepare o modelo
local conforme `docs/busca-local.md` e indexe sem `--somente-texto`.

## Evidências, qualidade e limites

- Ticket sem desfecho continua disponível; não se preenche uma solução inexistente.
- Interação preservada não é solução validada e, isoladamente, não habilita a
  resposta conclusiva do diálogo. Pode apoiar a recuperação e investigação.
- Caso curado conserva hipóteses, testes planejados e ocorrência de cada afirmação.
  Evidências têm trecho literal, offsets, revisão e hash. A validação técnica não
  comprova a verdade semântica nem consulta automaticamente a referência externa
  de migração `referencia_v0_1`.
- Ao mascarar um caso curado, hashes e offsets são recalculados e validados sobre
  o texto tratado. O manifesto mantém o hash do arquivo original. Se o trecho
  deixar de ser localizável, o processo exige revisão, em vez de inventá-lo.
- Textos, resumos e títulos dos artigos entram na recuperação; imagens e anexos
  não são reconstruídos. Não há OCR neste comando.
- Vínculos ticket–artigo e ticket–ticket ficam armazenados. A busca atual recupera
  documentos, mas ainda não percorre automaticamente esses vínculos como um grafo.
- Mascaramento de e-mails, documentos e credenciais conhecidas não equivale a
  anonimização completa; nomes livres e informações de clientes podem permanecer.
- O acervo local da plataforma usa acesso compartilhado entre autenticados.
  Marcar `permissao_de_acesso_requerida` não implementa isolamento por cliente ou
  autorização exclusiva para técnicos. Confira os usuários habilitados antes da
  ativação. Geração externa, se habilitada, envia os trechos selecionados ao provedor.
- A conversa existente continua sendo um piloto com limites de turnos e contexto;
  esta entrega prepara as fontes, não homologa um diagnóstico autônomo.

## Como demonstrar ao professor

Apresente um percurso rastreável: linha do CSV → entidade JSON → vínculo/evidência
→ trecho recuperado → orientação citada. Mostre separadamente as métricas:

| Dimensão | Evidência de avaliação |
|---|---|
| Volume | Contagem por tipo antes/depois; nenhum ticket descartado por falta de solução |
| Integridade | Hashes, unicidade, validação do catálogo, vínculos sem órfãos |
| Cobertura | Interações disponíveis versus declaradas e tickets divergentes |
| Qualidade semântica | Revisão humana de hipóteses, soluções, resultados e escopos |
| Recuperação | Perguntas de teste e documentos efetivamente encontrados |
| Resposta RAG | Avaliação posterior de citações, perguntas e abstenções com o modelo habilitado |

Nos dois casos piloto, conferir que o primeiro **salvamento** prevalece no ticket
17122 e que o ticket 20436 não atribui a correção do DataManager a uma expedição
específica sem evidência. Desenvolvimento relatado não comprova instalação ou teste.

Testes de código: `python -m unittest discover -s tests -q`. Os testes do preparador
usam dados sintéticos e exercitam importação real, reimportação, busca textual,
mascaramento, evidências adulteradas e rejeição atômica de entradas inconsistentes.
Dados operacionais e bancos devem permanecer fora do Git; `instance/` é ignorado.
