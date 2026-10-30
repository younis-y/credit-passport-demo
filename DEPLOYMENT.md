# Vercel CSV demo

The deployed entrypoint is `server:app`, selected by `pyproject.toml`. Vercel uses the Flask framework and Python 3.12 or 3.13; direct dependencies are pinned. `requirements-dev.txt` adds testing and PDF inspection tools. `requirements-streamlit.txt` retains the original local application's dependencies.

## Deploy

```sh
npx vercel login
npx vercel --prod
```

Use a new Vercel project for this demo. No database or build command is required. The PayPal features are optional: set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (sandbox REST app credentials) to show them; without both, the PayPal panels are hidden. `PAYPAL_API_BASE` defaults to `https://api-m.sandbox.paypal.com`. Set `ANTHROPIC_API_KEY` to add Claude's reading; `PASSPORT_AI_MODEL` defaults to `claude-opus-5-5`. The Flask function and static assets use the repository root; assets live under `public/`. The existing Streamlit `app.py` is excluded by `.vercelignore`. Set `SECRET_KEY` (any long random string) so the session cookie and the landlord's signed link work across instances, and `PAYPAL_DEMO_PAYER_EMAIL` (the sandbox buyer's email) as the rent-invoice recipient when nobody has logged in with PayPal. `PAYPAL_LOGIN=1` shows **Log in with PayPal**; turn it on only after enabling Log in with PayPal on the PayPal app with `https://<host>/paypal/login/return` as the return URL and the email and account-verification scopes. The function has a 60 second maximum duration, because the deposit agent makes up to three Claude turns.

Official runtime reference: https://vercel.com/docs/frameworks/backend/flask

## Behaviour and boundaries

- This deployment is for synthetic data. Do not upload real personal financial information.
- Only CSV input is enabled. PDF is an output format; AI reading of PDF statements is not enabled.
- Uploaded CSVs are processed in memory per request. The application does not write uploads, reports or issued-record files to disk or call a bank or email service; Claude and PayPal are called as described below. Hosting infrastructure still handles request metadata; this is not a general data-retention guarantee.
- The browser retains the selected file for the current flow so a PDF request can re-submit the original data. The server re-runs the checks rather than trusting client-supplied findings. Reloading clears that client state.
- With PayPal configured, **Import PayPal history** reads the sandbox account's last six months through the Transaction Search API (31-day windows, balance-affecting records only, amounts net of fees, the account's most-used currency) and runs the same checks. If PayPal returns no running balances, the import is refused rather than balances being invented. The app needs Transaction Search enabled on the PayPal app.
- With `ANTHROPIC_API_KEY` set, every statement that passes the balance check (sample, upload or PayPal import) is sent to Claude as rows of date, description, amount and balance (first 300 rows). Claude labels each row (income, rent, bills, transfer, deposit, other) and writes a short note for the landlord, using structured JSON output and server-side refusal fallbacks. The reading is advisory and shown beside the fixed checks; it never changes a finding or report eligibility. If Claude is unavailable, the checks still return. Identical statements reuse one call per warm instance.
- **Sample PayPal history** (`synthetic_demo/paypal-sample.json`, made by `scripts/make_paypal_sample.py`) is six months of a fictional freelancer stored in the Transaction Search response shape and mapped by the same code as a live import. It is labelled as a sample wherever it is shown. Sandbox history cannot be backdated, which is why the demo needs it.
- **Ask Claude** (`/api/ask`) answers a free question about a statement that passes the balance check in at most three short points, each citing statement rows. Structured JSON output; answers are cached per statement and question on a warm instance.
- **Deposit agent** (`/api/agent`): Claude applies the landlord's rule to the fixed-rule figures and, if it is met, calls `hold_deposit` (Orders v2 with intent `AUTHORIZE`, so the money is held, not taken) and the PayPal Agent Toolkit's `create_recurring_series` and `activate_recurring_series` (monthly rent invoices in GBP). The server checks every call before PayPal sees it: the deposit is at most one week's rent (annual rent / 52, rounded down, the holding-deposit cap in England), and the rent series must be the agreed rent, monthly, GBP, at most 12 cycles, to the applicant. Refused calls go back to Claude as tool errors. The toolkit's `shared/` package is vendored unmodified under `passport/vendor/` (Apache-2.0) because its PyPI wheel pins openai-agents, langchain and crewai-tools.
- **Landlord link**: after the applicant approves the deposit, `/paypal/return` authorises the order and signs a link (`/l/<token>`, no database) carrying the summary and the authorisation ID. The landlord's **Accept** captures the hold and **Refund** voids it.
- **Log in with PayPal** (optional) stores the applicant's name, email and PayPal `verified_account` flag in the signed session cookie; the landlord view shows "PayPal verified" only from that.
- No database, persistent account or simulated bank consent is included in this Flask version; the landlord link is signed, not stored.
- Currency is selected explicitly. There is no live or invented exchange-rate conversion.
- A passing balance check establishes arithmetic consistency and date order only. It cannot establish authenticity, ownership or completeness; it does not validate the first row against an independent opening balance.
- Recurring credit is a median of a detected group, not total monthly income. Rent is inferred from transaction patterns; timing is measured against the observed usual day, not a contractual payment date.
- Missing rent, irregular credits and negative balances are advisory findings, preserved in the report. Arithmetic or date-order failure prevents report download.

