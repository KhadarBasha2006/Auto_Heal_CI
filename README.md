# Autonomous CI/CD Healing Agent

Paste a GitHub repository URL, your name, a branch name, and a token. The agent
clones the repository, finds real defects, repairs them, validates the result
through a CI/CD-style gate loop, pushes a healed branch, and hands you the link
— while continuously monitoring the pipeline so it can tell **"your code broke
the build"** apart from **"the platform is degraded."**


The dashboard uses a dark glass interface with an animated Three.js node field.
The five workflow controls under the title are clickable: select Detect,
Diagnose, Heal, Validate, or Communicate to reveal that stage's description.
After a run, the dashboard shows live metrics, findings, applied fixes,
validation gates, pipeline health, repository health, the pushed branch link,
and the post-incident report.

---

## Why this exists

GitHub Actions has run at a near-weekly incident cadence through 2026. When it
degrades, runs fail, hang in the queue, or die with actively misleading errors —
including spurious `account suspended` messages for perfectly healthy accounts.
Pipelines can't tell a broken commit from a broken provider, so they retry
blindly through real bugs and open incidents against commits that were never at
fault.

This agent makes that distinction explicitly, shows the evidence behind every
verdict, and responds differently to each case.

---

## What it does

| Stage | Behaviour |
|---|---|
| **Detect** | Clones the repo and scans for syntax errors, tab/space indentation faults, unresolved imports, undefined names and type problems, lint defects, and malformed JSON/YAML — including GitHub workflow files. |
| **Diagnose** | Reads Actions telemetry (failure rate, queue latency, stuck jobs, failing steps) and the provider status page, then classifies the failure as `code`, `platform`, `mixed`, or `unknown` — with every contributing signal and its weight recorded. |
| **Heal** | Applies deterministic repairs first, then model-generated ones. Every model patch must parse **and** reduce that file's problem count **and** preserve every function and class it defined, or it is rolled back. |
| **Validate** | Runs `syntax → imports → lint → compile → tests` gates, looping heal/validate until they pass or no further repair is possible. |
| **Communicate** | Pushes the branch, returns the link, and writes an auto-generated post-incident report with root cause, evidence, repairs, and gate results. |

---

## Quick start

Requires **Python 3.11+** and **Node 18+**.

### macOS / Linux

```bash
git clone https://github.com/KhadarBasha2006/Auto_Heal_CI.git
cd Auto_Heal_CI

# Terminal 1 - backend
pip install -r backend/requirements.txt
python backend/run.py                 # http://127.0.0.1:8000

# Terminal 2 - frontend
cd frontend && npm install && npm run dev    # http://127.0.0.1:5173
```

### Windows (PowerShell)

Windows PowerShell 5.1 does not support `&&` as a statement separator -- use
`;`, or just run the commands on separate lines.

```powershell
git clone https://github.com/KhadarBasha2006/Auto_Heal_CI.git
cd Auto_Heal_CI

# Terminal 1 - backend
pip install -r backend/requirements.txt
python backend/run.py                 # http://127.0.0.1:8000
```

```powershell
# Terminal 2 - frontend
cd frontend
npm install
npm run dev                           # http://127.0.0.1:5173
```

If `python` opens the Microsoft Store, use `py` instead (`py backend/run.py`),
or install Python from python.org with "Add to PATH" ticked.

Open http://127.0.0.1:5173, fill in the form, and click **Analyze & heal
repository**.

The agent always runs the deterministic repair tier. For model-assisted repairs,
choose one of these backends:

| Backend | Setup | API key required |
|---|---|---|
| `anthropic` | Set `ANTHROPIC_API_KEY` | Yes |
| `simple` | Run Ollama, LM Studio, or another OpenAI-compatible local server | No for local servers |
| `openhands` | Install the OpenHands CLI and configure its local model route | No Anthropic key |

For Ollama on Windows, start Ollama and run:

```powershell
ollama pull qwen2.5-coder:7b
$env:HEALER_BACKEND="simple"
$env:LOCAL_LLM_BASE_URL="http://localhost:11434/v1"
$env:LOCAL_LLM_MODEL="qwen2.5-coder:7b"
python backend/run.py
```

For OpenHands:

