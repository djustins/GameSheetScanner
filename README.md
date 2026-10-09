# Ball Hockey Game Sheet → PostgreSQL

Scans handwritten Team Pittsburgh Ball Hockey game sheets (PDF or photo) and stores
the stats — goals, assists, penalties, shootout results — in a PostgreSQL database.

**This is a Streamlit web app.** Run it with `streamlit run app.py`, not as a
standalone Python script — running `app.py` directly with `python` will not work.

There are three ways in, all over the same database and the same logic in
`game_sheet_core.py`:

| Piece | What it is | Where to read more |
|---|---|---|
| `app.py` | The Streamlit app | this file |
| `api.py` | An HTTP API (FastAPI), deployed on Render | [Running the API](#running-the-api) |
| `frontend/` | A React app that talks to the API, deployed on Vercel | [frontend/README.md](frontend/README.md) |

Finished games can also be pulled in automatically from the league's stats site
instead of scanned — see [Syncing from the league stats site](#syncing-from-the-league-stats-site).

## How it works

1. In the app, you upload one or more scanned game sheet files (PDF, PNG, or JPG) —
   multi-page PDFs can be split into individual sheets first.
2. Each sheet is sent to Claude (vision) with a prompt tailored to this exact game
   sheet layout, which returns structured JSON: teams, colors, final score, goal-by-goal
   detail, penalties, and shootout rounds (circled numbers = goals).
3. The extraction opens in an editable form so you can review and correct it before
   saving — nothing is written to the database until you confirm.
4. Confirmed data is inserted into Postgres. The app also has standings, player
   stats, team rosters, an Excel export, and a Schedule tab: upload a season-schedule
   CSV once and it's saved to the database, then compared against stored games (by
   date and matchup) every time you view it — so which games still need a sheet stays
   current as games are added, edited, or removed, with no need to re-upload.
5. Each division (Teams → Divisions) has its own Teams/Coaches/Players sections and an
   **Import Players** uploader (CSV/Excel/ODS). Column headers are matched flexibly
   (e.g. "Player Name"/"Name"/"Full Name" all work), and players are matched to
   existing profiles by name — using birth date too, when given, to tell same-named
   players apart or flag a possible mismatch — rather than creating duplicates.
   Anything the importer can't resolve on its own (an ambiguous name match, or a name
   match with a conflicting birth date) is shown for you to resolve before anything is
   written. A Team column also adds the player to that team's roster; a Coach column
   assigns that coach to the team too.

## Setup

```bash
pip install -r requirements.txt
```

Then create a `.env` file in the project root (loaded automatically on startup —
never overrides a variable already set in the real environment):

```bash
ANTHROPIC_API_KEY=sk-ant-...    # your own API key
DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
# or individually: PGHOST / PGDATABASE / PGUSER / PGPASSWORD / PGPORT / PGSSLMODE

# Optional — only needed if your Postgres is a manageable service (e.g. an
# Aiven service) you sometimes power off to save cost. If all three are
# set, the app checks the service's status on startup and powers it back
# on (waiting for it to come up) before connecting. Get a token from the
# Aiven console under Profile -> API Tokens.
AIVEN_API_TOKEN=...
AIVEN_PROJECT_NAME=your-project
AIVEN_SERVICE_NAME=your-pg-service
```

The schema (`schema_postgres.sql`) is created automatically on first connect.
Migrating an existing `hockey.db` (SQLite) into a fresh Postgres database:

```bash
python scripts/migrate_sqlite_to_postgres.py path/to/hockey.db
```

### Managing the Aiven API token

If you've set `AIVEN_API_TOKEN` (see above), `scripts/aiven_token.py` checks and
rotates it from the command line — deliberately a script, not a button in the app,
since creating or revoking an account-wide API token is a real, hard-to-reverse
action against your Aiven account:

```bash
python scripts/aiven_token.py validate          # confirm it still works, show expiry/last-used
python scripts/aiven_token.py list              # list every token on the account
python scripts/aiven_token.py rotate --description "GameSheetScanner app"
python scripts/aiven_token.py revoke <token_prefix> --yes
```

