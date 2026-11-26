// As páginas e a API são servidas pela mesma aplicação Flask.
const API_URL = location.protocol === "file:" ? "http://localhost:5000" : location.origin;

const $ = (s, r = document) => r.querySelector(s);

/* ---------------- Tema claro / escuro ---------------- */
const theme = {
  get() {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
    return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  },
  apply(name) {
    const next = name === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    const btn = $("#theme-toggle");
    if (btn) btn.setAttribute("aria-label", next === "light" ? "Mudar para tema escuro" : "Mudar para tema claro");
  },
  toggle() { theme.apply(theme.get() === "light" ? "dark" : "light"); },
};
theme.apply(theme.get());
document.addEventListener("click", (e) => {
  if (e.target.closest("#theme-toggle")) theme.toggle();
});
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (v) => (v ? new Date(v).toLocaleString("pt-BR") : "—");

const STATUS = { open: "Aberto", in_progress: "Em andamento", resolved: "Resolvido" };
const ROLE = { admin: "Administrador", agent: "Atendente" };
const ACTIONS = {
  login_success: "Login", login_failed: "Login falhou", login_blocked: "Login bloqueado", logout: "Logout",
  password_changed: "Senha alterada", user_created: "Usuário criado", user_updated: "Usuário alterado",
  user_password_reset: "Senha redefinida", user_deleted: "Usuário excluído", team_created: "Equipe criada",
  team_renamed: "Equipe renomeada", team_activated: "Equipe ativada", team_deactivated: "Equipe desativada",
  team_deleted: "Equipe excluída", ticket_created: "Ticket criado",
  ticket_updated: "Ticket alterado", ticket_deleted: "Ticket excluído", tickets_imported: "Tickets importados",
};

/* ---------------- Sessão ---------------- */
const session = {
  get token() { return localStorage.getItem("token"); },
  get user() { return JSON.parse(localStorage.getItem("user") || "{}"); },
  set user(u) { localStorage.setItem("user", JSON.stringify(u)); },
  save(d) {
    localStorage.setItem("token", d.access_token);
    localStorage.setItem("refresh_token", d.refresh_token);
    localStorage.setItem("expires_at", String(Date.now() + (d.expires_in || 3600) * 1000));
    session.user = d.user;
  },
  clear() { ["token", "refresh_token", "expires_at", "user"].forEach((k) => localStorage.removeItem(k)); },
  expiresSoon() { return Number(localStorage.getItem("expires_at") || 0) - Date.now() < 60_000; },
};

function endSession() {
  session.clear();
  location.href = "index.html";
}

async function logout() {
  try { await api("/auth/logout", { method: "POST", retry: false }); } catch { /* sessão já inválida */ }
  endSession();
}

// Várias chamadas simultâneas compartilham o mesmo refresh.
let refreshing = null;
function refreshSession() {
  refreshing ??= (async () => {
    const refresh_token = localStorage.getItem("refresh_token");
    if (!refresh_token) return false;
    const res = await fetch(API_URL + "/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token }),
    });
    if (!res.ok) return false;
    session.save(await res.json());
    return true;
  })().finally(() => (refreshing = null));
  return refreshing;
}

async function api(path, { method = "GET", body, form, auth = true, retry = true } = {}) {
  const headers = {};
  if (!form) headers["Content-Type"] = "application/json";
  let res;
  try {
    if (auth && session.expiresSoon()) await refreshSession();
    // Werkzeug e Gunicorn descartam headers com "_" (token_request), por isso o token vai em Authorization.
    if (auth) headers.Authorization = `Bearer ${session.token || ""}`;
    res = await fetch(API_URL + path, { method, headers, body: form || (body ? JSON.stringify(body) : undefined) });
  } catch {
    throw new Error(`Não foi possível conectar à API em ${API_URL}. Confira se o back-end está rodando.`);
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && auth) {
    if (retry && (await refreshSession().catch(() => false))) return api(path, { method, body, form, auth, retry: false });
    endSession();
    throw new Error("Sessão expirada.");
  }
  if (!res.ok) throw new Error(data.error || `Erro ${res.status}`);
  return data;
}

