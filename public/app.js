"use strict";

const byId = (id) => document.getElementById(id);
let current = null;   // the last statement that passed: {scenario_id} or {file, currency}
let statement = null; // its analysis
let busy = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

const money = (value, digits = 0) => `£${new Intl.NumberFormat("en-GB", {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(Math.abs(value))}`;
const signed = (value) => `${value < 0 ? "−" : "+"}${new Intl.NumberFormat("en-GB", {maximumFractionDigits: 0}).format(Math.abs(value))}`;

function setBusy(value, message) {
  busy = value;
  byId("status").hidden = !value;
  byId("status").textContent = message || "";
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
}

function showError(message, title = "Something went wrong") {
  byId("error-title").textContent = title;
  byId("error-message").textContent = message;
  byId("error").hidden = false;
}

function body(input, extra = {}) {
  if (input.scenario_id) {
    return {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scenario_id: input.scenario_id, ...extra})};
  }
  const form = new FormData();
  form.append("statement", input.file);
  form.append("currency", input.currency);
  Object.entries(extra).forEach(([key, value]) => form.append(key, value));
  return {method: "POST", body: form};
}

async function call(url, options) {
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* not JSON */ }
  if (!response.ok) throw new Error(data.error || "The server could not complete that. Try again.");
  return data;
}

async function analyse(input, message = "Claude is reading the rows…") {
  if (busy) return null;
  byId("error").hidden = true;
  setBusy(true, message);
  try {
    return await call("/api/analyse", body(input));
  } catch (error) {
    showError(error.message, "We could not read this statement");
    return null;
  } finally {
    setBusy(false);
  }
}

// 1 · Tamper test
function fillPane(pane, data) {
  const ok = data.balanced;
  pane.classList.remove("good", "bad");
  pane.classList.add(ok ? "good" : "bad");
  pane.querySelector(".ic").textContent = ok ? "✓" : "✕";
  let note = `All ${data.row_count} rows add up`;
  if (!ok) {
    const first = (data.findings[0].evidence || [])[0];
    note = first && first.expected_balance !== undefined
      ? `Row ${first.row} off by ${data.currency === "GBP" ? money(first.balance - first.expected_balance) : `${data.currency} ${Math.abs(first.balance - first.expected_balance).toLocaleString("en-GB")}`}`
      : "Rows out of order";
  }
  pane.querySelector(".pane-note").textContent = note;
}

document.querySelectorAll(".pane").forEach((pane) => {
  pane.addEventListener("click", async () => {
    const input = {scenario_id: pane.dataset.scenario};
    const data = await analyse(input, pane.dataset.scenario === "paypal-sample" ? "Claude is reading the rows…" : "Checking the running balance…");
    if (!data) return;
    fillPane(pane, data);
    if (data.balanced) showStatement(input, data);
  });
});

// 2 · Statement
function showStatement(input, data) {
  current = input;
  statement = data;
  document.querySelectorAll(".chip").forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.scenario === input.scenario_id)));
  byId("t2").textContent = data.applicant || "Statement";
  byId("source").textContent = `${data.source} · ${data.row_count} rows`;
  const kpis = byId("kpis");
  kpis.replaceChildren();
  const m = data.metrics;
  [[m.income === null ? "–" : money(m.income), "a month"], [m.rent_record || "–", "rent on time"], [m.ratio === null ? "–" : `${Math.round(m.ratio * 100)}%`, "rent share now"]]
    .forEach(([value, label]) => { const k = node("div", "kpi"); k.append(node("b", "", value), node("span", "", label)); kpis.append(k); });
  kpis.hidden = false;

  const labels = {};
  (data.ai && data.ai.rows || []).forEach((row) => { labels[row.row] = row.label; });
  const rows = byId("rows");
  rows.replaceChildren();
  data.rows.forEach((row, index) => {
    const tr = node("tr");
    tr.dataset.row = row.row;
    tr.style.animationDelay = `${Math.min(index, 30) * 25}ms`;
    const label = labels[row.row];
    const tag = node("td");
    if (label) tag.append(node("span", `tag ${label}`, label));
    tr.append(node("td", "date", row.date), node("td", "desc", row.description), node("td", "num", row.amount === 0 ? "" : signed(row.amount)), node("td", "ok", row.row === 1 ? "" : "✓"), tag);
    rows.append(tr);
  });

  const list = byId("check-list");
  list.replaceChildren();
  data.findings.forEach((finding) => {
    const li = node("li");
    li.append(node("span", "", finding.label), node("b", finding.ok ? "ok" : "warn", finding.ok ? "✓" : "!"));
    li.title = finding.detail;
    list.append(li);
  });
  byId("checks").hidden = false;
  byId("download-report").hidden = false;
  if (byId("answer")) byId("answer").replaceChildren();
  updateRent();
}

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", async () => {
    const input = {scenario_id: chip.dataset.scenario};
    const data = await analyse(input, chip.dataset.scenario === "paypal" ? "Fetching your PayPal history…" : undefined);
    if (data && data.balanced) showStatement(input, data);
    else if (data) showError("This statement does not add up, so nothing is built on it.", "Check failed");
  });
});

function highlight(rows) {
  document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.toggle("hl", rows.includes(Number(tr.dataset.row))));
  const first = document.querySelector("#rows tr.hl");
  if (first) first.scrollIntoView({behavior: "smooth", block: "nearest"});
}

