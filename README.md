# Model FOMO Comparator

A dependency-free Python proof of concept for choosing up to three models from a
10-model DigitalOcean Serverless Inference catalog. It includes a browser
experience and preserves a scriptable CLI. The default selections remain
`deepseek-3.2`, `kimi-k2.5`, and `qwen3.5-397b-a17b`.

The dropdown also includes DigitalOcean-hosted access to OpenAI GPT-4.1,
GPT-5 Mini, and GPT-4o Mini. Gemini was not included because it is not currently
returned by DigitalOcean's `/v1/models` catalog for this inference key.

## Product walkthrough

Select up to three models from the server-controlled catalog and submit one
prompt with a shared timeout and output budget.

![Prompt entry and three-model selector](docs/images/model-selection.png)

Results appear independently as concurrent requests complete. Each card shows
the normalized status, latency, token usage, response, and copy action.

![Three model responses rendered in parallel](docs/images/parallel-results.png)

The combined panel summarizes operational differences without claiming a
universal quality winner. Recent comparison metadata remains in browser-local
storage only.

![Deterministic comparison summary and recent comparisons](docs/images/comparison-summary.png)

## Run locally

Python 3.10+ is required; there are no third-party packages to install.

1. Create `.env` beside `multi_model_inference.py`:

   ```dotenv
   DO_INFERENCE_API_KEY=your-digitalocean-model-access-key
   ```

2. Start the web app and open <http://127.0.0.1:8000>:

   ```bash
   python3 multi_model_inference.py --serve
   ```

The key is loaded by Python and used only in the server-to-server authorization
header. It is never included in HTML, browser JavaScript, results, or logs.
`DO_INFERENCE_TIMEOUT` optionally changes the per-model web timeout from 30
seconds. Values are bounded to 1–300 seconds. An HTTP error such as 400 fails that
model immediately; reaching the cutoff marks it Timed out. Neither prevents the
other selected models from completing or the comparison panel from rendering.

The CLI remains available:

```bash
python3 multi_model_inference.py "Explain horizontal pod autoscaling"
python3 multi_model_inference.py "query" --models deepseek-3.2 kimi-k2.5
python3 multi_model_inference.py "query" --json-output results/run.json
```

Validate locally with:

```bash
python3 -m unittest -v
python3 -m py_compile multi_model_inference.py
```

## Backend architecture and end-to-end request flow



```text
Browser
  │ POST /api/compare {prompt, model IDs}
  ▼
Python comparison API
  ├─ authenticate/configure (PoC: one server-side inference key)
  ├─ validate prompt and model allow-list
  ├─ create one isolated task per selected model
  ├─ fan out concurrently with a per-model deadline
  ├─ normalize every provider outcome into one result contract
  ├─ stream completed results as NDJSON
  └─ calculate a deterministic operational comparison
  │
  ├──────────────┬──────────────┐
  ▼              ▼              ▼
Model A        Model B        Model C
  └──────── DigitalOcean Serverless Inference ────────┘
```

The end-to-end flow is:

1. `GET /api/config` returns the server-controlled model allow-list, display
   labels, defaults, maximum selection count, and timeout. The client cannot
   submit arbitrary provider URLs or model IDs.
2. `POST /api/compare` validates the request size, prompt, and one-to-three unique
   model IDs before doing billable work. The DigitalOcean key is read from the
   server environment and never crosses the browser boundary.
3. A bounded thread pool starts all selected calls concurrently. Each request has
   the same prompt, system instruction, sampling configuration, output budget,
   and deadline so the operational comparison is explainable.
4. Each task independently maps its outcome to `Complete`, `No final answer`,
   `Failed`, or `Timed out`. An HTTP 400 ends immediately as `Failed`; it is not
   retried. A slow request ends at the configured deadline. Neither blocks valid
   peer results.
5. The API emits one NDJSON event whenever a model finishes. This avoids waiting
   for the slowest model before showing useful work and does not require WebSocket
   infrastructure for a one-directional result stream.
6. After all tasks reach a terminal state, the backend calculates fastest
   successful model and shortest successful output when token usage exists. The
   recommendation uses only status, latency, and output tokens.


