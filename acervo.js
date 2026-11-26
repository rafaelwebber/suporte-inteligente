/* Reutiliza a autenticação e o cliente HTTP existentes. Nenhuma chave de IA no navegador. */
function initKnowledgeSearch({
  form = $("#knowledge-form"), input = $("#knowledge-query"),
  results = $("#knowledge-results"), submit = $("#knowledge-submit"),
  beforeSearch = () => {},
  onReset = () => {},
  onConversationSaved = () => {},
} = {}) {
  if (!session.token) { location.replace("index.html"); return; }
  const error = $("#knowledge-error");
  let busy = false, conversationId = null, pendingRequest = null, conversationClosed = false;
  let currentOptions = [];
  let retryMessage = "";
  const technicalFailures = {
    rejected: "Não foi possível validar a orientação.",
    unavailable: "O serviço de IA está indisponível.",
    provider_busy: "O serviço de IA está temporariamente indisponível (HTTP 503).",
    rate_limited: "O serviço de IA atingiu o limite de uso.",
    busy: "O assistente está atendendo outras consultas.",
    disabled: "O assistente está desativado.",
    not_configured: "O assistente não está configurado.",
  };
  const transcript = $("#knowledge-conversation"), reset = $("#knowledge-reset");
  const chatView = form.closest?.(".stage, .knowledge-main");
  const initialPlaceholder = input.placeholder;
  const thinking = '<p class="knowledge-thinking" role="status" aria-atomic="true">'
    + '<span data-wait-label>Consultando o acervo</span>'
    + '<span class="knowledge-thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span></p>';
  const retryButton = '<button class="ghost knowledge-retry" type="button" data-retry>Tentar novamente</button>';
  function controls(locked) {
    busy = locked;
    submit.disabled = locked;
    if (reset) reset.disabled = locked;
    input.disabled = locked;
    transcript?.querySelectorAll?.("[data-option], [data-other], [data-retry]").forEach(button => { button.disabled = locked; });
  }
  function resetConversation() {
    if (busy) return;
    conversationId = null; pendingRequest = null; conversationClosed = false;
    currentOptions = [];
    retryMessage = "";
    if (transcript) transcript.innerHTML = "";
    chatView?.classList.remove("has-conversation");
    results.innerHTML = ""; error.textContent = ""; input.value = "";
    input.placeholder = initialPlaceholder; input.dispatchEvent(new Event("input")); input.focus();
    onReset();
  }

  function loadConversation(data) {
    if (busy) throw new Error("Aguarde a consulta em andamento antes de abrir outra investigação.");
    conversationId = data.session_id;
    pendingRequest = null;
    currentOptions = data.decision === "ask" ? (data.clarification_options || []) : [];
    const retryable = ["rejected", "unavailable", "provider_busy", "rate_limited", "busy"].includes(data.generation_status);
    const lastMessage = data.messages?.at(-1)?.message || "";
    retryMessage = retryable ? lastMessage : "";
    conversationClosed = !retryable && Number(data.turn || 0) >= 6;
    const choices = currentOptions.length ? `<ol class="knowledge-options" aria-label="Escolha uma alternativa">${currentOptions.map((option, index) => `<li><button type="button" data-option="${index}"><span class="knowledge-option-number" aria-hidden="true">${index + 1}.</span><span>${esc(option.label)}</span></button></li>`).join("")}</ol><button class="knowledge-other" type="button" data-other>Nenhuma dessas — vou descrever</button>` : "";
    const localFallback = !data.generated_answer && data.decision !== "ask" && Boolean(technicalFailures[data.generation_status]);
    const response = data.generated_answer
      ? (data.local_evidence ? "<p>Estes são os pontos que o acervo permite comparar até aqui. A causa ainda precisa ser confirmada.</p>" : "")
        + answerContent(data.generated_answer, data.results || [])
        + (data.follow_up && !conversationClosed ? `<p class="knowledge-follow-up">${esc(data.follow_up)}</p>` : "")
      : `<p>${esc(localFallback && data.results?.length
          ? "Encontrei atendimentos relacionados no acervo; eles aparecem abaixo."
          : technicalFailures[data.generation_status] || data.follow_up || "Ainda não há informações suficientes para orientar esta investigação.")}</p>`;
    if (transcript) transcript.innerHTML = (data.messages || []).map(turn =>
      (turn.prior_answer ? assistantMessage(answerContent(turn.prior_answer, data.results || [])) : "")
      + (turn.asked ? assistantMessage(`<p>${esc(turn.asked)}</p>`) : "") + userMessage(turn.message)).join("")
      + assistantMessage(response + choices + (retryable ? retryButton : ""));
    input.value = "";
    input.placeholder = data.follow_up && !conversationClosed ? "Responda à pergunta do assistente…"
      : data.generated_answer && !conversationClosed ? "Continue a investigação…" : initialPlaceholder;
    input.dispatchEvent(new Event("input"));
    chatView?.classList.add("has-conversation");
    results.hidden = false;
    results.innerHTML = "";
    error.textContent = "";
    render(data);
    transcript?.firstElementChild?.scrollIntoView?.({ block: "start" });
  }
  if (reset) reset.onclick = resetConversation;
  const usages = {
    hipotese_para_investigacao: "Hipótese para investigação",
    contexto_historico: "Contexto histórico",
    acao_historica_condicionada: "Ação histórica — conferir condições",
    resultado_historico_condicionado: "Resultado histórico — conferir condições",
  };
  const evidenceLabels = {
    acao_com_resultado: "Ação com resultado registrado",
    acao_registrada: "Ação registrada · resultado não confirmado",
    solucao_documentada: "Solução documentada",
    solucao_candidata: "Solução candidata · requer revisão",
    evidencia_parcial: "Evidência técnica parcial",
    caso_em_revisao: "Caso técnico em revisão",
    sem_solucao_identificada: "Solução não identificada no resumo",
    interacao_nao_classificada: "Interação ainda não classificada",
    documentacao: "Documentação técnica",
  };
  const historyOnly = new Set(["sem_solucao_identificada", "interacao_nao_classificada", "caso_em_revisao"]);
  const claimLabels = {
    observacao: "Observação", analise: "Análise", acao: "Ação", correcao: "Correção", resultado: "Resultado",
    regra: "Regra", teste: "Teste", esclarecimento: "Esclarecimento", relatado: "Relatado",
    hipotese: "Hipótese", especificado: "Especificado", observado_na_imagem: "Observado na imagem",
    confirmado_pelo_usuario: "Confirmado pelo usuário", planejada: "Planejado",
    realizada_conforme_relato: "Executado conforme relato", pendente: "Revisão pendente",
    esclarecido_pelo_usuario: "Esclarecido pelo usuário", revisado: "Revisado",
  };
  const policy = (metadata) => `<p class="knowledge-policy">${esc(usages[metadata.uso_na_recomendacao] || "Conteúdo sujeito a revisão técnica")}. A pertinência para esta ocorrência precisa ser verificada.</p>`;
  const reference = (item) => `<p class="knowledge-reference">Fonte: ${esc(item.source_id)}<br>Pacote: ${esc(item.package_id)}<br>Registro: ${esc(item.origin.registro_id || item.source_id)}<br>Arquivo de origem: ${esc(item.origin.arquivo_relativo || "não informado")}</p>`;
  function documentDetails(doc) {
    const data = doc.ticket_details;
    const origin = `<details class="knowledge-document-origin"><summary>Dados da fonte</summary>${reference(doc)}</details>`;
    if (!data) return policy(doc.metadata) + `<pre>${esc(doc.body)}</pre>` + origin;
    const fields = [
      ["Problema relatado", data.problem], ["Diagnóstico documentado", data.diagnosis],
      ["Ação documentada", data.action], ["Resultado observado", data.outcome],
      ["Contexto técnico", data.context.join("\n")],
    ];
    if (data.candidate) fields.push(["Solução candidata", data.candidate]);
    const summary = data.claims || doc.metadata.tipo_registro === "interacao" ? "" :
      `<dl class="knowledge-document-fields">${fields.map(([label, value]) =>
        `<div><dt>${label}</dt><dd>${esc(value || "Não registrado em campo próprio")}</dd></div>`).join("")}</dl>`;
    const context = data.claims && data.context.length ? `<h3>Contexto técnico</h3><p>${esc(data.context.join("\n"))}</p>` : "";
    const claims = data.claims?.map(item => {
      const qualifiers = [item.kind, item.qualification, item.execution, item.review].map(key => claimLabels[key]).filter(Boolean);
      return `<article class="knowledge-history-item"><h4>${esc(qualifiers.join(" · "))}</h4><p>${esc(item.statement)}</p>
        <details><summary>Evidências de origem (${item.evidence.length})</summary>${item.evidence.map(e =>
          `<blockquote><p>${esc(e.quote)}</p><footer>${esc(e.source)} · ${esc(e.reference)}</footer></blockquote>`).join("")}</details></article>`;
    }).join("");
    const history = data.interactions || [];
    const coverage = data.available_interactions == null ? "" :
      `<p>${esc(data.available_interactions)} no acervo · ${esc(data.declared_interactions ?? "quantidade não informada")} declaradas${data.derived_history ? " · fonte derivada" : ""}</p>`;
    const historyItems = history.map(item =>
      `<article class="knowledge-history-item"><h4>${esc(item.reference)} · ${esc(item.date || "Data não informada")}</h4><p>${esc(item.text)}</p></article>`).join("");
    return `<p class="knowledge-document-status">${esc(evidenceLabels[data.evidence_status])}</p>${policy(doc.metadata)}
      ${summary}${context}${claims ? `<h3>Registros técnicos e evidências</h3>${claims}` : ""}
      ${doc.metadata.tipo_registro === "interacao" ? `<pre>${esc(doc.body)}</pre>` : ""}
      ${coverage || historyItems ? `<details class="knowledge-document-history"><summary>Histórico no acervo (${history.length} exibidas)</summary>
        ${coverage}<p>Interações históricas sem classificação de diagnóstico, ação ou resultado.</p>${historyItems}</details>` : ""}
      ${origin}`;
  }
  if ($("#knowledge-logout")) $("#knowledge-logout").onclick = logout;
  $("#knowledge-close").onclick = () => $("#knowledge-document").close();
  const userMessage = text => `<article class="knowledge-message user" aria-label="Sua mensagem"><p>${esc(text)}</p></article>`;
  const assistantMessage = content => `<article class="knowledge-message assistant" aria-label="Resposta do assistente">${content}</article>`;
  const ticketKey = item => `${item.package_id || ""}:${item.ticket_reference || item.document_key}`;
  const sourceLabel = source => {
    const ref = String(source.source_id || "");
    return ref.startsWith("interacao:") ? `Interação #${ref.slice(10)}` : (ref || "Fonte no acervo");
  };
  const sourceButton = (key, label) => `<button class="ghost knowledge-source-link" type="button" data-document="${esc(key)}">${esc(label)}</button>`;
  const uniqueSources = citations => [...new Map(citations.map(citation => [citation.document_key, citation])).values()];

  function answerContent(answer, cards = []) {
    const byKey = new Map(cards.map(card => [card.document_key, card]));
    const byTicket = new Map(cards.map(card => [ticketKey(card), card]));
    const cardFor = citation => byTicket.get(ticketKey(citation)) || byKey.get(citation.document_key);
    if (answer.paragraphs.every(p => p.finding)) {
      return `<div class="knowledge-findings">${answer.paragraphs.map(paragraph => {
        const { ticket, fields, missing } = paragraph.finding;
        const sources = uniqueSources(paragraph.citations);
        const citationKeys = new Set(sources.map(source => source.document_key));
        const ticketCard = paragraph.citations.map(cardFor).find(card => card && !citationKeys.has(card.document_key));
        const status = fields.action
          ? (fields.outcome ? "Ação e resultado registrados no histórico; efetividade no caso atual não confirmada."
            : "Ação registrada; resultado não confirmado nos trechos consultados.")
          : "Sem ação corretiva registrada nos trechos consultados.";
        const details = [
          ["Sintoma relatado", fields.problem || "Não registrado nos trechos consultados."],
          ["Contexto técnico", fields.context || "Não registrado nos trechos consultados."],
          ["Diagnóstico mencionado", fields.diagnosis || "Não identificado nos trechos consultados."],
          ["Ação executada", fields.action || "Nenhuma ação corretiva identificada nos trechos consultados."],
          ["Resultado observado", fields.outcome || "Não confirmado nos trechos consultados."],
          ["O que falta confirmar", missing || "Não especificado no resumo do atendimento."],
        ];
        return `<article class="knowledge-finding">
          <div class="knowledge-finding-head"><h3>Ticket #${esc(ticket)}</h3>
            ${ticketCard ? sourceButton(ticketCard.document_key, "Ver atendimento") : ""}</div>
          <p class="knowledge-finding-status">${status}</p>
          <dl class="knowledge-finding-fields">${details.map(([label, value]) =>
            `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
          <div class="knowledge-finding-footer">${sources.map(source => sourceButton(source.document_key, sourceLabel(source))).join("")}</div>
        </article>`;
      }).join("")}</div>`;
    }
    const text = answer.paragraphs.map(p => `<p class="knowledge-answer-text">${esc(p.text)}</p>`).join("");
    const findings = answer.historical_findings?.length
      ? `<section class="knowledge-cited-cases" aria-label="Detalhes dos atendimentos citados">
          <h3>Detalhes dos atendimentos citados</h3>
          ${answerContent({ paragraphs: answer.historical_findings }, cards)}</section>` : "";
    return `<div class="knowledge-answer">${text}${findings}</div>`;
  }

  function localResults(records) {
    const cards = [...new Map(records.map(record => [ticketKey(record), record])).values()];
    const card = r => {
      const referenceValue = r.ticket_reference ? `Ticket ${r.ticket_reference}` : `Referência ${r.source_id}`;
      const sourceValue = r.origin?.arquivo_relativo || r.source_id || "Não informado";
      const status = r.evidence_status || (r.has_solution ? "solucao_documentada" : "sem_solucao_identificada");
      return `<article class="knowledge-card">
        <div class="knowledge-card-head"><span>${esc(referenceValue)}</span></div>
        <h3>${esc(r.title.replace(/<\/?b>/gi, ""))}</h3>
        <dl class="knowledge-card-fields"><div><dt>Evidência</dt><dd>${esc(evidenceLabels[status])}</dd></div><div><dt>Fonte</dt><dd>${esc(sourceValue)}</dd></div></dl>
        <button class="ghost" type="button" data-document="${esc(r.document_key)}">Ver detalhes no acervo</button>
      </article>`;
    };
    const section = (title, records) => records.length ? `<section class="knowledge-ticket-section" aria-label="${title}">
      <div class="knowledge-ticket-heading"><h3>${title}</h3><span class="knowledge-ticket-count">${records.length}</span></div>
      <div class="knowledge-ticket-grid">${records.map(card).join("")}</div></section>` : "";
    const isHistory = item => item.evidence_status ? historyOnly.has(item.evidence_status) : !item.has_solution;
    if (!cards.length) {
      return '<p class="knowledge-status">Nenhum ticket relacionado foi encontrado. Tente descrever o sintoma ou a mensagem de erro.</p>';
    }
    const related = section("Evidências técnicas relacionadas", cards.filter(item => !isHistory(item)))
      + section("Atendimentos relacionados", cards.filter(isHistory));
    return related;
  }

  function render(data) {
    const answer = data.generated_answer;
    const conversationView = Boolean(answer) || data.decision === "ask";
    const localFallback = Boolean(data.local_fallback_message || (data.session_id && !answer
      && data.decision !== "ask" && technicalFailures[data.generation_status]));
    const warnings = [...data.warnings];
    // A conversa acrescenta a mensagem da falha de geração por último; preserve os avisos da busca local.
    if (technicalFailures[data.generation_status] && warnings.length) warnings.pop();
    const noticeItems = warnings.filter(w => !(data.decision === "ask" && w.startsWith("Preciso entender o sintoma")))
      .map(w => esc(w));
    if (localFallback) {
      noticeItems.push(esc(data.local_fallback_message || technicalFailures[data.generation_status]));
    } else if (data.local_evidence && technicalFailures[data.generation_status]) {
      noticeItems.push(esc(technicalFailures[data.generation_status]));
    } else if (!data.generation_available && !data.session_id) {
      noticeItems.push("A orientação por IA não está disponível no servidor.");
    }
    const noticeLabel = localFallback || (!data.generation_available && !data.session_id) ? "Busca local" : "Aviso da pesquisa";
    const notice = noticeItems.length ? `<aside class="knowledge-search-notice" aria-label="Informações da pesquisa">
      <span class="knowledge-search-label">${noticeLabel}</span><div>${noticeItems.map(item => `<p>${item}</p>`).join("")}</div></aside>` : "";
    if (conversationView) {
      const content = transcript ? "" : assistantMessage(answer
        ? answerContent(answer, data.results) : `<p>${esc(data.follow_up)}</p>`);
      const showRelated = data.decision !== "ask" && !data.follow_up;
      results.innerHTML = content + (showRelated && data.results.length ? localResults(data.results) : "");
      return;
    }
    results.innerHTML = notice + localResults(data.results);
  }
  async function search(retrying = false) {
    if (busy) return;
    if (conversationClosed) { error.textContent = "Inicie uma nova investigação para outro problema."; return; }
    const query = input.value.trim();
    if (!query) { input.focus(); error.textContent = "Informe o problema que deseja pesquisar."; return; }
    beforeSearch(query);
    chatView?.classList.add("has-conversation");
    controls(true); error.textContent = "";
    // A dúvida já foi enviada: libera a caixa para a próxima mensagem.
    input.value = ""; input.dispatchEvent(new Event("input"));
    const previousResults = results.innerHTML;
    const replacesFailure = retrying || retryMessage === query;
    const previousRetry = transcript?.querySelector?.("[data-retry]");
    if (replacesFailure) previousRetry?.closest(".knowledge-message")?.remove();
    else previousRetry?.remove();
    const previousTranscript = transcript?.innerHTML || "";
    const submittedTranscript = previousTranscript + (replacesFailure ? "" : userMessage(query));
    if (transcript) {
      transcript.innerHTML = submittedTranscript + assistantMessage(thinking);
      results.innerHTML = "";
      transcript.lastElementChild?.scrollIntoView?.({ block: "start" });
    } else {
      results.innerHTML = thinking;
    }
    const waitingLabel = (transcript || results).querySelector?.("[data-wait-label]");
    const slowTimer = setTimeout(() => {
      if (waitingLabel) waitingLabel.textContent = "A consulta ainda está em andamento.";
    }, 8000);
    try {
      if (!pendingRequest || pendingRequest.message !== query) {
        pendingRequest = { message: query, request_id: crypto.randomUUID(), ...(conversationId ? { session_id: conversationId } : {}) };
      }
      const data = await api("/knowledge/conversation", { method: "POST", body: pendingRequest });
      pendingRequest = null; conversationId = data.session_id;
      onConversationSaved();
      const retryable = ["rejected", "unavailable", "provider_busy", "rate_limited", "busy"].includes(data.generation_status);
      retryMessage = retryable ? query : "";
      conversationClosed = !retryable && data.turn >= 6;
      currentOptions = data.decision === "ask" && !conversationClosed ? (data.clarification_options || []) : [];
      const choices = currentOptions.length ? `<ol class="knowledge-options" aria-label="Escolha uma alternativa">${currentOptions.map((option, index) => `<li><button type="button" data-option="${index}"><span class="knowledge-option-number" aria-hidden="true">${index + 1}.</span><span>${esc(option.label)}</span></button></li>`).join("")}</ol><button class="knowledge-other" type="button" data-other>Nenhuma dessas — vou descrever</button>` : "";
      const localFallback = !data.generated_answer && data.decision !== "ask" && Boolean(technicalFailures[data.generation_status]);
      const response = data.generated_answer
        ? (data.local_evidence ? "<p>Estes são os pontos que o acervo permite comparar até aqui. A causa ainda precisa ser confirmada.</p>" : "")
        + answerContent(data.generated_answer, data.results)
        + (data.follow_up && !conversationClosed ? `<p class="knowledge-follow-up">${esc(data.follow_up)}</p>` : "")
        : `<p>${esc(localFallback && data.results.length
            ? "Encontrei atendimentos relacionados no acervo; eles aparecem abaixo."
            : technicalFailures[data.generation_status] || data.follow_up || (conversationClosed
              ? "Não encontrei evidências suficientes para orientar este caso. Você pode iniciar uma nova investigação com mais detalhes."
              : "Ainda não tenho informações suficientes para orientar. Pode descrever melhor o que acontece?"))}</p>`;
      if (transcript) transcript.innerHTML = data.messages.map(t =>
        (t.prior_answer ? assistantMessage(answerContent(t.prior_answer, data.results)) : "")
        + (t.asked ? assistantMessage(`<p>${esc(t.asked)}</p>`) : "") + userMessage(t.message)).join("")
        + assistantMessage(response + choices + (retryable ? retryButton : ""));
      input.value = "";
      input.placeholder = data.follow_up && !conversationClosed ? "Responda à pergunta do assistente…"
        : data.generated_answer && !conversationClosed ? "Continue a investigação…" : initialPlaceholder;
      input.dispatchEvent(new Event("input"));
      render(data);
      transcript?.lastElementChild?.scrollIntoView?.({ block: "start" });
    } catch (ex) {
      try {
        const data = await api("/knowledge/search", { method: "POST", body: { query } });
        conversationId = null; pendingRequest = null; conversationClosed = false;
        currentOptions = [];
        retryMessage = data.generation_available ? query : "";
        if (transcript) transcript.innerHTML = submittedTranscript
          + assistantMessage(`<p>Não foi possível obter a orientação do assistente. ${data.results.length ? "Os resultados locais estão disponíveis abaixo." : "Tente descrever o problema com mais detalhes."}</p>${retryMessage ? retryButton : ""}`);
        input.placeholder = initialPlaceholder;
        input.dispatchEvent(new Event("input"));
        render({ ...data, local_fallback_message: "O assistente não respondeu. Esta é a busca feita no acervo local." });
        transcript?.lastElementChild?.scrollIntoView?.({ block: "start" });
      } catch {
        results.innerHTML = previousResults;
        retryMessage = query;
        if (transcript) {
          transcript.innerHTML = submittedTranscript
            + assistantMessage("<p>" + esc(ex.message || "Não foi possível concluir a consulta.") + "</p>" + retryButton);
        } else {
          error.textContent = ex.message;
        }
        input.value = query; input.dispatchEvent(new Event("input"));
      }
    }
    finally {
      clearTimeout(slowTimer);
      controls(false);
      input.focus({ preventScroll: true });
    }
  }
  form.addEventListener("submit", e => { e.preventDefault(); search(); });
  if (transcript) transcript.addEventListener("click", e => {
    const source = e.target.closest("[data-document]");
    if (source) {
      showDocument(source);
      return;
    }
    if (busy || conversationClosed) return;
    if (e.target.closest("[data-retry]")) {
      if (!retryMessage) return;
      input.value = retryMessage;
      search(true);
      return;
    }
    const option = e.target.closest("[data-option]");
    if (option) {
      const choice = currentOptions[Number(option.dataset.option)];
      if (!choice) return;
      input.value = choice.label;
      input.dispatchEvent(new Event("input"));
      search();
    } else if (e.target.closest("[data-other]")) {
      input.placeholder = "Descreva o que acontece no seu caso…";
      input.focus();
    }
  });
  async function showDocument(button) {
    if (!button || button.disabled) return;
    button.disabled = true; error.textContent = "";
    try {
      const doc = await api(`/knowledge/documents/${encodeURIComponent(button.dataset.document)}`);
      $("#knowledge-document-title").textContent = doc.title;
      $("#knowledge-document-body").innerHTML = documentDetails(doc);
      $("#knowledge-document").showModal();
    } catch (ex) { error.textContent = ex.message; }
    finally { button.disabled = false; }
  }
  results.addEventListener("click", e => showDocument(e.target.closest("[data-document]")));
  return { loadConversation, resetConversation, isCurrentConversation: id => conversationId === id };
}

if (document.body.dataset.page === "knowledge") initKnowledgeSearch();
