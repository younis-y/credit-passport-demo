"use strict";

const phone = document.querySelector(".phone");
const outcome = document.getElementById("outcome");

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

const askForm = document.getElementById("ask-form");
const pounds = (n) => `${n < 0 ? "−" : "+"}£${Math.abs(n).toLocaleString("en-GB", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const day = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {day: "numeric", month: "short"});

document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
  document.getElementById("question").value = chip.dataset.q;
  askForm.requestSubmit();
}));

askForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("question");
  const question = input.value.trim();
  if (!question) return input.focus();
  const answer = document.getElementById("answer");
  const buttons = askForm.parentElement.querySelectorAll("button");
  answer.replaceChildren(node("div", "q", question), node("div", "a thinking", "Claude is reading the payments…"));
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const response = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scenario_id: phone.dataset.scenario, question})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Claude could not answer.");
    const a = node("div", "a");
    a.append(node("b", `verdict ${data.verdict}`, {yes: "Yes", no: "No", unclear: "Unclear"}[data.verdict]));
    const list = node("ul", "points");
    data.points.forEach((point) => {
      const li = node("li");
      li.append(node("span", "", point.text));
      if (point.rows.length) {
        const cite = node("button", "c", `${point.rows.length} ${point.rows.length === 1 ? "payment" : "payments"} ›`);
        cite.type = "button";
        cite.setAttribute("aria-expanded", "false");
        const rows = node("div", "rows");
        rows.hidden = true;
        point.rows.forEach((r) => {
          const e = data.evidence?.[r];
          if (!e) return;
          const line = node("p");
          line.append(node("span", "d", day(e.date)), node("span", "t", e.text), node("b", e.amount < 0 ? "out" : "in", pounds(e.amount)));
          rows.append(line);
        });
        cite.addEventListener("click", () => {
          rows.hidden = !rows.hidden;
          cite.setAttribute("aria-expanded", String(!rows.hidden));
        });
        li.append(cite, rows);
      }
      list.append(li);
    });
    a.append(list, node("p", "foot", "From the PayPal history only. Not a credit decision."));
    answer.replaceChildren(node("div", "q", question), a);
    input.value = "";
  } catch (error) {
    answer.replaceChildren(node("div", "q", question), node("div", "a warn", error.message));
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
});

document.querySelectorAll("[data-decision]").forEach((button) => {
  button.addEventListener("click", async () => {
    const decision = button.dataset.decision;
    document.querySelectorAll("[data-decision]").forEach((b) => { b.disabled = true; });
    try {
      const response = await fetch(`/api/l/${phone.dataset.token}/${decision}`, {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "PayPal did not complete that.");
      outcome.textContent = decision === "accept" ? `✓ Deposit taken · ${data.reference}` : "✓ Hold released · nothing taken";
      outcome.className = "outcome good";
      document.getElementById("decision").hidden = true;
    } catch (error) {
      outcome.textContent = error.message;
      outcome.className = "outcome bad";
      document.querySelectorAll("[data-decision]").forEach((b) => { b.disabled = false; });
    }
    outcome.hidden = false;
  });
});
