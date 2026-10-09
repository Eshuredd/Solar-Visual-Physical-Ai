/* DeepDrishti Solar Twin — zero-build frontend for the runnable MVP. */

const state = {
  view: "portfolio",
  selectedSiteId: "site-001",
  portfolio: null,
  sites: [],
  site: null,
  anomalies: [],
  inspections: [],
  tasks: [],
  assets: null,
  drawerId: null,
  drawerData: null,
  inverterPanel: null,
  modal: null,
  uploadFile: null,
  twinView: "map",
  zoom: 1.14,
  filters: { priority: "All", status: "Active", type: "All", query: "" },
  layers: { equipment: true, anomalies: true, labels: true },
  commandOpen: false,
  commandQuery: "",
  loading: true,
};

const navItems = [
  ["portfolio", "Portfolio", "grid"],
  ["site", "Site dashboard", "activity"],
  ["twin", "Digital Twin", "map"],
  ["inspections", "Inspections", "scan"],
  ["tasks", "Tasks", "clipboard"],
  ["analytics", "Analytics", "chart"],
  ["assets", "Assets", "box"],
  ["field", "Field app", "phone"],
];

const iconPaths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  activity: '<path d="M3 12h4l2-5 4 10 2-5h6"/>',
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"/><path d="M9 3v15M15 6v15"/>',
  scan: '<path d="M3 7V4a1 1 0 0 1 1-1h3M17 3h3a1 1 0 0 1 1 1v3M21 17v3a1 1 0 0 1-1 1h-3M7 21H4a1 1 0 0 1-1-1v-3"/><path d="M7 12h10M12 7v10"/>',
  clipboard: '<path d="M9 5h6M9 3h6v4H9z"/><rect x="5" y="5" width="14" height="16" rx="2"/><path d="m9 13 2 2 4-4"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  box: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 8 9 5 9-5M3 8v9l9 5 9-5V8M12 13v9"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  alert: '<path d="M12 3 2.8 19a1 1 0 0 0 .9 1.5h16.6a1 1 0 0 0 .9-1.5L12 3Z"/><path d="M12 9v4M12 17h.01"/>',
  bolt: '<path d="m13 2-9 12h7l-1 8 9-12h-7l1-8Z"/>',
  money: '<circle cx="12" cy="12" r="9"/><path d="M16 8h-5a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H8M12 6v12"/>',
  panel: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M3 9h18M9 5v12M15 5v12M8 21h8M12 17v4"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/>',
  filter: '<path d="M4 5h16M7 12h10M10 19h4"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3" cy="6" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="18" r="1"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16M15 4v16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 19h16"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5L15 12l-3-3 2.7-2.7Z"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  layers: '<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
};

function icon(name, className = "") {
  return `<span class="icon-inline ${className}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.info}</svg></span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const config = { ...options };
  config.headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || payload.message || message;
    } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

function formatCurrency(value, compact = true) {
  const number = Number(value || 0);
  if (compact) {
    if (Math.abs(number) >= 1e7) return `₹${(number / 1e7).toFixed(2)} Cr`;
    if (Math.abs(number) >= 1e5) return `₹${(number / 1e5).toFixed(2)} L`;
    if (Math.abs(number) >= 1e3) return `₹${(number / 1e3).toFixed(1)}K`;
  }
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(number);
}

function formatNumber(value, digits = 0) {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(Number(value || 0));
}

function knownCount(value) { return value == null ? "Unknown" : formatNumber(value); }

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

function className(value) {
  return String(value || "").toLowerCase().replaceAll(" ", "-");
}

function priorityColor(priority) {
  return { Critical: "#ff686d", High: "#ff8a5b", Medium: "#ffc166", Low: "#79a7ff" }[priority] || "#9dff75";
}

function statusPill(status) {
  return `<span class="status-pill ${className(status)}">${escapeHtml(status)}</span>`;
}

function priorityPill(priority) {
  return `<span class="priority-pill ${className(priority)}">${escapeHtml(priority)}</span>`;
}

function setPage(title, breadcrumb) {
  document.getElementById("page-title").textContent = title;
  document.getElementById("breadcrumb").textContent = breadcrumb;
}

async function init() {
  try {
    const [portfolio, sites] = await Promise.all([api("/api/portfolio"), api("/api/sites")]);
    state.portfolio = portfolio;
    state.sites = sites;
    await loadSiteData(state.selectedSiteId);
    state.loading = false;
    populateSitePicker();
    renderNav();
    render();
    document.getElementById("app").hidden = false;
    setTimeout(() => document.getElementById("boot-screen").classList.add("done"), 180);
  } catch (error) {
    document.getElementById("boot-screen").innerHTML = `<div class="boot-copy"><strong>Unable to load the Solar Twin</strong><span>${escapeHtml(error.message)}</span><button class="button primary" onclick="location.reload()">Retry</button></div>`;
  }
}

async function loadSiteData(siteId) {
  state.selectedSiteId = siteId;
  const [site, anomalies, inspections, tasks, assets] = await Promise.all([
    api(`/api/sites/${siteId}`),
    api(`/api/sites/${siteId}/anomalies`),
    api(`/api/sites/${siteId}/inspections`),
    api(`/api/tasks?site_id=${encodeURIComponent(siteId)}`),
    api(`/api/sites/${siteId}/assets`),
  ]);
  state.site = site;
  state.anomalies = anomalies;
  state.inspections = inspections;
  state.tasks = tasks;
  state.assets = assets;
}

function populateSitePicker() {
  const picker = document.getElementById("site-picker");
  picker.innerHTML = state.sites.map(site => `<option value="${site.id}" ${site.id === state.selectedSiteId ? "selected" : ""}>${escapeHtml(site.name)}</option>`).join("");
}

function renderNav() {
  const activeTasks = state.tasks.filter(task => !["Completed", "Verified"].includes(task.status)).length;
  document.getElementById("primary-nav").innerHTML = navItems.map(([view, label, iconName]) => `
    <button class="nav-item ${state.view === view ? "active" : ""}" onclick="navigate('${view}')">
      <span class="nav-icon">${icon(iconName)}</span>
      <span>${label}</span>
      ${view === "tasks" && activeTasks ? `<span class="nav-badge">${activeTasks}</span>` : ""}
    </button>
  `).join("");
}

function navigate(view) {
  state.view = view;
  state.drawerId = null;
  state.drawerData = null;
  state.modal = null;
  state.inverterPanel = null;
  state.commandOpen = false;
  renderNav();
  render();
  document.getElementById("content")?.focus();
  toggleSidebar(false);
}

async function selectSite(siteId) {
  try {
    await loadSiteData(siteId);
    state.filters = { priority: "All", status: "Active", type: "All", query: "" };
    state.drawerId = null;
    state.drawerData = null;
    state.inverterPanel = null;
    populateSitePicker();
    renderNav();
    render();
    showToast(`Active site changed to ${state.site.name}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function render() {
  const content = document.getElementById("content");
  if (!content) return;
  const views = {
    portfolio: renderPortfolio,
    site: renderSiteDashboard,
    twin: renderTwin,
    inspections: renderInspections,
    tasks: renderTasks,
    analytics: renderAnalytics,
    assets: renderAssets,
    field: renderField,
  };
  content.innerHTML = (views[state.view] || renderPortfolio)();
  renderOverlays();
  populateSitePicker();
}

function kpiCard(label, value, meta, iconName, trend = "", trendType = "up") {
  return `
    <article class="kpi-card">
      <div class="kpi-top"><span class="kpi-label">${label}</span><span class="kpi-icon">${icon(iconName)}</span></div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-meta">${trend ? `<span class="trend-${trendType}">${trend}</span>` : ""}<span>${meta}</span></div>
    </article>
  `;
}

function renderPortfolio() {
  setPage("Portfolio overview", "Solar intelligence / Portfolio");
  const k = state.portfolio.kpis;
  return `
    <div class="page-stack">
      <section class="page-header">
        <div>
          <h2>Physical condition, portfolio-wide.</h2>
          <p>One operational view of visual findings, energy impact, remediation and asset history across every solar site.</p>
        </div>
        <div class="page-actions">
          <button class="button" onclick="resetDemo()">${icon("refresh")} Reset demo</button>
          <button class="button primary" onclick="openInspectionModal()">${icon("upload")} Run AI inspection</button>
        </div>
      </section>

      <section class="kpi-grid">
        ${kpiCard("Annual revenue at risk", formatCurrency(k.annual_revenue_loss), "Active visual findings", "money", "↓ 11.4%", "up")}
        ${kpiCard("Portfolio capacity", `${formatNumber(k.capacity_mw, 1)} MWdc`, `${k.sites} operating sites`, "panel", "+18.2 MW", "up")}
        ${kpiCard("Affected power", `${formatNumber(k.affected_kw, 1)} kW`, `${formatNumber(k.power_loss_pct, 3)}% of portfolio`, "bolt", "Needs action", "down")}
        ${kpiCard("Open findings", formatNumber(k.active_findings), "AI + field observations", "alert", `${k.overdue_tasks} overdue`, "down")}
        ${kpiCard("Annual energy loss", `${formatNumber(k.annual_kwh_loss / 1000, 1)} MWh`, "Modeled recoverable output", "activity", "↑ ₹147K recovered", "up")}
      </section>

      <section class="dashboard-grid">
        <article class="panel">
          <header class="panel-header"><div><h3>Portfolio loss and recovery</h3><p>Modeled annual revenue at risk versus verified recovery</p></div><span class="tag success">Live aggregation</span></header>
          <div class="panel-body">${lineChart(state.portfolio.trend)}</div>
        </article>
        <article class="panel">
          <header class="panel-header"><div><h3>Loss by anomaly type</h3><p>Ranked by annual monetary impact</p></div><button class="button small" onclick="navigate('analytics')">View analytics</button></header>
          <div class="panel-body">${barChart(state.portfolio.loss_by_type)}</div>
        </article>
      </section>

      <section>
        <div class="page-header" style="align-items:center;margin-bottom:12px">
          <div><h2 style="font-size:18px">Solar sites</h2><p>Open a site to move from portfolio risk to equipment-level evidence.</p></div>
          <button class="button small" onclick="navigate('site')">Open active site ${icon("arrow")}</button>
        </div>
        <div class="site-grid">${state.portfolio.sites.map(siteCard).join("")}</div>
      </section>

      <section class="dashboard-grid equal">
        <article class="panel">
          <header class="panel-header"><div><h3>Operations activity</h3><p>Recent AI, inspection and field-work events</p></div><span class="status-pill healthy">Synchronized</span></header>
          <div class="panel-body activity-list">${state.portfolio.activity.map(activityRow).join("")}</div>
        </article>
        <article class="panel">
          <header class="panel-header"><div><h3>Recommended next actions</h3><p>Generated from severity, recoverable loss and task status</p></div>${icon("bolt")}</header>
          <div class="panel-body">
            ${recommendationRow("Restore the critical string anomaly", "SRP-B1-R10-M21", "Estimated recoverable annual impact", "₹41.2K", "openAnomaly('an-002')")}
            ${recommendationRow("Clear vegetation near C1-R17", "Overdue field task", "Safety + shading risk", "Critical", "navigate('tasks')")}
            ${recommendationRow("Review new thermal candidates", "AI review queue", "Human validation required", "6 items", "openInspectionModal()")}
          </div>
        </article>
      </section>
    </div>
  `;
}

function lineChart(data) {
  const width = 760, height = 210, left = 48, right = 22, top = 14, bottom = 34;
  const max = Math.max(...data.flatMap(d => [d.loss, d.recovered])) * 1.1;
  const x = i => left + i * ((width - left - right) / (data.length - 1));
  const y = value => top + (height - top - bottom) * (1 - value / max);
  const lossPoints = data.map((d, i) => `${x(i)},${y(d.loss)}`).join(" ");
  const recoveredPoints = data.map((d, i) => `${x(i)},${y(d.recovered)}`).join(" ");
  const area = `${left},${height-bottom} ${lossPoints} ${x(data.length-1)},${height-bottom}`;
  const grid = [0, .25, .5, .75, 1].map(t => {
    const yy = top + (height-top-bottom) * t;
    const label = formatCurrency(max * (1-t));
    return `<line class="grid-line" x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}"/><text class="axis-label" x="2" y="${yy+3}">${label}</text>`;
  }).join("");
  const labels = data.map((d, i) => `<text class="axis-label" x="${x(i)-9}" y="${height-8}">${d.month}</text>`).join("");
  return `
    <div class="chart-legend"><span class="legend-key"><i class="legend-dot"></i>Revenue at risk</span><span class="legend-key"><i class="legend-dot cyan"></i>Verified recovery</span></div>
    <svg class="line-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <defs><linearGradient id="lossArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9dff75" stop-opacity=".18"/><stop offset="1" stop-color="#9dff75" stop-opacity="0"/></linearGradient></defs>
      ${grid}
      <polygon class="area-loss" points="${area}"/>
      <polyline class="line-loss" points="${lossPoints}"/>
      <polyline class="line-recovered" points="${recoveredPoints}"/>
      ${data.map((d, i) => `<circle cx="${x(i)}" cy="${y(d.loss)}" r="3.5" fill="#9dff75" stroke="#10251c" stroke-width="2"/>`).join("")}
      ${labels}
    </svg>
  `;
}

function barChart(rows) {
  const max = Math.max(1, ...rows.map(row => Number(row.value)));
  return `<div class="bar-list">${rows.map(row => `
    <div class="bar-row">
      <div class="bar-head"><span>${escapeHtml(row.label)}</span><strong>${formatCurrency(row.value)} · ${row.count}</strong></div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, Number(row.value)/max*100)}%"></div></div>
    </div>`).join("")}</div>`;
}