```powershell
pip install openhands
$env:HEALER_BACKEND="openhands"
python backend/run.py
```

The local agent edits the checkout, then the normal scanner and validation loop
must confirm that the defect count decreased before the repair is credited.

```bash
cp .env.example .env      # then edit
```

---

## API

Two endpoints carry the product; the rest support the live dashboard.

### `GET /api/health`

Liveness plus a capability report, so a deployment problem (no git binary, no
API key, read-only workspace) is visible here rather than surfacing later as a
confusing mid-run failure.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "checks": { "git_binary": true, "ruff": true, "ai_repair_tier": true },
  "config": {
    "repo_backend": "git-cli",
    "model": "claude-opus-5",
    "healer_backend": "simple",
    "local_llm_model": "qwen2.5-coder:7b"
  }
}
```

### `POST /api/analyze`

Starts a run and returns immediately with a job id.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{
        "repo_url": "https://github.com/owner/repo",
        "author_name": "Ada Lovelace",
        "branch_name": "heal/auto-fixes",
        "github_token": "ghp_…"
      }'
```

| Field | Required | Notes |
|---|---|---|
| `repo_url` | yes | Any GitHub URL form: `https://`, `git@`, with or without `.git` |
| `author_name` | yes | Recorded as the commit author |
| `branch_name` | yes | Created by the agent; validated as a legal git ref |
| `github_token` | to push | `repo` scope, or fine-grained *Contents: read & write* |
| `base_branch` | no | Defaults to the repository's default branch |
| `push` / `run_tests` / `use_ai` | no | Default `true`; set `push:false` for a dry run |

Supporting endpoints: `GET /api/jobs/{id}` (snapshot), `GET /api/jobs/{id}/events`
(SSE stream), `GET /api/jobs/{id}/report` (Markdown post-incident report),
`GET /api/health/platform` (live provider status).

---

## How the healing agent decides

The classifier is rule-first and evidence-carrying — every signal that moves the
verdict is recorded with its weight, so a conclusion can be audited rather than
trusted.

**Platform signals** — provider status page degradation, active incidents, queue
latency and stuck jobs, several unrelated workflows failing at once, and error
signatures for auth failures, rate limits, runner-capacity exhaustion, and
network faults.

**Code signals** — test assertions, parse failures, type errors, unresolved
imports, and — the strongest of them — defects reproduced locally by this agent
with no provider involved.

Two rules matter more than the weights:

- **A locally reproduced defect can never be outvoted by a platform outage.**
  Both facts can be true at once, so the verdict floors at `mixed` and the code
  still gets repaired. Otherwise the agent would tell you to fail over a runner
  while a real syntax error sat unfixed.
- **A misleading platform error is still a platform error.** `account suspended`
  during an auth incident classifies as `platform`, never as your fault.

| Diagnosis | Action |
|---|---|
| Runner capacity exhausted | `failover_runner` — parameterise `runs-on:` so the pool can be switched by a repo variable |
| Rate limit / auth / network | `retry_with_backoff` — exponential backoff with full jitter |
| Declared major incident | `reroute_pipeline` |
| Bad workflow config change | `rollback_config` — identifies the commit to revert |
| Code defect | `fix_code` — repair rather than retry a build that would fail identically |

### What runs autonomously, and what doesn't

Actions are split by blast radius:

- **Written into the agent's own branch** (runner failover, repairs) — done
  automatically. They land on a new branch you review, so the worst case is a
  diff you discard.
- **Touching the live pipeline** (re-running jobs, reverting commits on your
  default branch) — planned and reported, but not executed. The agent cannot
  un-run a job it should not have started.

---

## Safety

**The model is never trusted to be right.** A candidate repair is accepted only
if it parses, strictly reduces that file's problem count without adding a new
critical defect, and preserves every function and class the original defined.
Anything else is rolled back to the original file.

That last check exists because problem count alone is a corruptible objective:
deleting the offending function drives it to zero. Deletion is an automatic
rejection, not a winning strategy.

**Your token never leaks.** It is held in memory for the run only, never written
to the job record, and every log line, event, error, and API response is passed
through a scrubber that removes both registered secrets and credential-shaped
strings. Git receives it through `GIT_CONFIG_*` environment variables, so it
never reaches `.git/config` or the process argument list.

