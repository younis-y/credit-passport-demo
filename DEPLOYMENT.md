# Vercel CSV demo

The deployed entrypoint is `server:app`, selected by `pyproject.toml`. Vercel uses the Flask framework and Python 3.12 or 3.13; direct dependencies are pinned. `requirements-dev.txt` adds testing and PDF inspection tools. `requirements-streamlit.txt` retains the original local application's dependencies.

## Deploy

```sh
npx vercel login
npx vercel --prod
```

Use a new Vercel project for this demo. No environment secrets, API keys, database or build command are required. The Flask function and static assets use the repository root; assets live under `public/`. The existing Streamlit `app.py` is excluded by `.vercelignore`. The function has a 30 second maximum duration.

Official runtime reference: https://vercel.com/docs/frameworks/backend/flask

## Behaviour and boundaries

- This deployment is for synthetic data. Do not upload real personal financial information.
- Only CSV input is enabled. PDF is an output format; AI reading of PDF statements is not enabled.
- Uploaded CSVs are processed in memory per request. The application does not write uploads, reports or issued-record files to disk, maintain a server-side session, or call a bank, email or AI service. Hosting infrastructure still handles request metadata; this is not a general data-retention guarantee.
- The browser retains the selected file for the current flow so a PDF request can re-submit the original data. The server re-runs the checks rather than trusting client-supplied findings. Reloading clears that client state.
- No landlord verification code, persistent account, external share link or simulated bank consent is included in this Flask version.
- Currency is selected explicitly. There is no live or invented exchange-rate conversion.
- A passing balance check establishes arithmetic consistency and date order only. It cannot establish authenticity, ownership or completeness; it does not validate the first row against an independent opening balance.
- Recurring credit is a median of a detected group, not total monthly income. Rent is inferred from transaction patterns; timing is measured against the observed usual day, not a contractual payment date.
- Missing rent, irregular credits and negative balances are advisory findings, preserved in the report. Arithmetic or date-order failure prevents report download.

## Synthetic test pack

Run `python scripts/generate_demo_data.py` to regenerate the bundled CSVs, manifest, guide and ZIP. See `synthetic_demo/README.md` for exact outcomes. Run `python -m pytest -q` for automated checks.

## Verification record

Deployment URL and observed live checks are recorded here after a successful deployment. A prepared local build alone is not a deployment.