function siteCard(site) {
  return `
    <article class="site-card ${site.id === state.selectedSiteId ? "selected" : ""}" onclick="selectSite('${site.id}').then(()=>navigate('site'))">
      <div class="site-card-top">
        <div><h4>${escapeHtml(site.name)}</h4><div class="region">${escapeHtml(site.region)} · ${escapeHtml(site.owner)}</div></div>
        ${statusPill(site.status)}
      </div>
      <div class="mini-farm">${Array.from({length:4}, () => `<span class="mini-block">${"<i></i>".repeat(4)}</span>`).join("")}</div>
      <div class="site-stats">
        <span class="site-stat"><span>Capacity</span><strong>${formatNumber(site.capacity_mw,1)} MW</strong></span>
        <span class="site-stat"><span>Findings</span><strong>${site.findings || 0}</strong></span>
        <span class="site-stat"><span>At risk</span><strong>${formatCurrency(site.annual_revenue_loss || 0)}</strong></span>
      </div>
      <div class="site-card-footer"><span>Last inspection ${formatDate(site.last_inspection)}</span><span>${formatNumber(site.performance_ratio,1)}% PR →</span></div>
    </article>
  `;
}

function activityRow(item) {
  return `<div class="activity-row"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p><span class="activity-time">${escapeHtml(item.time)}</span></div>`;
}

function recommendationRow(title, subtitle, label, value, action) {
  return `<button class="finding-row" style="grid-template-columns:9px minmax(0,1fr) auto;padding-left:0;padding-right:0" onclick="${action}"><i class="finding-dot" style="background:var(--lime)"></i><span><strong>${title}</strong><p>${subtitle}</p><span class="finding-meta"><span>${label}</span></span></span><span class="finding-impact"><strong>${value}</strong><span>Open →</span></span></button>`;
}

function renderSiteDashboard() {
  setPage(state.site.name, `Solar intelligence / ${state.site.region}`);
  const activeTasks = state.tasks.filter(t => !["Completed", "Verified"].includes(t.status));
  const inverterAlerts = state.site.inverters.filter(inverter => ["Critical", "Watch"].includes(inverter.status)).length;
  return `
    <div class="page-stack">
      <section class="page-header">
        <div>
          <div class="chip-row" style="margin-bottom:9px">${statusPill(state.site.status)}<span class="tag">${escapeHtml(state.site.region)}</span><span class="tag">${formatNumber(state.site.capacity_mw,1)} MWdc</span></div>
          <h2>${escapeHtml(state.site.name)}</h2>
          <p>${escapeHtml(state.site.owner)} · Commissioned ${formatDate(state.site.commissioned)} · Last inspection ${formatDate(state.site.last_inspection)}</p>
        </div>
        <div class="page-actions">
          <button class="button" onclick="navigate('assets')">${icon("box")} Equipment</button>
          <button class="button primary" onclick="navigate('twin')">${icon("map")} Open Digital Twin</button>
        </div>
      </section>

      <section class="metric-strip">
        <div class="stat-card"><span>Annual revenue at risk</span><strong>${formatCurrency(state.site.annual_revenue_loss)}</strong></div>
        <div class="stat-card"><span>Affected DC power</span><strong>${formatNumber(state.site.affected_kw,1)} kW</strong></div>
        <div class="stat-card"><span>Open findings</span><strong>${state.site.findings}</strong></div>
        <div class="stat-card"><span>Performance ratio</span><strong>${formatNumber(state.site.performance_ratio,1)}%</strong></div>
      </section>

      <section class="dashboard-grid">
        <article class="panel">
          <header class="panel-header"><div><h3>Inverter relative yield</h3><p>Irradiance and temperature-normalized · synthetic demo telemetry</p></div><span class="tag warning">${inverterAlerts} inverter${inverterAlerts===1?'':'s'} to review</span></header>
          <div class="panel-body">
            ${state.site.inverters.length ? `<div class="heatmap-grid">${state.site.inverters.map(inverter => {
              const value = inverter.yield;
              const cls = value == null ? "heat-empty" : value >= 95 ? "heat-excellent" : value >= 90 ? "heat-good" : value >= 75 ? "heat-watch" : "heat-critical";
              return `<button class="heat-cell ${cls}" onclick="openInverter('${inverter.inverter_id}')" aria-label="Open ${escapeHtml(inverter.id)} monitoring details"><span>${escapeHtml(inverter.id)}</span><strong>${value == null ? 'No data' : `${formatNumber(value,1)}%`}</strong><small>${escapeHtml(inverter.status)}</small></button>`;
            }).join("")}</div>` : `<div class="empty-state"><div><strong>No inverter data</strong><p>No monitored inverters are configured for this site.</p></div></div>`}
            <div class="synthetic-notice">${icon("info")} Simulated readings for product demonstration only — not live SCADA data or a fault diagnosis.</div>
          </div>
        </article>
        <article class="panel">
          <header class="panel-header"><div><h3>Operations pulse</h3><p>Tasks requiring action at this site</p></div><button class="button small" onclick="navigate('tasks')">All tasks</button></header>
          <div class="panel-body task-list-compact">
            ${activeTasks.slice(0,4).map(task => `<button class="finding-row" onclick="openTaskUpdate('${task.id}')"><i class="finding-dot" style="background:${priorityColor(task.priority)}"></i><span><strong>${escapeHtml(task.title)}</strong><p>${escapeHtml(task.owner)} · due ${formatDate(task.due_date)}</p></span><span>${statusPill(task.status)}</span></button>`).join("") || `<div class="empty-state"><div><div class="empty-icon">✓</div><strong>No active tasks</strong></div></div>`}
          </div>
        </article>
      </section>

      <section class="dashboard-grid equal">
        <article class="panel">
          <header class="panel-header"><div><h3>Latest inspections</h3><p>All inspection layers remain attached to the same physical site model</p></div><button class="button small" onclick="openInspectionModal()">Run inspection</button></header>
          <div class="panel-body flush table-wrap">
            <table class="data-table"><thead><tr><th>Inspection</th><th>Status</th><th>Findings</th><th>Impact</th></tr></thead><tbody>
              ${state.inspections.slice(0,4).map(ins => `<tr><td><span class="table-title"><strong>${escapeHtml(ins.name)}</strong><small>${formatDate(ins.captured_at)} · ${escapeHtml(ins.source)}</small></span></td><td>${statusPill(ins.status === "Reviewed" ? "Verified" : ins.status)}</td><td>${ins.findings}</td><td>${formatCurrency(ins.annual_revenue_loss)}</td></tr>`).join("")}
            </tbody></table>
          </div>
        </article>
        <article class="panel">
          <header class="panel-header"><div><h3>Site information</h3><p>Persistent metadata used by the Digital Twin and impact engine</p></div>${icon("info")}</header>
          <div class="panel-body">
            <div class="detail-metrics">
              <div class="detail-metric"><span>Owner</span><strong style="font-size:12px">${escapeHtml(state.site.owner)}</strong></div>
              <div class="detail-metric"><span>Coordinates</span><strong style="font-size:12px">${state.site.latitude.toFixed(3)}, ${state.site.longitude.toFixed(3)}</strong></div>
              <div class="detail-metric"><span>Mapped modules</span><strong>${knownCount(state.assets.hierarchy.modules)}</strong></div>
            </div>
            <div class="recommendation" style="margin-top:14px"><strong>Electrical and visual evidence remain separate</strong><p>The inverter indicators use synthetic electrical telemetry. Thermal and visual findings stay linked to physical assets independently; a reviewer must establish evidence before creating a diagnosis or dispatching field work.</p></div>
          </div>
        </article>
      </section>
    </div>
  `;
}

function anomalyTypes() {
  return [...new Set(state.anomalies.map(item => item.anomaly_type))].sort();
}

