"use strict";

const phone = document.querySelector(".phone");
const outcome = document.getElementById("outcome");

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
