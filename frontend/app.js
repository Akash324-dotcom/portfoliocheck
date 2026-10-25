const SVG_NS = "http://www.w3.org/2000/svg";
const COLORS = ["#2563eb", "#06b6d4", "#8b5cf6", "#f59e0b", "#10b981", "#ec4899", "#14b8a6"];
const OTHER_COLOR = "#94a3b8";
const MAX_SEGMENTS = COLORS.length;
const DONUT_LENGTH = 2 * Math.PI * 70;
const GAUGE_LENGTH = Math.PI * 80;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const LEVEL_TEXT = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
  unknown: "No data",
};

const LEVEL_NOTE = {
  low: "Your holdings look well spread out.",
  medium: "Some concentration. Worth a closer look at the items below.",
  high: "A lot of your portfolio depends on very little. See what we found.",
  unknown: "We could not find positions with a usable value.",
};

const state = {
  accountId: "",
  data: null,
  sort: { key: "value", dir: -1 },
  query: "",
  groups: [],
  symbolGroup: {},
};

function $(id) {
  return document.getElementById(id);
}

function show(id, visible) {
  $(id).classList.toggle("hidden", !visible);
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgNode(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
  return node;
}

function kindLabel(kind) {
  if (kind === "etf") return "ETF";
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

function percent(weight, digits = 1) {
  return (weight * 100).toFixed(digits) + "%";
}

function money(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
  } catch (error) {
    return amount.toLocaleString(undefined, { maximumFractionDigits: 2 }) + (currency ? " " + currency : "");
  }
}

function animateNumber(node, target, format, duration = 900) {
  if (node._frame) cancelAnimationFrame(node._frame);
  if (reduceMotion) {
    node.textContent = format(target);
    return;
  }
  const start = performance.now();
  function tick(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    node.textContent = format(target * eased);
    if (progress < 1) node._frame = requestAnimationFrame(tick);
  }
  node._frame = requestAnimationFrame(tick);
}

function afterPaint(callback) {
  requestAnimationFrame(() => requestAnimationFrame(callback));
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("theme", theme);
  } catch (error) {}
}

async function getPortfolio() {
  let url = "/api/portfolio";
  if (state.accountId) url += "?account_id=" + encodeURIComponent(state.accountId);
  const response = await fetch(url, { credentials: "same-origin" });
  if (response.status === 401) return null;
  if (!response.ok) {
    let detail = "Something went wrong (status " + response.status + ").";
    try {
      detail = (await response.json()).detail || detail;
    } catch (error) {}
    throw new Error(detail);
  }
  return response.json();
}

function renderChips(accounts) {
  const box = $("chips");
  box.replaceChildren();
  if (accounts.length < 2) {
    show("chips", false);
    return;
  }
  const entries = [{ id: "", name: "All accounts" }].concat(accounts);
  for (const entry of entries) {
    const chip = make("button", "chip" + (entry.id === state.accountId ? " active" : ""), entry.name);
    chip.type = "button";
    chip.setAttribute("role", "tab");
    chip.setAttribute("aria-selected", entry.id === state.accountId ? "true" : "false");
    chip.addEventListener("click", () => {
      if (state.accountId === entry.id) return;
      state.accountId = entry.id;
      load(false);
    });
    box.append(chip);
  }
  show("chips", true);
}

function renderGauge(risk) {
  const card = $("gauge-card");
  card.dataset.level = risk.level;
  $("level-label").textContent = LEVEL_TEXT[risk.level] || LEVEL_TEXT.unknown;
  $("score-note").textContent = LEVEL_NOTE[risk.level] || "";
  const fill = $("gauge-fill");
  fill.style.strokeDashoffset = GAUGE_LENGTH;
  afterPaint(() => {
    fill.style.strokeDashoffset = GAUGE_LENGTH * (1 - risk.risk_score / 100);
  });
  animateNumber($("score-num"), risk.risk_score, (value) => String(Math.round(value)), 1100);
}

