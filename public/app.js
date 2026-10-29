"use strict";

const byId = (id) => document.getElementById(id);
let selectedInput = null;
let busy = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function setBusy(value, message = "Checking the statement…") {
  busy = value;
  byId("status").hidden = !value;
  byId("status").textContent = message;
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
  byId("result").setAttribute("aria-busy", String(value));
}

function showError(message, title = "We could not read this statement") {
  byId("error-title").textContent = title;
  byId("error-message").textContent = message;
  byId("error").hidden = false;
  byId("error").scrollIntoView({behavior: "smooth", block: "center"});
}

function requestOptions(input) {
  if (input.scenario_id) {
    return {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({scenario_id: input.scenario_id})};
  }
  const form = new FormData();
  form.append("statement", input.file);
  form.append("currency", input.currency);
  return {method: "POST", body: form};
}

async function responseError(response) {
  try {
    const data = await response.json();
    return data.error || "The request could not be completed. Please try again.";
  } catch (_) {
    return "The server could not complete the request. Please try again.";
  }
}

const formatNumber = (value) => new Intl.NumberFormat("en-GB", {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(value);
const formatAmount = (value, currency) => value === null ? "Not found" : `${currency} ${new Intl.NumberFormat("en-GB", {maximumFractionDigits: 0}).format(value)}`;
const formatDate = (value) => new Intl.DateTimeFormat("en-GB", {day:"numeric", month:"short", year:"numeric", timeZone:"UTC"}).format(new Date(`${value}T12:00:00Z`));

function renderMetrics(data) {
  const container = byId("metrics");
  container.replaceChildren();
  container.hidden = !data.balanced;
  if (!data.balanced) return;
  const items = [
    ["Typical regular credit", formatAmount(data.metrics.income, data.currency), "Estimate from recurring payments"],
    ["Typical rent payment", formatAmount(data.metrics.rent, data.currency), "Identified by label or payment pattern"],
    ["Rent / regular credit", data.metrics.ratio === null ? "Not available" : `${Math.round(data.metrics.ratio * 100)}%`, "Ratio of detected typical amounts"],
  ];
  items.forEach(([label, value, note]) => {
    const card = node("div", "metric");
    card.append(node("p", "metric-label", label), node("p", "metric-value", value), node("p", "metric-note", note));
    container.append(card);
  });
}

function renderEvidence(finding, currency) {
  const details = node("details", "evidence");
  const label = finding.evidence_total > finding.evidence.length
    ? `Show ${finding.evidence.length} of ${finding.evidence_total} evidence rows`
    : `Show ${finding.evidence_total} evidence row${finding.evidence_total === 1 ? "" : "s"}`;
  details.append(node("summary", "", label));
  const wrap = node("div", "table-wrap");
  const table = node("table");
  const thead = node("thead");
  const head = node("tr");
  const expected = finding.evidence.some((row) => Object.prototype.hasOwnProperty.call(row, "expected_balance"));
  const headers = ["Row", "Date", "Description", `Amount (${currency})`, "Balance"];
  if (expected) headers.push("Expected balance");
  headers.forEach((title, index) => { const th = node("th", index >= 3 ? "numeric" : "", title); th.scope = "col"; head.append(th); });
  thead.append(head);
  table.append(thead);
  const tbody = node("tbody");
  finding.evidence.forEach((record) => {
    const row = node("tr");
    row.append(node("td", "", record.row), node("td", "", record.date), node("td", "description", record.description), node("td", "numeric", formatNumber(record.amount)), node("td", "numeric", formatNumber(record.balance)));
    if (expected) row.append(node("td", "numeric", formatNumber(record.expected_balance)));
    tbody.append(row);
  });
  table.append(tbody);
  wrap.append(table);
  details.append(wrap);
  return details;
}

function renderResult(data) {
  const hasNotes = data.findings.some((finding) => !finding.ok);
  byId("result-title").textContent = data.balanced ? "Your statement summary" : "The checks found a problem";
  const badge = byId("result-badge");
  badge.className = `result-badge${!data.balanced ? " failed" : hasNotes ? " note" : ""}`;
  badge.textContent = !data.balanced ? "Check failed" : hasNotes ? "Balance check passed · notes found" : "Balance check passed";
  byId("result-summary").textContent = data.balanced
    ? "The running balances add up and the dates are in order. Review every finding below; this does not establish that the statement is authentic."
    : "The running balances or date order did not pass. No income summary or PDF is issued from this statement. Inspect the finding below.";
  byId("result-meta").textContent = `${data.source} · ${data.filename} · ${data.row_count} rows · ${formatDate(data.period[0])} – ${formatDate(data.period[1])}`;
  renderMetrics(data);
  byId("report-bar").hidden = !data.balanced;
  if (byId("deposit-bar")) byId("deposit-bar").hidden = !data.balanced;
  const findings = byId("findings");
  findings.replaceChildren();
  data.findings.forEach((finding) => {
    const article = node("article", "finding");
    const head = node("div", "finding-head");
    const status = node("span", `finding-status${finding.ok ? "" : data.balanced ? " note" : " failed"}`, finding.ok ? "PASS" : data.balanced ? "NOTE" : "FAIL");
    const body = node("div");
    body.append(node("h4", "", finding.label), node("p", "", finding.detail));
    head.append(status, body);
    article.append(head);
    if (finding.evidence.length) article.append(renderEvidence(finding, data.currency));
    findings.append(article);
  });
  byId("result").hidden = false;
  byId("result-title").focus({preventScroll: true});
  byId("result").scrollIntoView({behavior: "smooth", block: "start"});
}

async function analyse(input) {
  if (busy) return;
  selectedInput = null;
  byId("result").hidden = true;
  byId("error").hidden = true;
  document.querySelectorAll(".scenario").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.scenario === input.scenario_id)));
  setBusy(true);
  try {
    const response = await fetch("/api/analyse", requestOptions(input));
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json();
    selectedInput = input;
    renderResult(data);
  } catch (error) {
    showError(error.message || "The connection was interrupted. Please try again.");
  } finally {
    setBusy(false);
  }
}

