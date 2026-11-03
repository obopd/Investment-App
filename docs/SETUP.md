# Setup (all from a phone)

About 15 minutes. Do the steps in order.

## 1. Supabase (database)
1. Go to **supabase.com** → Sign up (GitHub login is easiest) → **New project**.
   - Name: `trading-arena`. Pick a region close to you. Save the database password somewhere safe (you won't need it often).
2. Wait until the project finishes setting up (about 2 minutes).
3. Create the tables: left menu **SQL Editor** → **New query**.
   - In another tab, open this repo on GitHub → `arena/db/schema.sql` → tap **Raw** → select all → copy.
   - Paste into the SQL Editor → **Run**. You should see "Success. No rows returned".
   - Running it again later is safe.
4. Copy two values: **Project Settings** (gear) → **API Keys** / **Data API**.
   - **Project URL** (looks like `https://abcd1234.supabase.co`)
   - **Secret key** (`sb_secret_...`). If you only see legacy keys, use **service_role**.
   - Keep the secret key private: it can write to your database.

## 2. Tiingo (stock prices)
1. Go to **tiingo.com** → Sign up (free plan).
2. Menu → **Account** → **API** → **Token**. Copy the token.

## 3. GitHub Secrets
On GitHub: this repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
(On the phone app this is easiest in the browser: github.com, "Desktop site" not needed.)

| Name | Value |
|---|---|
| `SUPABASE_URL` | Project URL from step 1.4 |
| `SUPABASE_SERVICE_KEY` | Secret key from step 1.4 |
| `TIINGO_API_KEY` | Token from step 2 |
| `SEC_USER_AGENT` | `AI-Trading-Arena marvinlim7501@gmail.com` |

## 4. Turn on Actions
Repo → **Settings** → **Actions** → **General** → "Allow all actions" → Save.

## 5. First run (after the code is merged into `main`)
Repo → **Actions** → **Daily pipeline** → **Run workflow** → Run.
- The first run downloads ~25 years of prices for 31 tickers (a few minutes).
- Green check = done. In Supabase → **Table Editor**, `prices_daily`, `articles` and `runs` should have rows.
- After that it runs by itself every weekday evening (US time).
