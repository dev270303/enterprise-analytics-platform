# Going live with Phases 2–4

You already have Phase 1 running (Spring Boot on Render + Neon). This guide adds the Python
**data & AI service**, the **AI key**, the **owner account**, the **daily e-mail report** and **Power BI**.
Allow about 45 minutes. Everything stays on free plans.

```
Browser ─▶ enterprise-analytics (Java, Render) ──▶ Neon Postgres
                     │ X-Internal-Token                ▲
                     ▼                                  │
           enterprise-analytics-ai (Python, Render) ────┘──▶ Gemini API (free key)
GitHub Actions (daily 08:00 IST) ───────────────────────┘
```

---

## Step 1: Merge the pull request

1. Open the repository on GitHub → **Pull requests** → *Phases 2-4: data engineering, AI analyst, automated insights*.
2. Check that all checks are green (Backend, Data & AI service, Docker images).
3. Click **Ready for review** (if it is still a draft), then **Merge pull request** → **Confirm merge**.

The Java service redeploys by itself. On start it runs the new database migrations (V3–V7) on Neon.
Wait until the Render **Logs** show `Started EapApplication` again.

## Step 2: Get a free AI key (Google Gemini)

1. Go to <https://aistudio.google.com> and sign in with a Google account.
2. Click **Get API key** → **Create API key** (in a new or existing project).
3. Copy the key (it starts with `AIza…`) and keep it private.

The free tier is enough for a portfolio demo. The app also enforces its own limits (15 questions per user per hour and 300 per day).
On Gemini's free tier Google may use prompts to improve its models. All the data here is synthetic, so that's fine.

> **Model names change often.** The defaults are `gemini-3.6-flash` (chat) and `gemini-embedding-2` (embeddings).
> If Render logs show `404 … model not found`, open AI Studio → **Models**, pick a current Flash model, and set
> `LLM_MODEL` (and `EMBEDDING_MODEL`) on **enterprise-analytics-ai → Environment**. No code change is needed.
> If embeddings fail, document search quietly falls back to keyword search.
>
> Any OpenAI-compatible provider works instead (Groq, OpenRouter, OpenAI): change `LLM_BASE_URL`, `LLM_MODEL`
> and `LLM_API_KEY`. If your provider has no embeddings endpoint, remove `EMBEDDING_MODEL`. Document search then uses keyword search only.

## Step 3: Copy two values from Neon

In the Neon console → your project → **Connect** (with **Connection pooling OFF**), copy the connection string **as-is**:

```
postgresql://neondb_owner:npg_xxx@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

That whole string is the Python service's `DATABASE_URL`. (The Java service keeps its existing `DB_URL`, `DB_USERNAME` and `DB_PASSWORD`.)

## Step 4: Create the AI service on Render (Blueprint sync)

1. Render dashboard → **Blueprints** → open the blueprint you created in Phase 1.
   (If you created the service manually instead, use **New → Blueprint** and pick the repo.)
2. Click **Manual Sync** (or **Sync**). Render reads the new `render.yaml` and proposes:
   - updating **enterprise-analytics** (new environment variables)
   - creating **enterprise-analytics-ai** (new free web service)
3. It asks for the values marked `sync: false`. Fill them in:

   | Service | Variable | Value |
   |---|---|---|
   | enterprise-analytics | `OWNER_USERNAME` | a private login name, e.g. `dev` (not admin/analyst/manager/employee) |
   | enterprise-analytics | `OWNER_PASSWORD` | a strong password of 10+ characters. **Never share this one.** |
   | enterprise-analytics | `DATA_SERVICE_URL` | `https://enterprise-analytics-ai.onrender.com` |
   | enterprise-analytics-ai | `DATABASE_URL` | the Neon string from Step 3 |
   | enterprise-analytics-ai | `LLM_API_KEY` | the Gemini key from Step 2 |

   `INTERNAL_API_TOKEN` is generated automatically and shared between both services.
4. Click **Apply**. The Python service builds in about 3–5 minutes.
5. Open **enterprise-analytics-ai** in Render and check its URL at the top. If it is *not* exactly
   `https://enterprise-analytics-ai.onrender.com` (Render adds a suffix when a name is taken), copy the real URL,
   then go to **enterprise-analytics → Environment**, set `DATA_SERVICE_URL` to it, and **Save** (it redeploys).