function filteredAnomalies() {
  const q = state.filters.query.trim().toLowerCase();
  return state.anomalies.filter(item => {
    if (state.filters.priority !== "All" && item.priority !== state.filters.priority) return false;
    if (state.filters.status === "Active" && item.status === "Resolved") return false;
    if (state.filters.status !== "All" && state.filters.status !== "Active" && item.status !== state.filters.status) return false;
    if (state.filters.type !== "All" && item.anomaly_type !== state.filters.type) return false;
    if (q && !`${item.asset_id} ${item.anomaly_type} ${item.block_name}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function setFilter(key, value) {
  state.filters[key] = value;
  render();
}

function setSearchFilter(value) {
  state.filters.query = value;
  render();
  const input = document.querySelector(".search-input");
  if (input) {
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

function toggleLayer(key) {
  state.layers[key] = !state.layers[key];
  render();
}

function setTwinView(view) {
  state.twinView = view;
  render();
}

function setZoom(value) {
  state.zoom = Math.max(.78, Math.min(1.75, Number(value)));
  render();
}

function renderTwin() {
  setPage("Digital Twin", `Solar intelligence / ${state.site.name} / Live asset map`);
  const items = filteredAnomalies();
  const typeCounts = Object.fromEntries(anomalyTypes().map(type => [type, state.anomalies.filter(a => a.anomaly_type === type && a.status !== "Resolved").length]));
  const statusCounts = Object.fromEntries(["Detected","Verified","Assigned","In Progress","Resolved"].map(status => [status, state.anomalies.filter(a => a.status === status).length]));
  const priorityCounts = Object.fromEntries(["Critical","High","Medium","Low"].map(priority => [priority, state.anomalies.filter(a => a.priority === priority && a.status !== "Resolved").length]));
  return `
    <div class="twin-shell">
      <aside class="twin-filters">
        <div class="twin-sidebar-header"><div><h3>Inspection layers</h3><span>${state.inspections.length} reports available</span></div>${icon("layers")}</div>
        <div class="filter-section">
          <div class="filter-label"><span>Priority</span><button class="button small" onclick="setFilter('priority','All')">Clear</button></div>
          ${["All","Critical","High","Medium","Low"].map(priority => `<button class="filter-option ${state.filters.priority===priority?"active":""}" onclick="setFilter('priority','${priority}')"><i class="filter-swatch" style="background:${priority==='All'?'#9dff75':priorityColor(priority)}"></i><span>${priority}</span><span class="filter-count">${priority==='All'?state.anomalies.filter(a=>a.status!=='Resolved').length:priorityCounts[priority]}</span></button>`).join("")}
        </div>
        <div class="filter-section">
          <div class="filter-label"><span>Status</span></div>
          ${["Active","Detected","Verified","Assigned","In Progress","Resolved","All"].map(status => `<button class="filter-option ${state.filters.status===status?"active":""}" onclick="setFilter('status','${status}')"><span>${status}</span><span class="filter-count">${status==='Active'?state.anomalies.filter(a=>a.status!=='Resolved').length:status==='All'?state.anomalies.length:(statusCounts[status]||0)}</span></button>`).join("")}
        </div>
        <div class="filter-section">
          <div class="filter-label"><span>Anomaly type</span></div>
          <button class="filter-option ${state.filters.type==='All'?"active":""}" onclick="setFilter('type','All')"><i class="filter-swatch"></i><span>All findings</span><span class="filter-count">${state.anomalies.length}</span></button>
          ${anomalyTypes().map((type,index) => `<button class="filter-option ${state.filters.type===type?"active":""}" data-filter-value="${escapeHtml(type)}" onclick="setFilter('type',this.dataset.filterValue)"><i class="filter-swatch" style="background:${["#ff686d","#ff8a5b","#ffc166","#79a7ff","#5be7d4","#b591ff"][index%6]}"></i><span>${escapeHtml(type)}</span><span class="filter-count">${typeCounts[type]}</span></button>`).join("")}
        </div>
        <div class="filter-section">
          <div class="filter-label"><span>Map layers</span></div>
          ${[["equipment","Equipment geometry"],["anomalies","AI findings"],["labels","Asset labels"]].map(([key,label]) => `<button class="layer-toggle" onclick="toggleLayer('${key}')"><span>${label}</span><i class="switch ${state.layers[key]?"on":""}"></i></button>`).join("")}
        </div>
      </aside>

      <section class="map-column">
        <div class="map-toolbar">
          <div class="map-toolbar-left">
            <div class="view-switch"><button class="${state.twinView==='map'?'active':''}" onclick="setTwinView('map')">Map</button><button class="${state.twinView==='table'?'active':''}" onclick="setTwinView('table')">Table</button></div>
            <span class="tag">${items.length} visible</span>
            <span class="tag warning">${formatNumber(items.reduce((s,a)=>s+a.affected_kw,0),1)} kW affected</span>
          </div>
          <div class="map-toolbar-right">
            <input class="search-input" placeholder="Search asset or finding" value="${escapeHtml(state.filters.query)}" oninput="setSearchFilter(this.value)" />
            <a class="button small" href="/api/sites/${state.selectedSiteId}/export.csv">${icon("download")} Export</a>
            <button class="button primary small" onclick="openInspectionModal()">${icon("scan")} New inspection</button>
          </div>
        </div>
        ${state.twinView === "map" ? renderMapStage(items) : renderAnomalyTable(items)}
      </section>

      <aside class="twin-list">
        <div class="twin-list-header"><div><h3>Findings</h3><span>Sorted by operational priority</span></div><span class="count-pill">${items.length}</span></div>
        <div class="finding-list">${items.length ? items.map(findingRow).join("") : `<div class="empty-state"><div><div class="empty-icon">✓</div><strong>No findings match these filters</strong><p>Clear filters or run another inspection.</p></div></div>`}</div>
      </aside>
    </div>
  `;
}

function renderMapStage(items) {
  return `
    <div class="map-stage">
      ${farmMapSvg(items, state.zoom)}
      <div class="map-zoom-controls"><button onclick="setZoom(state.zoom+.14)">+</button><button onclick="setZoom(state.zoom-.14)">−</button><button onclick="setZoom(1.14)" style="font-size:10px">1:1</button></div>
      <div class="map-legend"><span class="legend-item"><i style="background:var(--red)"></i>Critical</span><span class="legend-item"><i style="background:var(--orange)"></i>High</span><span class="legend-item"><i style="background:var(--amber)"></i>Medium</span><span class="legend-item"><i style="background:var(--blue)"></i>Low</span></div>
      <div class="map-scale"><span>100 m</span><i class="scale-line"></i></div>
    </div>`;
}

function farmMapSvg(items, zoom = 1.1, mini = false) {
  const rowBlocks = [
    {id:"B1", x:75, y:90, w:350, h:170, inv:"INV-01 / 03", inverters:[1,2,3]},
    {id:"B2", x:565, y:90, w:350, h:170, inv:"INV-04 / 06", inverters:[4,5,6]},
    {id:"C1", x:75, y:355, w:350, h:170, inv:"INV-07 / 09", inverters:[7,8,9]},
    {id:"C2", x:565, y:355, w:350, h:170, inv:"INV-10 / 12", inverters:[10,11,12]},
  ];
  const selectedInverter = state.inverterPanel?.inverter?.inverter_id || state.inverterPanel?.inverterId;
  const highlightedBlocks = state.inverterPanel?.topology?.blocks || [];
  const rows = rowBlocks.map(block => {
    const rowHeight = block.h / 15;
    return `<g class="${highlightedBlocks.includes(block.id)?'electrical-block-highlight':''}">${Array.from({length:14}, (_, i) => `<rect class="solar-row" x="${block.x+8+(i%2)*4}" y="${block.y+10+i*rowHeight}" width="${block.w-35}" height="${Math.max(4,rowHeight-4)}" rx="2"/>`).join("")}
      ${block.inverters.map((number,index)=>{const id=`site-001-INV-${String(number).padStart(2,'0')}`;return `<rect class="inverter-pad selectable ${selectedInverter===id?'selected':''}" x="${block.x+block.w-23}" y="${block.y+54+index*22}" width="22" height="18" rx="4" onclick="openInverter('${id}')"><title>Open INV-${String(number).padStart(2,'0')}</title></rect>`}).join('')}
      ${state.layers.labels && !mini ? `<text class="block-label" x="${block.x}" y="${block.y-12}">${block.id}</text><text class="block-sub" x="${block.x+28}" y="${block.y-12}">${block.inv}</text><text class="inverter-label" x="${block.x+block.w-19}" y="${block.y+block.h/2+2}">INV</text>` : ""}
    </g>`;
  }).join("");
  const transform = `translate(500 310) scale(${zoom}) translate(-500 -310)`;
  let markers = "";
  if (state.layers.anomalies) {
    if (zoom < .99 && !mini) {
      const blocks = ["B1","B2","C1","C2"].map(block => ({ block, items: items.filter(i=>i.block_name===block) })).filter(group=>group.items.length);
      const centers = {B1:[250,175],B2:[740,175],C1:[250,440],C2:[740,440]};
      markers = blocks.map(group => `<g class="cluster-marker" onclick="setZoom(1.3)"><circle cx="${centers[group.block][0]}" cy="${centers[group.block][1]}" r="22"/><text x="${centers[group.block][0]}" y="${centers[group.block][1]+1}">${group.items.length}</text></g>`).join("");
    } else {
      markers = items.map(item => {
        const x = 70 + Number(item.map_x) * 8.65;
        const y = 70 + Number(item.map_y) * 4.85;
        const selected = state.drawerId === item.id ? "selected" : "";
        return `<g class="anomaly-marker ${selected}" onclick="openAnomaly('${item.id}')"><circle class="marker-${item.priority.toLowerCase()}" cx="${x}" cy="${y}" r="${mini?7:8}" stroke-width="2"/><circle cx="${x}" cy="${y}" r="2.2" fill="#07130d"/></g>`;
      }).join("");
    }
  }
  return `<svg class="farm-map" viewBox="0 0 1000 620" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equipment-level solar farm digital twin">
    <defs><linearGradient id="panelGradient" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#173f49"/><stop offset=".55" stop-color="#235d5b"/><stop offset="1" stop-color="#1a454b"/></linearGradient></defs>
    <g transform="${mini?"":""}">
      <path class="farm-boundary" d="M45 42H950L972 292 945 578H50L28 315Z"/>
      <path class="access-road" d="M50 310H950M495 55V565"/>
      <path class="access-road-center" d="M50 310H950M495 55V565"/>
    </g>
    <g transform="${mini?"":transform}">${state.layers.equipment ? rows : ""}${markers}</g>
  </svg>`;
}

function findingRow(item) {
  return `<button class="finding-row ${state.drawerId===item.id?"selected":""}" onclick="openAnomaly('${item.id}')">
    <i class="finding-dot" style="background:${priorityColor(item.priority)}"></i>
    <span><strong>${escapeHtml(item.anomaly_type)}</strong><p class="mono">${escapeHtml(item.asset_id)}</p><span class="finding-meta"><span>${item.status}</span><span>${Math.round(item.confidence*100)}% confidence</span>${item.delta_t?`<span>ΔT ${item.delta_t}°C</span>`:""}</span></span>
    <span class="finding-impact"><strong>${formatCurrency(item.annual_revenue_loss)}</strong><span>${formatNumber(item.affected_kw,2)} kW</span></span>
  </button>`;
}

function renderAnomalyTable(items) {
  return `<div class="map-stage" style="overflow:auto;background:#081711"><table class="data-table"><thead><tr><th>Asset</th><th>Anomaly</th><th>Priority</th><th>Status</th><th>Confidence</th><th>ΔT</th><th>Affected kW</th><th>Annual impact</th><th></th></tr></thead><tbody>${items.map(item=>`<tr><td class="mono">${escapeHtml(item.asset_id)}</td><td><span class="table-title"><strong>${escapeHtml(item.anomaly_type)}</strong><small>${escapeHtml(item.category)}</small></span></td><td>${priorityPill(item.priority)}</td><td>${statusPill(item.status)}</td><td>${Math.round(item.confidence*100)}%</td><td>${item.delta_t?`${item.delta_t}°C`:'—'}</td><td>${formatNumber(item.affected_kw,2)}</td><td>${formatCurrency(item.annual_revenue_loss)}</td><td><button class="button small" onclick="openAnomaly('${item.id}')">Review</button></td></tr>`).join("")}</tbody></table></div>`;
}

async function openInverter(inverterId) {
  state.drawerId = null;
  state.drawerData = null;
  state.inverterPanel = { loading: true, inverterId };
  renderOverlays();
  try {
    await api(`/api/inverters/${encodeURIComponent(inverterId)}/analyze`, { method: "POST" });
    const [inverter, telemetry, alerts, topology, mlScores, mlComparison] = await Promise.all([
      api(`/api/inverters/${encodeURIComponent(inverterId)}`),
      api(`/api/inverters/${encodeURIComponent(inverterId)}/telemetry`),
      api(`/api/inverters/${encodeURIComponent(inverterId)}/alerts`),
      api(`/api/inverters/${encodeURIComponent(inverterId)}/topology`),
      api(`/api/inverters/${encodeURIComponent(inverterId)}/ml-scores`).catch(error => ({ enabled:false, reason:error.message })),
      api(`/api/inverters/${encodeURIComponent(inverterId)}/ml-comparison`).catch(error => ({ enabled:false, reason:error.message })),
    ]);
    const active = alerts.find(item => item.lifecycle_status !== "Resolved") || alerts[0];
    const evidence = active ? await api(`/api/inverter-alerts/${encodeURIComponent(active.alert_id)}/evidence`) : null;
    state.inverterPanel = { loading: false, inverter, summary: inverter.summary, readings: telemetry.readings, alerts, topology, evidence, mlScores, mlComparison, selectedAlertId: active?.alert_id || null };
  } catch (error) {
    state.inverterPanel = { loading: false, inverterId, error: error.message };
  }
  if (state.view === "twin") render(); else renderOverlays();
}

function closeInverter() {
  state.inverterPanel = null;
  if (state.view === "twin") render(); else renderOverlays();
}

function inverterChart(readings, series, ariaLabel, alerts = []) {
  if (!readings?.length) return `<div class="chart-empty">No telemetry is available for this period.</div>`;
  const sampled = readings.filter((_, index) => index % 4 === 0 || index === readings.length - 1);
  const width = 680, height = 190, left = 42, right = 12, top = 12, bottom = 28;
  const values = sampled.flatMap(row => series.map(item => Number(row[item.field] || 0)));
  const maximum = Math.max(1, ...values) * 1.08;
  const x = index => left + index * ((width - left - right) / Math.max(1, sampled.length - 1));
  const y = value => top + (height - top - bottom) * (1 - Number(value || 0) / maximum);
  const grid = [0, .5, 1].map(step => {
    const yy = top + (height-top-bottom)*step;
    return `<line class="grid-line" x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}"/><text class="axis-label" x="0" y="${yy+3}">${formatNumber(maximum*(1-step),0)}</text>`;
  }).join("");
  const lines = series.map(item => `<polyline fill="none" stroke="${item.color}" stroke-width="2" vector-effect="non-scaling-stroke" points="${sampled.map((row,index)=>`${x(index)},${y(row[item.field])}`).join(' ')}"/>`).join("");
  const timeStart = new Date(sampled[0].timestamp).getTime();
  const timeSpan = Math.max(1, new Date(sampled[sampled.length-1].timestamp).getTime() - timeStart);
  const markers = alerts.map(alert => {
    const startX = left + Math.max(0, Math.min(1, (new Date(alert.onset_timestamp || alert.start_timestamp).getTime()-timeStart)/timeSpan)) * (width-left-right);
    const endX = left + Math.max(0, Math.min(1, (new Date(alert.last_abnormal_timestamp || alert.end_timestamp).getTime()-timeStart)/timeSpan)) * (width-left-right);
    return `<rect class="alert-chart-marker ${className(alert.severity)}" x="${startX}" y="${top}" width="${Math.max(2,endX-startX)}" height="${height-top-bottom}" rx="2"/>`;
  }).join('');
  const first = sampled[0]?.timestamp?.slice(5,10) || "";
  const last = sampled[sampled.length-1]?.timestamp?.slice(5,10) || "";
  return `<div class="inverter-chart-legend">${series.map(item=>`<span><i style="background:${item.color}"></i>${item.label}</span>`).join("")}${alerts.length?'<span><i class="alert-key"></i>Alert window</span>':''}</div><svg class="inverter-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(ariaLabel)}">${grid}${markers}${lines}<text class="axis-label" x="${left}" y="${height-6}">${first}</text><text class="axis-label" text-anchor="end" x="${width-right}" y="${height-6}">${last}</text></svg>`;
}

