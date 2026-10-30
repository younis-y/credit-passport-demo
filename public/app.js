"use strict";

const byId = (id) => document.getElementById(id);
const SCREENS = ["start", "passport", "apply", "done"];
const RULE_SHARE = 0.35; // the landlord's rule the agent applies: rent under 35% of income
let source = null;    // "paypal" or "paypal-sample"
let statement = null; // its analysis
let busy = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

const pounds = (value, digits = 0) => `£${Number(value).toLocaleString("en-GB", {minimumFractionDigits: digits, maximumFractionDigits: digits})}`;

function show(screen) {
  SCREENS.forEach((id) => { byId(id).hidden = id !== screen; });
  byId("error").hidden = true;
  window.scrollTo(0, 0);
}

function setBusy(value, message) {
  busy = value;
  byId("status").hidden = !value;
  byId("status").textContent = message || "";
  document.querySelectorAll("button").forEach((button) => { button.disabled = value; });
}

function showError(message) {
  byId("error").textContent = message;
  byId("error").hidden = false;
}

async function call(url, payload) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  let data = {};
  try { data = await response.json(); } catch (_) { /* not JSON */ }
  if (!response.ok) throw new Error(data.error || "The server could not complete that. Try again.");
  return data;
}

async function work(message, task) {
  if (busy) return;
  byId("error").hidden = true;
  setBusy(true, message);
  try {
    await task();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

// 1 · Start
document.querySelectorAll("[data-source]").forEach((button) => {
  button.addEventListener("click", () => openPassport(button.dataset.source));
});

function openPassport(id) {
  return work(id === "paypal" ? "Reading your PayPal history…" : "Reading the sample history…", async () => {
    const data = await call("/api/analyse", {scenario_id: id});
    if (!data.balanced) throw new Error("This history does not add up, so no passport was issued.");
    source = id;
    statement = data;
    fillPassport(data);
    show("passport");
  });
}

// 2 · Passport
function months(period) {
  const [start, end] = period.map((day) => new Date(day));
  return Math.max(1, (end.getFullYear() - start.getFullYear()) * 12 + end.getMonth() - start.getMonth());
}

function fillPassport(data) {
  const m = data.metrics;
  const sample = source === "paypal-sample";
  byId("p-name").textContent = data.applicant || "You";
  byId("p-badge").textContent = sample ? "Sample" : "✓ From PayPal";
  byId("p-income").textContent = m.income ? pounds(m.income) : "–";
  byId("p-period").textContent = m.income ? `a month, last ${months(data.period)} months` : "no regular income yet";
  byId("p-record").textContent = m.rent_record || "–";
  byId("p-rows").textContent = Math.max(0, data.row_count - 1);
  const list = byId("p-checks");
  list.replaceChildren();
  data.findings.forEach((finding, index) => {
    const li = node("li");
    li.style.animationDelay = `${index * 120}ms`;
    li.title = finding.detail;
    li.append(node("span", "", finding.label), node("b", finding.ok ? "ok" : "warn", finding.ok ? "✓" : "!"));
    list.append(li);
  });
  byId("p-thin").hidden = Boolean(m.income);
  byId("to-apply").hidden = !m.income;
  byId("p-sample").hidden = Boolean(m.income);
  byId("fake-result").hidden = true;
}

byId("fake").addEventListener("click", () => work("Checking an edited statement…", async () => {
  const data = await call("/api/analyse", {scenario_id: "edited-balances"});
  const first = (data.findings[0].evidence || [])[0];
  const off = first && first.expected_balance !== undefined
    ? ` · row ${first.row} is off by ${data.currency} ${Math.abs(first.balance - first.expected_balance).toLocaleString("en-GB")}`
    : "";
  byId("fake-result").textContent = `✕ Edited statement rejected${off}`;
  byId("fake-result").hidden = false;
}));

byId("to-apply").addEventListener("click", () => {
  updateRent();
  show("apply");
});

// 3 · Apply
function updateRent() {
  const rent = Number(byId("rent").value || 0);
  const income = statement.metrics.income;
  const share = rent / income;
  const afford = byId("afford");
  afford.textContent = share < RULE_SHARE ? `✓ Affordable · ${Math.round(share * 100)}% of income` : `Over the 35% limit · ${Math.round(share * 100)}% of income`;
  afford.className = share < RULE_SHARE ? "ok-box" : "warn-box";
  // Holding deposit cap in England: one week's rent, annual rent / 52, rounded down to the penny.
  byId("deposit").textContent = pounds(Math.floor(rent * 12 / 52 * 100) / 100, 2);
  byId("steps").replaceChildren();
  byId("agent-message").textContent = "";
  byId("approve").hidden = true;
  if (byId("pay")) byId("pay").hidden = false;
}
byId("rent").addEventListener("input", updateRent);
byId("back").addEventListener("click", () => show("passport"));

const STEP_LABELS = {
  hold_deposit: (s) => ["Deposit requested", s.result ? s.result.amount.replace("GBP ", "£") : ""],
  create_recurring_series: () => ["Rent invoices created", ""],
  activate_recurring_series: () => ["Monthly rent switched on", ""],
};

byId("pay")?.addEventListener("click", () => work("Claude is setting up your deposit on PayPal…", async () => {
  const data = await call("/api/agent", {scenario_id: source, rent: byId("rent").value});
  const steps = byId("steps");
  steps.replaceChildren();
  data.steps.forEach((step, index) => {
    const [label, value] = (STEP_LABELS[step.tool] || (() => [step.tool, ""]))(step);
    const li = node("li");
    li.style.animationDelay = `${index * 250}ms`;
    li.append(node("span", "", label), node("b", step.ok ? "ok" : "warn", step.ok ? `${value} ✓`.trim() : `✕ ${step.error || "refused"}`));
    steps.append(li);
  });
  byId("agent-message").textContent = data.message;
  if (data.approve_url) {
    const approve = byId("approve");
    approve.href = data.approve_url;
    approve.textContent = `Approve £${data.deposit} on PayPal`;
    approve.hidden = false;
    byId("pay").hidden = true;
  }
}));

// 4 · Back from PayPal
function showOutcome() {
  const params = new URLSearchParams(window.location.search);
  window.history.replaceState(null, "", "/");
  if (params.get("connected")) return openPassport("paypal");
  const deposit = params.get("deposit");
  if (deposit === "held" && params.get("landlord")) {
    const link = `${window.location.origin}/l/${params.get("landlord")}`;
    byId("done-amount").textContent = (params.get("amount") || "").replace("GBP ", "£");
    byId("landlord-link").href = link;
    byId("copy-link").addEventListener("click", async () => {
      await navigator.clipboard.writeText(link);
      byId("copy-link").textContent = "Copied ✓";
    });
    show("done");
  } else if (deposit || params.get("login")) {
    const messages = {cancelled: "PayPal payment cancelled. Nothing was held.", failed: "PayPal did not hold the deposit. Nothing was taken."};
    byId("notice").textContent = params.get("login") ? "Connecting PayPal did not finish." : messages[deposit] || messages.failed;
    byId("notice").hidden = false;
  }
}

showOutcome();
