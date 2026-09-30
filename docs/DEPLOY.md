# Deploying for free: Neon (Postgres) + Render (app)

Allow about 20 minutes. Both services have free plans. Neon does not need a card. Render's free web service normally doesn't either, but Render may ask for card verification on some accounts.

```
Browser ──HTTPS──▶ Render (Docker: Spring Boot + dashboard) ──JDBC/SSL──▶ Neon (PostgreSQL)
```

---

## 1. Push the code to GitHub

```bash
cd enterprise-analytics-platform
git init
git add .
git commit -m "Enterprise Analytics Platform: phase 1"
git branch -M main
git remote add origin https://github.com/<your-username>/enterprise-analytics-platform.git
git push -u origin main
```

Once the push lands, the **CI** workflow runs. It builds the app, runs the Flyway migrations against a throwaway Postgres, runs the integration tests and builds the Docker image. Check the **Actions** tab and wait for a green tick before you deploy.

## 2. Create the database on Neon

1. Sign up at <https://neon.tech> and click **New project**.
   - Name: `enterprise-analytics`
   - Postgres version: default (16 or 17)
   - Region: **AWS Asia Pacific (Singapore)**, the closest to Render's Singapore region
2. Open **Connect** on the project dashboard.
   - **Turn "Connection pooling" OFF.** Flyway migrations need a direct connection, not the PgBouncer pooled endpoint.
   - You will see a string like:
     ```
     postgresql://neondb_owner:AbC123xyz@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
     ```
3. Split it into the three values the app expects:

   | Env var | Value (from the example above) |
   |---|---|
   | `DB_URL` | `jdbc:postgresql://ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require` |
   | `DB_USERNAME` | `neondb_owner` |
   | `DB_PASSWORD` | `AbC123xyz` |

   The only change for `DB_URL` is to prefix `jdbc:` and drop the `user:password@` part. Leave out `channel_binding=require` if the string has it.

You don't need to create any tables. On first start the app runs Flyway, which creates the schema and loads the synthetic dataset (about 40 MB, well under Neon's 0.5 GB free limit).

## 3. Deploy the app on Render

**Option A: Blueprint (uses `render.yaml`)**
1. Sign up at <https://render.com> with your GitHub account.
2. Go to **New → Blueprint** and pick your repo. Render reads `render.yaml`.
3. When it prompts for the variables marked `sync: false`, paste `DB_URL`, `DB_USERNAME` and `DB_PASSWORD`. Render generates `JWT_SECRET` for you.
4. Click **Apply**.

**Option B: Manual web service**
1. Go to **New → Web Service**, pick the repo, set **Runtime: Docker** and **Instance type: Free**, and pick region **Singapore**.
2. Add the environment variables `DB_URL`, `DB_USERNAME`, `DB_PASSWORD`, `JWT_SECRET` (any random string of 32 or more characters) and `DEMO_PASSWORD=demo123`.
3. Set **Health Check Path** to `/api/health`.

The first build takes about 5–8 minutes (Maven downloads dependencies inside Docker). The first boot also runs the migrations, which takes about a minute on the free CPU. When the logs show `Started EapApplication`, open:

```
https://<your-service-name>.onrender.com
```

and click **Analyst**.

## 4. Put it on your resume

- **Live demo:** `https://<your-service-name>.onrender.com`
- **Code:** `https://github.com/<your-username>/enterprise-analytics-platform`

Add the live link to the top of the README (replace the placeholder) and the GitHub repo "About" box.

---

## Free-tier behaviour (worth knowing for interviews)

| What | Why | What the app does about it |
|---|---|---|
| Render sleeps after 15 min without traffic, and the next visit takes about 30–60 s | Free web services spin down when idle | The login page polls `/api/health` and shows a "waking up" status instead of a blank page |
| Neon suspends compute after 5 min idle | Scale-to-zero; the free plan has 100 compute-hours a month | Hikari `minimum-idle: 0` lets connections close; analytics results are cached in the app; `/api/health` never touches the DB |
| Neon wakes in about 1 s | Cold compute start | API retries on `503`, and the connection timeout is 30 s |

**Optional: keep the app warm.** A free uptime monitor such as UptimeRobot can ping `https://<service>.onrender.com/api/health` every 10 minutes. That endpoint doesn't query Postgres, so it keeps Render awake without spending Neon compute-hours. One always-on service fits inside Render's 750 free instance-hours a month. It's optional: the demo works without it, the first visit is just slower.

## If Render asks for a card and you'd rather not add one

The same Docker image runs unchanged on other container hosts with a free tier, such as Koyeb. Point it at the repo's `Dockerfile`, set the same five environment variables and use `/api/health` as the health check. Free-tier terms change often, so check the host's current pricing page first.

## Troubleshooting

| Symptom in Render logs | Fix |
|---|---|
| `FATAL: password authentication failed` | Re-copy `DB_USERNAME` and `DB_PASSWORD` from Neon's Connect dialog |
| `Connection to ... refused` / `SSL` errors | `DB_URL` must start with `jdbc:postgresql://` and end with `?sslmode=require` |
| Flyway hangs or reports `prepared statement ... already exists` | You used the **pooled** host (`-pooler` in the hostname). Switch pooling off in Neon's Connect dialog |
| `app.jwt.secret (JWT_SECRET) must be at least 32 characters` | Set a longer `JWT_SECRET` |
| Out of memory / restarts | Keep `JAVA_OPTS` from the Dockerfile. The free instance has 512 MB |