function alertLabel(value) { return String(value || '').replaceAll('_',' ').replace(/\b\w/g, char=>char.toUpperCase()); }

function renderExperimentalMl(panel) {
  const scores = panel.mlScores || {};
  const comparison = panel.mlComparison || {};
  if (!scores.enabled) return `<div class="detail-section ml-experimental"><div class="detail-section-title">Experimental ML review layer</div><div class="synthetic-notice">${icon("info")} ML is disabled: ${escapeHtml(scores.reason || 'no compatible offline-trained artifact is installed')}. Rule-based monitoring remains active.</div></div>`;
  const chartRows = (scores.scores || []).map(row => ({ ...row, decision_threshold: scores.threshold }));
  const candidates = comparison.ml_candidates || [];
  const agreement = comparison.agreement || [];
  return `<div class="detail-section ml-experimental">
    <div class="detail-section-title">Experimental ML review layer <span class="tag warning">Not an operational alert</span></div>
    <div class="synthetic-notice strong">${icon("info")} Synthetic-data novelty scores are review aids only. They do not diagnose a fault, change alert lifecycle, or create work.</div>
    <div class="chip-row"><span class="tag">${escapeHtml(scores.model_version)}</span><span class="tag">${escapeHtml(scores.feature_version)}</span><span class="tag">Threshold ${formatNumber(scores.threshold,3)}</span><span class="tag">Higher = more unusual</span></div>
    ${inverterChart(chartRows,[{field:'anomaly_score',label:'Novelty score',color:'#c89cff'},{field:'decision_threshold',label:'Review threshold',color:'#ffc166'}],'Experimental anomaly score and review threshold')}
    <div class="detail-section-title" style="margin-top:12px">Candidate windows and rule comparison</div>
    ${candidates.length ? `<div class="inverter-alert-list">${candidates.map((candidate,index)=>{
      const match = agreement[index]?.rule_agreement;
      const deviations = (candidate.contributing_feature_deviations || []).map(item=>`${alertLabel(item.feature)} ${formatNumber(item.deviation,2)}`).join(' · ');
      return `<div class="inverter-alert-card"><i class="severity-bar ${match?'medium':'low'}"></i><span><strong>${escapeHtml(alertLabel(candidate.anomaly_category))}</strong><small>${formatDate(candidate.onset_timestamp)} · peak ${formatNumber(candidate.peak_anomaly_score,3)} · ${match?'agrees with an overlapping rule event':'ML-only review candidate'}</small><small>${escapeHtml(deviations || 'No deviation explanation available')}</small></span><span class="tag ${match?'success':'warning'}">${match?'Agreement':'Disagreement'}</span></div>`;
    }).join('')}</div>` : `<div class="empty-indicators">No ML review candidates crossed the frozen threshold for this period.</div>`}
    <div class="recommendation" style="margin-top:10px"><strong>Limitations</strong><p>${escapeHtml((scores.limitations || []).join(' · '))}</p></div>
  </div>`;
}

function renderTopologyEvidence(panel, selectedAlert) {
  const topology = panel.topology || {};
  const evidence = panel.evidence;
  if (!topology.available) return `<div class="detail-section topology-section"><div class="detail-section-title">Electrical-to-physical topology</div><div class="synthetic-notice">${icon("info")} ${escapeHtml(topology.message || 'Topology unavailable')}. Counts and relationships remain explicitly unknown.</div></div>`;
  const mppts = topology.mppts || [];
  const mappingLabel = topology.is_verified_as_built ? 'Verified as-built' : 'Simulated mapping — not as-built';
  const findings = evidence?.related_visual_findings || [];
  const associations = evidence?.reviewed_associations || [];
  const associationByFinding = Object.fromEntries(associations.map(item=>[item.anomaly_id,item]));
  return `<div class="detail-section topology-section">
    <div class="detail-section-title">Connected electrical and physical assets <span class="tag ${topology.is_verified_as_built?'success':'warning'}">${mappingLabel}</span></div>
    <div class="synthetic-notice strong">${icon("info")} ${escapeHtml(topology.message)} Spatial proximity alone is not evidence of a shared electrical cause.</div>
    <div class="detail-metrics inverter-metrics topology-metrics">
      <div class="detail-metric"><span>Connected block</span><strong>${escapeHtml((topology.blocks || []).join(', ') || 'Unknown')}</strong></div>
      <div class="detail-metric"><span>MPPT inputs</span><strong>${topology.mppt_count ?? 'Unknown'}</strong></div>
      <div class="detail-metric"><span>PV strings</span><strong>${topology.string_count ?? 'Unknown'}</strong></div>
      <div class="detail-metric"><span>Mapped demo modules</span><strong>${topology.mapped_module_count ?? 'Unknown'}</strong></div>
    </div>
    <div class="topology-tree">${mppts.map(mppt=>`<details><summary>${escapeHtml(mppt.label)} <span>${mppt.strings.length} string${mppt.strings.length===1?'':'s'}</span></summary>${mppt.strings.map(string=>`<div class="topology-string"><strong>${escapeHtml(string.label)}</strong><span>${string.asset_group ? `${escapeHtml(string.asset_group.block_code)} · rows ${string.asset_group.row_start}–${string.asset_group.row_end} · ${string.asset_group.mapped_module_count} module positions` : 'Physical mapping missing'}</span></div>`).join('')}</details>`).join('')}</div>
    ${selectedAlert ? `<div class="detail-section-title" style="margin-top:14px">Related visual and thermal evidence</div>
      ${panel.evidenceLoading ? '<div class="skeleton"></div>' : findings.length ? `<div class="related-findings">${findings.map(finding=>{
        const association = associationByFinding[finding.id];
        return `<article class="related-finding"><div><strong>${escapeHtml(finding.anomaly_type)}</strong><small>${escapeHtml(finding.asset_id)} · ${escapeHtml(finding.inspection_name)} · ${formatNumber(finding.temporal_proximity.absolute_days,1)} days from alert</small><small>${escapeHtml(finding.relationship_status.replaceAll('_',' '))} · ${escapeHtml(finding.relationship_basis)}</small>${association?`<small>Latest review: ${escapeHtml(association.reviewer)} — ${escapeHtml(association.explanation)} · ${association.history.length} audit event${association.history.length===1?'':'s'}</small>`:''}</div><div class="inline-actions"><button class="button small" onclick="openLightbox('${finding.thermal_image}','${escapeHtml(finding.asset_id)} thermal evidence')">Inspect evidence</button>${association ? `<span class="tag ${association.review_status==='Accepted'?'success':'warning'}">${escapeHtml(association.review_status)}</span>${association.review_status!=='Removed'?`<button class="button small" onclick="reviewEvidenceAssociation('${association.association_id}','Accepted','${selectedAlert.alert_id}')">Accept</button><button class="button small" onclick="reviewEvidenceAssociation('${association.association_id}','Rejected','${selectedAlert.alert_id}')">Reject</button><button class="button small" onclick="reviewEvidenceAssociation('${association.association_id}','Removed','${selectedAlert.alert_id}')">Remove</button>`:''}` : `<button class="button small primary" onclick="proposeEvidenceAssociation('${selectedAlert.alert_id}','${finding.id}')">Propose link</button>`}</div></article>`;
      }).join('')}</div>` : '<div class="empty-indicators">No inspection findings are connected through the available topology.</div>'}
      <div class="recommendation" style="margin-top:10px"><strong>Still needed to confirm a cause</strong><p>${escapeHtml((evidence?.missing_confirmation_information || []).join(' · '))}</p></div>` : '<div class="empty-indicators" style="margin-top:12px">Run or select an electrical alert to correlate inspection evidence.</div>'}
  </div>`;
}

async function selectInverterAlert(alertId) {
  if (!state.inverterPanel) return;
  state.inverterPanel.selectedAlertId = alertId;
  state.inverterPanel.evidenceLoading = true;
  renderOverlays();
  try {
    state.inverterPanel.evidence = await api(`/api/inverter-alerts/${encodeURIComponent(alertId)}/evidence`);
  } catch (error) { showToast(error.message, "error"); }
  state.inverterPanel.evidenceLoading = false;
  renderOverlays();
}

async function proposeEvidenceAssociation(alertId, anomalyId) {
  const explanation = prompt("Why should this visual finding be reviewed with the electrical alert?");
  if (!explanation) return;
  const reviewer = prompt("Reviewer name", "DeepDrishti operator");
  if (!reviewer) return;
  try {
    await api(`/api/inverter-alerts/${encodeURIComponent(alertId)}/associations`, { method:"POST", body:{ anomaly_id:anomalyId, reviewer, explanation } });
    await selectInverterAlert(alertId);
    showToast("Evidence association proposed for human review", "success");
  } catch (error) { showToast(error.message, "error"); }
}