`multi_model_inference.py` currently combines HTTP transport, orchestration,
DigitalOcean integration, safeguards, and result aggregation.

## DigitalOcean Serverless Inference

The backend sends OpenAI-compatible chat-completion requests to
`https://inference.do-ai.run/v1/chat/completions`. Every selected model receives
the same system instruction, user prompt, temperature, and output-token budget.
Create a model access key in the DigitalOcean Gradient AI Platform, grant it
access to the configured models, and set it as `DO_INFERENCE_API_KEY`.

 Gaurdrail - Prompt checks block likely credentials and excessive input , displayed outputs redact
likely secrets and PII.

## Backend design choices and tradeoffs

| Decision | Why it was chosen | Tradeoff and production response |
| --- | --- | --- |
| Server-side DigitalOcean key | Prevents credentials from reaching browser code, history, or exports. | A single key has a large blast radius. Production should use secret-scoped runtime configuration, rotation, tenant authorization, and separate keys/environments. |
| Concurrent bounded fan-out | Reduces comparison wall time from the sum of three calls to approximately the slowest call. | Multiplies token cost and rate-limit pressure. Add admission control, per-tenant concurrency and token budgets, caching, and selective routing. |
| One deadline per model | A slow model cannot hold the whole user experience indefinitely. | A strict cutoff can discard an answer that would have completed. Tune deadlines by workload/model from observed percentiles and support cancellation propagation. |
| No retry for HTTP 400 | A 400 is normally a permanent request/model compatibility error; retrying wastes time and money. | Transient 429 and 5xx failures may merit bounded exponential-backoff retries with jitter, but retries must remain visible in latency and cost metrics. |
| Normalized result contract | Keeps provider payload differences out of application and UI logic and prevents reasoning fields from leaking. | Lowest-common-denominator fields can hide useful provider features. Preserve selected metadata in an internal versioned schema while maintaining a strict public allow-list. |
| NDJSON response stream | Works with `fetch`, allows partial cards, and is simpler than bidirectional WebSockets. | Disconnect recovery is limited. Production should assign a comparison ID, persist state, support reconnect/resume, and cancel abandoned work. |
| Shared prompts and settings | Makes latency and length differences easy to explain for one run. | Models have different optimal parameters and context behavior. Store versioned per-model presets and compare them against a controlled baseline. |
| Deterministic comparison | Adds no judge latency/cost and never presents subjective scoring as fact. | It measures operations, not correctness. Quality and safety belong in offline/controlled evaluations plus human review. |
| Local-only history | Delivers useful recents without a database, account model, or retention risk on the server. | It cannot support teams, auditability, cross-device use, or recovery. Production persistence must be opt-in, encrypted, tenant-isolated, and governed by retention policy. |
| Python standard-library server | Keeps setup dependency-free and makes the PoC easy to review. | It is not a production application server. Replace it with a supported framework/runtime, structured middleware, graceful shutdown, connection limits, and load testing. |

## Why there is no universally “best” model

One prompt cannot establish general quality. Results vary by task, language,
context, model version, safety requirements, latency target, and budget. The
fastest answer may be less useful, and the shortest may merely be terse. The app
therefore describes operational differences for this run and leaves answer
quality to the customer rather than presenting an ungrounded leaderboard.

## Longer-term production roadmap

### Phase 1 — Production backend foundation