/* ---------------- Login: POST /auth/login ---------------- */
function initLogin() {
  if (session.token) return location.replace("app.html");
  const form = $("#login-form"), err = $("#login-error"), btn = $("#login-btn");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    if (!form.email.value || !form.password.value) {
      err.textContent = "Informe e-mail e senha.";
      return;
    }
    btn.disabled = true;
    btn.textContent = "Entrando…";
    try {
      const d = await api("/auth/login", { method: "POST", auth: false, body: { email: form.email.value, password: form.password.value } });
      session.save(d);
      location.href = "app.html";
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
      btn.textContent = "Entrar";
    }
  });
}

/* ---------------- Painel ---------------- */
function initApp() {
  if (!session.token) return location.replace("index.html");

  let me = session.user;
  const isAdmin = () => me.profile?.role === "admin";
  const applyUser = () => {
    document.body.classList.toggle("is-admin", isAdmin());
    $("#who").textContent = me.profile?.full_name || me.email || "";
  };
  applyUser();
  api("/auth/me").then((d) => { me = d.user; session.user = me; applyUser(); }).catch(() => {});

  $("#logout").onclick = logout;

  const stage = $("#stage"), panel = $("#panel"), body = $("#panel-body"), q = $("#q"), filter = $("#filter");
  const results = $("#results");
  const state = { users: [], teams: null, view: null, editingUser: null, editingTeam: null, passwordUser: null, teamReturnId: null, linkingTeamId: null };

  const closeNav = () => document.body.classList.remove("nav-open");
  $("#nav-toggle").onclick = () => document.body.classList.toggle("nav-open");

  function setActive(name) {
    document.querySelectorAll(".side-nav button").forEach((b) => b.classList.toggle("active", name ? b.dataset.view === name : b.hasAttribute("data-home")));
  }

  const toast = (msg, isErr) => {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toast.id);
    toast.id = setTimeout(() => (t.hidden = true), 3500);
  };

  const table = (head, rows) =>
    `<div class="table-scroll"><table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  const badge = (text, kind = "") => `<span class="badge ${kind}">${esc(text)}</span>`;

  function statisticsContent(data) {
    const summary = data.summary || {};
    const count = value => Number(value || 0).toLocaleString("pt-BR");
    const rate = summary.evidence_rate == null ? "—" : `${Math.round(summary.evidence_rate * 100)}%`;
    const maxTrend = Math.max(1, ...data.trend.map(item => item.investigations));
    const maxTeam = Math.max(1, ...data.teams.map(item => item.investigations));
    const card = (label, value, hint = "") => `<article class="stat"><span>${label}</span><strong>${esc(value)}</strong>${hint ? `<small>${esc(hint)}</small>` : ""}</article>`;
    const rank = (items, empty) => items.length ? `<ol class="dashboard-ranking">${items.map(item => {
      const width = Math.max(2, item.investigations / Math.max(1, items[0].investigations) * 100);
      return `<li><div class="dashboard-source-row"><span title="${esc(item.title)}">${esc(item.ticket_reference ? `Ticket #${item.ticket_reference} — ${item.title}` : item.title)}</span>
        <strong>${count(item.investigations)}</strong></div><span class="dashboard-bar"><i style="width:${width}%"></i></span>
        <small>${count(item.investigations)} investiga\u00e7\u00f5es · ${count(item.appearances)} apari\u00e7\u00f5es</small></li>`;
    }).join("")}</ol>`
      : `<p class="dashboard-empty">${empty}</p>`;
    const panel = (title, content, hint = "") => `<section class="dashboard-panel"><h3>${title}</h3>${hint ? `<p class="dashboard-hint">${hint}</p>` : ""}${content}</section>`;
    const trend = data.trend.length ? `<div class="dashboard-trend" role="img" aria-label="Investiga\u00e7\u00f5es iniciadas por dia">${data.trend.map(item => {
      const height = item.investigations ? Math.max(5, item.investigations / maxTrend * 100) : 2;
      return `<span class="dashboard-trend-bar" title="${esc(item.date)}: ${count(item.investigations)} investiga\u00e7\u00f5es"><i style="height:${height}%"></i></span>`;
    }).join("")}</div><div class="dashboard-axis"><span>${esc(data.trend[0].date)}</span><span>${esc(data.trend.at(-1).date)}</span></div>`
      : `<p class="dashboard-empty">Ainda n\u00e3o h\u00e1 investiga\u00e7\u00f5es no per\u00edodo.</p>`;
    const teamBars = data.teams.length ? `<ol class="dashboard-team-bars">${data.teams.slice(0, 8).map(team =>
      `<li><div><span>${esc(team.name)}</span><strong>${count(team.investigations)}</strong></div>
       <span class="dashboard-bar"><i style="width:${Math.max(0, Math.min(100, team.investigations / maxTeam * 100))}%"></i></span>
       <small>${count(team.active_users)} usu\u00e1rio(s) ativo(s)</small></li>`).join("")}</ol>`
      : `<p class="dashboard-empty">Ainda n\u00e3o h\u00e1 dados por equipe.</p>`;
    const trackingNote = `<p class="dashboard-note">As m\u00e9tricas s\u00e3o acumuladas a partir da ativa\u00e7\u00e3o deste painel; consultas anteriores n\u00e3o est\u00e3o inclu\u00eddas.</p>`;
    const noEvents = !summary.messages ? `<p class="dashboard-empty">Ainda n\u00e3o existem consultas registradas nesse per\u00edodo.</p>` : "";
    return `${trackingNote}${noEvents}<div class="stats dashboard-kpis">
      ${card("Investiga\u00e7\u00f5es iniciadas", count(summary.investigations), `${count(summary.messages)} mensagens analisadas`)}
      ${card("Usu\u00e1rios ativos", count(summary.active_users), "no per\u00edodo selecionado")}
      ${card("Equipe mais ativa", summary.most_active_team?.name || "—", summary.most_active_team ? `${count(summary.most_active_team.investigations)} investiga\u00e7\u00f5es` : "sem dados no per\u00edodo")}
      ${card("Respostas com evid\u00eancia", rate, "propor\u00e7\u00e3o das mensagens")}
    </div><div class="dashboard-grid">
      ${panel("Investiga\u00e7\u00f5es ao longo do tempo", trend, "Investiga\u00e7\u00f5es iniciadas por dia")}
      ${panel("Uso por equipe", teamBars, "Quantidade de investiga\u00e7\u00f5es e usu\u00e1rios ativos")}
      ${panel("Tickets mais recuperados", rank(data.top_tickets, "Nenhum ticket recuperado neste per\u00edodo."), "Contagem por investiga\u00e7\u00f5es distintas")}
      ${panel("Artigos mais consultados", rank(data.top_articles, "Nenhum artigo recuperado neste per\u00edodo."), "Contagem por investiga\u00e7\u00f5es distintas")}
      ${panel("Fontes mais citadas nas respostas", rank(data.top_cited, "Ainda n\u00e3o h\u00e1 fontes citadas em respostas."), "Cita\u00e7\u00f5es validadas nas respostas da RAG")}
    </div>`;
  }

  async function loadTeams(force) {
    if (!state.teams || force) state.teams = (await api("/teams")).teams;
    return state.teams;
  }
  const teamName = (id) => state.teams?.find((t) => t.id === id)?.name || "—";
  async function fillTeams(select, selected) {
    select.innerHTML = `<option value="">Carregando…</option>`;
    const teams = await loadTeams();
    const available = teams.filter((t) => t.active !== false || t.id === selected);
    select.innerHTML = available.length
      ? available.map((t) => `<option value="${esc(t.id)}" ${t.id === selected ? "selected" : ""}>${esc(t.name)}${t.active === false ? " (desativada)" : ""}</option>`).join("")
      : `<option value="">Nenhuma equipe ativa</option>`;
  }

  /* ---------- Listas ---------- */
  const views = {
    users: {
      title: "Usuários",
      async render() {
        const { users } = await api("/users");
        state.users = users;
        const createButton = `<div class="user-list-actions"><button class="primary" type="button" data-new="user">Criar usuário</button></div>`;
        if (!users.length) return createButton + `<p class="empty">Nenhum usuário cadastrado.</p>`;
        return createButton + table(["Nome", "E-mail", "Equipe", "Perfil", "Situação", "Criado em", ""], users.map((u) => {
          const self = u.id === me.id;
          return `<tr><td>${esc(u.full_name || "—")}</td><td>${esc(u.email)}</td><td>${esc(u.teams?.name || "—")}</td>
           <td>${esc(ROLE[u.role] || u.role)}</td><td>${u.disabled ? badge("Desativado", "off") : badge("Ativo", "ok")}</td><td>${fmtDate(u.created_at)}</td>
           <td class="row-actions"><button class="mini" data-act="edit-user" data-id="${esc(u.id)}">Editar</button>
           <button class="mini" data-act="password" data-id="${esc(u.id)}">Senha</button>
           ${self ? "" : `<button class="mini" data-act="toggle-user" data-id="${esc(u.id)}">${u.disabled ? "Ativar" : "Desativar"}</button>
           <button class="del" data-act="del-user" data-id="${esc(u.id)}">Excluir</button>`}</td></tr>`;
        }));
      },
    },
    teams: {
      title: "Equipes",
      async render() {
        const [teams, { users }] = await Promise.all([loadTeams(true), api("/users")]);
        state.users = users;
        const createButton = `<div class="team-list-actions"><button class="primary" type="button" data-act="new-team">Nova equipe</button></div>`;
        if (!teams.length) return createButton + `<p class="empty">Nenhuma equipe criada.</p>`;
        const members = (teamId) => users.filter((u) => u.team_id === teamId);
        return createButton + table(["Equipe", "Membros ativos", "Situa\u00e7\u00e3o", "Criada em", ""], teams.map((t) => {
            const teamMembers = members(t.id);
            const activeMembers = teamMembers.filter((u) => !u.disabled).length;
            const active = t.active !== false;
            return `<tr><td><button class="link" type="button" data-act="view-team" data-id="${esc(t.id)}">${esc(t.name)}</button></td>
              <td>${activeMembers} de ${teamMembers.length}</td><td>${badge(active ? "Ativa" : "Desativada", active ? "ok" : "off")}</td>
              <td>${fmtDate(t.created_at)}</td><td class="row-actions">
                <button class="mini" data-act="edit-team" data-id="${esc(t.id)}">Renomear</button>
                <button class="mini" data-act="toggle-team" data-id="${esc(t.id)}">${active ? "Desativar" : "Ativar"}</button>
                ${teamMembers.length ? "" : `<button class="del" data-act="del-team" data-id="${esc(t.id)}">Excluir</button>`}</td></tr>`;
          }));
      },
    },
    audit: {
      title: "Auditoria",
      async render() {
        const { logs } = await api("/audit?limit=150");
        if (!logs.length) return `<p class="empty">Nenhum registro.</p>`;
        return table(["Data", "Ação", "Quem", "Detalhes", "IP"], logs.map((l) => {
          const details = Object.entries(l.details || {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
          return `<tr><td>${fmtDate(l.created_at)}</td><td>${esc(ACTIONS[l.action] || l.action)}</td><td>${esc(l.actor_email || l.details?.email || "—")}</td>
           <td class="small">${esc(details || "—")}</td><td>${esc(l.ip_address || "—")}</td></tr>`;
        }));
      },
    },
    stats: {
      title: "Estatísticas da plataforma",
      noFilter: true,
      async render() {
        const [data, { teams }] = await Promise.all([
          api("/knowledge/stats?days=30"), api("/teams"),
        ]);
        const options = teams.map(team => `<option value="${esc(team.id)}">${esc(team.name)}${team.active === false ? " (desativada)" : ""}</option>`).join("");
        return `<div class="dashboard">
          <div class="dashboard-filters">
            <label>Per\u00edodo<select id="stats-days" data-stats-filter><option value="7">7 dias</option><option value="30" selected>30 dias</option><option value="90">90 dias</option></select></label>
            <label>Equipe<select id="stats-team" data-stats-filter><option value="">Todas as equipes</option>${options}</select></label>
          </div>
          <div id="stats-content">${statisticsContent(data)}</div>
        </div>`;
      },
    },
  };

  function applyFilter() {
    const term = filter.value.trim().toLowerCase();
    body.querySelectorAll("tbody tr").forEach((tr) => (tr.hidden = !!term && !tr.textContent.toLowerCase().includes(term)));
  }

  function setHomeLayout() {
    const hasResults = !results.hidden;
    stage.classList.toggle("has-results", hasResults);
  }

  async function openView(name) {
    state.view = name;
    stage.classList.add("has-panel");
    results.hidden = true;
    setHomeLayout();
    setActive(name);
    closeNav();
    panel.hidden = false;
    filter.hidden = !!views[name].noFilter;
    $("#panel-title").textContent = views[name].title;
    body.innerHTML = `<p class="loading">Carregando…</p>`;
    try {
      body.innerHTML = await views[name].render();
      applyFilter();
    } catch (ex) {
      body.innerHTML = `<p class="error">${esc(ex.message)}</p>`;
    }
  }
  const reloadView = (name) => state.view === name && openView(name);

  function closeView() {
    state.view = null;
    filter.value = "";
    stage.classList.remove("has-panel");
    results.hidden = !results.innerHTML;
    setHomeLayout();
    setActive(null);
    panel.hidden = true;
  }

  document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => openView(b.dataset.view)));
  $("#back").onclick = closeView;
  filter.addEventListener("input", applyFilter);
  body.addEventListener("change", async (event) => {
    if (!event.target.matches("[data-stats-filter]")) return;
    const content = $("#stats-content", body);
    if (!content) return;
    const days = $("#stats-days", body).value;
    const teamId = $("#stats-team", body).value;
    const query = new URLSearchParams({ days });
    if (teamId) query.set("team_id", teamId);
    content.innerHTML = `<p class="loading">Atualizando estat\u00edsticas…</p>`;
    try { content.innerHTML = statisticsContent(await api(`/knowledge/stats?${query}`)); }
    catch (ex) { content.innerHTML = `<p class="error">${esc(ex.message)}</p>`; }
  });

  /* ---------- Pesquisa principal no acervo local ---------- */
  function resizeQuery() {
    q.style.height = "auto";
    q.style.height = `${Math.min(q.scrollHeight, 200)}px`;
  }
  q.addEventListener("input", resizeQuery);
  window.addEventListener("resize", resizeQuery);
  q.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#ask-form").requestSubmit();
    }
  });
  resizeQuery();
  const recentList = $("#recent-investigations");

  /* ---------- Pop-up de opções (abre ao lado da barra lateral) ---------- */
  // Fica fora da lista rolável para não ser cortado por ela.
  const recentPopup = document.createElement("div");
  recentPopup.className = "recent-popup";
  recentPopup.setAttribute("role", "menu");
  recentPopup.hidden = true;
  recentPopup.innerHTML = `<button type="button" role="menuitem" data-popup-delete>Excluir investigação</button>`;
  document.body.appendChild(recentPopup);
  let popupSession = null, popupTrigger = null;

  function closeRecentPopup(restoreFocus = false) {
    recentPopup.hidden = true;
    popupTrigger?.setAttribute("aria-expanded", "false");
    popupTrigger?.classList.remove("open");
    if (restoreFocus) popupTrigger?.focus();
    popupSession = null; popupTrigger = null;
  }

  function openRecentPopup(trigger) {
    closeRecentPopup();
    popupSession = trigger.dataset.investigationMenu;
    popupTrigger = trigger;
    trigger.setAttribute("aria-expanded", "true");
    trigger.classList.add("open");
    recentPopup.hidden = false;
    const t = trigger.getBoundingClientRect();
    const side = trigger.closest(".sidebar")?.getBoundingClientRect() || t;
    const w = recentPopup.offsetWidth, h = recentPopup.offsetHeight, gap = 8;
    // Preferência: ao lado direito da barra lateral, alinhado ao botão de três pontos.
    let left = side.right + gap;
    if (left + w > window.innerWidth - gap) left = Math.max(gap, window.innerWidth - w - gap);
    const top = Math.max(gap, Math.min(t.top, window.innerHeight - h - gap));
    recentPopup.style.left = `${left}px`;
    recentPopup.style.top = `${top}px`;
    recentPopup.querySelector("button").focus({ preventScroll: true });
  }

  document.addEventListener("click", event => {
    if (!recentPopup.hidden && !event.target.closest(".recent-popup, [data-investigation-menu]")) closeRecentPopup();
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !recentPopup.hidden) closeRecentPopup(true); });
  window.addEventListener("resize", () => closeRecentPopup());
  recentList?.addEventListener("scroll", () => closeRecentPopup());
  $(".sidebar")?.addEventListener("scroll", () => closeRecentPopup());
  async function refreshRecentInvestigations() {
    if (!recentList) return;
    closeRecentPopup();
    recentList.innerHTML = `<p class="recent-empty">Carregando histórico…</p>`;
    try {
      const { investigations } = await api("/knowledge/conversations");
      recentList.innerHTML = investigations.length ? investigations.map(item => {
        const date = new Date(item.updated_at * 1000).toLocaleDateString("pt-BR");
        return `<article class="recent-investigation">
          <button class="recent-investigation-open" type="button" data-investigation-open="${esc(item.session_id)}" title="${esc(item.title)}">
            <span>${esc(item.title)}</span><time>${date} · ${item.turn_count} mensagem(ns)</time>
          </button>
          <button class="recent-investigation-more" type="button" aria-label="Mais opções" aria-haspopup="menu" aria-expanded="false" data-investigation-menu="${esc(item.session_id)}">⋯</button>
        </article>`;
      }).join("") : `<p class="recent-empty">Nenhuma dúvida recente.</p>`;
    } catch (ex) {
      recentList.innerHTML = `<p class="recent-empty">Não foi possível carregar o histórico.</p>`;
    }
  }
  const knowledge = initKnowledgeSearch({
    form: $("#ask-form"), input: q, results, submit: $("#send"),
    onConversationSaved: refreshRecentInvestigations,
    beforeSearch(query) {
      closeView();
      closeNav();
      results.hidden = false;
      setHomeLayout();
    },
    onReset() {
      results.hidden = true;
      setHomeLayout();
    },
  });
  refreshRecentInvestigations();

  async function deleteInvestigation(sessionId, button) {
    const opener = [...recentList.querySelectorAll("[data-investigation-open]")].find(b => b.dataset.investigationOpen === sessionId);
    const title = opener?.querySelector("span")?.textContent.trim();
    if (!confirm(`Excluir esta investigação${title ? `: ${title}` : ""}?`)) return;
    button.disabled = true;
    try {
      await api(`/knowledge/conversations/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      if (knowledge.isCurrentConversation(sessionId)) knowledge.resetConversation();
      await refreshRecentInvestigations();
      toast("Investigação excluída.");
    } catch (ex) { toast(ex.message, true); }
    finally { button.disabled = false; }
  }

  recentPopup.addEventListener("click", async event => {
    const deleteButton = event.target.closest("[data-popup-delete]");
    if (!deleteButton || !popupSession) return;
    const sessionId = popupSession;
    closeRecentPopup();
    await deleteInvestigation(sessionId, deleteButton);
  });

  recentList?.addEventListener("click", async event => {
    const menuButton = event.target.closest("[data-investigation-menu]");
    if (menuButton) {
      if (popupTrigger === menuButton) closeRecentPopup(); else openRecentPopup(menuButton);
      return;
    }
    const openButton = event.target.closest("[data-investigation-open]");
    if (!openButton) return;
    try {
      const data = await api(`/knowledge/conversations/${encodeURIComponent(openButton.dataset.investigationOpen)}`);
      closeView();
      closeNav();
      await knowledge.loadConversation(data);
    } catch (ex) { toast(ex.message, true); }
  });

  /* ---------- Nova dúvida (nova janela de conversa) ---------- */
  $("#new-chat")?.addEventListener("click", () => {
    closeView();
    closeNav();
    if (knowledge.resetConversation()) q.focus();
    else toast("Aguarde a resposta em andamento para iniciar uma nova dúvida.", true);
  });

  /* ---------- Atalhos ---------- */
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));

  document.addEventListener("click", (e) => {
    const nb = e.target.closest("[data-new]");
    if (nb) {
      closeNav();
      return openNew(nb.dataset.new);
    }
  });

  function openNew(kind) {
    if (kind === "user") { state.teamReturnId = null; return openUserForm(null); }
    if (kind === "team") return openTeamForm(null);
    if (kind === "account") {
      $("#form-account").reset();
      $("#account-error").textContent = "";
      return $("#dlg-account").showModal();
    }
  }

  /* ---------- Usu?rios ---------- */
  const dlgUser = $("#dlg-user");

  async function openUserForm(u, defaultTeamId = null) {
    const f = $("#form-user");
    f.reset();
    state.editingUser = u;
    $("#user-error").textContent = "";
    $("#user-title").textContent = u ? "Editar usuário" : "Cadastrar usuário";
    f.querySelectorAll("[data-create-only]").forEach((el) => {
      el.hidden = !!u;
      el.querySelector("input").disabled = !!u;
    });
    f.full_name.value = u?.full_name || "";
    f.role.value = u?.role || "agent";
    f.role.disabled = !!u && u.id === me.id;
    dlgUser.showModal();
    try { await fillTeams(f.team_id, u?.team_id || defaultTeamId); } catch (ex) { $("#user-error").textContent = ex.message; }
  }

  $("#form-user").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#user-error"), u = state.editingUser;
    err.textContent = "";
    try {
      if (u) {
        const changes = { full_name: f.full_name.value, team_id: f.team_id.value };
        if (!f.role.disabled) changes.role = f.role.value;
        await api(`/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body: changes });
      } else {
        await api("/users", { method: "POST", body: { full_name: f.full_name.value, email: f.email.value, password: f.password.value, team_id: f.team_id.value, role: f.role.value } });
      }
      const returnToTeam = state.teamReturnId;
      state.teamReturnId = null;
      dlgUser.close();
      toast(u ? "Usuário atualizado." : "Usuário cadastrado.");
      if (returnToTeam) await openTeamDetails(returnToTeam);
      else openView("users");
    } catch (ex) { err.textContent = ex.message; }
  });

  $("#form-password").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#password-error");
    err.textContent = "";
    try {
      await api(`/users/${encodeURIComponent(state.passwordUser.id)}/password`, { method: "POST", body: { password: e.target.password.value } });
      $("#dlg-password").close();
      toast("Senha redefinida. As sessões abertas desse usuário foram encerradas.");
    } catch (ex) { err.textContent = ex.message; }
  });

  /* ---------- Equipes ---------- */
  const dlgTeam = $("#dlg-team");
  const dlgTeamDetails = $("#dlg-team-details");
  const dlgTeamLinkUser = $("#dlg-team-link-user");

  async function openTeamDetails(teamId) {
    const [teams, { users }] = await Promise.all([loadTeams(true), api("/users")]);
    state.users = users;
    const team = teams.find((item) => item.id === teamId);
    if (!team) return toast("Equipe não encontrada.", true);
    const members = users.filter((user) => user.team_id === teamId)
      .sort((a, b) => (a.full_name || a.email).localeCompare(b.full_name || b.email, "pt-BR"));
    const activeMembers = members.filter((user) => !user.disabled).length;
    $("#team-details-title").textContent = team.name;
    $("#team-details-body").innerHTML = `<div class="team-summary">
      <p>${badge(team.active === false ? "Desativada" : "Ativa", team.active === false ? "off" : "ok")}</p>
      <p>${activeMembers} membro(s) ativo(s) de ${members.length}</p>
      <p>Criada em ${fmtDate(team.created_at)}</p>
      ${team.active === false ? "" : `<button class="primary" type="button" data-team-add-member="${esc(team.id)}">Vincular usuário existente</button>`}
    </div>${members.length ? table(["Nome", "E-mail", "Perfil", "Situa\u00e7\u00e3o"], members.map((user) =>
      `<tr><td>${esc(user.full_name || "—")}</td><td>${esc(user.email)}</td><td>${esc(ROLE[user.role] || user.role)}</td>
        <td>${user.disabled ? badge("Desativado", "off") : badge("Ativo", "ok")}</td></tr>`
    )) : `<p class="empty">Esta equipe ainda não tem membros.</p>`}`;
    dlgTeamDetails.showModal();
  }

  dlgTeamDetails.addEventListener("click", (event) => {
    const add = event.target.closest("[data-team-add-member]");
    if (add) {
      dlgTeamDetails.close();
      openTeamLinkUser(add.dataset.teamAddMember);
      return;
    }
  });

  async function openTeamLinkUser(teamId) {
    state.linkingTeamId = teamId;
    const select = $("#team-link-user");
    const error = $("#team-link-error");
    error.textContent = "";
    select.innerHTML = `<option value="">Carregando usuários…</option>`;
    try {
      const [{ users }, teams] = await Promise.all([api("/users"), loadTeams(true)]);
      state.users = users;
      state.teams = teams;
      const eligible = users.filter(user => !user.disabled && user.team_id !== teamId)
        .sort((a, b) => (a.full_name || a.email).localeCompare(b.full_name || b.email, "pt-BR"));
      select.innerHTML = eligible.length
        ? `<option value="">Selecione um usuário</option>${eligible.map(user => {
          const currentTeam = teams.find(team => team.id === user.team_id);
          return `<option value="${esc(user.id)}">${esc(user.full_name || user.email)}${currentTeam ? ` — atualmente em ${esc(currentTeam.name)}` : " — sem equipe"}</option>`;
        }).join("")}`
        : `<option value="">Nenhum usuário disponível para vincular</option>`;
      select.disabled = !eligible.length;
      $("#team-link-submit").disabled = !eligible.length;
    } catch (ex) {
      select.innerHTML = `<option value="">Não foi possível carregar os usuários</option>`;
      select.disabled = true;
      $("#team-link-submit").disabled = true;
      error.textContent = ex.message;
    }
    dlgTeamLinkUser.showModal();
  }

  dlgTeamLinkUser.addEventListener("close", () => {
    if (!state.linkingTeamId) return;
    const teamId = state.linkingTeamId;
    state.linkingTeamId = null;
    openTeamDetails(teamId);
  });

  $("#form-team-link-user").addEventListener("submit", async event => {
    event.preventDefault();
    const error = $("#team-link-error");
    const userId = $("#team-link-user").value;
    error.textContent = "";
    try {
      await api(`/users/${encodeURIComponent(userId)}`, { method: "PATCH", body: { team_id: state.linkingTeamId } });
      const teamId = state.linkingTeamId;
      state.linkingTeamId = null;
      dlgTeamLinkUser.close();
      toast("Usuário vinculado à equipe.");
      await openTeamDetails(teamId);
    } catch (ex) { error.textContent = ex.message; }
  });

  $("#dlg-user").addEventListener("close", () => {
    if (!state.teamReturnId) return;
    const teamId = state.teamReturnId;
    state.teamReturnId = null;
    openTeamDetails(teamId);
  });

  function openTeamForm(t) {
    const f = $("#form-team");
    f.reset();
    state.editingTeam = t;
    $("#team-error").textContent = "";
    $("#team-title").textContent = t ? "Renomear equipe" : "Criar equipe";
    f.name.value = t?.name || "";
    dlgTeam.showModal();
  }

  $("#form-team").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#team-error"), t = state.editingTeam;
    err.textContent = "";
    try {
      const name = e.target.name.value;
      if (t) await api(`/teams/${encodeURIComponent(t.id)}`, { method: "PATCH", body: { name } });
      else await api("/teams", { method: "POST", body: { name } });
      dlgTeam.close();
      state.teams = null;
      toast(t ? "Equipe renomeada." : "Equipe criada.");
      openView("teams");
    } catch (ex) { err.textContent = ex.message; }
  });

  /* ---------- Ações das tabelas ---------- */
  body.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    e.stopPropagation();
    const id = btn.dataset.id;
    const user = state.users.find((u) => u.id === id);
    const team = state.teams?.find((t) => t.id === id);
    try {
      switch (btn.dataset.act) {
        case "new-team":
          return openTeamForm(null);
        case "view-team":
          return openTeamDetails(id);
        case "edit-user":
          return openUserForm(user);
        case "password":
          state.passwordUser = user;
          $("#form-password").reset();
          $("#password-error").textContent = "";
          $("#password-who").textContent = user.email;
          return $("#dlg-password").showModal();
        case "toggle-user":
          if (!user.disabled && !confirm(`Desativar ${user.email}? A pessoa não conseguirá mais entrar.`)) return;
          await api(`/users/${encodeURIComponent(id)}`, { method: "PATCH", body: { disabled: !user.disabled } });
          toast(user.disabled ? "Usuário ativado." : "Usuário desativado.");
          return openView("users");
        case "del-user":
          if (!confirm(`Excluir o usuário ${user.email}? Essa ação não pode ser desfeita.`)) return;
          await api(`/users/${encodeURIComponent(id)}`, { method: "DELETE" });
          toast("Usuário excluído.");
          return openView("users");
        case "edit-team":
          return openTeamForm(team);
        case "toggle-team": {
          const active = team.active !== false;
          if (active && !confirm(`Desativar a equipe ${team.name}? Os membros permanecerão vinculados, mas ela não poderá receber novos usuários.`)) return;
          await api(`/teams/${encodeURIComponent(id)}/status`, { method: "PATCH", body: { active: !active } });
          state.teams = null;
          toast(active ? "Equipe desativada." : "Equipe ativada.");
          return openView("teams");
        }
        case "del-team":
          if (!confirm(`Excluir a equipe ${team.name}?`)) return;
          await api(`/teams/${encodeURIComponent(id)}`, { method: "DELETE" });
          state.teams = null;
          toast("Equipe excluída.");
          return openView("teams");
      }
    } catch (ex) { toast(ex.message, true); }
  });

  /* ---------- Minha conta: POST /auth/password ---------- */
  $("#form-account").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#account-error");
    err.textContent = "";
    if (f.new_password.value !== f.confirm.value) {
      err.textContent = "A confirmação não confere com a nova senha.";
      return;
    }
    try {
      const d = await api("/auth/password", { method: "POST", body: { current_password: f.current_password.value, new_password: f.new_password.value } });
      session.save(d);
      $("#dlg-account").close();
      toast("Senha alterada. Outras sessões abertas foram encerradas.");
    } catch (ex) { err.textContent = ex.message; }
  });
}

const page = document.body.dataset.page;
if (page === "login") initLogin();
if (page === "app") initApp();