function renderStats(risk) {
  if (risk.currency) {
    animateNumber($("total"), risk.total_value, (value) => money(value, risk.currency), 1000);
  } else {
    $("total").textContent = "—";
  }
  const other = Object.keys(risk.excluded.other_currencies || {});
  $("total-sub").textContent = other.length ? "in " + risk.currency + " only" : risk.currency ? "in " + risk.currency : "";

  animateNumber($("count"), risk.holding_count, (value) => String(Math.round(value)), 700);
  $("effective").textContent = risk.holding_count
    ? "Acts like " + risk.effective_holdings + " equal-sized " + (risk.effective_holdings === 1 ? "holding" : "holdings")
    : "";

  if (risk.holding_count) {
    animateNumber($("top-weight"), risk.top_holding_weight, (value) => percent(value), 900);
    animateNumber($("top-three"), risk.top_three_weight, (value) => percent(value), 900);
    $("top-symbol").textContent = risk.holdings[0].symbol;
  } else {
    $("top-weight").textContent = "—";
    $("top-three").textContent = "—";
    $("top-symbol").textContent = "";
  }
}

function buildGroups(risk) {
  const holdings = risk.holdings;
  const groups = [];
  state.symbolGroup = {};
  const individual = holdings.length <= MAX_SEGMENTS ? holdings : holdings.slice(0, MAX_SEGMENTS - 1);
  individual.forEach((holding, index) => {
    groups.push({ key: holding.symbol, label: holding.symbol, weight: holding.weight, color: COLORS[index] });
    state.symbolGroup[holding.symbol] = holding.symbol;
  });
  if (holdings.length > individual.length) {
    const rest = holdings.slice(individual.length);
    groups.push({
      key: "__other",
      label: "Other (" + rest.length + ")",
      weight: rest.reduce((sum, holding) => sum + holding.weight, 0),
      color: OTHER_COLOR,
    });
    for (const holding of rest) state.symbolGroup[holding.symbol] = "__other";
  }
  state.groups = groups;
}

function setActive(key) {
  const donut = document.querySelector(".donut");
  const legend = $("legend");
  donut.classList.toggle("has-active", Boolean(key));
  legend.classList.toggle("has-active", Boolean(key));
  for (const node of document.querySelectorAll("#donut-segs .seg, #legend li, #holdings tr")) {
    node.classList.toggle("active", Boolean(key) && node.dataset.group === key);
  }
  const group = state.groups.find((item) => item.key === key);
  if (group) {
    $("donut-main").textContent = percent(group.weight);
    $("donut-sub").textContent = group.label;
  } else {
    $("donut-main").textContent = String(state.data.risk.holding_count);
    $("donut-sub").textContent = state.data.risk.holding_count === 1 ? "holding" : "holdings";
  }
}

function renderDonut(risk) {
  const segments = $("donut-segs");
  const legend = $("legend");
  segments.replaceChildren();
  legend.replaceChildren();
  const empty = risk.holdings.length === 0;
  show("donut-wrap", !empty);
  show("donut-empty", empty);
  if (empty) return;

  let offset = 0;
  const finals = [];
  for (const group of state.groups) {
    const length = group.weight * DONUT_LENGTH;
    const visible = Math.max(length - (state.groups.length > 1 ? 3 : 0), 0.5);
    const circle = svgNode("circle", {
      class: "seg",
      cx: 100,
      cy: 100,
      r: 70,
      stroke: group.color,
      "stroke-dasharray": "0 " + DONUT_LENGTH,
      "stroke-dashoffset": -offset,
    });
    circle.dataset.group = group.key;
    circle.addEventListener("mouseenter", () => setActive(group.key));
    circle.addEventListener("mouseleave", () => setActive(null));
    segments.append(circle);
    finals.push([circle, visible + " " + (DONUT_LENGTH - visible)]);
    offset += length;

    const item = make("li");
    item.dataset.group = group.key;
    const swatch = make("span", "swatch");
    swatch.style.background = group.color;
    item.append(swatch, make("span", "l-name", group.label), make("span", "l-pct", percent(group.weight)));
    item.addEventListener("mouseenter", () => setActive(group.key));
    item.addEventListener("mouseleave", () => setActive(null));
    legend.append(item);
  }
  afterPaint(() => {
    for (const [circle, dash] of finals) circle.setAttribute("stroke-dasharray", dash);
  });
  setActive(null);
}

