# PayPal Verified Income: design (2026-10-30)

Pitch: newcomers to the UK have no credit file but do have PayPal history. Since 1 May 2026 landlords in
England cannot take more than one month's rent upfront, so "pay six months in advance" is gone. Credit
Passport turns PayPal history into verified income, lets the landlord ask Claude about it, and an agent
holds the deposit and sets up rent on PayPal.

## Demo story (one page, minimal text, round-3 mockup styling)
1. Can it be faked? Edited file fails (row named), PayPal history passes.
2. From PayPal: rows with a check mark and Claude's label tag. Sources: sample PayPal history
   (labelled, Transaction Search format, same mapping code) or the live sandbox account.
   Log in with PayPal adds a "PayPal verified" badge (needs the owner to enable it on the app).
3. Ask Claude: free question, answer in short lines, each citing statement rows (chips highlight rows).
4. Agent: landlord's rule ("rent under 35% of income: hold one week's deposit, set up monthly rent").
   Claude calls tools: hold_deposit (ours, Orders v2 intent AUTHORIZE) and the PayPal Agent Toolkit's
   create_product / create_subscription_plan / create_subscription. Server caps amounts: deposit <= one
   week's rent, rent = the agreed rent. Tenant approves both on PayPal.
5. Landlord's phone: signed link (no database) shows income, rent on time, rent share, deposit held,
   Accept (capture the authorisation) or Refund (void it).

## Out of scope
Open Banking stays simulated (no FCA licence; TrueLayer optional later). Deterministic rules still decide
every finding; Claude never changes one.

## Plan
1. Vendor paypal_agent_toolkit/shared 1.11.0 (Apache-2.0; PyPI wheel pins crewai/langchain/openai-agents).
2. paypal.py: sample history, authorize flow (create AUTHORIZE order, authorize, capture, void).
3. ai.py: ask(); agent.py: tool loop with caps.
4. server.py: /api/ask, /api/agent, landlord link + capture/void, Log in with PayPal, rent return.
5. New single-page UI in the mockup style; keep CSV upload and PDF as small secondary actions.
6. Tests for each, live sandbox check, deploy, record in DEPLOYMENT.md.
