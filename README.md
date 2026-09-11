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
```

The schema (`schema_postgres.sql`) is created automatically on first connect.
Migrating an existing `hockey.db` (SQLite) into a fresh Postgres database:

```bash
python scripts/migrate_sqlite_to_postgres.py path/to/hockey.db
```

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
Non-admin accounts are limited to whichever tabs an admin has checked off for
them in User Management; admins always see every tab.

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