function renderFlags(flags) {
  const list = $("flags");
  list.replaceChildren();
  const badges = { high: "!", medium: "!", low: "✓", info: "i" };
  flags.forEach((flag, index) => {
    const item = make("li", "flag " + flag.severity);
    item.style.setProperty("--k", index);
    const body = make("div", "f-body");
    body.append(make("span", "f-title", flag.title), make("span", "f-detail", flag.detail));
    item.append(make("span", "f-badge", badges[flag.severity] || "i"), body);
    list.append(item);
  });
}

function updateWhatIf() {
  const risk = state.data.risk;
  const card = document.querySelector(".whatif-result");
  const holding = risk.holdings.find((item) => item.symbol === $("whatif-select").value);
  const range = $("whatif-range");
  const pct = Number(range.value);
  $("whatif-pct").textContent = pct + "%";
  range.style.setProperty("--pct", ((pct - range.min) / (range.max - range.min)) * 100 + "%");
  if (!holding) return;
  const impact = holding.weight * (pct / 100);
  const lost = holding.value * (pct / 100);
  $("whatif-impact").textContent = "−" + percent(impact);
  $("whatif-money").textContent = "about " + money(lost, risk.currency) + " lost";
  $("whatif-text").textContent =
    "If " + holding.symbol + " falls " + pct + "%, your whole portfolio would lose about " + percent(impact) + " of its value.";
  card.classList.remove("pop");
  void card.offsetWidth;
  card.classList.add("pop");
}

function renderWhatIf(risk) {
  show("whatif", risk.holdings.length > 0);
  const select = $("whatif-select");
  const previous = select.value;
  select.replaceChildren();
  for (const holding of risk.holdings.slice(0, 20)) {
    const option = make("option", "", holding.symbol + " (" + percent(holding.weight) + ")");
    option.value = holding.symbol;
    select.append(option);
  }
  if (risk.holdings.some((holding) => holding.symbol === previous)) select.value = previous;
  if (risk.holdings.length) updateWhatIf();
}

function visibleHoldings() {
  const query = state.query.trim().toLowerCase();
  const { key, dir } = state.sort;
  const rows = state.data.risk.holdings.filter(
    (holding) => !query || holding.symbol.toLowerCase().includes(query) || holding.kind.toLowerCase().includes(query)
  );
  rows.sort((a, b) => {
    const left = a[key];
    const right = b[key];
    if (typeof left === "string") return left.localeCompare(right) * dir;
    return (left - right) * dir;
  });
  return rows;
}

