# Validação do acervo preparado

O comando abaixo consulta o banco e o índice reais, sem alterá-los, e seleciona
as mesmas evidências utilizadas pela conversa da plataforma:

```sh
python -m evaluation.acervo --banco instance/preparacao-piloto.sqlite3 --indice instance/busca-piloto.sqlite3 --saida instance/avaliacao-acervo-001
```

A saída deve ser uma pasta nova. Contém HTML, JSON com evidências e hashes do
código, e uma planilha de revisão humana. Código de saída 0 significa que os oito
cenários atenderam às verificações; 1 indica falha de qualidade. Falhas de execução
não são contabilizadas como aprovação ou abstenção.

São oito casos de desenvolvimento baseados nos tickets 17122 e 20436, incluindo
precedência do primeiro salvamento, testes planejados, decks, hipótese por
expedição, cronologia e implantação não documentada. As verificações exigem o
documento esperado entre os cinco primeiros resultados, sua presença entre as
três evidências e os qualificadores esperados no trecho desse documento.

Esses rótulos não são exaustivos nem independentes do desenvolvimento. Uma falha
significa que a evidência alvo não passou no contrato, não que todas as demais
fontes recuperadas sejam irrelevantes. Não se calcula precisão geral usando
apenas essas referências. A aprovação não mede uma resposta de IA nem substitui
revisão humana ou um conjunto separado de atendimentos para homologação.

## Referências explícitas

Consultas com `Ticket #17122`, `Expedição 4798309` ou `Produção 57922` restringem
os candidatos à referência informada. O filtro é aplicado antes do limite da
busca textual e também aos candidatos vetoriais. Números de versão e datas não
ativam esse filtro. Em uma consulta com várias referências explícitas, basta
corresponder a uma delas; isso não prova relação causal entre elas.

Para ticket, o título projetado precisa identificar o ticket. Para expedição ou
produção, o número precisa ocorrer no trecho. Esse reconhecimento é lexical:
o mesmo número em outro contexto ainda requer conferência. Perguntas genéricas
continuam usando a ordenação normal. Uma pergunta sobre referência inexistente
não é respondida com outro ticket apenas porque contém palavras semelhantes.

No caso curado, a seleção conserva o bloco completo da afirmação, com natureza,
qualificação, execução, escopo e limites. Referências de ocorrências também
restringem o bloco selecionado. Uma pergunta sobre teste favorece afirmações cuja
natureza é teste; não converte um plano de teste em resultado realizado.

## Busca híbrida e geração

`python -m evaluation.hybrid --saida instance/avaliacao-hibrida-001` testa o
modelo de embeddings real no corpus sintético existente. Não é avaliação de
todos os documentos do acervo. O modelo é obtido por
`python -m knowledge_search preparar-modelo`, com revisão e hashes fixados.
Somente pesos públicos são baixados; os textos são vetorizados localmente.

Na rodada de desenvolvimento de 21/11/2026, a suíte sintética teve 13/15 cenários
atendidos, Recall@5 de 83,3% nas 12 consultas positivas e abstenção correta nas
três negativas. A busca híbrida não melhorou a textual nessa configuração. As
paráfrases B13 e B14 continuaram falhando; a meta de Recall@5 ≥ 90% não foi
atingida. O limiar padrão não foi reduzido para aprovar a suíte. É necessário
calibrar com exemplos de desenvolvimento e validar em um conjunto separado.

A geração não foi executada: o provedor está desabilitado neste ambiente. Não se
deve apresentar evidência recuperada como se fosse resposta produzida pelo modelo.
Após configurar o provedor na plataforma, avaliar também perguntas direcionadas,
fidelidade das citações, escopo por ocorrência e abstenção por falta de informação.