- Split the code into a stateless API, comparison orchestrator, DigitalOcean
  inference adapter, policy layer, and result aggregator. Package it as a
  container in [DigitalOcean Container Registry](https://docs.digitalocean.com/products/container-registry/)
  and deploy the API and workers on
  [DigitalOcean App Platform](https://docs.digitalocean.com/products/app-platform/).
- Use App Platform readiness/liveness checks against `/healthz`, rolling deploys,
  encrypted runtime secrets, and request- or P95-latency-based autoscaling. Keep
  at least two API instances in production and test graceful shutdown while
  comparisons are active.
- Introduce authenticated tenant/workspace boundaries. Store comparison metadata,
  model policy, audit events, and retention settings in
  [DigitalOcean Managed PostgreSQL](https://docs.digitalocean.com/products/databases/postgresql/).
  Store large customer-approved exports and evaluation datasets in private
  [DigitalOcean Spaces](https://docs.digitalocean.com/products/spaces/) buckets
  with short-lived access.
- Use [DigitalOcean Managed Valkey](https://docs.digitalocean.com/products/databases/valkey/)
  for rate limiting, idempotency keys,
  short-lived result state, cancellation signals, and cache entries. Use TLS,
  trusted sources, and client-side connection pooling; PostgreSQL remains the
  durable source of truth.
- Add per-tenant request, concurrency, token, and daily/monthly spend limits before
  fan-out. Estimate maximum cost at admission time and reject or reduce fan-out
  when a budget cannot cover the run.
- Add explicit total and per-attempt deadlines, client disconnect cancellation,
  circuit breakers per model, and retry only for selected 429/5xx/network errors
  with bounded exponential backoff and jitter.

### Phase 2 — Observability, security, and operational quality

- Generate a comparison ID and child request ID for every model call. Record
  status, model/version, attempts, time to first token, end-to-end latency, input
  and output tokens, estimated cost, and redaction counts—never credentials or
  raw chain-of-thought.
- Send structured App Platform logs to an approved destination, use **App Platform
  alerts** for deployment/scaling failures and CPU/RAM/restarts, and add
  [DigitalOcean Uptime](https://docs.digitalocean.com/products/uptime/) checks for
  public health endpoints. Define SLOs for API
  availability, comparison completion, P95 latency, timeout rate, and result-stream
  reconnect success.
- Add authentication, authorization, CSRF/origin controls, request-size and content
  limits, key rotation, dependency/container scanning, least-privilege network
  access, encryption in transit/at rest, audit trails, and configurable retention.
- Add contract tests against DigitalOcean's model catalog, failure-injection tests,
  concurrency/load tests, timeout/cancellation tests, and disaster-recovery drills.
  Canary releases compare error, latency, and cost signals before full rollout.

### Phase 3 — Evidence-based quality and release gates

- Build versioned [DigitalOcean Evaluations](https://docs.digitalocean.com/products/inference/how-to/evaluate-models/)
  using customer-approved datasets
  split by task, language, risk, and traffic frequency. Measure correctness,
  completeness, groundedness, harmfulness/safety, latency, tokens, and cost.
- Treat automated judging as advisory. Calibrate it with blinded **human review**,
  domain experts for high-impact use cases, inter-rater agreement, and documented
  adjudication. Never expose evaluator chain-of-thought.
- Establish release gates for new models, model versions, prompts, guardrails, and
  routing policies. Block promotion when a star quality/safety metric regresses,
  a critical safety case fails, latency exceeds its SLO, or cost exceeds budget.
  Keep datasets and thresholds versioned so every decision is reproducible.
- Use shadow traffic and canaries with redacted or synthetic inputs before sending
  production traffic to a new configuration. Provide automatic rollback to the
  last known-good model policy.

### Phase 4 — Routing instead of default three-model fan-out

- Feed evaluation and production telemetry into
  [DigitalOcean Inference Router](https://docs.digitalocean.com/products/inference/how-to/use-inference-router/).
  Define workload-specific routes for coding, support, extraction, writing, and
  multilingual tasks, prioritizing optimal quality, cost efficiency, or speed as
  appropriate.
- Start in shadow mode: compare the router's chosen model against the existing
  three-model fan-out and measure routing regret, quality, latency, and cost.
  Promote gradually only after evaluation and human-review gates pass.
- Use single-model routing for routine prompts, fall back to another model on
  bounded retryable failure, and reserve parallel fan-out or human escalation for
  low-confidence, high-risk, or high-value requests. This turns the comparator
  from the steady-state architecture into the evidence-gathering and debugging
  surface for a more efficient production router.

The target production outcome is not a global model leaderboard. It is a
tenant-specific, measurable policy that selects a suitable model for a defined
workload within quality, safety, latency, reliability, and budget constraints.

## Security notes

`.env`, generated reports, caches, and virtual environments
are ignored by Git. Never put the access key in browser code, command output,
screenshots, commits, or exported comparison JSON.