async function loadScenarios() {
  try {
    const response = await fetch("/api/scenarios");
    if (!response.ok) throw new Error("Sample scenarios could not be loaded. Refresh the page to try again.");
    const data = await response.json();
    const grid = byId("scenarios");
    grid.replaceChildren();
    data.scenarios.forEach((scenario, index) => {
      const button = node("button", "scenario");
      button.type = "button";
      button.dataset.scenario = scenario.id;
      button.setAttribute("aria-pressed", "false");
      const expectation = scenario.expected.error ? "Expected: input rejected" : scenario.expected.verified === false ? "Expected: check fails" : [scenario.expected.income_ok, scenario.expected.rent_ok, scenario.expected.buffer_ok].includes(false) ? "Expected: passes with a note" : "Expected: all checks pass";
      button.append(node("span", "scenario-number", `SAMPLE ${String(index + 1).padStart(2, "0")} · ${scenario.currency}`), node("span", "scenario-title", scenario.title), node("span", "scenario-description", scenario.description), node("span", "scenario-outcome", expectation));
      const arrow = node("span", "scenario-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      button.append(arrow);
      button.addEventListener("click", () => analyse({scenario_id: scenario.id}));
      grid.append(button);
    });
  } catch (error) {
    byId("scenarios").replaceChildren(node("p", "muted", error.message));
  }
}

byId("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (busy) return;
  const file = byId("statement").files[0];
  const currency = byId("currency").value;
  if (!file || !currency) return;
  byId("error").hidden = true;
  if (!file.name.toLowerCase().endsWith(".csv")) { showError("Choose a CSV file. PDF reading is not enabled in this demo."); return; }
  if (file.size > 2 * 1024 * 1024) { showError("The CSV is too large. Use a file smaller than 2 MB."); return; }
  analyse({file, currency});
});

byId("download-report").addEventListener("click", async () => {
  if (busy || !selectedInput) return;
  byId("error").hidden = true;
  setBusy(true, "Rechecking the original data and preparing your PDF…");
  try {
    const response = await fetch("/api/report", requestOptions(selectedInput));
    if (!response.ok) throw new Error(await responseError(response));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = node("a");
    link.href = url;
    link.download = "credit-passport-demo.pdf";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (error) {
    showError(error.message, "The PDF could not be prepared");
  } finally {
    setBusy(false);
  }
});

byId("paypal-import")?.addEventListener("click", () => analyse({scenario_id: "paypal"}));

byId("deposit-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  byId("error").hidden = true;
  setBusy(true, "Opening PayPal Checkout…");
  try {
    const response = await fetch("/api/paypal/deposit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({amount: byId("deposit-amount").value})});
    if (!response.ok) throw new Error(await responseError(response));
    window.location.assign((await response.json()).approve_url);
  } catch (error) {
    showError(error.message, "The deposit could not be started");
    setBusy(false);
  }
});

function showDepositOutcome() {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("deposit");
  if (!outcome) return;
  const messages = {
    paid: `Deposit paid: ${params.get("amount")} · PayPal reference ${params.get("ref")}`,
    cancelled: "The PayPal payment was cancelled. Nothing was charged.",
    failed: "PayPal did not complete the payment. Nothing was charged.",
  };
  byId("deposit-notice").textContent = messages[outcome] || messages.failed;
  byId("deposit-notice").hidden = false;
  window.history.replaceState(null, "", "/");
}

showDepositOutcome();
loadScenarios();