`rotate` creates a new token and prints it once (Aiven never shows it again) without
touching the old one — copy it into `.env`, restart the app, confirm it still
connects, *then* `revoke` the old token's prefix. Passing `--revoke-old --yes` to
`rotate` does both steps at once, if you're confident you won't need to roll back.

### Restricting database access to Streamlit Community Cloud

By default an Aiven service accepts connections from any IP. `scripts/aiven_ip_filter.py`
restricts this app's Postgres service to just [Streamlit Community Cloud's published
outbound IPs](https://docs.streamlit.io/deploy/streamlit-community-cloud/status), plus
any extra CIDRs you name (e.g. your own machine, for direct/admin access):

```bash
python scripts/aiven_ip_filter.py show                                    # what's currently allowed
python scripts/aiven_ip_filter.py check --extra-cidr <your-ip>/32         # exit 1 if out of sync (cron-friendly)
python scripts/aiven_ip_filter.py sync --extra-cidr <your-ip>/32 --yes    # apply
```

Streamlit's docs explicitly warn that IP list "may change at any time without notice",
and there's no API for it — only that same page to re-check, which is what `check`/`sync`
scrape. The app itself also does a lightweight version of this check on startup (sidebar
warning if the configured filter is missing a current Streamlit IP) and again if the
database connection fails outright, to help tell "Streamlit rotated its IPs" apart from
other causes.

`sync` always prints the exact before/after diff and needs `--yes` to apply — get the diff
right before running with `--yes`, since anything not in the resulting list (including,
potentially, this app's own access) loses its database connection immediately.

### Sign-in

The app requires every session to sign in with an email/password account — there's
no public signup. Accounts are created and their page access managed from the
**User Management** tab, visible only to admins, once at least one admin exists.

Since nobody can reach that tab before an admin exists, bootstrap the first one
from the command line:

```bash
python scripts/manage_users.py add you@example.com --admin
```

`scripts/manage_users.py` also handles `list`, `set-password`, `deactivate`, and
`reactivate` — useful for emergency access if every admin ever gets locked out.
Non-admin accounts are limited to whichever pages an admin has granted them
through a role; admins always see every page. A role can also be marked
**read-only** (can view its pages but not save/create/delete) and/or
**hide contact details** (a player's parent contact *name* stays visible,
but phone/email don't) — both configured per role in User Management.

## Usage

```bash
streamlit run app.py
```

This opens the app in your browser. From there you can upload/split game sheets,
review and save extractions, correct previously-stored games, and browse standings
and stats.

### Command-line scripts

The extraction/DB logic lives in `game_sheet_core.py` (no Streamlit dependency), so
the same logic also backs a couple of terminal scripts useful for batch work or
scripting:

```bash
# Process one sheet, reviewing before it's saved
python process_game_sheet.py game1.pdf

# Process a whole folder's worth at once
python process_game_sheet.py scans/*.pdf

# Skip the review step (trust the extraction, insert directly)
python process_game_sheet.py game1.pdf --no-review

# List stored games and their ids, then correct one from the terminal
python edit_game.py --list
python edit_game.py --game-id 5
```

### Running the API

`api.py` is an HTTP API over the same `game_sheet_core.py` logic and database —
a separate service from the Streamlit app, for anything that needs programmatic
(read or write) access: divisions, teams, coaches, players, rosters, drafts,
evaluations, schedule/games/stats.

```bash
pip install -r requirements-api.txt
export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
uvicorn api:app --reload
```

Then open `http://localhost:8000/docs` for interactive API docs (Swagger UI).

Auth is either HTTP Basic against the same `users` table the Streamlit app's
login screen uses (any existing account works), or a long-lived **API
token** — better for a script or integration that shouldn't hold a real
password. Create one from the Streamlit app's sidebar (**🔑 API Tokens**,
under "Signed in as...") or via `POST /tokens` (needs Basic auth once to
bootstrap); the raw token is shown exactly once, then send it as
`Authorization: Bearer <token>` on every request afterward. It carries
whatever access the user who created it has, and can be revoked (from that
same sidebar section, or `DELETE /tokens/{id}`) without touching their
password.

Writes require the same "not read-only" check the app applies (an admin, or
a non-read-only role); deletes require admin. A move-reason note (`POST
/players/{id}/move`) and move history (`GET /players/{id}/move-notes`) are
admin-only, same as the Streamlit app's Team Rosters page.

**Deploying it**: Streamlit Community Cloud only serves the Streamlit process
itself, so this needs its own host — `render.yaml` at the repo root is a
ready-to-use blueprint for [Render](https://render.com)'s free tier:

1. On Render: **New +** → **Blueprint** → connect this GitHub repo. It reads
   `render.yaml` and creates a `gamesheetscanner-api` web service automatically
   (build: `pip install -r requirements-api.txt`; start:
   `uvicorn api:app --host 0.0.0.0 --port $PORT`).
2. In that service's **Environment** tab, set `DATABASE_URL` to the same
   connection string the Streamlit app uses (`render.yaml` deliberately leaves
   it blank — never commit it).
3. Deploy, then find the service's outbound IP (Render's dashboard shows this
   under the service's **Connect** info, or add a temporary log line that
   hits `https://api.ipify.org` on startup).
4. Add that IP to your Postgres's `ip_filter` alongside Streamlit's own IPs
   and any admin ones:
   ```bash
   python scripts/aiven_ip_filter.py sync --extra-cidr <render-ip>/32 --extra-cidr <your-other-extras> --yes
   ```
   Render's free tier doesn't guarantee that IP stays static long-term —
   re-run `aiven_ip_filter.py check` occasionally (or after a Render
   redeploy) to confirm it's still allowed; a paid Render plan or a host
   with a dedicated static IP (Fly.io, Railway, a small VM) avoids the
   re-check entirely.
5. Your API is now live at `https://gamesheetscanner-api.onrender.com` (or
   whatever Render named it) — `/docs` has the same Swagger UI you saw
   locally.

Prefer a different host (Fly.io, Railway, a VM)? The same three inputs apply
anywhere: `pip install -r requirements-api.txt`, `DATABASE_URL` set, and
`uvicorn api:app --host 0.0.0.0 --port $PORT` (or that host's equivalent) as
the start command.

## Syncing from the league stats site

The league publishes its schedule, scores, goals and penalties at
<https://teampgh-statsandstandings.web.app>. `league_site_sync.py` reads the JSON
API behind that site (the same read-only endpoints its public pages use — no HTML
scraping) and saves the data here, so those games don't have to be scanned.

What it does, per current league on the site:

- **Division** — "Penguin - Fall - 2026" on the site is the division with the same
  year, season and age group here, created if it doesn't exist yet. A league whose
  name isn't one of the app's age groups (`AGE_GROUPS` in `game_sheet_core.py`) is
  skipped and reported — currently "Super League".
- **Schedule** — every scheduled game is upserted into `schedule_games`, the same
  table a schedule CSV upload fills. Practices are left out.
- **Played games** — every game with a final score is saved with its goals and
  penalties, under a `source_file` of `teampgh-site:<game id>`. Running it again
  refreshes that game in place rather than adding a second copy.
- **Standings and stats** — not copied. The app computes both from the games, and
  its points rules match the site's (3 for a win, 2 / 1 for an overtime or shootout
  win / loss).

Things worth knowing:

- **A scanned game wins.** If a game between the same two teams on the same date is
  already stored from a scoresheet, the site's copy is skipped and reported as
  "already here".
- **Jersey numbers.** Stats attach by jersey number per team, so before a team's
  games are saved its roster is lined up with the site's: a rostered player still on
  a draft placeholder (`TBD3`) gets their real number, matched by name, or by
  nickname and last name ("Teddy Davis" finds Theodore "Teddy" Davis). A real number
  that disagrees with the site is reported and left alone. A number nobody on the
  roster wears is added under the site's name for that player, unlinked.
- **Shootouts.** The site records only who won a shootout, so it's stored as one
  round with unknown shooters ("?") — enough to count as a shootout win/loss.
- **Ties.** The app doesn't store tied games, so a tie on the site is reported as
  failed rather than saved.
- **Refresh window.** An imported game is re-read for 7 days after it was played
  (scorekeepers correct sheets), then left as imported. `--refresh-all` re-reads
  everything.

### Running it by hand

```bash
export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
python scripts/sync_league_site.py --dry-run          # report what would change, save nothing
python scripts/sync_league_site.py                    # every current league
python scripts/sync_league_site.py --league Penguin   # just one (repeatable)
python scripts/sync_league_site.py --refresh-all      # re-read every imported game
```

It prints one block per league: games added / refreshed / already here / failed,
how many roster numbers were set, and anything it couldn't resolve.

### Running it automatically

`POST /league-site/sync` on the API (admin only; `?dry_run=true` and
`?refresh_all=true` are supported) runs the same sync on the API server, which
already has database access. `.github/workflows/nightly-league-sync.yml` calls it
every hour at a quarter past, and fails the run — so GitHub emails you — if any
game was refused.

How often a sync actually happens is a setting in the React app: admins open **My
Account → League site sync** and choose every hour (the default), every 2, 3, 4, 6
or 12 hours, once a day at a chosen hour (Pittsburgh time), or off. The job always
knocks hourly; the API answers with an empty list when no sync is due. The same
panel has **Sync now**. Changing the minute of the hour means editing the `cron`
line in the workflow file, and GitHub often starts scheduled jobs several minutes
late.

Each team page shows a **Last updated** banner with the time its division was
last synced (`GET /divisions/{id}/league-sync`).

One-time setup:

1. Sign in to the app as an admin, open **API Tokens**, create a token and copy it.
2. In the GitHub repo: **Settings → Secrets and variables → Actions**, add
   - `LEAGUE_SYNC_TOKEN` — the token from step 1, as a repository **secret**
   - `API_BASE_URL` — the deployed API's address, e.g.
     `https://gamesheetscanner-api.onrender.com`, as a repository secret *or*
     variable (the workflow reads either tab)
3. Test it: **Actions → League site sync → Run workflow**. A run started by hand always syncs, whatever the schedule says.

If a run fails with "… is empty", that name isn't set on this repository, or is
misspelled. To retry after changing the workflow file, start a new run with **Run
workflow** — **Re-run jobs** replays the old commit's version of the workflow.

## Email

The app sends email through [Resend](https://resend.com) from an address on the
league's own domain (`mailer.py`). Four things use it:

- **Messages to parents and coaches** — in the React app, admins open **Email**,
  pick a division (and optionally teams), tick Parents and/or Coaches, and write a
  message. The page lists exactly who will get it, and who won't because there's no
  email on file, before anything is sent. Each person gets their own copy, replies go
  to the admin who sent it, and **Send a test to me** shows how it will look.
- **Invitations** — on User Management, **Email a set-password link** sends a user a
  one-time link (good for 7 days) to choose their own password.
- **Password resets** — "Forgot your password?" on the sign-in page emails a
  one-time link (good for 2 hours). It answers the same way whether or not the
  address has an account.
- **Automatic notices** — after the nightly league-site sync, the admins get an email
  listing new games and any that couldn't be saved. Nothing is sent on a night when
  nothing changed.

Every send is recorded (the **Sent** tab on the Email page, `email_log` in the
database). Until it's configured the app simply sends nothing: the Email page says
so, and the reset and invitation links aren't available.

### One-time setup

1. Create a Resend account, then **Domains → Add Domain** for the domain mail will
   come from (e.g. `tptm.io`). Resend lists a few DNS records; add them wherever the
   domain's DNS is managed and wait for Resend to show the domain as verified.
2. **API Keys → Create API Key** (sending access is enough) and copy it.
3. On Render, in the API service's **Environment** tab, add:

   | Variable | Value |
   |---|---|
   | `RESEND_API_KEY` | the key from step 2 |
   | `EMAIL_FROM` | the from address, e.g. `Team Pittsburgh <noreply@tptm.io>` — must be on the verified domain |
   | `APP_URL` | the React app's address, e.g. `https://tptm.io` — used for links in emails |

4. Save (Render restarts the API), then open **Email** in the app and send yourself a
   test.

Resend's free plan allows 100 emails a day and 3,000 a month, counted per recipient;
a message to a large division can use most of a day's allowance on its own.

## Running tests

Tests run against `TEST_DATABASE_URL` — a separate *database* on the same Aiven
Postgres node as the real one (same server/plan, no extra cost, but a fully
separate namespace: own tables, own everything). Never the production database;
`tests/conftest.py` truncates every table before and after each test, and refuses
to run at all unless the configured database's name contains "test", as a guard
against a misconfigured `TEST_DATABASE_URL` pointing at real league data.

One-time setup:

```bash
pip install -r requirements-dev.txt
python -c "
import os
from dotenv import load_dotenv; load_dotenv()
import aiven_service as av
token, project, service = os.environ['AIVEN_API_TOKEN'], os.environ['AIVEN_PROJECT_NAME'], os.environ['AIVEN_SERVICE_NAME']
av.create_service_database(token, project, service, 'gamesheetscanner_test')
"
# Then add TEST_DATABASE_URL to .env: same as DATABASE_URL, with the dbname
# in the path swapped to gamesheetscanner_test.
```

Then, any time:

```bash
pytest
```

Coverage so far: games (insert/update/delete, shootout/OT resolution, duplicate
detection), schedule import and the schedule+results view (including a direct
regression test for a `list_schedule()` crash that reached production once — see
"Fix crash in list_schedule()" in git log), standings, player stats, divisions/teams/
roster CRUD, players/coaches, users/roles/permissions, and the player-list importer
(column detection, name/birth-date matching, ambiguous/conflict resolution, team/
roster/coach assignment), and the league-site sync (`tests/test_league_site_sync.py`,
against a stand-in for the site — the tests never call the real one). Not yet covered: the draft flow, Excel export, and anything
Streamlit-UI-specific (widget layout, the Home page's card navigation) — those need
driving an actual browser, not `pytest`.

## Database layout (`schema_postgres.sql`)

- **games** — one row per sheet: date, division, home/away team & color, final scores,
  whether it went to a shootout, and the source filename (also prevents double-entry
  of the same sheet).
- **goals** — one row per goal: side, scorer #, up to two assists, period, time.
- **penalties** — one row per penalty: side, player #, penalty type, period, time.
- **shootout_attempts** — one row per shootout round per side: player # and whether
  it scored.
- **player_goal_stats** / **player_assist_stats** — views that roll goals/assists up
  per player per game, so you can build season totals with a `GROUP BY player_number`
  across games.
- **schedule_games** — one row per scheduled matchup from an uploaded CSV: date,
  home/away team, round, times, location. Whether it's "accounted for" isn't stored
  here — it's computed live against `games` every time the Schedule tab is viewed.

## Notes / things to watch for

- Team rosters list by last name, then first name, in both apps and on the Excel
  export's Rosters sheet.
- Two players' jersey numbers can be swapped: in the React app, edit one player's
  number to the other's and the dialog offers "Swap numbers"; in the Streamlit
  roster grid, swap the two numbers and save. Stats are attributed by jersey number
  per team, so a player's existing goals and penalties follow the number.

- Handwriting extraction won't be perfect every time — that's what the review step
  is for. If a field is unreadable, the script will fill in `"?"` rather than guess.
- Re-running the same file against the same database is safe for the `games` row
  (it won't create a duplicate game), but if you edit-and-reinsert, it will add a
  *second* set of goals/penalties/shootout rows for that game — delete the old rows
  first if you're correcting an already-stored sheet.
- Want to skip the review prompt and just trust Claude's read? Use `--no-review`,
  useful once you've spot-checked accuracy on a handful of sheets.
- Query examples once you've got a season's worth of sheets loaded:
  ```sql
  -- Team records
  SELECT home_team, away_team, home_final_score, away_final_score FROM games;

  -- A player's total goals across all games (by number, per team side)
  SELECT player_number, SUM(goals) FROM player_goal_stats
  WHERE player_number = '14' GROUP BY player_number;
  ```

## To Do

- [ ] Analyze Team Stats
- [ ] Analyze Player Stats