// 3 · Ask Claude
byId("ask-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (!current) { showError("Pick a statement first: tap PayPal in step 1.", "No statement yet"); return; }
  const question = byId("question").value.trim();
  const answer = byId("answer");
  answer.replaceChildren(node("div", "q", question));
  byId("error").hidden = true;
  setBusy(true, "Claude is thinking…");
  try {
    const data = await call("/api/ask", body(current, {question}));
    const a = node("div", "a");
    a.append(node("b", `verdict ${data.verdict}`, {yes: "Yes.", no: "No.", unclear: "Unclear."}[data.verdict]));
    data.points.forEach((point) => {
      const line = node("div", "line", point.text);
      if (point.rows.length) {
        const cite = node("button", "c", point.rows.length > 2 ? `${point.rows.length} rows` : point.rows.join(", "));
        cite.type = "button";
        cite.addEventListener("click", () => highlight(point.rows));
        line.append(cite);
      }
      a.append(line);
    });
    answer.append(a);
  } catch (error) {
    showError(error.message, "Claude could not answer");
  } finally {
    setBusy(false);
  }
});

// 4 · Deposit agent
function updateRent() {
  const rent = Number(byId("rent")?.value || 0);
  const run = byId("agent-run");
  if (!run) return;
  const share = statement && statement.metrics && statement.metrics.income ? ` · ${Math.round(rent / statement.metrics.income * 100)}% of income` : "";
  run.textContent = `Run agent${share}`;
}
byId("rent")?.addEventListener("input", updateRent);

const STEP_LABELS = {
  hold_deposit: (s) => ["Deposit", s.result ? s.result.amount.replace("GBP ", "£") : "–"],
  create_recurring_series: () => ["Rent invoices", "created"],
  activate_recurring_series: () => ["Monthly rent", "on"],
};

byId("agent-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (!current) { showError("Pick a statement first: tap PayPal in step 1.", "No statement yet"); return; }
  const steps = byId("agent-steps");
  steps.replaceChildren();
  byId("agent-message").textContent = "";
  byId("approve").hidden = true;
  byId("error").hidden = true;
  setBusy(true, "Claude is applying your rule on PayPal…");
  try {
    const data = await call("/api/agent", body(current, {rent: byId("rent").value, rule: byId("rule").value}));
    data.steps.forEach((step, index) => {
      const [label, value] = (STEP_LABELS[step.tool] || (() => [step.tool, ""]))(step);
      const li = node("li");
      li.style.animationDelay = `${index * 250}ms`;
      li.append(node("span", "", label), node("b", step.ok ? "" : "warn", step.ok ? `${value} ✓` : `✕ ${step.error || "refused"}`));
      steps.append(li);
    });
    if (!data.steps.length) steps.append(node("li", "", "No action: rule not met"));
    byId("agent-message").textContent = data.message;
    if (data.approve_url) {
      const approve = byId("approve");
      approve.href = data.approve_url;
      approve.textContent = `Approve £${data.deposit} on PayPal`;
      approve.hidden = false;
      byId("agent-run").hidden = true;
    }
  } catch (error) {
    showError(error.message, "The agent stopped");
  } finally {
    setBusy(false);
  }
});

// Other statements
async function loadScenarios() {
  try {
    const data = await call("/api/scenarios");
    const grid = byId("scenarios");
    data.scenarios.forEach((scenario) => {
      const button = node("button", "scenario");
      button.type = "button";
      button.append(node("span", "scenario-title", scenario.title), node("span", "scenario-description", scenario.description));
      button.addEventListener("click", async () => {
        const input = {scenario_id: scenario.id};
        const result = await analyse(input);
        if (result && result.balanced) { showStatement(input, result); byId("statement").scrollIntoView({behavior: "smooth"}); }
        else if (result) showError(result.findings[0].detail, "Check failed: nothing is built on this statement");
      });
      grid.append(button);
    });
  } catch (_) { /* the main story does not need the extra samples */ }
}

byId("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = byId("statement-file").files[0];
  const currency = byId("currency").value;
  if (!file || !currency) return;
  const input = {file, currency};
  const data = await analyse(input);
  if (data && data.balanced) { showStatement(input, data); byId("statement").scrollIntoView({behavior: "smooth"}); }
  else if (data) showError(data.findings[0].detail, "Check failed: nothing is built on this statement");
});

byId("download-report").addEventListener("click", async () => {
  if (busy || !current) return;
  setBusy(true, "Preparing the PDF…");
  try {
    const response = await fetch("/api/report", body(current));
    if (!response.ok) throw new Error((await response.json()).error);
    const url = URL.createObjectURL(await response.blob());
    const link = node("a");
    link.href = url;
    link.download = "credit-passport.pdf";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (error) {
    showError(error.message || "The PDF could not be prepared.");
  } finally {
    setBusy(false);
  }
});

// Back from PayPal
function showOutcome() {
  const params = new URLSearchParams(window.location.search);
  const deposit = params.get("deposit");
  const login = params.get("login");
  if (!deposit && !login) return;
  if (deposit === "held" && params.get("landlord")) {
    const link = `${window.location.origin}/l/${params.get("landlord")}`;
    byId("handoff-amount").textContent = (params.get("amount") || "").replace("GBP ", "£");
    byId("landlord-link").href = link;
    byId("copy-link").addEventListener("click", async () => {
      await navigator.clipboard.writeText(link);
      byId("copy-link").textContent = "Copied ✓";
    });
    byId("handoff").hidden = false;
  } else {
    const messages = {
      cancelled: "PayPal payment cancelled. Nothing was held.",
      failed: "PayPal did not hold the deposit. Nothing was taken.",
    };
    byId("notice").textContent = login ? "Log in with PayPal did not finish." : messages[deposit] || messages.failed;
    byId("notice").hidden = false;
  }
  window.history.replaceState(null, "", "/");
}

showOutcome();
loadScenarios();