**Check it works:** open `https://enterprise-analytics-ai.onrender.com/health`. You should see `{"status":"UP","llm_configured":true,…}`.

## Step 5: Try everything on the live site

1. Open your site and click **Analyst**.
2. **AI Analyst** tab: the status line should say *Ready · model gemini-3.6-flash · role isolation on*.
   The first time, it can take up to a minute while the free service wakes up.
   Click the example questions. Open **SQL** under an answer to see the generated query.
3. **Customers** tab: the *Machine-learning segments* card says "not computed yet". Sign in as **Admin** → **Admin** tab → **Re-run customer segmentation**. Then reopen Customers.
4. **Insights** tab (as Admin): **Generate a report now**.
5. **Data** tab (as Admin): **Run the whole batch**. You get a full data-quality report. As a demo account it is a dry run, so nothing changes.
6. Sign in with your **owner** account (manual sign-in). On the Data tab, tick **Commit to the database** → **Run the whole batch**.
   This loads the next days of business, so the dashboards move forward in time. Do it whenever you like. Each run continues from the last loaded day.

## Step 6: Daily e-mail report (GitHub Actions)

### 6a. Create a Gmail app password (for sending)
1. Google Account → **Security** → turn on **2-Step Verification** (if it isn't already).
2. Search the account settings for **App passwords** → create one named `EAP reports`.
3. Copy the 16-character password (remove the spaces).

### 6b. Add repository secrets
GitHub → your repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `DATABASE_URL` | the Neon string from Step 3 |
| `LLM_API_KEY` | the Gemini key (optional: adds an AI-written summary) |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the app password from 6a |
| `REPORT_FROM` | your Gmail address |
| `REPORT_RECIPIENTS` | where to send it (comma-separated for several) |

### 6c. Test it
**Actions** tab → **Daily insights report** → **Run workflow** → **Run workflow**.
After about a minute it is green, the e-mail arrives, and the report shows on the dashboard's **Insights** tab.
From now on it runs automatically every day at 08:00 IST.

> GitHub pauses scheduled workflows in repositories with no activity for 60 days. Any commit re-enables them.

## Step 7: Make deployments wait for CI (check)

`render.yaml` sets `autoDeployTrigger: checksPass`, so Render only deploys a commit on `main` after GitHub Actions passes.
Confirm it in Render → each service → **Settings → Build & Deploy → Auto-Deploy**: it should say **After CI Checks Pass**.

## Step 8: Power BI (Windows)

Follow **[docs/POWERBI.md](POWERBI.md)**: create a read-only `bi_reader` login, connect Power BI Desktop to the `bi` star schema,
add the DAX measures, build 4 pages, then commit the `.pbix` and screenshots.

## Step 9: Update your resume and the README

- Replace the screenshots in `docs/screenshots/` if you like (the README already has the new ones).
- Suggested resume bullets are in the README under **Resume bullets**.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| AI tab: "not configured on this deployment" | `DATA_SERVICE_URL` is empty on **enterprise-analytics** (Step 4) |
| AI tab: "invalid internal token" / 401 | Both services must have the same `INTERNAL_API_TOKEN`. In Render, copy it from enterprise-analytics → Environment to enterprise-analytics-ai → Environment |
| AI tab: "starting up or unreachable" | The free service is waking up (about a minute). Try again |
| Answers say "no language model is configured" | `LLM_API_KEY` is missing on **enterprise-analytics-ai** |
| `HTTP 429` from the model | The Gemini free-tier rate limit was hit. Wait a minute, or lower the per-user limit with `AI_DEMO_REQUESTS_PER_HOUR` on the Java service |
| Admin → service status shows *role isolation Off* | Neon did not allow `GRANT ai_reader`. Run this in the Neon SQL editor: `GRANT ai_reader TO neondb_owner;` (the read-only transaction and SQL validator still protect you) |
| Daily workflow fails at "Build and send" with an SMTP error | Recheck `SMTP_USER` and `SMTP_PASSWORD` (it must be the app password, not your normal password) |
| Python service logs `knowledge base indexing skipped` | The database was asleep or not migrated yet. Admin tab → **Re-index policy documents** |
