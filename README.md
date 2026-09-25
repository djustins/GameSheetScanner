# Ball Hockey Game Sheet → PostgreSQL

Scans handwritten Team Pittsburgh Ball Hockey game sheets (PDF or photo) and stores
the stats — goals, assists, penalties, shootout results — in a PostgreSQL database.

**This is a Streamlit web app.** Run it with `streamlit run app.py`, not as a
standalone Python script — running `app.py` directly with `python` will not work.

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
