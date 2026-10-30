"use strict";

const phone = document.querySelector(".phone");
const outcome = document.getElementById("outcome");

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

document.getElementById("ask-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.getElementById("question").value.trim();
  const answer = document.getElementById("answer");
  const send = event.target.querySelector("button");
  answer.replaceChildren(node("div", "q", question), node("div", "a thinking", "Claude is reading the history…"));
  send.disabled = true;
  try {
    const response = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scenario_id: phone.dataset.scenario, question})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Claude could not answer.");
    const a = node("div", "a");
    a.append(node("b", `verdict ${data.verdict}`, {yes: "Yes.", no: "No.", unclear: "Unclear."}[data.verdict]));
    data.points.forEach((point) => {
      const line = node("div", "", point.text);
      if (point.rows.length) line.append(node("span", "c", point.rows.length > 2 ? `${point.rows.length} rows` : `row ${point.rows.join(", ")}`));
      a.append(line);
    });
    answer.replaceChildren(node("div", "q", question), a);
  } catch (error) {
    answer.replaceChildren(node("div", "q", question), node("div", "a warn", error.message));
  } finally {
    send.disabled = false;
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