async function reviewEvidenceAssociation(associationId, status, alertId) {
  const explanation = prompt(`Reason for ${status.toLowerCase()} status`);
  if (!explanation) return;
  const reviewer = prompt("Reviewer name", "DeepDrishti operator");
  if (!reviewer) return;
  try {
    await api(`/api/inverter-alert-associations/${encodeURIComponent(associationId)}`, { method:"PATCH", body:{ review_status:status, reviewer, explanation } });
    await selectInverterAlert(alertId);
    showToast(`Association ${status.toLowerCase()}`, "success");
  } catch (error) { showToast(error.message, "error"); }
}

async function updateInverterAlert(alertId, lifecycleStatus) {
  try {
    const updated = await api(`/api/inverter-alerts/${alertId}`, { method: "PATCH", body: { lifecycle_status: lifecycleStatus, note: `Operator changed alert status to ${lifecycleStatus}.` } });
    const index = state.inverterPanel.alerts.findIndex(item=>item.alert_id===alertId);
    if (index >= 0) state.inverterPanel.alerts[index] = updated;
    renderOverlays();
    showToast(`Inverter alert changed to ${lifecycleStatus}`, "success");
  } catch (error) { showToast(error.message, "error"); }
}

function renderInverterDrawer() {
  const panel = state.inverterPanel;
  if (!panel) return "";
  if (panel.loading) return `<div class="drawer-backdrop" onclick="closeInverter()"></div><aside class="detail-drawer inverter-drawer" aria-label="Loading inverter details"><div class="drawer-body"><div class="inverter-loading"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div></aside>`;
  if (panel.error) return `<div class="drawer-backdrop" onclick="closeInverter()"></div><aside class="detail-drawer inverter-drawer"><header class="drawer-header"><div class="drawer-title"><span>Inverter monitoring</span><h3>Unable to load data</h3></div><button class="icon-button" onclick="closeInverter()">${icon("close")}</button></header><div class="drawer-body"><div class="empty-state"><div><strong>Monitoring data unavailable</strong><p>${escapeHtml(panel.error)}</p><button class="button" onclick="openInverter('${escapeHtml(panel.inverterId)}')">Retry</button></div></div></div></aside>`;
  const inverter = panel.inverter;
  const summary = panel.summary;
  const latest = summary.latest || {};
  const indicators = summary.indicators || [];
  const alerts = panel.alerts || [];
  const activeAlerts = alerts.filter(item=>item.lifecycle_status!=="Resolved");
  const selectedAlert = alerts.find(item=>item.alert_id===panel.selectedAlertId) || alerts[0];
  const evidenceReadings = selectedAlert ? panel.readings.filter(row => {
    const time = new Date(row.timestamp).getTime();
    return time >= new Date(selectedAlert.onset_timestamp || selectedAlert.start_timestamp).getTime()-30*60000 && time <= new Date(selectedAlert.recovery_timestamp || selectedAlert.last_abnormal_timestamp || selectedAlert.end_timestamp).getTime()+30*60000;
  }) : [];
  return `<div class="drawer-backdrop" onclick="closeInverter()"></div>
    <aside class="detail-drawer inverter-drawer" role="dialog" aria-modal="true" aria-label="Inverter monitoring details">
      <header class="drawer-header"><div class="drawer-title"><span>${escapeHtml(inverter.block_info || 'Unassigned block')} · ${formatNumber(inverter.rated_ac_power_kw,0)} kW AC</span><h3>${escapeHtml(inverter.name)} monitoring</h3></div><button class="icon-button" onclick="closeInverter()">${icon("close")}</button></header>
      <div class="drawer-body">
        <div class="chip-row">${statusPill(summary.historical_health || summary.status)}<span class="tag">Current state: ${escapeHtml(summary.current_operating_state || latest.operating_state || 'No data')}</span><span class="tag warning">${activeAlerts.length} active alert${activeAlerts.length===1?'':'s'}</span><span class="tag warning">Synthetic demo data</span></div>
        <div class="synthetic-notice strong">${icon("info")} These are deterministic simulated readings, not actual sensors or a confirmed fault diagnosis.</div>
        <div class="detail-section"><div class="detail-section-title">Latest reading · ${escapeHtml(latest.timestamp || 'unavailable')}</div><div class="detail-metrics inverter-metrics">
          <div class="detail-metric"><span>AC output</span><strong>${formatNumber(latest.ac_power_kw,1)} kW</strong></div>
          <div class="detail-metric"><span>DC input</span><strong>${formatNumber(latest.dc_power_kw,1)} kW</strong></div>
          <div class="detail-metric"><span>Conversion efficiency</span><strong>${latest.conversion_efficiency_pct == null ? 'N/A' : `${formatNumber(latest.conversion_efficiency_pct,1)}%`}</strong></div>
          <div class="detail-metric"><span>Relative yield</span><strong>${summary.relative_yield_pct == null ? 'N/A' : `${formatNumber(summary.relative_yield_pct,1)}%`}</strong></div>
          <div class="detail-metric"><span>Inverter temperature</span><strong>${latest.inverter_temperature_c == null ? 'N/A' : `${formatNumber(latest.inverter_temperature_c,1)}°C`}</strong></div>
          <div class="detail-metric"><span>7-day energy</span><strong>${formatNumber(summary.actual_energy_kwh/1000,1)} MWh</strong></div>
          <div class="detail-metric"><span>Active alerts</span><strong>${activeAlerts.length}</strong></div>
        </div></div>
        <div class="detail-section"><div class="detail-section-title">Historical input and output power · kW</div>${inverterChart(panel.readings,[{field:'dc_power_kw',label:'DC input',color:'#5be7d4'},{field:'ac_power_kw',label:'AC output',color:'#9dff75'}],'Historical DC input and AC output power',alerts)}</div>
        <div class="detail-section"><div class="detail-section-title">Expected versus actual AC power · kW</div>${inverterChart(panel.readings,[{field:'expected_ac_power_kw',label:'Modeled expected',color:'#ffc166'},{field:'ac_power_kw',label:'Actual',color:'#9dff75'}],'Expected versus actual AC power',alerts)}</div>
        ${renderTopologyEvidence(panel, selectedAlert)}
        ${renderExperimentalMl(panel)}
        <div class="detail-section"><div class="detail-section-title">Telemetry anomaly events</div>${alerts.length ? `<div class="inverter-alert-list">${alerts.map(alert=>`<button class="inverter-alert-card ${alert.alert_id===selectedAlert?.alert_id?'selected':''}" onclick="selectInverterAlert('${alert.alert_id}')"><i class="severity-bar ${className(alert.severity)}"></i><span><strong>${escapeHtml(alertLabel(alert.anomaly_category))}</strong><small>${escapeHtml(alert.severity)} · ${formatDate(alert.onset_timestamp || alert.start_timestamp)} · ${alert.active_duration_minutes ?? '—'} active min</small></span>${statusPill(alert.lifecycle_status)}</button>`).join('')}</div>` : `<div class="empty-indicators">No event-based alerts were detected for this period.</div>`}</div>
        ${selectedAlert ? `<div class="detail-section alert-evidence"><div class="detail-section-title">Selected telemetry evidence window</div><div class="chip-row">${priorityPill(selectedAlert.severity)}${statusPill(selectedAlert.lifecycle_status)}<span class="tag">${selectedAlert.active_duration_minutes ?? 0} active min</span><span class="tag">${selectedAlert.wall_clock_duration_minutes ?? selectedAlert.duration_minutes} wall-clock min</span><span class="tag">Unconfirmed cause</span></div><div class="alert-timestamp-grid"><span>Onset<strong>${escapeHtml(selectedAlert.onset_timestamp)}</strong></span><span>Detection eligible<strong>${escapeHtml(selectedAlert.detection_eligible_timestamp)}</strong></span><span>Last abnormal<strong>${escapeHtml(selectedAlert.last_abnormal_timestamp)}</strong></span><span>Recovery<strong>${escapeHtml(selectedAlert.recovery_timestamp || 'Not observed')}</strong></span></div>${inverterChart(evidenceReadings,[{field:'expected_ac_power_kw',label:'Modeled expected',color:'#ffc166'},{field:'ac_power_kw',label:'Actual AC',color:'#9dff75'},{field:'dc_power_kw',label:'DC input',color:'#5be7d4'}],'Telemetry around the selected alert')}<div class="recommendation" style="margin-top:10px"><strong>Evidence-backed explanation</strong><p>${escapeHtml(selectedAlert.explanation)}</p></div><div class="recommendation" style="margin-top:8px"><strong>Investigation recommendation</strong><p>${escapeHtml(selectedAlert.recommended_investigation)}</p></div><div class="inline-actions alert-actions">${['Acknowledged','Investigating','Resolved'].map(status=>`<button class="button small ${selectedAlert.lifecycle_status===status?'primary':''}" onclick="updateInverterAlert('${selectedAlert.alert_id}','${status}')">${status}</button>`).join('')}</div><div class="timeline alert-history">${selectedAlert.lifecycle_history.map(item=>`<div class="timeline-item"><strong>${escapeHtml(item.status)}</strong><p>${escapeHtml(item.note)} · ${escapeHtml(item.actor)}</p><time>${formatDate(item.at)}</time></div>`).join('')}</div></div>`:''}
        <div class="detail-section"><div class="detail-section-title">Recent abnormal performance indicators</div>${indicators.length ? `<div class="indicator-list">${indicators.map(item=>`<div class="indicator-item ${escapeHtml(item.severity)}"><strong>${escapeHtml(item.code.replaceAll('_',' '))}</strong><p>${escapeHtml(item.message)}</p></div>`).join('')}</div>` : `<div class="empty-indicators">No rule-based abnormal-performance indicators in this period.</div>`}</div>
        <div class="detail-section"><div class="recommendation"><strong>How this is calculated</strong><p>${escapeHtml(summary.methodology)}</p></div></div>
      </div>
    </aside>`;
}