**Untrusted archives are treated as untrusted.** Tarball extraction rejects path
traversal, absolute paths, and symlinks before writing anything to disk.

---

## Deployment

### Recommended: one service, one URL

The API process serves the built dashboard, so a single deploy gives you one
link to share — no CORS to configure and no backend address baked into the
frontend bundle.

**Render (free tier, from the included blueprint):**

1. Push this repository to your GitHub account.
2. On [render.com](https://render.com): **New → Blueprint** → pick the repo.
   `render.yaml` supplies the rest.
3. Optionally add `ANTHROPIC_API_KEY` in the dashboard to enable AI repairs.

Your URL is then `https://<service-name>.onrender.com`. Free instances sleep
after inactivity, so the first request after an idle period takes ~50s.

**Updating it.** `autoDeploy` is on, so pushing to the connected branch
redeploys. The image builds the frontend itself, so a UI change ships with the
same push as a backend change -- there is nothing to build locally first.

To deploy without a push (a rollback, or a rebuild after changing an
environment variable), use **Manual Deploy** in the Render dashboard:
*Deploy latest commit* rebuilds from the branch head, and *Clear build cache &
deploy* forces a full rebuild when a dependency change has not been picked up.
Changing an env var alone triggers a restart, not a rebuild.

Watch the deploy log for the two stages: `npm run build` for the dashboard,
then the Python image. The service is live once `/api/health` answers -- that
is the path Render's health check polls.

**Any Docker host** (Fly.io, Railway, Cloud Run, ECS, your own box):

```bash
docker build -t healing-agent .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-… healing-agent
```

Open http://127.0.0.1:8000 — dashboard and API on the same port. The image
includes `git` and `node`, which enable the higher-fidelity repository backend
and JavaScript syntax checking.

### Before you share the URL

This app accepts a **GitHub token** from whoever uses it. On a link you hand
out, that means other people's credentials pass through your server. Tokens are
never logged, never stored on the job record, and every response is scrubbed —
but they are held in memory for the duration of a run. Treat a public
deployment accordingly:

| Setting | For a shared URL |
|---|---|
| `HEALING_AGENT_ALLOW_TEST_EXECUTION` | **`false`** — the default. Enabling it runs submitted repositories' code on your server. |
| `HEALING_AGENT_CORS_ORIGINS` | `none` — the dashboard is same-origin, so nothing else needs access. |
| `HEALING_AGENT_JOB_TIMEOUT` | Lower it (e.g. `600`) to bound what one request can consume. |

If the audience is untrusted, prefer having each person run their own instance
over sharing one that holds everyone's tokens.

### Vercel

Vercel's Python runtime locates a top-level FastAPI instance named `app` by
scanning a fixed set of paths. Ours lives in a package under `backend/`, so
`pyproject.toml` points at it directly:

```toml
[tool.vercel]
entrypoint = "backend.healing_agent.app:app"
```

There is deliberately **no `api/` shim and no rewrite**. Vercel now routes
internal rewrites using the *rewritten* destination path, so a
`"/api/(.*)" -> "/api/index"` rule would hand FastAPI the literal path
`/api/index` and 404 every real route. Instead every request reaches FastAPI,
which already namespaces its routes under `/api` and serves the built
dashboard for everything else.

```bash
npm i -g vercel && vercel
```

**Know the limits before choosing this target.** Vercel functions cap
execution at 60s on Hobby (higher on Pro with fluid compute), and analyzing a
large repository routinely takes longer. There is also no `git` binary, so the
agent falls back to its pure-HTTP repository backend — functional, but the
lesser of the two paths. For anything beyond small repositories, deploy the
Docker image somewhere without a request ceiling.

## Configuration

### A note on dependency versions

`requirements.txt` uses version *ranges*, not exact pins. That is deliberate:
exact pins are more reproducible, but they hard-fail on newer interpreters.
Pinning `pydantic==2.10.4` resolves `pydantic-core 2.27`, which ships no
Python 3.14 wheel -- so on 3.14 pip falls back to a Rust source build that
fails on a stock machine. Ranges let pip pick a build that exists for whichever
Python you have, while the upper bounds still stop a major release from
silently breaking things.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the AI repair tier. Optional. |
| `HEALING_AGENT_MODEL` | `claude-opus-5` | Model for repair and diagnosis |
| `HEALER_BACKEND` | `anthropic` | `anthropic`, `simple`, or `openhands` |
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible local server URL |
| `LOCAL_LLM_MODEL` | `qwen2.5-coder:7b` | Local coding model name |
| `LOCAL_LLM_API_KEY` | `not-needed` | Local server credential, if required |
| `OPENHANDS_BIN` | `openhands` | OpenHands executable name or path |
| `LOCAL_AGENT_TIMEOUT` | `900` | Local agent timeout in seconds |
| `HEALING_AGENT_EFFORT` | `high` | `low` … `max` |
| `GITHUB_TOKEN` | — | Fallback token for local development |
| `HEALING_AGENT_WORKSPACE` | `.workspaces` | Checkout directory; falls back to `/tmp` if read-only |
| `HEALING_AGENT_MAX_ROUNDS` | `3` | Heal/validate rounds |
| `HEALING_AGENT_JOB_TIMEOUT` | `900` | Hard ceiling per run, seconds |
| `HEALING_AGENT_CORS_ORIGINS` | `*` | Comma-separated allowlist, or `none` to block all cross-origin requests |
| `HEALING_AGENT_ALLOW_TEST_EXECUTION` | `false` | Runs submitted repos' test suites, which executes their code. Local/trusted use only |
| `VITE_API_BASE_URL` | same origin | Backend URL for the frontend |

---

## Scoring

Each run is graded out of 100, with the breakdown shown in the dashboard:

- **Repair effectiveness (40)** — severity-weighted, so clearing one syntax
  error outranks ten unused-import cleanups.
- **Validation gates (25)** — share of active gates passing on the final round.
- **Speed (20)** — full credit under 45s, tapering to zero at 420s.
- **Critical defects cleared (15)**.

The displayed component points always add up to the displayed total. A run with
failed validation, or with requested tests blocked by server policy, is capped
below a perfect score. Tests intentionally disabled for a run are shown as
skipped rather than treated as evidence of success.

---

## Project layout

```
api/index.py              Vercel serverless entrypoint
backend/healing_agent/
  app.py                  FastAPI: /api/health, /api/analyze, SSE
  pipeline.py             Run orchestration
  analysis/               Detectors: syntax, indentation, imports, lint, config
  healing/                Two-tier repair + the verification gate
  cicd/                   Telemetry, status page, diagnosis, remediation, reports
  repo/                   Dual backend: git CLI and pure-HTTP GitHub API
  validation.py           CI/CD gate loop
  redaction.py            Secret scrubbing
frontend/src/             React dashboard (Vite)
tests/                    67 tests
```

---

## Tests

```bash
pip install -r backend/requirements.txt
python -m pytest tests
ruff check backend tests
```

Coverage focuses on the properties that matter: every defect category is
detected, repairs never make a file worse, deletion is rejected as a repair,
tokens never reach a snapshot, hostile archives are refused, and the diagnosis
engine reaches the right verdict on code failures, platform outages, misleading
auth errors, and the mixed case.

---

## Known limits

- Detection is deepest for Python. JavaScript gets syntax checking via
  `node --check`; TypeScript is not type-checked. JSON and YAML are validated
  everywhere, with GitHub workflow files graded as critical.
- Validation gates never install dependencies. A validator that reaches the
  network is slow and non-deterministic, and "your build failed because npm was
  down" is exactly the confusion this project exists to remove. `compile` uses
  byte-compilation, and `tests` runs only an already-installable pytest suite.
- Test execution is disabled by default for untrusted submitted repositories.
  Set `HEALING_AGENT_ALLOW_TEST_EXECUTION=true` only on a trusted local
  instance; otherwise a requested test run is reported as blocked and does not
  receive validation credit.
- GitHub branch publishing requires a token with write access to the target
  repository. A 403 permission response cannot be repaired by the agent; use a
  token with Contents read/write permission or push to a repository you own.
- Job state is in-process. Multiple backend replicas need shared storage before
  a job started on one is readable from another.
- Actions telemetry and the status page are best-effort. When either is
  unreachable the agent records that it could not check, rather than assuming
  everything is fine.
