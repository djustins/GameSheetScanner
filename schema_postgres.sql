-- Team Pittsburgh Ball Hockey — game sheet database schema (PostgreSQL)
--
-- Ported from schema.sql (SQLite). Differences from the SQLite version:
--   - INTEGER PRIMARY KEY AUTOINCREMENT -> id SERIAL PRIMARY KEY
--   - Foreign keys are always enforced by Postgres (no PRAGMA opt-in needed)
-- created_at/deleted_at/game_date etc. stay TEXT, and went_to_shootout/
-- scored stay INTEGER 0/1, to keep the exact semantics the app already
-- reads/writes today.

-- Small durable key/value store for app-level preferences that should
-- survive a restart (e.g. the last-selected Working Division) — scoped to
-- this database, same as everything else here.
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- A division is one season's instance of an age group, e.g. "2026 Summer
-- Penguin (U10)" — the same age group recurs every season as a new division.
-- Everything else (games, teams, and transitively player rosters) belongs to
-- exactly one division and is fully isolated from every other division.
CREATE TABLE IF NOT EXISTS divisions (
    id          SERIAL PRIMARY KEY,
    year        INTEGER NOT NULL,
    season      TEXT NOT NULL,          -- e.g. "Summer", "Winter", "Fall", "Spring"
    age_group   TEXT NOT NULL,          -- e.g. "Penguin"
    category    TEXT NOT NULL,          -- e.g. "U10"
    deleted_at  TEXT,                   -- set when soft-deleted; purged 30 days later
    UNIQUE(year, season, age_group)
);

CREATE TABLE IF NOT EXISTS games (
    id                  SERIAL PRIMARY KEY,
    game_date           TEXT,           -- as written, e.g. "7/14/26"
    division            TEXT,           -- e.g. "Penguin" (age group only; see division_id)
    division_id         INTEGER REFERENCES divisions(id),  -- which season's division this game belongs to
    home_team           TEXT,
    home_color          TEXT,
    home_final_score    INTEGER,
    away_team           TEXT,
    away_color          TEXT,
    away_final_score    INTEGER,
    went_to_shootout    INTEGER DEFAULT 0,   -- 1/0
    winner              TEXT CHECK(winner IN ('home','away','tie')),  -- final score, or shootout result if tied
    ot_winner           TEXT CHECK(ot_winner IN ('home','away')),  -- set only if decided by shootout
    ot_loser            TEXT CHECK(ot_loser  IN ('home','away')),  -- set only if decided by shootout
    source_file         TEXT,           -- original scan filename
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, home_team, away_team, source_file)
);

CREATE TABLE IF NOT EXISTS goals (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    side            TEXT NOT NULL CHECK(side IN ('home','away')),
    scorer_number   TEXT,           -- player # (text, since sheets sometimes have oddities)
    assist1_number  TEXT,
    assist2_number  TEXT,
    period          TEXT,
    time            TEXT
);

CREATE TABLE IF NOT EXISTS penalties (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    side            TEXT NOT NULL CHECK(side IN ('home','away')),
    player_number   TEXT,
    penalty_type    TEXT,
    period          TEXT,
    time            TEXT
);

CREATE TABLE IF NOT EXISTS shootout_attempts (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    side            TEXT NOT NULL CHECK(side IN ('home','away')),
    round           INTEGER NOT NULL,
    player_number   TEXT,
    scored          INTEGER NOT NULL DEFAULT 0   -- 1 if circled (goal), 0 if not
);

-- Teams (and, transitively through team_id, rosters) are fully scoped per
-- division — the same team name in a different division/season is a
-- separate row with its own roster, not shared.
CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    UNIQUE(division_id, name)
);