function renderTable(animate) {
  const risk = state.data.risk;
  const body = $("holdings");
  body.replaceChildren();
  const rows = visibleHoldings();
  show("empty", rows.length === 0);
  $("empty").textContent = risk.holdings.length ? "Nothing matches your search." : "No holdings to show.";

  for (const button of document.querySelectorAll(".sort")) {
    const on = button.dataset.sort === state.sort.key;
    button.classList.toggle("on", on);
    button.classList.toggle("asc", on && state.sort.dir === 1);
    button.classList.toggle("desc", on && state.sort.dir === -1);
  }

  const fills = [];
  rows.forEach((holding, index) => {
    const groupKey = state.symbolGroup[holding.symbol];
    const group = state.groups.find((item) => item.key === groupKey);
    const row = make("tr", animate ? "row-in" : "");
    row.style.setProperty("--r", index);
    row.dataset.group = groupKey;
    row.addEventListener("mouseenter", () => setActive(groupKey));
    row.addEventListener("mouseleave", () => setActive(null));

    const symbolCell = make("td");
    const symbol = make("span", "sym");
    const swatch = make("span", "swatch");
    swatch.style.background = group ? group.color : OTHER_COLOR;
    symbol.append(swatch, document.createTextNode(holding.symbol));
    symbolCell.append(symbol);

    const shareCell = make("td", "weight-col");
    const wrap = make("div", "weight");
    const bar = make("div", "bar");
    const fill = document.createElement("i");
    fill.style.background = group ? group.color : OTHER_COLOR;
    bar.append(fill);
    wrap.append(bar, make("b", "", percent(holding.weight)));
    shareCell.append(wrap);
    fills.push([fill, Math.min(holding.weight * 100, 100).toFixed(1) + "%"]);

    row.append(symbolCell, make("td", "kind", kindLabel(holding.kind)), make("td", "num", money(holding.value, risk.currency)), shareCell);
    body.append(row);
  });

  afterPaint(() => {
    for (const [fill, width] of fills) fill.style.width = width;
  });
}

function renderAccounts(accounts) {
  const list = $("accounts");
  list.replaceChildren();
  for (const account of accounts) {
    const item = make("li", account.error ? "bad" : "");
    const label = account.error ? account.name + " — could not load: " + account.error : account.name;
    const count = account.error ? "" : account.position_count + (account.position_count === 1 ? " position" : " positions");
    item.append(make("span", "", label), make("span", "a-count", count));
    list.append(item);
  }
}

function replay() {
  const nodes = document.querySelectorAll("#dashboard .reveal");
  nodes.forEach((node, index) => {
    node.classList.remove("reveal");
    void node.offsetWidth;
    node.style.setProperty("--i", index);
    node.classList.add("reveal");
  });
}

function render(data) {
  state.data = data;
  const risk = data.risk;
  renderChips(data.accounts);
  buildGroups(risk);
  renderGauge(risk);
  renderStats(risk);
  renderDonut(risk);
  renderFlags(risk.flags);
  renderWhatIf(risk);
  renderTable(true);
  renderAccounts(data.accounts);
  replay();
}

function showOnly(name) {
  for (const id of ["hero", "loading", "error", "dashboard"]) show(id, id === name);
  show("logout", name === "dashboard");
}

async function load(first) {
  const dashboard = $("dashboard");
  if (first) {
    showOnly("loading");
  } else {
    dashboard.classList.add("refreshing");
  }
  try {
    const data = await getPortfolio();
    dashboard.classList.remove("refreshing");
    if (data === null) {
      state.accountId = "";
      showOnly("hero");
      return;
    }
    showOnly("dashboard");
    render(data);
  } catch (error) {
    dashboard.classList.remove("refreshing");
    $("error-text").textContent = error.message;
    showOnly("error");
  }
}

$("theme").addEventListener("click", () => setTheme(currentTheme() === "dark" ? "light" : "dark"));

$("signin-btn").addEventListener("click", () => {
  $("signin-btn").classList.add("busy");
  $("signin-btn").querySelector(".btn-label").textContent = "Opening SnapTrade…";
});

$("retry").addEventListener("click", () => load(true));

$("logout").addEventListener("click", async () => {
  await fetch("/logout", { method: "POST", credentials: "same-origin" });
  state.accountId = "";
  load(true);
});

$("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderTable(false);
});

for (const button of document.querySelectorAll(".sort")) {
  button.addEventListener("click", () => {
    const key = button.dataset.sort;
    if (state.sort.key === key) {
      state.sort.dir *= -1;
    } else {
      state.sort = { key, dir: key === "symbol" || key === "kind" ? 1 : -1 };
    }
    renderTable(false);
    afterPaint(() => setActive(null));
  });
}

$("whatif-select").addEventListener("change", updateWhatIf);
$("whatif-range").addEventListener("input", updateWhatIf);

window.addEventListener("pageshow", (event) => {
  if (event.persisted) load(true);
});

load(true);