## Synthetic test pack

Run `python scripts/generate_demo_data.py` to regenerate the bundled CSVs, manifest, guide and ZIP. See `synthetic_demo/README.md` for exact outcomes. Run `python -m pytest -q` for automated checks.

## Verification record

Verified on 29 October 2026 (PayPal version):

- Production URL: https://credit-passport-paypal.vercel.app, Vercel project `younis11/credit-passport`, deployment `dpl_G4vx9ciymuQoC9jQJ2mL2Wg3oXJr`. Deployment Protection is off so judges need no Vercel login. `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` are set as sensitive production variables.
- Local suite: 106 tests passed.
- Checkout: a GBP 250 sandbox deposit was approved by a sandbox personal account and captured (capture `0VS24437EK787293J`, COMPLETED, net GBP 241.30). In production, PayPal's cancel link returned to the https production URL with the cancellation notice.
- Transaction Search: authorised. The sandbox account then held one transaction, so the import correctly refused to build a statement until more history exists. PayPal's reporting data lags new payments by up to a few hours.
- If Transaction Search returns 403 straight after enabling it in the PayPal dashboard, PayPal is serving a cached token minted before the change; it expires within 9 hours.

Verified on 13 October 2026 (original CSV demo):

Verified on 13 October 2026:

- Public production URL: https://credit-passport-demo.vercel.app
- Dashboard: https://vercel.com/mythicmindlabs/credit-passport-demo
- Vercel deployment: `dpl_2JLepNJ7EmZyBkZ26WUU6ZGCpUUk`; dashboard status Ready.
- Deployed application source: Git commit `0a65c2435ded73e6cc84df01614ac4c74bc26ba1`, uploaded through Vercel Drop. This documentation record was added afterwards.
- Local suite: 90 tests passed. GitHub CI passed on the deployed application commit.
- Public HTTP acceptance: 17 requests, 92 assertions passed across all seven scenarios, report eligibility/refusal, exact downloadable ZIP, individual CSV download and multipart re-upload.
- Browser verification: production page, passing metrics, inconsistent-statement failure, report download and no captured browser warnings/errors.
- Four eligible PDFs were verified as one page, visibly synthetic, and containing expected advisory notes. Edited/out-of-order scenarios returned HTTP 422 for reports, malformed input returned HTTP 400.

The deployment does not have a Git connection. Future GitHub pushes will not automatically redeploy it. The direct upload used the selected account's existing ACME workspace (`mythicmindlabs`); its private-repository Git import could not access this repository. The deployment remains manageable from that dashboard without broadening GitHub permissions.