async function openAnomaly(id) {
  state.drawerId = id;
  state.drawerData = state.anomalies.find(item => item.id === id) || null;
  renderOverlays();
  try {
    state.drawerData = await api(`/api/anomalies/${id}`);
    renderOverlays();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function closeDrawer() {
  state.drawerId = null;
  state.drawerData = null;
  renderOverlays();
  if (state.view === "twin") render();
}

function renderDrawer() {
  if (!state.drawerId) return "";
  const item = state.drawerData;
  if (!item) return `<div class="drawer-backdrop" onclick="closeDrawer()"></div><aside class="detail-drawer"><div class="drawer-body"><div class="empty-state"><div class="skeleton" style="width:100%;height:300px;border-radius:16px"></div></div></div></aside>`;
  const history = Array.isArray(item.history) ? item.history : [];
  return `
    <div class="drawer-backdrop" onclick="closeDrawer()"></div>
    <aside class="detail-drawer" role="dialog" aria-modal="true" aria-label="Anomaly details">
      <header class="drawer-header">
        <div class="drawer-title"><span>${escapeHtml(item.asset_id)}</span><h3>${escapeHtml(item.anomaly_type)}</h3></div>
        <button class="icon-button" onclick="closeDrawer()">${icon("close")}</button>
      </header>
      <div class="drawer-body">
        <div class="chip-row">${priorityPill(item.priority)}${statusPill(item.status)}<span class="tag">${Math.round(item.confidence*100)}% confidence</span></div>
        <div class="detail-section">
          <div class="detail-section-title">Visual evidence</div>
          <div class="evidence-grid">
            <button class="evidence-card" onclick="openLightbox('${item.rgb_image}','RGB evidence')"><img src="${item.rgb_image}" alt="RGB evidence for ${escapeHtml(item.asset_id)}"><span class="evidence-label">RGB</span></button>
            <button class="evidence-card" onclick="openLightbox('${item.thermal_image}','Thermal evidence')"><img src="${item.thermal_image}" alt="Thermal evidence for ${escapeHtml(item.asset_id)}"><span class="evidence-label">Thermal</span></button>
          </div>
        </div>
        <div class="detail-section">
          <div class="detail-section-title">Engineering and business impact</div>
          <div class="detail-metrics">
            <div class="detail-metric"><span>Temperature delta</span><strong>${item.delta_t ? `${item.delta_t}°C` : "N/A"}</strong></div>
            <div class="detail-metric"><span>Affected power</span><strong>${formatNumber(item.affected_kw,2)} kW</strong></div>
            <div class="detail-metric"><span>Annual impact</span><strong>${formatCurrency(item.annual_revenue_loss)}</strong></div>
            <div class="detail-metric"><span>Annual energy loss</span><strong>${formatNumber(item.annual_kwh_loss,0)} kWh</strong></div>
            <div class="detail-metric"><span>Normalized ΔT</span><strong>${item.normalized_delta_t ? `${item.normalized_delta_t}°C` : "N/A"}</strong></div>
            <div class="detail-metric"><span>Physical location</span><strong style="font-size:11px">${item.block_name} · R${String(item.row_no).padStart(2,'0')} · M${String(item.module_no).padStart(2,'0')}</strong></div>
          </div>
        </div>
        <div class="detail-section"><div class="detail-section-title">Diagnosis</div><p class="detail-copy">${escapeHtml(item.description)}</p></div>
        <div class="detail-section"><div class="recommendation"><strong>Recommended action</strong><p>${escapeHtml(item.recommended_action)}</p></div></div>
        <div class="detail-section">
          <div class="detail-section-title">Operational status</div>
          <div class="inline-actions">${["Verified","Assigned","In Progress","Resolved"].map(status => `<button class="button small ${item.status===status?'primary':''}" onclick="updateAnomalyStatus('${item.id}','${status}')">${status}</button>`).join("")}</div>
        </div>
        <div class="detail-section"><div class="detail-section-title">Asset history</div><div class="timeline">${history.map(event => `<div class="timeline-item"><strong>${escapeHtml(event.event)}</strong><p>${escapeHtml(event.actor)}</p><time>${formatDate(event.at)}</time></div>`).join("")}</div></div>
      </div>
      <footer class="drawer-footer"><button class="button" onclick="navigate('assets')">${icon("history")} Open asset history</button><button class="button primary" onclick="openTaskModal('${item.id}')">${icon("wrench")} Create work order</button></footer>
    </aside>`;
}

async function updateAnomalyStatus(id, status) {
  try {
    const updated = await api(`/api/anomalies/${id}`, { method: "PATCH", body: { status } });
    state.drawerData = updated;
    const index = state.anomalies.findIndex(item => item.id === id);
    if (index >= 0) state.anomalies[index] = updated;
    render();
    state.drawerId = id;
    state.drawerData = updated;
    renderOverlays();
    showToast(`Finding changed to ${status}`, "success");
  } catch (error) { showToast(error.message, "error"); }
}

function renderInspections() {
  setPage("Inspections", `Solar intelligence / ${state.site.name} / Inspection history`);
  const totalFindings = state.inspections.reduce((s,i)=>s+i.findings,0);
  return `
    <div class="page-stack">
      <section class="page-header"><div><h2>Inspection intelligence</h2><p>Ingest drone, robot or field imagery; convert evidence into localized, reviewable Digital Twin findings.</p></div><div class="page-actions"><button class="button" onclick="navigate('twin')">${icon("map")} View all findings</button><button class="button primary" onclick="openInspectionModal()">${icon("upload")} Analyze imagery</button></div></section>
      <section class="metric-strip"><div class="stat-card"><span>Total inspection runs</span><strong>${state.inspections.length}</strong></div><div class="stat-card"><span>Findings processed</span><strong>${totalFindings}</strong></div><div class="stat-card"><span>Latest model</span><strong style="font-size:14px">thermal-demo-v0.3</strong></div><div class="stat-card"><span>Review workflow</span><strong style="font-size:14px">Human-in-the-loop</strong></div></section>
      <section class="dashboard-grid">
        <article class="panel"><header class="panel-header"><div><h3>Inspection history</h3><p>Every report is retained as a time layer on the site twin</p></div><span class="tag success">Audit ready</span></header><div class="panel-body flush table-wrap"><table class="data-table"><thead><tr><th>Inspection</th><th>Type / source</th><th>Status</th><th>Findings</th><th>Affected kW</th><th>Annual impact</th><th></th></tr></thead><tbody>${state.inspections.map(ins=>`<tr><td><span class="table-title"><strong>${escapeHtml(ins.name)}</strong><small>${formatDate(ins.captured_at)} · ${escapeHtml(ins.weather)}</small></span></td><td><span class="table-title"><strong>${escapeHtml(ins.inspection_type)}</strong><small>${escapeHtml(ins.source)} · ${escapeHtml(ins.model_version)}</small></span></td><td>${statusPill(ins.status==='Reviewed'?'Verified':ins.status)}</td><td>${ins.findings}</td><td>${formatNumber(ins.affected_kw,1)}</td><td>${formatCurrency(ins.annual_revenue_loss)}</td><td><button class="button small" onclick="navigate('twin')">Open</button></td></tr>`).join("")}</tbody></table></div></article>
        <article class="panel"><header class="panel-header"><div><h3>Runnable AI demonstration</h3><p>The included sample is analyzed by the backend, not hard-coded in the browser</p></div>${icon("scan")}</header><div class="panel-body"><button class="evidence-card" style="width:100%" onclick="openInspectionModal()"><img src="/static/assets/sample_thermal_input.png" alt="Synthetic sample thermal inspection" style="height:260px"><span class="evidence-label">Bundled sample</span></button><div class="recommendation" style="margin-top:14px"><strong>What the demo engine does</strong><p>It thresholds warm regions, extracts connected components, estimates a temperature-delta proxy, maps centroids to physical assets and creates reviewable anomaly records. It is a practical demonstration pipeline—not a certified production thermography model.</p></div></div></article>
      </section>
    </div>`;
}

function renderTasks() {
  setPage("Tasks and remediation", `Solar intelligence / ${state.site.name} / Field operations`);
  const columns = [
    ["Overdue", state.tasks.filter(t=>t.status==="Overdue")],
    ["Assigned", state.tasks.filter(t=>t.status==="Assigned")],
    ["In Progress", state.tasks.filter(t=>t.status==="In Progress")],
    ["Completed", state.tasks.filter(t=>["Completed","Verified"].includes(t.status))],
  ];
  return `
    <div class="page-stack">
      <section class="page-header"><div><h2>From finding to verified repair.</h2><p>Prioritize visual findings, assign work, collect field evidence and preserve every status change in the asset history.</p></div><div class="page-actions"><button class="button" onclick="navigate('field')">${icon("phone")} Field mode</button><button class="button primary" onclick="openTaskModal(null)">${icon("plus")} New task</button></div></section>
      <section class="metric-strip"><div class="stat-card"><span>Overdue</span><strong class="text-red">${columns[0][1].length}</strong></div><div class="stat-card"><span>Assigned</span><strong>${columns[1][1].length}</strong></div><div class="stat-card"><span>In progress</span><strong>${columns[2][1].length}</strong></div><div class="stat-card"><span>Verified complete</span><strong class="text-lime">${columns[3][1].length}</strong></div></section>
      <section class="kanban">${columns.map(([name,tasks])=>`<div class="kanban-column"><div class="kanban-head"><span>${name}</span><span class="count-pill">${tasks.length}</span></div>${tasks.map(taskCard).join("") || `<div class="empty-state" style="min-height:160px;padding:20px"><div><div class="empty-icon">✓</div><p>No tasks</p></div></div>`}</div>`).join("")}</section>
    </div>`;
}

function taskCard(task) {
  return `<article class="task-card" onclick="openTaskUpdate('${task.id}')"><div class="chip-row">${priorityPill(task.priority)}${statusPill(task.status)}</div><h4>${escapeHtml(task.title)}</h4><p>${escapeHtml(task.requested_action)}</p><div class="task-card-meta"><span class="assignee"><i>${escapeHtml(task.owner.split(' ').map(x=>x[0]).join('').slice(0,2))}</i>${escapeHtml(task.owner)}</span><span>${formatDate(task.due_date)}</span></div></article>`;
}

function renderAnalytics() {
  setPage("Analytics", `Solar intelligence / ${state.site.name} / Performance and loss`);
  const active = state.anomalies.filter(a=>a.status!=="Resolved");
  const critical = active.filter(a=>a.priority==="Critical").length;
  const high = active.filter(a=>a.priority==="High").length;
  const medium = active.filter(a=>a.priority==="Medium").length;
  const low = active.filter(a=>a.priority==="Low").length;
  return `
    <div class="page-stack">
      <section class="page-header"><div><h2>Turn physical conditions into business decisions.</h2><p>Transparent impact estimates combine affected equipment, nameplate power, anomaly factors and customer-configurable energy economics.</p></div><div class="page-actions"><a class="button" href="/api/sites/${state.selectedSiteId}/export.csv">${icon("download")} Export CSV</a><button class="button primary" onclick="navigate('twin')">${icon("map")} Review evidence</button></div></section>
      <section class="kpi-grid">${kpiCard("Annual revenue at risk",formatCurrency(state.site.annual_revenue_loss),"Unresolved findings","money")}${kpiCard("Affected DC power",`${formatNumber(state.site.affected_kw,1)} kW`,"Asset-level estimate","bolt")}${kpiCard("Annual energy loss",`${formatNumber(state.site.annual_kwh_loss/1000,1)} MWh`,"Recoverable model","activity")}${kpiCard("Critical findings",critical,"Immediate attention","alert")}${kpiCard("Review coverage","96.4%","Human validation rate","check")}</section>
      <section class="analytics-layout">
        <article class="panel"><header class="panel-header"><div><h3>Loss trajectory</h3><p>Modeled risk versus verified recovery across inspection cycles</p></div><span class="tag success">Improving</span></header><div class="panel-body">${lineChart(state.portfolio.trend)}</div></article>
        <article class="panel"><header class="panel-header"><div><h3>Priority distribution</h3><p>Active findings by operational severity</p></div></header><div class="panel-body"><div class="donut-wrap"><div class="donut"><div class="donut-center"><strong>${active.length}</strong><span>active findings</span></div></div><div class="bar-list">${[["Critical",critical,"var(--red)"],["High",high,"var(--orange)"],["Medium",medium,"var(--amber)"],["Low",low,"var(--blue)"]].map(([label,value,color])=>`<div class="bar-row"><div class="bar-head"><span>${label}</span><strong>${value}</strong></div><div class="bar-track"><div class="bar-fill" style="width:${active.length?value/active.length*100:0}%;background:${color}"></div></div></div>`).join("")}</div></div></div></article>
      </section>
      <section class="dashboard-grid equal"><article class="panel"><header class="panel-header"><div><h3>Impact by anomaly type</h3><p>Annual revenue loss modeled at equipment level</p></div></header><div class="panel-body">${barChart(state.portfolio.loss_by_type)}</div></article><article class="panel"><header class="panel-header"><div><h3>Transparent impact model</h3><p>Example calculation used by the runnable MVP</p></div>${icon("info")}</header><div class="panel-body"><div class="code-block">affected_dc_kw = affected_modules × module_kw × anomaly_factor\nannual_kwh_loss = affected_dc_kw × sun_hours × 365 × availability\nannual_revenue_loss = annual_kwh_loss × tariff_per_kwh</div><div class="recommendation" style="margin-top:14px"><strong>Production note</strong><p>Real deployments should expose every factor, allow customer calibration, correlate SCADA/DAS evidence and retain the model/version used for each estimate.</p></div></div></article></section>
    </div>`;
}

function renderAssets() {
  setPage("Equipment and asset history", `Solar intelligence / ${state.site.name} / Asset registry`);
  const selected = state.drawerData || state.anomalies.find(a=>a.priority==="Critical") || state.anomalies[0];
  const cells = Array.from({length:60},(_,i)=>`<i class="asset-cell ${i===31?'flagged':''}"></i>`).join("");
  return `
    <div class="page-stack">
      <section class="page-header"><div><h2>One persistent record for every physical asset.</h2><p>Geometry, metadata, inspections, anomalies, tasks and evidence remain attached to the same equipment identity over time.</p></div><div class="page-actions"><button class="button" onclick="navigate('twin')">${icon("map")} Locate on map</button><button class="button primary" onclick="openAnomaly('${selected?.id || ''}')">${icon("eye")} Review active finding</button></div></section>
      <div class="synthetic-notice strong">${icon("info")} ${escapeHtml(state.assets.topology_coverage.message)} Coverage: ${escapeHtml(state.assets.topology_coverage.status)} · ${escapeHtml(state.assets.topology_coverage.mapping_classification.replaceAll('_',' '))}.</div>
      <section class="metric-strip"><div class="stat-card"><span>Mapped modules</span><strong>${knownCount(state.assets.hierarchy.modules)}</strong></div><div class="stat-card"><span>Mapped strings</span><strong>${knownCount(state.assets.hierarchy.strings)}</strong></div><div class="stat-card"><span>Mapped rows</span><strong>${knownCount(state.assets.hierarchy.rows)}</strong></div><div class="stat-card"><span>Registered inverters</span><strong>${knownCount(state.assets.hierarchy.inverters)}</strong></div></section>
      <section class="asset-layout">
        <article class="panel asset-tree-panel"><header class="panel-header"><div><h3>Asset hierarchy</h3><p>Database-derived topology; unknown levels remain unknown</p></div></header><div class="asset-tree"><button class="tree-node active"><span class="tree-chevron">▾</span>${icon("panel")} ${escapeHtml(state.site.name)}</button>${state.assets.blocks.map(block=>`<button class="tree-node depth-1"><span class="tree-chevron">›</span>${icon("grid")} Block ${escapeHtml(block.id)} · ${block.findings} findings · ${block.strings == null?'strings unknown':`${block.strings} strings`}</button>`).join("") || '<div class="empty-indicators">No topology is available for this site.</div>'}</div></article>
        <div class="page-stack">
          <article class="panel"><header class="panel-header"><div><h3>${escapeHtml(selected?.asset_id || 'No selected physical asset')}</h3><p>Synthetic equipment-level Digital Twin record</p></div><div class="chip-row">${selected?priorityPill(selected.priority):''}${selected?statusPill(selected.status):''}</div></header><div class="panel-body"><div class="asset-visual"><div class="asset-module">${cells}</div></div><div class="detail-metrics" style="margin-top:14px"><div class="detail-metric"><span>Registry provenance</span><strong style="font-size:12px">Synthetic demo</strong></div><div class="detail-metric"><span>Block</span><strong>${escapeHtml(selected?.block_name || 'Unknown')}</strong></div><div class="detail-metric"><span>Row</span><strong>${selected?.row_no ?? 'Unknown'}</strong></div><div class="detail-metric"><span>Module position</span><strong>${selected?.module_no ?? 'Unknown'}</strong></div><div class="detail-metric"><span>Electrical mapping</span><strong style="font-size:12px">Review inverter topology</strong></div><div class="detail-metric"><span>As-built verified</span><strong>No</strong></div></div></div></article>
          <article class="panel"><header class="panel-header"><div><h3>Lifecycle history</h3><p>The operational value of the Digital Twin grows with every inspection and repair</p></div>${icon("history")}</header><div class="panel-body timeline">${(selected?.history || []).map(event=>`<div class="timeline-item"><strong>${escapeHtml(event.event)}</strong><p>${escapeHtml(event.actor)}</p><time>${formatDate(event.at)}</time></div>`).join("") || '<div class="empty-indicators">No asset history is available.</div>'}</div></article>
        </div>
      </section>
    </div>`;
}

function renderField() {
  setPage("Field app", `Solar intelligence / ${state.site.name} / Technician mode`);
  const activeTasks = state.tasks.filter(t=>!["Completed","Verified"].includes(t.status));
  return `
    <div class="page-stack">
      <section class="page-header"><div><h2>The same Digital Twin, in the technician’s hand.</h2><p>This responsive field workflow shows task pins, technician location, requested action and status updates. Production would add offline site packages, GPS navigation and queued synchronization.</p></div><div class="page-actions"><button class="button" onclick="navigate('tasks')">${icon("clipboard")} Task board</button><button class="button primary" onclick="openTaskUpdate('${activeTasks[0]?.id || ''}')">${icon("wrench")} Update first task</button></div></section>
      <section class="field-layout">
        <article class="panel"><header class="panel-header"><div><h3>Field operations simulation</h3><p>Desktop explanation of the mobile workflow</p></div><span class="status-pill healthy">Online</span></header><div class="panel-body"><div class="metric-strip" style="grid-template-columns:repeat(3,1fr)"><div class="stat-card"><span>Assigned today</span><strong>${activeTasks.length}</strong></div><div class="stat-card"><span>Travel distance</span><strong>2.8 km</strong></div><div class="stat-card"><span>Offline package</span><strong style="font-size:13px">Synced</strong></div></div><div class="recommendation" style="margin-top:16px"><strong>Field loop</strong><p>Open task → navigate to mapped asset → inspect/repair → add note or evidence → mark completed → schedule targeted reinspection → verify resolved.</p></div><div class="code-block" style="margin-top:16px">Detected → Verified → Assigned → In Progress\n→ Repair Completed → Reinspection Pending → Verified Resolved</div></div></article>
        <div class="phone-shell"><div class="phone-screen"><div class="phone-status"><span>9:41</span><span>●●● 5G ▰</span></div><div class="phone-header"><div><strong>${escapeHtml(state.site.name)}</strong><div class="text-faint" style="font-size:8px;margin-top:3px">${activeTasks.length} assigned tasks</div></div><span class="avatar" style="width:30px;height:30px;border-radius:9px">AR</span></div><div class="phone-map">${farmMapSvg(state.anomalies.filter(a=>activeTasks.some(t=>t.anomaly_id===a.id)),.92,true)}<span class="user-location"></span></div><div class="phone-task-list">${activeTasks.slice(0,3).map(task=>`<button class="phone-task" style="width:100%;text-align:left;color:inherit" onclick="openTaskUpdate('${task.id}')"><div class="chip-row">${priorityPill(task.priority)}${statusPill(task.status)}</div><strong>${escapeHtml(task.title)}</strong><p>${escapeHtml(task.asset_id || 'Site task')} · due ${formatDate(task.due_date)}</p><span class="button small full">Open task</span></button>`).join("")}</div><div class="phone-nav"><span class="active">Map</span><span>Tasks</span><span>Capture</span><span>Sync</span></div></div></div>
      </section>
    </div>`;
}

function openInspectionModal() {
  state.uploadFile = null;
  state.modal = { type: "inspection" };
  renderOverlays();
}

function handleFileSelected(input) {
  state.uploadFile = input.files?.[0] || null;
  const summary = document.getElementById("file-summary");
  if (summary) {
    summary.classList.toggle("hidden", !state.uploadFile);
    summary.innerHTML = state.uploadFile ? `<span>${escapeHtml(state.uploadFile.name)}</span><strong>${formatNumber(state.uploadFile.size/1024,0)} KB</strong>` : "";
  }
}

async function runInspection(useSample = false) {
  if (!useSample && !state.uploadFile) {
    showToast("Choose an image or run the bundled sample", "error");
    return;
  }
  state.modal = { type: "processing", progress: 12, step: "Uploading imagery and reading metadata…" };
  renderOverlays();
  const steps = [
    [28, "Normalizing image and thermal color space…"],
    [46, "Detecting warm connected components…"],
    [63, "Associating candidates with Digital Twin assets…"],
    [81, "Estimating ΔT, power and revenue impact…"],
    [94, "Writing findings to the review queue…"],
  ];
  let stepIndex = 0;
  const timer = setInterval(() => {
    if (!state.modal || state.modal.type !== "processing") return clearInterval(timer);
    const step = steps[Math.min(stepIndex, steps.length-1)];
    state.modal.progress = step[0];
    state.modal.step = step[1];
    stepIndex++;
    renderOverlays();
  }, 520);
  try {
    const form = new FormData();
    form.append("site_id", state.selectedSiteId);
    if (!useSample && state.uploadFile) form.append("file", state.uploadFile);
    const result = await api("/api/inspections/analyze", { method: "POST", body: form });
    clearInterval(timer);
    state.modal.progress = 100;
    state.modal.step = `${result.findings} findings created. Opening the review map…`;
    renderOverlays();
    await loadSiteData(state.selectedSiteId);
    state.portfolio = await api("/api/portfolio");
    state.filters = { priority: "All", status: "Detected", type: "All", query: "" };
    setTimeout(() => {
      state.modal = null;
      state.view = "twin";
      renderNav();
      render();
      if (result.anomaly_ids[0]) openAnomaly(result.anomaly_ids[0]);
      showToast(`${result.findings} AI candidates added for human review`, "success");
    }, 650);
  } catch (error) {
    clearInterval(timer);
    state.modal = null;
    renderOverlays();
    showToast(error.message, "error");
  }
}

function openTaskModal(anomalyId) {
  const anomaly = anomalyId ? state.anomalies.find(item=>item.id===anomalyId) || state.drawerData : null;
  state.modal = { type: "task-create", anomaly };
  renderOverlays();
}

async function submitTask() {
  const anomaly = state.modal?.anomaly;
  const payload = {
    site_id: state.selectedSiteId,
    anomaly_id: anomaly?.id || null,
    title: document.getElementById("task-title").value,
    owner: document.getElementById("task-owner").value,
    due_date: document.getElementById("task-due").value,
    priority: document.getElementById("task-priority").value,
    requested_action: document.getElementById("task-action").value,
    notes: document.getElementById("task-notes").value,
  };
  try {
    await api("/api/tasks", { method: "POST", body: payload });
    await loadSiteData(state.selectedSiteId);
    state.modal = null;
    state.drawerId = null;
    state.drawerData = null;
    renderNav();
    render();
    showToast("Work order created and linked to the asset", "success");
  } catch (error) { showToast(error.message, "error"); }
}

function openTaskUpdate(taskId) {
  if (!taskId) return;
  const task = state.tasks.find(item=>item.id===taskId);
  if (!task) return showToast("Task not found", "error");
  state.modal = { type: "task-update", task };
  renderOverlays();
}

async function submitTaskUpdate() {
  const task = state.modal?.task;
  if (!task) return;
  const payload = {
    status: document.getElementById("task-update-status").value,
    owner: document.getElementById("task-update-owner").value,
    notes: document.getElementById("task-update-notes").value,
  };
  try {
    await api(`/api/tasks/${task.id}`, { method: "PATCH", body: payload });
    await loadSiteData(state.selectedSiteId);
    state.modal = null;
    renderNav();
    render();
    showToast("Task and linked asset history updated", "success");
  } catch (error) { showToast(error.message, "error"); }
}

function openLightbox(src, label) {
  state.modal = { type: "lightbox", src, label };
  renderOverlays();
}

function closeModal() {
  state.modal = null;
  state.uploadFile = null;
  renderOverlays();
}

function showAbout() {
  state.modal = { type: "about" };
  renderOverlays();
}

function openCommandPalette() {
  state.commandOpen = true;
  state.commandQuery = "";
  renderOverlays();
  setTimeout(()=>document.getElementById("command-input")?.focus(),20);
}

function closeCommandPalette() {
  state.commandOpen = false;
  state.commandQuery = "";
  renderOverlays();
}

function updateCommand(value) {
  state.commandQuery = value;
  renderOverlays();
  setTimeout(()=>{
    const input = document.getElementById("command-input");
    if (input) { input.focus(); input.setSelectionRange(value.length,value.length); }
  },0);
}

function commandResults() {
  const q = state.commandQuery.trim().toLowerCase();
  const commands = [
    {label:"Open portfolio overview", key:"P", action:"navigate('portfolio')", meta:"Navigation"},
    {label:"Open Digital Twin", key:"M", action:"navigate('twin')", meta:"Navigation"},
    {label:"Analyze thermal imagery", key:"A", action:"openInspectionModal();closeCommandPalette()", meta:"AI workflow"},
    {label:"Open task board", key:"T", action:"navigate('tasks')", meta:"Operations"},
    {label:"Open field app", key:"F", action:"navigate('field')", meta:"Mobile workflow"},
  ];
  const anomalies = state.anomalies.slice(0,12).map(item=>({label:item.asset_id, key:"⌖", action:`openAnomaly('${item.id}');closeCommandPalette()`, meta:item.anomaly_type}));
  return [...commands,...anomalies].filter(item=>!q || `${item.label} ${item.meta}`.toLowerCase().includes(q));
}

function renderOverlays() {
  const root = document.getElementById("overlay-root");
  if (!root) return;
  root.innerHTML = `${renderDrawer()}${renderInverterDrawer()}${renderModal()}${renderCommandPalette()}`;
}

function renderModal() {
  if (!state.modal) return "";
  const m = state.modal;
  if (m.type === "inspection") return `
    <div class="modal-backdrop" onclick="closeModal()"></div><section class="modal wide" role="dialog" aria-modal="true"><header class="modal-header"><div><h3>Analyze solar inspection imagery</h3><p>Upload a thermal/RGB image or run the bundled synthetic thermal sample.</p></div><button class="icon-button" onclick="closeModal()">${icon("close")}</button></header><div class="modal-body"><div class="upload-zone" onclick="document.getElementById('inspection-file').click()"><div><div class="upload-icon">${icon("upload")}</div><h4>Drop or choose an inspection image</h4><p>Accepted formats: PNG, JPG, WEBP and TIFF. The demo backend performs warm-region detection, component extraction and asset association.</p><span class="button">Choose image</span><input id="inspection-file" type="file" accept="image/png,image/jpeg,image/webp,image/tiff" hidden onchange="handleFileSelected(this)"><div id="file-summary" class="file-summary hidden"></div></div></div><div class="recommendation" style="margin-top:14px"><strong>Important</strong><p>This pipeline is a runnable technical demonstration. Customer or safety decisions require a validated radiometric workflow, calibrated cameras, domain-specific models and human review.</p></div></div><footer class="modal-footer"><button class="button" onclick="runInspection(true)">${icon("scan")} Run bundled sample</button><button class="button primary" onclick="runInspection(false)">${icon("bolt")} Analyze selected image</button></footer></section>`;
  if (m.type === "processing") return `<div class="modal-backdrop"></div><section class="modal" role="dialog" aria-modal="true"><div class="modal-body processing-card"><div><div class="processing-orbit"><span class="processing-core">AI</span></div><div class="processing-copy"><h4>Building operational findings</h4><p>${escapeHtml(m.step)}</p></div><div class="progress-bar"><i style="width:${m.progress}%"></i></div><div class="text-faint" style="font-size:9px;margin-top:9px">${m.progress}% complete</div></div></div></section>`;
  if (m.type === "task-create") {
    const a = m.anomaly;
    const due = new Date(Date.now()+4*86400000).toISOString().slice(0,10);
    return `<div class="modal-backdrop" onclick="closeModal()"></div><section class="modal" role="dialog" aria-modal="true"><header class="modal-header"><div><h3>Create work order</h3><p>${a?`Linked to ${escapeHtml(a.asset_id)} · ${escapeHtml(a.anomaly_type)}`:"Create a site-level maintenance task"}</p></div><button class="icon-button" onclick="closeModal()">${icon("close")}</button></header><div class="modal-body"><div class="form-grid"><div class="form-field full"><label>Task title</label><input id="task-title" class="text-input" value="${escapeHtml(a?`Inspect ${a.anomaly_type} at ${a.asset_id}`:'New solar O&M task')}"></div><div class="form-field"><label>Owner</label><select id="task-owner" class="select-input"><option>Aditi Rao</option><option>Vikram Shah</option><option>Neha Singh</option><option>O&M Vendor Team</option></select></div><div class="form-field"><label>Due date</label><input id="task-due" type="date" class="text-input" value="${due}"></div><div class="form-field"><label>Priority</label><select id="task-priority" class="select-input">${["Critical","High","Medium","Low"].map(p=>`<option ${a?.priority===p?'selected':''}>${p}</option>`).join("")}</select></div><div class="form-field"><label>Current status</label><input class="text-input" disabled value="Assigned on creation"></div><div class="form-field full"><label>Requested action</label><textarea id="task-action" class="textarea-input">${escapeHtml(a?.recommended_action || 'Inspect the mapped location, document the physical condition and complete the requested maintenance action.')}</textarea></div><div class="form-field full"><label>Notes</label><textarea id="task-notes" class="textarea-input" placeholder="Access instructions, safety notes or contractor context…"></textarea></div></div></div><footer class="modal-footer"><button class="button" onclick="closeModal()">Cancel</button><button class="button primary" onclick="submitTask()">${icon("wrench")} Create and assign</button></footer></section>`;
  }
  if (m.type === "task-update") {
    const t = m.task;
    return `<div class="modal-backdrop" onclick="closeModal()"></div><section class="modal" role="dialog" aria-modal="true"><header class="modal-header"><div><h3>${escapeHtml(t.title)}</h3><p>${escapeHtml(t.asset_id || 'Site-level task')} · due ${formatDate(t.due_date)}</p></div><button class="icon-button" onclick="closeModal()">${icon("close")}</button></header><div class="modal-body"><div class="chip-row" style="margin-bottom:16px">${priorityPill(t.priority)}${statusPill(t.status)}</div><div class="form-grid"><div class="form-field"><label>Technician</label><select id="task-update-owner" class="select-input">${[t.owner,"Aditi Rao","Vikram Shah","Neha Singh","O&M Vendor Team"].filter((x,i,a)=>a.indexOf(x)===i).map(owner=>`<option ${owner===t.owner?'selected':''}>${escapeHtml(owner)}</option>`).join("")}</select></div><div class="form-field"><label>Status</label><select id="task-update-status" class="select-input">${["Assigned","In Progress","Overdue","Completed","Verified"].map(status=>`<option ${status===t.status?'selected':''}>${status}</option>`).join("")}</select></div><div class="form-field full"><label>Requested action</label><div class="recommendation"><p>${escapeHtml(t.requested_action)}</p></div></div><div class="form-field full"><label>Field notes</label><textarea id="task-update-notes" class="textarea-input">${escapeHtml(t.notes || '')}</textarea></div></div></div><footer class="modal-footer"><button class="button" onclick="closeModal()">Cancel</button><button class="button primary" onclick="submitTaskUpdate()">${icon("check")} Save field update</button></footer></section>`;
  }
  if (m.type === "lightbox") return `<div class="modal-backdrop" onclick="closeModal()"></div><section class="modal wide" role="dialog" aria-modal="true"><header class="modal-header"><div><h3>${escapeHtml(m.label)}</h3><p>Original inspection evidence retained for audit and review.</p></div><button class="icon-button" onclick="closeModal()">${icon("close")}</button></header><div class="modal-body" style="padding:10px"><img src="${m.src}" alt="${escapeHtml(m.label)}" style="width:100%;max-height:76vh;object-fit:contain;border-radius:15px;background:#020805"></div></section>`;
  if (m.type === "about") return `<div class="about-backdrop" onclick="closeModal()"></div><section class="modal" role="dialog" aria-modal="true"><header class="modal-header"><div><h3>About this runnable MVP</h3><p>Independent DeepDrishti implementation of a solar visual-intelligence workflow.</p></div><button class="icon-button" onclick="closeModal()">${icon("close")}</button></header><div class="modal-body"><div class="recommendation"><strong>What is working</strong><p>FastAPI + SQLite backend, portfolio/site analytics, equipment-level SVG Digital Twin, synchronized filters, anomaly evidence, persistent status history, task creation/update, CSV export, mobile field simulation and image-analysis endpoint.</p></div><div class="detail-section"><div class="detail-section-title">What remains simulated</div><p class="detail-copy">The farm geometry, SCADA values and seed findings are demo data. The bundled thermal analysis is deliberately lightweight and must be replaced with calibrated, field-validated computer-vision models before commercial or safety-critical use.</p></div><div class="detail-section"><div class="detail-section-title">Independent design</div><p class="detail-copy">This application uses original code, synthetic imagery and DeepDrishti branding. It demonstrates industry-standard concepts without copying proprietary code, assets or model behavior.</p></div></div><footer class="modal-footer"><button class="button primary" onclick="closeModal()">Understood</button></footer></section>`;
  return "";
}

function renderCommandPalette() {
  if (!state.commandOpen) return "";
  const results = commandResults();
  return `<div class="command-backdrop" onclick="closeCommandPalette()"></div><section class="command-palette"><input id="command-input" class="command-input" placeholder="Search commands, asset IDs or anomaly types…" value="${escapeHtml(state.commandQuery)}" oninput="updateCommand(this.value)"><div class="command-results">${results.map(item=>`<button class="command-item" onclick="${item.action}"><span class="command-key">${escapeHtml(item.key)}</span><span><strong style="display:block;font-size:11px">${escapeHtml(item.label)}</strong><small class="text-faint">${escapeHtml(item.meta)}</small></span></button>`).join("") || `<div class="empty-state"><div><div class="empty-icon">⌕</div><p>No matching command or asset</p></div></div>`}</div></section>`;
}

function showToast(message, type = "info") {
  const root = document.getElementById("toast-root");
  const id = `toast-${Date.now()}`;
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.id = id;
  node.innerHTML = `<span class="toast-icon">${type === "error" ? "!" : "✓"}</span><span><strong>${type === "error" ? "Action failed" : "Solar Twin"}</strong><span>${escapeHtml(message)}</span></span><button onclick="document.getElementById('${id}')?.remove()">×</button>`;
  root.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

async function resetDemo() {
  try {
    await api("/api/reset", { method: "POST" });
    state.portfolio = await api("/api/portfolio");
    state.sites = await api("/api/sites");
    await loadSiteData("site-001");
    state.view = "portfolio";
    state.filters = { priority: "All", status: "Active", type: "All", query: "" };
    populateSitePicker();
    renderNav();
    render();
    showToast("Demo data restored", "success");
  } catch (error) { showToast(error.message, "error"); }
}

function toggleSidebar(open) {
  document.body.classList.toggle("sidebar-open", Boolean(open));
}

document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openCommandPalette();
  }
  if (event.key === "Escape") {
    if (state.commandOpen) closeCommandPalette();
    else if (state.modal) closeModal();
    else if (state.drawerId) closeDrawer();
    else toggleSidebar(false);
  }
});

document.addEventListener("DOMContentLoaded", init);