-- Global player profile — one row per real player, persisting across every
-- division/season they ever play in. Distinct from roster_entries below,
-- which is the per-team jersey-number row auto-extracted from game sheets;
-- a roster entry is linked to a player profile once someone identifies who
-- it is. "Which divisions this player played in" is derived by joining
-- roster_entries -> teams -> divisions rather than tracked as its own table,
-- so it can't drift out of sync with the actual rosters.
CREATE TABLE IF NOT EXISTS players (
    id                   SERIAL PRIMARY KEY,
    name                 TEXT NOT NULL,
    birth_date           TEXT,
    current_division_id  INTEGER REFERENCES divisions(id),
    contact_first_name   TEXT,
    contact_last_name    TEXT,
    contact_phone        TEXT,
    contact_email        TEXT,
    deleted_at           TEXT,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Per-team jersey-number roster row, auto-extracted from game sheets and
-- used to attribute goals/assists/penalties by number. Optionally linked to
-- a global player profile once identified.
CREATE TABLE IF NOT EXISTS roster_entries (
    id         SERIAL PRIMARY KEY,
    team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    number     TEXT NOT NULL,
    name       TEXT NOT NULL,
    player_id  INTEGER REFERENCES players(id) ON DELETE SET NULL,
    UNIQUE(team_id, number)
);

-- Global coach profile — persists across every team/division they coach.
CREATE TABLE IF NOT EXISTS coaches (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    deleted_at  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Which coach(es) are assigned to which team. The team already ties this to
-- one division (teams.division_id) and one roster (roster_entries.team_id),
-- so this single join relates a coach to a division and to that division's
-- players.
CREATE TABLE IF NOT EXISTS team_coaches (
    team_id   INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    coach_id  INTEGER NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id, coach_id)
);

-- A single evaluation of a player for a given division/team.
CREATE TABLE IF NOT EXISTS evaluations (
    id           SERIAL PRIMARY KEY,
    player_id    INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    division_id  INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    team_id      INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    grade        TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A player's current position on one team for one division — unlike
-- evaluations, this has no history; setting it just overwrites the row for
-- that (player, division, team).
CREATE TABLE IF NOT EXISTS player_positions (
    player_id    INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    division_id  INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    team_id      INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    position     TEXT,
    PRIMARY KEY (player_id, division_id, team_id)
);

-- Official season schedule, uploaded as a CSV and persisted here so it
-- survives a restart and stays comparable against stored games without
-- re-uploading. Scoped per division like teams/games. Re-uploading the same
-- schedule updates times/round/location for a (division, date, home, away)
-- match instead of creating a duplicate row, since schedules do get revised.
-- Whether a row is "accounted for" is never stored here — it's re-evaluated
-- live against the games table on every read, so it can't go stale.
CREATE TABLE IF NOT EXISTS schedule_games (
    id           SERIAL PRIMARY KEY,
    division_id  INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    order_num    INTEGER,
    round        TEXT,
    game_date    TEXT NOT NULL,        -- ISO 'YYYY-MM-DD'
    home_team    TEXT NOT NULL,
    away_team    TEXT NOT NULL,
    start_time   TEXT,
    end_time     TEXT,
    location     TEXT,
    field        TEXT,
    UNIQUE(division_id, game_date, home_team, away_team)
);

-- App users, authenticated by email + password (bcrypt hash) — who can log
-- into the app itself, distinct from players/coaches (who's on a roster).
-- is_admin bypasses per-page permission checks entirely: admins always see
-- every tab, including User Management, regardless of role. Non-admins get
-- whatever pages their assigned role carries (role_id NULL = no pages).
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    display_name   TEXT,
    is_admin       INTEGER NOT NULL DEFAULT 0,   -- 1/0
    -- role_id added below via ALTER TABLE, once the roles table it
    -- references exists (also how it's added to a pre-existing database).
    deleted_at     TEXT,                          -- deactivated; can no longer log in
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- A named bundle of page access (e.g. "Coach", "Scorer") — assigned to
-- users so an admin configures pages once per role instead of once per
-- person. Replaces the earlier per-user user_pages table: individual page
-- overrides per user turned out to be more bookkeeping than this app's
-- small, role-shaped user base actually needed.
CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 1/0: a read-only role's users can view their granted pages but can't
-- save/create/delete anything. Added via ALTER (not the CREATE TABLE
-- above) so it reaches an already-existing roles table the same way a
-- fresh one gets it.
ALTER TABLE roles ADD COLUMN IF NOT EXISTS read_only INTEGER NOT NULL DEFAULT 0;

-- 1/0: a role with this set can still see a player's parent contact NAME
-- (first/last) wherever it's shown, but not their phone/email — e.g. a
-- Coach role that should be able to identify whose parent is whose without
-- seeing everyone's personal contact details.
ALTER TABLE roles ADD COLUMN IF NOT EXISTS hide_contact_details INTEGER NOT NULL DEFAULT 0;

-- Which tabs (page keys defined in game_sheet_core.PAGES) a role grants.
CREATE TABLE IF NOT EXISTS role_pages (
    role_id  INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    page     TEXT NOT NULL,
    PRIMARY KEY (role_id, page)
);

-- Deleting a role clears role_id on any user who had it (they simply lose
-- page access until reassigned) rather than blocking the delete.
ALTER TABLE users ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL;

-- Superseded by roles/role_pages (see above) — dropped, not just left
-- unused, so the schema doesn't carry a second, no-longer-read source of
-- truth for page access.
DROP TABLE IF EXISTS user_pages;

-- Handy views for stat lookups

CREATE OR REPLACE VIEW player_goal_stats AS
SELECT
    g.id AS game_id,
    g.side,
    g.scorer_number AS player_number,
    COUNT(*) AS goals
FROM goals g
WHERE g.scorer_number IS NOT NULL AND g.scorer_number <> ''
GROUP BY g.id, g.side, g.scorer_number;

CREATE OR REPLACE VIEW player_assist_stats AS
SELECT game_id, side, player_number, SUM(cnt) AS assists FROM (
    SELECT game_id, side, assist1_number AS player_number, COUNT(*) AS cnt
    FROM goals WHERE assist1_number IS NOT NULL AND assist1_number <> ''
    GROUP BY game_id, side, assist1_number
    UNION ALL
    SELECT game_id, side, assist2_number AS player_number, COUNT(*) AS cnt
    FROM goals WHERE assist2_number IS NOT NULL AND assist2_number <> ''
    GROUP BY game_id, side, assist2_number
) sub
GROUP BY game_id, side, player_number;

-- Standings (points, W/L/OT-W/OT-L, head-to-head tiebreakers) are computed in
-- game_sheet_core.get_standings() rather than a view, since the tiebreak
-- chain needs pairwise comparisons a flat SQL query can't express cleanly.
