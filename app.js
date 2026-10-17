// Endereço do back-end Flask (python run.py → porta 5000)
const API_URL = location.port === "5000" ? location.origin : "http://localhost:5000";

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (v) => (v ? new Date(v).toLocaleString("pt-BR") : "—");

const STATUS = { open: "Aberto", in_progress: "Em andamento", resolved: "Resolvido" };
const ROLE = { admin: "Administrador", agent: "Atendente" };
const ACTIONS = {
  login_success: "Login", login_failed: "Login falhou", login_blocked: "Login bloqueado", logout: "Logout",
  password_changed: "Senha alterada", user_created: "Usuário criado", user_updated: "Usuário alterado",
  user_password_reset: "Senha redefinida", user_deleted: "Usuário excluído", team_created: "Equipe criada",
  team_renamed: "Equipe renomeada", team_deleted: "Equipe excluída", ticket_created: "Ticket criado",
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
  const results = $("#results"), statusFilter = $("#status-filter");
  const state = { users: [], teams: null, tickets: [], view: null, ticket: null, editingUser: null, editingTeam: null, passwordUser: null };

  const closeNav = () => document.body.classList.remove("nav-open");
  $("#nav-toggle").onclick = () => document.body.classList.toggle("nav-open");
  $("[data-home]").onclick = () => { closeView(); closeNav(); q.focus(); };

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

  async function loadTeams(force) {
    if (!state.teams || force) state.teams = (await api("/teams")).teams;
    return state.teams;
  }
  const teamName = (id) => state.teams?.find((t) => t.id === id)?.name || "—";
  async function fillTeams(select, selected) {
    select.innerHTML = `<option value="">Carregando…</option>`;
    const teams = await loadTeams();
    select.innerHTML = teams.length
      ? teams.map((t) => `<option value="${esc(t.id)}" ${t.id === selected ? "selected" : ""}>${esc(t.name)}</option>`).join("")
      : `<option value="">Crie uma equipe primeiro</option>`;
  }

  /* ---------- Listas ---------- */
  const views = {
    tickets: {
      title: "Tickets",
      statusFilter: true,
      async render() {
        await loadTeams();
        const params = new URLSearchParams({ limit: "300" });
        if (statusFilter.value) params.set("status", statusFilter.value);
        const { tickets } = await api(`/tickets?${params}`);
        state.tickets = tickets;
        if (!tickets.length) return `<p class="empty">Nenhum ticket encontrado. Use "Novo ticket" para registrar um atendimento.</p>`;
        const head = ["Título", "Categoria", "Status", "Base de conhecimento", ...(isAdmin() ? ["Equipe"] : []), "Aberto em"];
        return table(head, tickets.map((t) =>
          `<tr class="clickable" data-ticket="${esc(t.id)}"><td>${esc(t.title)}</td><td>${esc(t.category || "—")}</td>
           <td>${badge(STATUS[t.status] || t.status, t.status)}</td><td>${t.successful ? badge("Com êxito", "ok") : "—"}</td>
           ${isAdmin() ? `<td>${esc(teamName(t.team_id))}</td>` : ""}<td>${fmtDate(t.created_at)}</td></tr>`));
      },
    },
    users: {
      title: "Usuários",
      async render() {
        const { users } = await api("/users");
        state.users = users;
        if (!users.length) return `<p class="empty">Nenhum usuário cadastrado.</p>`;
        return table(["Nome", "E-mail", "Equipe", "Perfil", "Situação", "Criado em", ""], users.map((u) => {
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
        const teams = await loadTeams(true);
        if (!teams.length) return `<p class="empty">Nenhuma equipe criada. Use o botão + para criar a primeira.</p>`;
        return table(["Nome", "Criada em", ""], teams.map((t) =>
          `<tr><td>${esc(t.name)}</td><td>${fmtDate(t.created_at)}</td>
           <td class="row-actions"><button class="mini" data-act="edit-team" data-id="${esc(t.id)}">Renomear</button>
           <button class="del" data-act="del-team" data-id="${esc(t.id)}">Excluir</button></td></tr>`));
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
      title: "Estatísticas das sugestões",
      noFilter: true,
      async render() {
        const s = await api("/suggestions/stats");
        const pct = s.helpful_rate == null ? "—" : `${Math.round(s.helpful_rate * 100)}%`;
        const card = (label, value, hint = "") => `<div class="stat"><span>${label}</span><strong>${value}</strong>${hint ? `<small>${hint}</small>` : ""}</div>`;
        return `<div class="stats">
          ${card("Consultas feitas", s.queries)}
          ${card("Consultas com feedback", s.with_feedback)}
          ${card("Sugestões que ajudaram", pct, `${s.helpful} de ${s.with_feedback} com feedback`)}
          ${card("Base de conhecimento", s.knowledge_base, `tickets com êxito de ${s.tickets} no total`)}
        </div>
        <h3 class="sub">Soluções que mais ajudaram</h3>
        ${s.top_tickets.length
          ? `<ol class="top-tickets">${s.top_tickets.map((t) => `<li><button class="link" data-ticket="${esc(t.id)}">${esc(t.title)}</button> — ${t.helpful_count} voto(s)</li>`).join("")}</ol>`
          : `<p class="empty">Ainda sem feedback.</p>`}`;
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
    statusFilter.hidden = !views[name].statusFilter;
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
  statusFilter.addEventListener("change", () => openView("tickets"));

  /* ---------- Sugestões: POST /suggestions ---------- */
  let lastQuery = "";
  $("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = q.value.trim();
    if (!text) return q.focus();
    lastQuery = text;
    closeView();
    results.hidden = false;
    setHomeLayout();
    results.innerHTML = `<p class="loading">Procurando soluções em tickets resolvidos…</p>`;
    try {
      renderResults(await api("/suggestions", { method: "POST", body: { query: text } }));
    } catch (ex) {
      results.innerHTML = `<p class="error">${esc(ex.message)}</p>`;
    }
  });

  function renderResults({ query_id, results: items }) {
    if (!items.length) {
      results.innerHTML = `<div class="empty">
        <p>Nenhuma solução parecida na base de conhecimento.</p>
        <button class="ghost" type="button" data-new="ticket" data-prefill>Registrar como novo ticket</button></div>`;
      return;
    }
    results.innerHTML = `<p class="results-head">${items.length} solução(ões) de tickets resolvidos com êxito</p>` + items.map((r) => `
      <article class="card" data-qid="${esc(query_id)}" data-tid="${esc(r.ticket_id)}">
        <header><h3>${esc(r.title)}</h3><span class="score">${Math.round(r.score * 100)}% de correspondência</span></header>
        <p class="meta">${[r.category, r.product, r.resolved_at && `resolvido em ${fmtDate(r.resolved_at)}`].filter(Boolean).map(esc).join(" · ") || "&nbsp;"}</p>
        <div class="resolution">${esc(r.resolution)}</div>
        <footer>
          <span class="hint">Essa solução ajudou?</span>
          <button class="chip" type="button" data-fb="1">Ajudou</button>
          <button class="chip" type="button" data-fb="0">Não ajudou</button>
          <button class="link" type="button" data-ticket="${esc(r.ticket_id)}">Ver ticket</button>
        </footer>
      </article>`).join("");
  }

  results.addEventListener("click", async (e) => {
    const fb = e.target.closest("[data-fb]");
    if (!fb) return;
    const card = fb.closest(".card");
    const helpful = fb.dataset.fb === "1";
    try {
      await api(`/suggestions/${encodeURIComponent(card.dataset.qid)}/feedback`, { method: "POST", body: { ticket_id: card.dataset.tid, helpful } });
      card.querySelectorAll("[data-fb]").forEach((b) => b.classList.toggle("on", b === fb));
      toast("Obrigado pelo retorno!");
    } catch (ex) { toast(ex.message, true); }
  });

  /* ---------- Menu "+" e atalhos ---------- */
  const menu = $("#menu"), plus = $("#plus");
  const setMenu = (open) => { menu.hidden = !open; plus.setAttribute("aria-expanded", open); };
  plus.onclick = (e) => { e.stopPropagation(); setMenu(menu.hidden); };
  document.addEventListener("click", () => setMenu(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && setMenu(false));

  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));

  document.addEventListener("click", (e) => {
    const nb = e.target.closest("[data-new]");
    if (nb) {
      setMenu(false);
      closeNav();
      return openNew(nb.dataset.new, nb.hasAttribute("data-prefill"));
    }
    const tk = e.target.closest("[data-ticket]");
    if (tk) openTicket(tk.dataset.ticket);
  });

  function openNew(kind, prefill) {
    if (kind === "ticket") return openTicketForm(null, prefill ? lastQuery : "");
    if (kind === "user") return openUserForm(null);
    if (kind === "team") return openTeamForm(null);
    if (kind === "import") return openImport();
    if (kind === "account") {
      $("#form-account").reset();
      $("#account-error").textContent = "";
      return $("#dlg-account").showModal();
    }
  }

  /* ---------- Tickets ---------- */
  const dlgTicket = $("#dlg-ticket"), dlgView = $("#dlg-view");
  const canWrite = (t) => isAdmin() || t.team_id === me.profile?.team_id;

  async function openTicket(id) {
    $("#view-title").textContent = "Carregando…";
    $("#view-body").innerHTML = "";
    $("#view-edit").hidden = true;
    $("#view-delete").hidden = true;
    if (!dlgView.open) dlgView.showModal();
    try {
      const [{ ticket: t }] = await Promise.all([api(`/tickets/${encodeURIComponent(id)}`), loadTeams()]);
      state.ticket = t;
      $("#view-title").textContent = t.title;
      const item = (label, value) => `<div><dt>${label}</dt><dd>${value}</dd></div>`;
      $("#view-body").innerHTML = `
        <dl class="details">
          ${item("Status", badge(STATUS[t.status] || t.status, t.status))}
          ${item("Base de conhecimento", t.successful ? badge("Com êxito", "ok") : "Não")}
          ${item("Equipe", esc(teamName(t.team_id)))}
          ${item("Categoria", esc(t.category || "—"))}
          ${item("Produto", esc(t.product || "—"))}
          ${item("Tags", esc((t.tags || []).join(", ") || "—"))}
          ${item("Nota do cliente", esc(t.satisfaction ?? "—"))}
          ${item("Reaberturas", esc(t.reopen_count ?? 0))}
          ${item("Feedback das sugestões", `${t.helpful_count} ajudou · ${t.not_helpful_count} não ajudou`)}
          ${item("Aberto em", fmtDate(t.created_at))}
          ${item("Resolvido em", fmtDate(t.resolved_at))}
          ${item("Origem", t.source === "import" ? `Importado${t.external_id ? ` (#${esc(t.external_id)})` : ""}` : "Manual")}
        </dl>
        <h4>Descrição</h4><div class="resolution">${esc(t.description)}</div>
        <h4>Solução</h4><div class="resolution">${esc(t.resolution || "—")}</div>`;
      $("#view-edit").hidden = !canWrite(t);
      $("#view-delete").hidden = !isAdmin();
    } catch (ex) {
      $("#view-title").textContent = "Erro";
      $("#view-body").innerHTML = `<p class="error">${esc(ex.message)}</p>`;
    }
  }

  $("#view-edit").onclick = () => { dlgView.close(); openTicketForm(state.ticket); };
  $("#view-delete").onclick = async () => {
    const t = state.ticket;
    if (!t || !confirm(`Excluir o ticket "${t.title}"? Essa ação não pode ser desfeita.`)) return;
    try {
      await api(`/tickets/${encodeURIComponent(t.id)}`, { method: "DELETE" });
      dlgView.close();
      toast("Ticket excluído.");
      reloadView("tickets");
    } catch (ex) { toast(ex.message, true); }
  };

  async function openTicketForm(t, description = "") {
    const f = $("#form-ticket");
    f.reset();
    state.ticket = t;
    $("#ticket-error").textContent = "";
    $("#ticket-title").textContent = t ? "Editar ticket" : "Novo ticket";
    f.title.value = t?.title || "";
    f.description.value = t?.description || description;
    f.category.value = t?.category || "";
    f.product.value = t?.product || "";
    f.tags.value = (t?.tags || []).join(", ");
    f.status.value = t?.status || "open";
    f.satisfaction.value = t?.satisfaction ?? "";
    f.resolution.value = t?.resolution || "";
    dlgTicket.showModal();
    if (isAdmin()) {
      try { await fillTeams(f.team_id, t?.team_id || me.profile?.team_id); } catch (ex) { $("#ticket-error").textContent = ex.message; }
    }
  }

  $("#form-ticket").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#ticket-error");
    err.textContent = "";
    if (f.status.value === "resolved" && !f.resolution.value.trim()) {
      err.textContent = "Descreva a solução para marcar como resolvido.";
      return;
    }
    const payload = {
      title: f.title.value, description: f.description.value, category: f.category.value, product: f.product.value,
      tags: f.tags.value, status: f.status.value, resolution: f.resolution.value,
      satisfaction: f.satisfaction.value === "" ? null : Number(f.satisfaction.value),
    };
    if (isAdmin() && f.team_id.value) payload.team_id = f.team_id.value;
    const editing = state.ticket;
    try {
      const { ticket } = editing
        ? await api(`/tickets/${encodeURIComponent(editing.id)}`, { method: "PATCH", body: payload })
        : await api("/tickets", { method: "POST", body: payload });
      dlgTicket.close();
      toast(ticket.successful ? "Ticket salvo e incluído na base de conhecimento." : "Ticket salvo.");
      reloadView("tickets");
    } catch (ex) { err.textContent = ex.message; }
  });

  /* ---------- Usuários ---------- */
  const dlgUser = $("#dlg-user");

  async function openUserForm(u) {
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
    try { await fillTeams(f.team_id, u?.team_id); } catch (ex) { $("#user-error").textContent = ex.message; }
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
      dlgUser.close();
      toast(u ? "Usuário atualizado." : "Usuário cadastrado.");
      openView("users");
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
        case "del-team":
          if (!confirm(`Excluir a equipe ${team.name}?`)) return;
          await api(`/teams/${encodeURIComponent(id)}`, { method: "DELETE" });
          state.teams = null;
          toast("Equipe excluída.");
          return openView("teams");
      }
    } catch (ex) { toast(ex.message, true); }
  });

  /* ---------- Importação: POST /tickets/import ---------- */
  async function openImport() {
    const f = $("#form-import");
    f.reset();
    $("#import-error").textContent = "";
    $("#import-result").innerHTML = "";
    $("#dlg-import").showModal();
    try { await fillTeams(f.team_id, me.profile?.team_id); } catch (ex) { $("#import-error").textContent = ex.message; }
  }

  $("#form-import").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#import-error"), out = $("#import-result"), btn = $("#import-btn");
    err.textContent = "";
    out.innerHTML = "";
    btn.disabled = true;
    btn.textContent = "Importando…";
    try {
      const r = await api("/tickets/import", { method: "POST", form: new FormData(e.target) });
      out.innerHTML = `<p>${r.imported} ticket(s) importado(s), ${r.skipped} linha(s) ignorada(s).</p>` +
        (r.errors.length ? `<ul class="import-errors">${r.errors.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : "");
      reloadView("tickets");
    } catch (ex) { err.textContent = ex.message; }
    btn.disabled = false;
    btn.textContent = "Importar";
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
