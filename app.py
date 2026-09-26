#!/usr/bin/env python3
"""
app.py

Streamlit web app for the Team Pittsburgh Ball Hockey game sheet pipeline:
split multi-page PDF scans, upload a sheet, review Claude's extraction in an
editable form, and save it to the PostgreSQL database — or correct a game
already stored. Also has standings, player stats, team rosters, and an
Excel export.

All extraction/DB/winner logic lives in game_sheet_core.py, which has no
Streamlit dependency, so the same core also backs the terminal scripts
(process_game_sheet.py, edit_game.py) and could back a Flask app later
without rewriting any of that logic.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    export DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
    streamlit run app.py

DATABASE_URL can instead be given as separate PGHOST/PGDATABASE/PGUSER/
PGPASSWORD/PGPORT/PGSSLMODE environment variables.

Requires:
    pip install -r requirements.txt
"""

import base64
import os
import random
import re
import secrets
import urllib.parse
import zipfile
from io import BytesIO
from pathlib import Path

import altair as alt
import anthropic
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

import aiven_service
import game_sheet_core as core

LOGO_PATH = Path(__file__).parent / "logo.png"
_LOGO_B64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii") if LOGO_PATH.exists() else None
VERSION_PATH = Path(__file__).parent / "VERSION"
APP_VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0.0"

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

st.set_page_config(
    page_title="Team Pittsburgh Ball Hockey",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🏒",
    layout="wide",
)

SIDES = ["home", "away"]

# Chronological ordering for divisions that share the same year, used to
# sort a player's evaluation history for the progress chart — divisions
# don't otherwise carry a strict within-year order.
SEASON_ORDER = {"Spring": 0, "Summer": 1, "Fall": 2, "Winter": 3}

# Evaluation grades this league actually uses as a skill tier, best to
# worst — plotted as an ordinal axis (this order = top to bottom) so
# "progress" reads without guessing at a numeric scale. Anything else (a
# position note like "Goalie", a status like "New") isn't a tier and is
# left off the chart, though it still shows in the evaluations list.
GRADE_TIERS = ["A", "B", "C", "D"]

# Numeric weight for averaging a team's grades on the Teams tab — A is the
# top tier (see above), so it gets the highest value.
GRADE_VALUES = {tier: len(GRADE_TIERS) - i for i, tier in enumerate(GRADE_TIERS)}

# Sentinel distinguishing "no prefetched value was passed" from "the
# prefetched value is legitimately empty/None" for season_grade_input's and
# position_input's `prefetched` parameter below.
_UNSET = object()

# Team Pittsburgh Ball Hockey's actual black/gold/white branding (teampgh.com):
# gold nav/accent bars and buttons, black hero sections, white content areas,
# black body text. Both modes below reflect real sections of their own site.
THEME_COLORS = {
    "Dark": {
        "primary": "#FFC72C", "heading": "#FFC72C",
        "background": "#000000", "secondary_bg": "#161616", "text": "#F5F5F0",
        "panel_bg": "#242424",
    },
    "Light": {
        # Darker heading gold than the button gold — bright gold text on a
        # light background reads as washed out / hard to see.
        "primary": "#FFC72C", "heading": "#8A6D1B",
        "background": "#E9E9E9", "secondary_bg": "#D6D6D6", "text": "#000000",
        "panel_bg": "#DADADA",
    },
}


def inject_theme_css(mode: str):
    c = THEME_COLORS[mode]
    st.markdown(
        f"""
        <style>
        :root {{
            --primary-color: {c['primary']};
            --background-color: {c['background']};
            --secondary-background-color: {c['secondary_bg']};
            --text-color: {c['text']};
        }}
        .stApp {{ background-color: {c['background']}; color: {c['text']}; }}
        [data-testid="stSidebar"] {{ background-color: {c['secondary_bg']}; }}
        [data-testid="stSidebar"] * {{ color: {c['text']}; }}
        h1, h2, h3, h4, h5, h6 {{ color: {c['heading']} !important; }}
        .stButton > button, .stDownloadButton > button {{
            background-color: {c['primary']}; color: #000000;
            border: none; font-weight: 600;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{ opacity: 0.85; }}
        [data-testid="stTabs"] button[aria-selected="true"] {{ color: {c['heading']} !important; }}
        [data-baseweb="tab-highlight"] {{ background-color: {c['primary']} !important; }}
        [data-baseweb="tab-border"] {{ background-color: {c['secondary_bg']} !important; }}
        .st-key-dup_panel {{
            background-color: {c['panel_bg']} !important;
            border-radius: 10px; padding: 1rem 1.25rem;
        }}
        /* Zebra striping for the "tables" built from repeated st.columns()
           rows rather than a real st.dataframe (Player Stats, Team Rosters
           Player Details) — those need per-row buttons/inputs a dataframe
           can't host. Matched by a "zebraeven"/"zebraodd" substring baked
           into each row container's key (see highlighted_row()), since
           st.container has no class= param of its own to target directly. */
        div[class*="zebraeven"] {{ background-color: {c['background']}; border-radius: 6px; }}
        div[class*="zebraodd"] {{ background-color: {c['panel_bg']}; border-radius: 6px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_logo(mode: str):
    if _LOGO_B64:
        st.sidebar.markdown(
            f"""
            <div style="text-align:center; padding:0.5rem 0 1rem 0;">
                <div style="display:inline-block; background:#000000; border-radius:16px;
                            padding:10px; max-width:220px;">
                    <img src="data:image/png;base64,{_LOGO_B64}" style="width:100%; display:block;" />
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    c = THEME_COLORS[mode]
    st.sidebar.markdown(
        f"""
        <div style="text-align:center; padding:0.5rem 0 1rem 0;">
            <div style="
                width:72px; height:72px; margin:0 auto;
                border-radius:50%; background:#000000; border:3px solid {c['primary']};
                display:flex; align-items:center; justify-content:center; font-size:1.8rem;
            ">🏒</div>
            <div style="font-weight:800; font-size:1.15rem; letter-spacing:0.5px;
                        color:{c['primary']}; margin-top:0.5rem; line-height:1.1;">TEAM PITTSBURGH</div>
            <div style="font-weight:700; font-size:0.85rem; letter-spacing:3px;
                        color:{c['text']};">BALL HOCKEY</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def division_options(current: str | None) -> tuple[list[str], int]:
    """Division dropdown options (age groups), with the current value's
    index. A typo or OCR slip (e.g. "Pegin") is auto-corrected to the closest
    known age group; only text with no close match is kept as an extra
    leading option instead of being silently discarded."""
    age_groups = list(core.AGE_GROUPS)
    if current and current.strip():
        match = core.match_age_group(current)
        if match:
            return age_groups, age_groups.index(match)
        return [current] + age_groups, 0
    working_age_group = st.session_state.get("working_division_age_group")
    if working_age_group in age_groups:
        return age_groups, age_groups.index(working_age_group)
    return age_groups, 0


def division_label(age_group: str) -> str:
    category = core.AGE_GROUPS.get(age_group)
    return f"{age_group} ({category})" if category else age_group


def render_add_division_form(conn, key_prefix: str = ""):
    """Year/Season/Age Group inputs + Add Division button — shared by the
    Teams tab → Divisions and the top-of-page prompt shown when there are none yet.
    key_prefix keeps the two instances' widget keys from colliding."""
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        new_div_year = st.number_input(
            "Year", min_value=2000, max_value=2100, value=2026, step=1, key=f"{key_prefix}new_div_year",
            disabled=is_read_only,
        )
    with dcol2:
        new_div_season = st.selectbox(
            "Season", ["Summer", "Fall", "Winter", "Spring"], key=f"{key_prefix}new_div_season",
            disabled=is_read_only,
        )
    new_div_age_group = st.selectbox(
        "Age Group", list(core.AGE_GROUPS), key=f"{key_prefix}new_div_age_group", format_func=division_label,
        disabled=is_read_only,
    )
    st.caption(f"Category: {core.AGE_GROUPS[new_div_age_group]}")
    if st.button(
        "Add Division", key=f"{key_prefix}add_division_btn", type="primary", disabled=is_read_only
    ):
        core.add_division(conn, int(new_div_year), new_div_season, new_div_age_group)
        st.rerun()


def get_client(api_key: str) -> anthropic.Anthropic | None:
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def render_preview_png(data: bytes, mime: str | None) -> bytes:
    """Return PNG bytes to show in st.image — rasterize PDFs, pass images through."""
    if mime == "application/pdf" and fitz is not None:
        doc = fitz.open(stream=data, filetype="pdf")
        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        png = pix.tobytes("png")
        doc.close()
        return png
    return data


def display_width_for_height(png_bytes: bytes, target_height: int) -> int:
    """The pixel width to pass to st.image so the image renders at exactly
    target_height tall (preserving aspect ratio) — used so the whole sheet
    fits in its pane with no vertical scrolling, instead of stretching to
    the column's width and overflowing however tall that happens to make it."""
    img_w, img_h = Image.open(BytesIO(png_bytes)).size
    return max(1, round(img_w * (target_height / img_h)))


def read_uploaded_table(uploaded_file) -> pd.DataFrame:
    """A CSV/XLSX/XLS/ODS upload as a DataFrame of strings/native values,
    dispatched by file extension — the three spreadsheet formats a league
    admin plausibly exports a player list from. Raises ValueError for an
    unrecognized extension or a file pandas can't parse, with a message
    naming the actual problem rather than a raw pandas traceback."""
    name = (uploaded_file.name or "").lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(uploaded_file, dtype=str)
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded_file, dtype=str)
        if name.endswith(".ods"):
            return pd.read_excel(uploaded_file, engine="odf", dtype=str)
    except Exception as e:
        raise ValueError(f"Couldn't read {uploaded_file.name!r}: {e}") from e
    raise ValueError(f"Unrecognized file type for {uploaded_file.name!r} — use a .csv, .xlsx, .xls, or .ods file.")


# ---------------------------------------------------------------------------
# Editable row lists (goals / penalties / shootout attempts)
# ---------------------------------------------------------------------------

def _init_rows(state_key: str, items: list[dict]):
    if state_key not in st.session_state:
        st.session_state[state_key] = [{"_id": i, **item} for i, item in enumerate(items)]


def _next_id(rows: list[dict]) -> int:
    return max((r["_id"] for r in rows), default=-1) + 1


def _strip_ids(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "_id"} for r in rows]


def render_goals_editor(state_key: str, goals: list[dict]) -> list[dict]:
    _init_rows(state_key, goals)
    rows = st.session_state[state_key]

    to_remove = None
    for side, label in (("home", "Home"), ("away", "Away")):
        st.markdown(f"**{label} Goals**")
        for row in [r for r in rows if (r.get("side") or "home") == side]:
            c = st.columns([1, 1, 1, 0.8, 0.8, 0.4])
            row["side"] = side
            row["scorer_number"] = c[0].text_input("Scorer #", value=row.get("scorer_number") or "",
                                                    key=f"{state_key}_{row['_id']}_scorer",
                                                    label_visibility="collapsed", placeholder="Scorer #")
            row["assist1_number"] = c[1].text_input("Assist 1", value=row.get("assist1_number") or "",
                                                      key=f"{state_key}_{row['_id']}_a1",
                                                      label_visibility="collapsed", placeholder="Assist 1")
            row["assist2_number"] = c[2].text_input("Assist 2", value=row.get("assist2_number") or "",
                                                      key=f"{state_key}_{row['_id']}_a2",
                                                      label_visibility="collapsed", placeholder="Assist 2")
            row["period"] = c[3].text_input("Period", value=row.get("period") or "",
                                             key=f"{state_key}_{row['_id']}_period",
                                             label_visibility="collapsed", placeholder="Period")
            row["time"] = c[4].text_input("Time", value=row.get("time") or "",
                                           key=f"{state_key}_{row['_id']}_time",
                                           label_visibility="collapsed", placeholder="Time")
            if c[5].button("✕", key=f"{state_key}_{row['_id']}_remove"):
                to_remove = row["_id"]
        if st.button(f"+ Add {label.lower()} goal", key=f"{state_key}_add_{side}"):
            st.session_state[state_key].append({"_id": _next_id(st.session_state[state_key]), "side": side,
                                                 "scorer_number": "", "assist1_number": "", "assist2_number": "",
                                                 "period": "", "time": ""})
            st.rerun()

    if to_remove is not None:
        st.session_state[state_key] = [r for r in rows if r["_id"] != to_remove]
        st.rerun()

    return _strip_ids(st.session_state[state_key])


def render_penalties_editor(state_key: str, penalties: list[dict]) -> list[dict]:
    _init_rows(state_key, penalties)
    rows = st.session_state[state_key]

    st.markdown("**Penalties**")
    to_remove = None
    for row in rows:
        c = st.columns([1, 1, 1.5, 0.8, 0.8, 0.4])
        row["side"] = c[0].selectbox("Side", SIDES, index=SIDES.index(row.get("side") or "home"),
                                      key=f"{state_key}_{row['_id']}_side", label_visibility="collapsed")
        row["player_number"] = c[1].text_input("Player #", value=row.get("player_number") or "",
                                                 key=f"{state_key}_{row['_id']}_player",
                                                 label_visibility="collapsed", placeholder="Player #")
        row["penalty_type"] = c[2].text_input("Penalty", value=row.get("penalty_type") or "",
                                                key=f"{state_key}_{row['_id']}_type",
                                                label_visibility="collapsed", placeholder="Penalty type")
        row["period"] = c[3].text_input("Period", value=row.get("period") or "",
                                         key=f"{state_key}_{row['_id']}_period",
                                         label_visibility="collapsed", placeholder="Period")
        row["time"] = c[4].text_input("Time", value=row.get("time") or "",
                                       key=f"{state_key}_{row['_id']}_time",
                                       label_visibility="collapsed", placeholder="Time")
        if c[5].button("✕", key=f"{state_key}_{row['_id']}_remove"):
            to_remove = row["_id"]

    if to_remove is not None:
        st.session_state[state_key] = [r for r in rows if r["_id"] != to_remove]
        st.rerun()
    if st.button("+ Add penalty", key=f"{state_key}_add"):
        st.session_state[state_key].append({"_id": _next_id(rows), "side": "home", "player_number": "",
                                             "penalty_type": "", "period": "", "time": ""})
        st.rerun()

    return _strip_ids(st.session_state[state_key])


def render_shootout_editor(state_key: str, attempts: list[dict]) -> list[dict]:
    _init_rows(state_key, attempts)
    rows = st.session_state[state_key]

    st.markdown("**Shootout Attempts**")
    to_remove = None
    for row in rows:
        c = st.columns([1, 0.8, 1.5, 0.8, 0.4])
        row["side"] = c[0].selectbox("Side", SIDES, index=SIDES.index(row.get("side") or "home"),
                                      key=f"{state_key}_{row['_id']}_side", label_visibility="collapsed")
        row["round"] = c[1].number_input("Round", min_value=1, step=1, value=int(row.get("round") or 1),
                                          key=f"{state_key}_{row['_id']}_round", label_visibility="collapsed")
        row["player_number"] = c[2].text_input("Player #", value=row.get("player_number") or "",
                                                 key=f"{state_key}_{row['_id']}_player",
                                                 label_visibility="collapsed", placeholder="Player #")
        row["scored"] = c[3].checkbox("Scored", value=bool(row.get("scored")),
                                       key=f"{state_key}_{row['_id']}_scored")
        if c[4].button("✕", key=f"{state_key}_{row['_id']}_remove"):
            to_remove = row["_id"]

    if to_remove is not None:
        st.session_state[state_key] = [r for r in rows if r["_id"] != to_remove]
        st.rerun()
    if st.button("+ Add shootout attempt", key=f"{state_key}_add"):
        st.session_state[state_key].append({"_id": _next_id(rows), "side": "home",
                                             "round": len(rows) + 1, "player_number": "", "scored": False})
        st.rerun()

    return _strip_ids(st.session_state[state_key])


# ---------------------------------------------------------------------------
# Full game form
# ---------------------------------------------------------------------------

def render_team_field(container, label: str, field_key: str, current_value: str | None, conn, division_id: int) -> str:
    """A team-name text input with a fuzzy-match suggestion against teams
    already in this division (e.g. "Avachale" -> "Avalanche"), shown right in
    the form so a typo/OCR slip doesn't quietly create a duplicate team."""
    pending_key = f"{field_key}_pending"
    if pending_key in st.session_state:
        st.session_state[field_key] = st.session_state.pop(pending_key)
    elif field_key not in st.session_state:
        st.session_state[field_key] = core.display_text(current_value) or ""

    value = container.text_input(label, key=field_key)
    match = core.match_team_name(conn, division_id, value)
    if match and match.strip().lower() != (value or "").strip().lower():
        if container.button(f"↪ Use \"{match}\"", key=f"{field_key}_suggest_btn"):
            st.session_state[pending_key] = match
            st.rerun()
    return value


def swap_home_away(prefix: str):
    """Flip every home/away-scoped piece of this form's state: team names
    (via the same _pending mechanism render_team_field uses for its
    suggestion button), colors, scores, each goal's side, each penalty/
    shootout row's Side widget, and the Winner pick. For when a scoresheet
    turns out to have had home and away backwards — everything entered
    against the wrong side needs to move to the right one, not just the
    team names."""
    home_team_key, away_team_key = f"{prefix}_home_team", f"{prefix}_away_team"
    st.session_state[f"{home_team_key}_pending"] = st.session_state.get(away_team_key, "")
    st.session_state[f"{away_team_key}_pending"] = st.session_state.get(home_team_key, "")

    home_color_key, away_color_key = f"{prefix}_home_color", f"{prefix}_away_color"
    st.session_state[home_color_key], st.session_state[away_color_key] = (
        st.session_state.get(away_color_key, ""), st.session_state.get(home_color_key, ""),
    )

    home_score_key, away_score_key = f"{prefix}_home_score", f"{prefix}_away_score"
    st.session_state[home_score_key], st.session_state[away_score_key] = (
        st.session_state.get(away_score_key, 0), st.session_state.get(home_score_key, 0),
    )
    # Flip the auto-score-from-goals bookkeeping in lockstep with the score
    # swap above, so a game that was auto-scoring doesn't get bumped into
    # manual mode just because this function moved the numbers between keys
    # (the auto-sync check below would otherwise read that as "user typed
    # something else by hand").
    last_synced_key = f"{prefix}_score_last_synced"
    last_synced = st.session_state.get(last_synced_key)
    if last_synced is not None:
        st.session_state[last_synced_key] = (last_synced[1], last_synced[0])

    # Goals have no per-row Side widget (side is fixed by which "+ Add goal"
    # button created the row) — flipping the row dicts themselves is enough,
    # since the editor groups by that field on every render.
    for row in st.session_state.get(f"{prefix}_goals", []):
        row["side"] = "away" if row.get("side", "home") == "home" else "home"

    # Penalties/shootout rows DO have a Side selectbox, keyed independently
    # per row — its widget key is what actually needs to change, not just
    # the row dict (which the widget overwrites right back on every render).
    for editor_key in (f"{prefix}_penalties", f"{prefix}_shootout"):
        for row in st.session_state.get(editor_key, []):
            side_widget_key = f"{editor_key}_{row['_id']}_side"
            current = st.session_state.get(side_widget_key, row.get("side", "home"))
            st.session_state[side_widget_key] = "away" if current == "home" else "home"

    winner_key = f"{prefix}_winner"
    if st.session_state.get(winner_key) in ("home", "away"):
        st.session_state[winner_key] = "away" if st.session_state[winner_key] == "home" else "home"


def render_game_form(prefix: str, data: dict, conn, division_id: int) -> dict:
    """Render an editable form for one game record and return the current
    (live, possibly-edited) data dict matching the extraction schema plus a
    "winner" field."""
    c1, c2 = st.columns(2)
    game_date = c1.text_input(
        "Game date", value=core.display_date(core.normalize_date(data.get("game_date"))) or "",
        key=f"{prefix}_game_date",
    )
    div_options, div_index = division_options(data.get("division"))
    division = c2.selectbox(
        "Division", div_options, index=div_index, key=f"{prefix}_division",
        format_func=lambda d: division_label(d) if d in core.AGE_GROUPS else d,
    )

    # Auto-fill an empty (0-0) score from the goals recorded below, and KEEP
    # tracking the goal tally — including through goals being reassigned
    # between home/away — until the user types a score by hand that doesn't
    # match what we last auto-set, at which point we stop touching it (so a
    # real extracted/entered score is never silently overwritten).
    home_score_key, away_score_key = f"{prefix}_home_score", f"{prefix}_away_score"
    auto_key, last_synced_key = f"{prefix}_score_auto", f"{prefix}_score_last_synced"
    force_resync_key = f"{prefix}_force_resync"
    goals_state_key = f"{prefix}_goals"
    if goals_state_key in st.session_state:
        current_goals = _strip_ids(st.session_state[goals_state_key])
    else:
        current_goals = data.get("goals", [])
    home_goal_count = sum(1 for g in current_goals if g.get("side") == "home")
    away_goal_count = sum(1 for g in current_goals if g.get("side") == "away")

    if auto_key not in st.session_state:
        starting_home = int(data.get("home_final_score") or 0)
        starting_away = int(data.get("away_final_score") or 0)
        st.session_state[auto_key] = starting_home == 0 and starting_away == 0

    # Applied here (before the score widgets render) rather than from the
    # Recalculate button itself, which sits below them and so can't legally
    # write to their session_state keys once they're already instantiated.
    if st.session_state.pop(force_resync_key, False):
        st.session_state[auto_key] = True
    elif home_score_key in st.session_state and away_score_key in st.session_state:
        # Both keys are only guaranteed together once a full render_game_form
        # pass has completed — a st.rerun() triggered mid-form (e.g. by the
        # team-name suggestion button, which sits between the two score
        # widgets) can leave home_score_key set but away_score_key not yet
        # created for one interrupted pass.
        last_synced = st.session_state.get(last_synced_key)
        current_pair = (st.session_state[home_score_key], st.session_state[away_score_key])
        if last_synced is not None and current_pair != last_synced:
            st.session_state[auto_key] = False  # user typed something else by hand

    score_auto_filled = False
    if st.session_state[auto_key]:
        st.session_state[home_score_key] = home_goal_count
        st.session_state[away_score_key] = away_goal_count
        st.session_state[last_synced_key] = (home_goal_count, away_goal_count)
        score_auto_filled = True

    # Seed from the extracted/loaded data only if nothing (auto-fill above,
    # or a prior run) has already put a value in session_state for these
    # keys — passing both `value=` and a session_state entry to the same
    # widget triggers a Streamlit warning, so the widgets below rely on
    # session_state alone.
    if home_score_key not in st.session_state:
        st.session_state[home_score_key] = int(data.get("home_final_score") or 0)
    if away_score_key not in st.session_state:
        st.session_state[away_score_key] = int(data.get("away_final_score") or 0)

    if st.button(
        "🔄 Swap Home/Away", key=f"{prefix}_swap_sides",
        help="Use if the scoresheet had home and away backwards — swaps teams, "
             "colors, scores, and every goal/penalty/shootout entry to the other side.",
    ):
        swap_home_away(prefix)
        st.rerun()

    st.markdown("##### Home")
    hc1, hc2, hc3 = st.columns(3)
    home_team = render_team_field(hc1, "Home team", f"{prefix}_home_team", data.get("home_team"), conn, division_id)
    home_color = hc2.text_input("Home color", value=data.get("home_color") or "", key=f"{prefix}_home_color")
    home_final_score = hc3.number_input("Home final score", min_value=0, step=1, key=home_score_key)

    st.markdown("##### Away")
    ac1, ac2, ac3 = st.columns(3)
    away_team = render_team_field(ac1, "Away team", f"{prefix}_away_team", data.get("away_team"), conn, division_id)
    away_color = ac2.text_input("Away color", value=data.get("away_color") or "", key=f"{prefix}_away_color")
    away_final_score = ac3.number_input("Away final score", min_value=0, step=1, key=away_score_key)
    score_cap_col, score_btn_col = st.columns([4, 1])
    with score_cap_col:
        if score_auto_filled:
            st.caption("Score is auto-calculated from the goals recorded below.")
    with score_btn_col:
        if st.button("↻ Recalculate", key=f"{prefix}_recalc_score", help="Reset the score to match the goals below"):
            st.session_state[force_resync_key] = True
            st.rerun()

    st.divider()
    goals = render_goals_editor(f"{prefix}_goals", data.get("goals", []))
    st.divider()
    penalties = render_penalties_editor(f"{prefix}_penalties", data.get("penalties", []))
    st.divider()
    shootout_attempts = render_shootout_editor(f"{prefix}_shootout", data.get("shootout_attempts", []))
    st.divider()

    merged = {
        "game_date": game_date, "division": division,
        "home_team": home_team, "home_color": home_color, "home_final_score": home_final_score,
        "away_team": away_team, "away_color": away_color, "away_final_score": away_final_score,
        "goals": goals, "penalties": penalties, "shootout_attempts": shootout_attempts,
    }

    default_winner = data.get("winner") if data.get("winner") in ("home", "away", "tie") else core.compute_winner(merged)
    winner_options = ["home", "away", "tie"]
    winner_labels = {"home": home_team or "Home", "away": away_team or "Away", "tie": "Tie"}
    winner = st.selectbox(
        "Winner", winner_options,
        index=winner_options.index(default_winner) if default_winner in winner_options else 2,
        format_func=lambda o: winner_labels[o], key=f"{prefix}_winner",
    )
    merged["winner"] = winner

    ot_winner, ot_loser = core.compute_ot_result(merged)
    if ot_winner:
        winner_team = home_team if ot_winner == "home" else away_team
        loser_team = home_team if ot_loser == "home" else away_team
        st.info(f"Decided in a shootout — **OT Winner:** {winner_team}  ·  **OT Loser:** {loser_team}")
    elif shootout_attempts and home_final_score != away_final_score:
        st.warning(
            "Shootout attempts are recorded, but the score above isn't tied — a shootout only "
            "happens after a tied game. This won't save until the score is tied (so it counts "
            "as a shootout win/loss) or the shootout attempts are removed."
        )

    return merged


def game_form_error(merged: dict) -> str | None:
    """Pre-save validation for a render_game_form() result — surfaced as
    st.error before the user clicks save, rather than only after hitting the
    ValueError core.insert_game()/update_game() raise for the same problems.
    Both checks live in game_sheet_core so the CLI scripts enforce them too;
    this just reuses that logic for a friendlier in-form message."""
    if merged["winner"] == "tie":
        return (
            "Games can't end in a tie — fix the score, add shootout results, "
            "or pick a winner below before saving."
        )
    try:
        core.validate_shootout(merged)
    except ValueError as e:
        return str(e)
    return None


def season_grade_input(
    conn, player_id: int, division_id: int, team_id: int | None, key: str,
    prefetched=_UNSET, **text_input_kwargs,
):
    """A "Season Grade" text input backed by core.set_season_grade, usable
    both from the Player Panel and the Team Rosters grid. Both render on
    every script run (st.tabs() executes every tab's body regardless of
    which one is visually selected), so without this, Streamlit's rule
    that a widget's `value=` is ignored once its key already exists in
    session_state would let whichever surface has a stale value silently
    overwrite the other's edit — including deleting a grade someone just
    set — on the very next rerun. Resyncing session_state here whenever
    the DB value changed out from under this widget avoids that.

    `prefetched`, if given (even None/""), skips the individual
    get_season_grade() round trip — pass it when the caller already batch-
    fetched grades for a whole roster (core.get_season_grades_for_division)
    to avoid one query per row."""
    current_grade = (core.get_season_grade(conn, player_id, division_id) if prefetched is _UNSET else prefetched) or ""
    synced_key = f"{key}__synced"
    if st.session_state.get(synced_key) != current_grade:
        st.session_state[key] = current_grade
        st.session_state[synced_key] = current_grade
    new_grade = st.text_input("Season Grade", key=key, **text_input_kwargs)
    if new_grade.strip() != current_grade:
        core.set_season_grade(conn, player_id, division_id, team_id, new_grade)
        st.session_state[synced_key] = new_grade.strip()
        st.rerun()


POSITION_OPTIONS = ["", "Forward", "Defense", "Forward or Defense", "Goalie"]


def position_input(
    conn, player_id: int, division_id: int, team_id: int, key: str, prefetched=_UNSET, **selectbox_kwargs,
):
    """A "Position" dropdown backed by core.set_position, for one specific
    player+team+division. Mirrors season_grade_input's session_state resync
    trick since this can likewise render in more than one place (Player
    Panel, Team Rosters grid) within the same script run. `prefetched`
    mirrors season_grade_input's — pass core.get_positions_for_team()'s
    result to skip the individual get_position() round trip in a roster
    loop."""
    current_position = (core.get_position(conn, player_id, division_id, team_id) if prefetched is _UNSET else prefetched) or ""
    synced_key = f"{key}__synced"
    if st.session_state.get(synced_key) != current_position:
        st.session_state[key] = current_position
        st.session_state[synced_key] = current_position
    new_position = st.selectbox(
        "Position", options=POSITION_OPTIONS, format_func=lambda p: "(none)" if p == "" else p,
        key=key, **selectbox_kwargs,
    )
    if new_position.strip() != current_position:
        core.set_position(conn, player_id, division_id, team_id, new_position)
        st.session_state[synced_key] = new_position.strip()
        st.rerun()


def zebra_style(df: pd.DataFrame):
    """Alternating row background colors for a real st.dataframe table.
    st.dataframe's grid is canvas-rendered (glide-data-grid), so the plain
    CSS row-striping that works for the app's other, manually-built
    "tables" (see highlighted_row) can't reach it — a pandas Styler is the
    one styling channel st.dataframe actually respects."""
    c = THEME_COLORS[theme_mode]
    colors = [c["background"], c["panel_bg"]]
    return df.style.apply(lambda row: [f"background-color: {colors[row.name % 2]}"] * len(row), axis=1)


PLAYER_EXPERIENCE_NOTE_COLORS = {
    "Moved Up": "#2e7d32", "Has Experience": "#1565c0", "Played Before": "#8d6e63",
}


def style_player_notes(df: pd.DataFrame, note_column: str = "Note"):
    """zebra_style plus a colored, bolded background for the Note column's
    cell (see core.player_experience_notes for what populates it) so a
    player's playing history stands out at a glance instead of blending
    into an otherwise plain row. A blank note gets no extra styling."""
    def _note_style(value):
        color = PLAYER_EXPERIENCE_NOTE_COLORS.get(value)
        return f"background-color: {color}; color: white; font-weight: 600;" if color else ""
    return zebra_style(df).map(_note_style, subset=[note_column])


def highlighted_row(player_id: int | None, row_key: str, index: int):
    """A row container for a "table" that's actually built from repeated
    st.columns() calls (Player Stats, Team Rosters Player Details) rather
    than a real st.dataframe, which can't host per-row buttons/inputs the
    way these need. Gives it the same alternating background every other
    table in the app has (see the "zebraeven"/"zebraodd" CSS in
    inject_theme_css, matched against this container's key), plus a border
    for the player most recently linked/created via a "Link / Create Player"
    popover so that row stands out from the rest right after it appears.

    row_key must be unique among this call site's own rows (e.g. a roster
    entry id) — it's combined with a caller-specific prefix internally so
    two different tables' rows never collide on the same Streamlit widget
    key, since every tab's rows render on every rerun regardless of which
    tab is visible."""
    is_new = player_id is not None and player_id == st.session_state.get("just_linked_player_id")
    zebra = "zebraeven" if index % 2 == 0 else "zebraodd"
    return st.container(border=is_new, key=f"row_{zebra}_{row_key}")


def render_create_player_popover(
    conn, key_prefix: str, team_id: int, team_name: str, number: str, working_division_id
):
    """A "Link / Create Player" popover — search existing player profiles
    first (players are global, so the person filling this roster slot may
    already have a profile from another team/division/season and shouldn't
    get a duplicate), falling back to creating a brand-new profile. Reused
    by the Player Stats tab and the Team Rosters tab so there's one
    implementation of "link or create & link a player" regardless of where
    it's opened from."""
    with st.popover("➕ Link / Create Player"):
        st.caption(f"Linking {core.display_text(team_name)} #{number}")

        st.markdown("**Search for an existing player**")
        search_needle = st.text_input("Search by name", key=f"{key_prefix}_link_search")
        # "Sub"/"SUB" placeholder players aren't a real person to link —
        # same exclusion the Players tab applies to its own picker.
        searchable_players = [p for p in core.list_players(conn) if p["name"].strip().lower() != "sub"]
        if search_needle.strip():
            needle = search_needle.strip().lower()
            matches = [
                p for p in searchable_players
                if needle in p["name"].lower() or needle in (p["nickname"] or "").lower()
            ]
            if not matches:
                st.caption("No matching players.")
            else:
                histories = core.player_division_histories(conn, [p["id"] for p in matches])
                match_options = {p["id"]: player_label(conn, p, histories.get(p["id"], [])) for p in matches}
                lcol1, lcol2 = st.columns([3, 1])
                with lcol1:
                    link_pick_id = st.selectbox(
                        "Matches", options=list(match_options), format_func=lambda i: match_options[i],
                        key=f"{key_prefix}_link_pick", label_visibility="collapsed",
                    )
                with lcol2:
                    if st.button("Link", key=f"{key_prefix}_link_btn", type="primary", disabled=is_read_only):
                        entry = conn.execute(
                            "SELECT id FROM roster_entries WHERE team_id = %s AND number = %s",
                            (team_id, number),
                        ).fetchone()
                        if entry:
                            core.link_roster_entry_to_player(conn, entry[0], link_pick_id)
                        st.success(f"Linked {match_options[link_pick_id]}.")
                        st.session_state["just_linked_player_id"] = link_pick_id
                        st.rerun()

        st.divider()
        st.markdown("**Or create a new player**")
        ncol0a, ncol0b = st.columns(2)
        new_first = ncol0a.text_input("First name", key=f"{key_prefix}_new_first", disabled=is_read_only)
        new_last = ncol0b.text_input("Last name", key=f"{key_prefix}_new_last", disabled=is_read_only)
        new_nickname = st.text_input(
            "Nickname", key=f"{key_prefix}_new_nickname", disabled=is_read_only,
            help="Optional — shown alongside their name wherever it's picked or displayed.",
        )
        new_dob = st.text_input(
            "Birth date", key=f"{key_prefix}_new_dob", placeholder="YYYY-MM-DD", disabled=is_read_only
        )
        ncol1, ncol2 = st.columns(2)
        new_cfn = ncol1.text_input("Contact first name", key=f"{key_prefix}_new_cfn", disabled=is_read_only)
        new_cln = ncol2.text_input("Contact last name", key=f"{key_prefix}_new_cln", disabled=is_read_only)
        new_cph = new_cem = ""
        if hide_contact_details:
            st.caption("🔒 Phone/email are hidden for your role.")
        else:
            ncol3, ncol4 = st.columns(2)
            new_cph = ncol3.text_input("Contact phone", key=f"{key_prefix}_new_cph", disabled=is_read_only)
            new_cem = ncol4.text_input("Contact email", key=f"{key_prefix}_new_cem", disabled=is_read_only)
        if st.button(
            "Create & Link", key=f"{key_prefix}_create_link", type="primary", disabled=is_read_only
        ):
            if not new_first.strip():
                st.error("First name is required.")
            else:
                new_player_id = core.add_player(
                    conn, new_first.strip(), new_last.strip() or None, new_nickname.strip() or None,
                    birth_date=new_dob.strip() or None, current_division_id=working_division_id,
                    contact_first_name=new_cfn.strip() or None, contact_last_name=new_cln.strip() or None,
                    contact_phone=new_cph.strip() or None, contact_email=new_cem.strip() or None,
                )
                entry = conn.execute(
                    "SELECT id FROM roster_entries WHERE team_id = %s AND number = %s",
                    (team_id, number),
                ).fetchone()
                if entry:
                    core.link_roster_entry_to_player(conn, entry[0], new_player_id)
                display_name = core.full_name(new_first.strip(), new_last.strip())
                st.success(f"Created and linked {display_name}.")
                st.session_state["just_linked_player_id"] = new_player_id
                st.rerun()


def player_label(conn, player: dict, prefetched_history: list[dict] | None = _UNSET) -> str:
    """A player's label for pickers: name plus their current jersey
    number/team, if they have one — so two players sharing a name (e.g. a
    duplicate accidentally created for the wrong roster number) can still
    be told apart well enough to pick the right one to delete.

    `prefetched_history` mirrors position_input's/season_grade_input's —
    pass core.player_division_histories()' result (keyed by player id) when
    labeling many players at once (the Players tab's picker) to avoid one
    player_division_history() round trip per player."""
    base_name = f'{player["name"]} "{player["nickname"]}"' if player.get("nickname") else player["name"]
    if player["current_division_id"] is not None:
        history = core.player_division_history(conn, player["id"]) if prefetched_history is _UNSET else prefetched_history
        entries = [h for h in history if h["division_id"] == player["current_division_id"]]
        if entries:
            numbers = ", ".join(f"#{h['number']} {h['team_name']}" for h in entries)
            return f"{base_name} ({numbers})"
    return base_name


@st.dialog("Delete player?")
def confirm_delete_player_dialog(key_prefix: str, player_id: int, player_name: str):
    """A dialog's body runs as a Streamlit fragment in its own thread,
    separate from the thread that ran the rest of the script. That used to
    matter here — sqlite3 connections can't cross threads — but psycopg2
    connections don't have that restriction (sequential cross-thread use is
    fine), so this can safely reuse the shared session connection (`conn`,
    the same one every other tab/function in this file uses) instead of
    opening a second one."""
    st.write(f"Delete **{player_name}**? This can be undone in the Players tab's Deleted Players list.")
    yes_col, cancel_col = st.columns(2)
    with yes_col:
        if st.button(
            "Yes, delete this player", key=f"{key_prefix}_delete_confirm_{player_id}",
            type="primary", width="stretch",
        ):
            core.soft_delete_player(conn, player_id)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", key=f"{key_prefix}_delete_cancel_{player_id}", width="stretch"):
            st.rerun()


@st.dialog("Delete coach?")
def confirm_delete_coach_dialog(key_prefix: str, coach_id: int, coach_name: str):
    st.write(f"Delete **{coach_name}**? This can be undone in the Coaches tab's Deleted Coaches list.")
    yes_col, cancel_col = st.columns(2)
    with yes_col:
        if st.button(
            "Yes, delete this coach", key=f"{key_prefix}_delete_confirm_{coach_id}",
            type="primary", width="stretch",
        ):
            core.soft_delete_coach(conn, coach_id)
            st.rerun()
    with cancel_col:
        if st.button("Cancel", key=f"{key_prefix}_delete_cancel_{coach_id}", width="stretch"):
            st.rerun()


def coach_label(coach: dict) -> str:
    return f'{coach["name"]} "{coach["nickname"]}"' if coach.get("nickname") else coach["name"]


def team_label_with_coaches(team: dict, coaches_by_team: dict) -> str:
    """A team's label for pickers: name plus its assigned coach(es), so
    picking a team doesn't mean going to look up who coaches it first —
    e.g. for the Draft setup order, or "which team is this evaluation for"."""
    coaches = coaches_by_team.get(team["id"], [])
    coach_part = ", ".join(coach_label(c) for c in coaches) if coaches else "no coach"
    return f'{team["name"]} ({coach_part})'


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def render_color_swatch(color: str | None):
    """A small colored square next to a team's color code, so it reads as
    an actual color rather than a hex string — mirrors what st.color_picker
    itself already shows on its swatch button."""
    if not color:
        st.write("—")
    elif _HEX_COLOR_RE.match(color):
        # Only ever a plain #RRGGBB from st.color_picker, but validated
        # before going into raw HTML regardless — never trust stored text.
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
            f'background:{color};border:1px solid rgba(128,128,128,0.4);flex-shrink:0;"></span>'
            f'<span>{color}</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.write(color)


def render_team_coach_manager(conn, team_id: int, team_name: str, key_prefix: str):
    """Assign/remove/create coaches for one team — reused by the Team
    Rosters tab (scoped to the Working Division) and the Teams tab → Divisions
    (which manages every division, not just the working one), so both stay
    on one implementation of "the coach manager for a team" instead of two
    copies that can quietly drift apart.

    The assign flow mirrors render_create_player_popover's "search
    existing, or create new" pattern — search first (coaches are global,
    so the person being assigned may already have a profile from another
    team/division/season and shouldn't get a duplicate), falling back to
    creating a brand-new one, both in the same popover instead of a
    separate pick-from-a-full-list dropdown and a disconnected "add a
    coach" control."""
    assigned = core.list_team_coaches(conn, team_id)
    if assigned:
        st.write(", ".join(coach_label(c) for c in assigned))
    else:
        st.caption("No coaches assigned to this team yet.")

    with st.popover("➕ Assign Coach"):
        st.caption(f"Assigning to {core.display_text(team_name)}")
        assigned_ids = {c["id"] for c in assigned}
        available_coaches = [c for c in core.list_coaches(conn) if c["id"] not in assigned_ids]

        st.markdown("**Search for an existing coach**")
        search_needle = st.text_input("Search by name", key=f"{key_prefix}_coach_search_{team_id}")
        if search_needle.strip():
            needle = search_needle.strip().lower()
            matches = [
                c for c in available_coaches
                if needle in c["name"].lower() or needle in (c["nickname"] or "").lower()
            ]
            if not matches:
                st.caption("No matching coaches.")
            else:
                match_options = {c["id"]: coach_label(c) for c in matches}
                mcol1, mcol2 = st.columns([3, 1])
                with mcol1:
                    assign_pick_id = st.selectbox(
                        "Matches", options=list(match_options), format_func=lambda i: match_options[i],
                        key=f"{key_prefix}_coach_pick_{team_id}", label_visibility="collapsed",
                    )
                with mcol2:
                    if st.button(
                        "Assign", key=f"{key_prefix}_coach_assign_btn_{team_id}", type="primary",
                        disabled=is_read_only,
                    ):
                        try:
                            core.assign_coach_to_team(conn, team_id, assign_pick_id)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))

        st.divider()
        st.markdown("**Or create a new coach**")
        ncc1, ncc2 = st.columns(2)
        new_coach_first = ncc1.text_input(
            "First name", key=f"{key_prefix}_new_coach_first_{team_id}", disabled=is_read_only
        )
        new_coach_last = ncc2.text_input(
            "Last name", key=f"{key_prefix}_new_coach_last_{team_id}", disabled=is_read_only
        )
        st.caption("Add phone/email/nickname/children for this coach in the Coaches tab.")
        if st.button(
            "Create & Assign", key=f"{key_prefix}_create_coach_btn_{team_id}", type="primary",
            disabled=is_read_only,
        ):
            if new_coach_first.strip():
                new_coach_id = core.add_coach(conn, new_coach_first.strip(), new_coach_last.strip() or None)
                try:
                    core.assign_coach_to_team(conn, team_id, new_coach_id)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.error("First name is required.")

    if assigned:
        assigned_names = {c["id"]: coach_label(c) for c in assigned}
        remove_col1, remove_col2 = st.columns([3, 1])
        with remove_col1:
            coach_to_remove = st.selectbox(
                "Remove coach", options=list(assigned_ids), format_func=lambda i: assigned_names[i],
                key=f"{key_prefix}_remove_coach_pick_{team_id}",
            )
        with remove_col2:
            if st.button("Remove", key=f"{key_prefix}_remove_coach_btn_{team_id}", disabled=is_read_only):
                core.remove_coach_from_team(conn, team_id, coach_to_remove)
                st.rerun()


def render_coach_panel(
    conn, coach_id: int, division_name_by_id: dict, all_divisions: list[dict], key_prefix: str,
    nav_ids: list[int] | None = None, nav_pending_key: str | None = None,
):
    """The full coach profile editor — name/contact/children/teams coached,
    mirroring render_player_panel's shape (see its docstring for the
    nav_ids/nav_pending_key mechanism, reused as-is here)."""
    coach = core.get_coach(conn, coach_id)
    if coach is None:
        st.error("Coach not found.")
        return

    st.subheader(coach_label(coach))
    ccol1, ccol2, ccol3 = st.columns(3)
    edit_first_name = ccol1.text_input(
        "First name", value=coach["first_name"] or "", key=f"{key_prefix}_first_{coach_id}",
        disabled=is_read_only,
    )
    edit_last_name = ccol2.text_input(
        "Last name", value=coach["last_name"] or "", key=f"{key_prefix}_last_{coach_id}",
        disabled=is_read_only,
    )
    edit_nickname = ccol3.text_input(
        "Nickname", value=coach["nickname"] or "", key=f"{key_prefix}_nickname_{coach_id}",
        disabled=is_read_only,
    )

    with st.expander("📇 Contact info"):
        if hide_contact_details:
            st.caption("🔒 Phone/email are hidden for your role.")
        else:
            phcol1, phcol2 = st.columns(2)
            edit_phone = phcol1.text_input(
                "Phone", value=coach["phone"] or "", key=f"{key_prefix}_phone_{coach_id}", disabled=is_read_only
            )
            edit_email = phcol2.text_input(
                "Email", value=coach["email"] or "", key=f"{key_prefix}_email_{coach_id}", disabled=is_read_only
            )

    with st.expander("👶 Children registered"):
        children = core.list_coach_children(conn, coach_id)
        if children:
            for child in children:
                chcol1, chcol2 = st.columns([4, 1])
                with chcol1:
                    child_div = (
                        division_name_by_id.get(child["current_division_id"], "—")
                        if child["current_division_id"] else "—"
                    )
                    st.write(f'{child["name"]} ({child_div})')
                with chcol2:
                    if st.button("✕", key=f"{key_prefix}_unlink_child_{coach_id}_{child['id']}", disabled=is_read_only):
                        core.unlink_coach_child(conn, coach_id, child["id"])
                        st.rerun()
        else:
            st.caption("No registered children linked yet.")

        linked_ids = {c["id"] for c in children}
        # "Sub"/"SUB" placeholder players aren't a real person to link —
        # same exclusion the Players tab applies (see its picker below).
        pickable_players = [
            p for p in core.list_players(conn)
            if p["id"] not in linked_ids and p["name"].strip().lower() != "sub"
        ]
        if pickable_players:
            player_pick_options = {p["id"]: p["name"] for p in pickable_players}
            pcol1, pcol2 = st.columns([3, 1])
            with pcol1:
                child_to_link = st.selectbox(
                    "Link a registered player as this coach's child", options=list(player_pick_options),
                    format_func=lambda i: player_pick_options[i], key=f"{key_prefix}_link_child_pick_{coach_id}",
                )
            with pcol2:
                if st.button("Link", key=f"{key_prefix}_link_child_btn_{coach_id}", disabled=is_read_only):
                    core.link_coach_child(conn, coach_id, child_to_link)
                    st.rerun()

    with st.expander("🏒 Teams coached"):
        history = core.list_coach_teams(conn, coach_id)
        if history:
            for h in history:
                st.write(f"- {h['year']} {h['season']} — {division_label(h['age_group'])} ({h['team_name']})")
        else:
            st.caption("Not assigned to any team yet.")

        st.divider()
        st.caption("Assign to a team")
        if not all_divisions:
            st.write("No divisions yet — add one in the Teams tab → Divisions.")
        else:
            assign_division_id = st.selectbox(
                "Division", options=list(division_name_by_id), format_func=lambda i: division_name_by_id[i],
                key=f"{key_prefix}_assign_division_{coach_id}",
            )
            assign_teams = core.list_teams(conn, assign_division_id)
            already_coaching = {h["team_id"] for h in history if h["division_id"] == assign_division_id}
            assignable_teams = [t for t in assign_teams if t["id"] not in already_coaching]
            if not assignable_teams:
                st.caption("No unassigned teams in this division.")
            else:
                assign_coaches_by_team = core.list_team_coaches_for_division(conn, assign_division_id)
                assign_team_options = {
                    t["id"]: team_label_with_coaches(t, assign_coaches_by_team) for t in assignable_teams
                }
                assign_team_id = st.selectbox(
                    "Team", options=list(assign_team_options), format_func=lambda i: assign_team_options[i],
                    key=f"{key_prefix}_assign_team_{coach_id}",
                )
                if st.button(
                    "Assign", key=f"{key_prefix}_assign_team_btn_{coach_id}", type="primary", disabled=is_read_only
                ):
                    try:
                        core.assign_coach_to_team(conn, assign_team_id, coach_id)
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    show_nav = nav_ids is not None and nav_pending_key is not None
    nav_idx = nav_ids.index(coach_id) if show_nav and coach_id in nav_ids else None
    if show_nav:
        prev_col, save_col, delete_col, next_col = st.columns(4)
        with prev_col:
            can_prev = nav_idx is not None and nav_idx > 0
            if st.button(
                "◀ Previous", key=f"{key_prefix}_nav_prev_{coach_id}", disabled=not can_prev, width="stretch"
            ):
                st.session_state[nav_pending_key] = nav_ids[nav_idx - 1]
                st.rerun()
    else:
        save_col, delete_col = st.columns(2)
    with save_col:
        if st.button(
            "Save changes", key=f"{key_prefix}_save_{coach_id}", type="primary", disabled=is_read_only
        ):
            save_fields = {
                "first_name": edit_first_name.strip(), "last_name": edit_last_name.strip() or None,
                "nickname": edit_nickname.strip() or None,
            }
            if not hide_contact_details:
                save_fields["phone"] = edit_phone.strip() or None
                save_fields["email"] = edit_email.strip() or None
            core.update_coach(conn, coach_id, **save_fields)
            st.success("Saved.")
            st.rerun()
    with delete_col:
        if st.button("🗑️ Delete coach", key=f"{key_prefix}_delete_{coach_id}", disabled=is_read_only):
            confirm_delete_coach_dialog(key_prefix, coach_id, coach_label(coach))
    if show_nav:
        with next_col:
            can_next = nav_idx is not None and nav_idx < len(nav_ids) - 1
            if st.button(
                "Next ▶", key=f"{key_prefix}_nav_next_{coach_id}", disabled=not can_next, width="stretch"
            ):
                st.session_state[nav_pending_key] = nav_ids[nav_idx + 1]
                st.rerun()


@st.fragment(run_every="5s")
def render_draft_live(conn, draft_division_id: int):
    """The stateful part of the Draft tab — setup-vs-in-progress-vs-completed,
    current turn, pick pool/controls, and history. Streamlit has no
    cross-session push, so this is wrapped in a fragment that reruns on its
    own every 5s: every open Draft tab (any division) picks up picks made
    elsewhere without anyone needing to hit Refresh. Only this fragment
    reruns on its timer, not the whole page, so it doesn't disturb state
    elsewhere (other tabs, the Working Division picker, widgets mid-edit)."""
    draft = core.get_draft(conn, draft_division_id)

    if draft is None:
        st.subheader("Set up a new draft")
        draft_teams = core.list_teams(conn, draft_division_id)
        pool = core.draft_pool(conn, draft_division_id)
        auto_draft_run = core.get_auto_draft_run(conn, draft_division_id)

        if "draft_tab_auto_draft_msg" in st.session_state:
            auto_msg, auto_msg_kind = st.session_state.pop("draft_tab_auto_draft_msg")
            (st.warning if auto_msg_kind == "warning" else st.success)(auto_msg)

        st.caption(f"{len(pool)} player(s) currently eligible for this division's pool.")
        if len(draft_teams) < 2:
            st.warning("Add at least two teams to this division (in the Teams tab → Divisions) before drafting.")
        elif not pool and not auto_draft_run:
            st.warning(
                "No eligible players — everyone registered for this division is already rostered, "
                "or nobody's current division is set to this one yet."
            )
        else:
            with st.expander("🤖 Auto-Draft", expanded=True):
                st.caption(
                    "Assigns everyone currently in the pool at once, balanced by rank and roster size, "
                    "keeping siblings together and seating a coach's own registered child with their "
                    "team. Running this again undoes the previous auto-draft first, so re-drafting from "
                    "scratch is just clicking it again."
                )
                acol1, acol2 = st.columns(2)
                with acol1:
                    if st.button(
                        "🔁 Re-run Auto-Draft" if auto_draft_run else "🤖 Auto-Draft",
                        key="draft_tab_auto_draft", type="primary", disabled=is_read_only,
                    ):
                        try:
                            result = core.auto_draft(conn, draft_division_id)
                            msg = f"Auto-drafted {result['assigned']} player(s) across {result['teams']} teams."
                            if result["warnings"]:
                                msg += " Warnings: " + "; ".join(result["warnings"])
                                st.session_state["draft_tab_auto_draft_msg"] = (msg, "warning")
                            else:
                                st.session_state["draft_tab_auto_draft_msg"] = (msg, "success")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                with acol2:
                    if auto_draft_run and st.button(
                        "↩️ Undo Auto-Draft", key="draft_tab_undo_auto_draft", disabled=is_read_only,
                    ):
                        core.undo_auto_draft(conn, draft_division_id)
                        st.rerun()

            if pool:
                st.divider()
                order_pending_key = "draft_tab_order_pending"
                if order_pending_key in st.session_state:
                    st.session_state["draft_tab_order_pick"] = st.session_state.pop(order_pending_key)
                if st.button("🔀 Randomize order", key="draft_tab_shuffle"):
                    shuffled = [t["id"] for t in draft_teams]
                    random.shuffle(shuffled)
                    st.session_state[order_pending_key] = shuffled
                    st.rerun()

                draft_coaches_by_team = core.list_team_coaches_for_division(conn, draft_division_id)
                team_name_by_id = {t["id"]: team_label_with_coaches(t, draft_coaches_by_team) for t in draft_teams}
                order_pick = st.multiselect(
                    "Draft order — click teams in the order they should pick (round 1; later rounds snake back)",
                    options=list(team_name_by_id), format_func=lambda i: team_name_by_id[i],
                    key="draft_tab_order_pick",
                )
                missing = [t for t in draft_teams if t["id"] not in order_pick]
                if missing:
                    st.caption(f"Still need to add: {', '.join(t['name'] for t in missing)}")
                if st.button(
                    "Start Draft", key="draft_tab_start", type="primary",
                    disabled=is_read_only or bool(missing),
                ):
                    try:
                        core.start_draft(conn, draft_division_id, order_pick)
                        st.session_state.pop("draft_tab_order_pick", None)
                        st.success("Draft started!")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    else:
        order = core.list_draft_order(conn, draft["id"])
        picks = core.list_draft_picks(conn, draft["id"])
        pool = core.draft_pool(conn, draft_division_id)

        if draft["status"] == "completed":
            st.success("🏁 Draft complete!")
        else:
            current_team_id = core.current_pick_team_id(conn, draft["id"])
            current_team_name = next((t["team_name"] for t in order if t["team_id"] == current_team_id), "?")
            n_teams = len(order)
            round_num = (draft["current_pick_number"] - 1) // n_teams + 1
            st.info(
                f"**Round {round_num}, Pick #{draft['current_pick_number']}** — "
                f"it's **{current_team_name}**'s turn. {len(pool)} player(s) left in the pool."
            )

            team_coach_ids = {c["id"] for c in core.list_team_coaches(conn, current_team_id)}
            can_pick = user["is_admin"] or (user.get("coach_id") is not None and user["coach_id"] in team_coach_ids)

            if not pool:
                st.warning("The pool is empty but the draft hasn't been marked complete — try Undo below.")
            elif not can_pick:
                st.caption(f"Waiting for {current_team_name}'s coach (or an admin) to make this pick.")
            else:
                pick_search = st.text_input("Search the pool", key="draft_tab_pick_search")
                pool_matches = pool
                if pick_search.strip():
                    needle = pick_search.strip().lower()
                    pool_matches = [
                        p for p in pool
                        if needle in p["name"].lower() or needle in (p["nickname"] or "").lower()
                    ]
                if not pool_matches:
                    st.caption("No matching players in the pool.")
                else:
                    pool_options = {
                        p["id"]: (f'{p["name"]} "{p["nickname"]}"' if p["nickname"] else p["name"])
                        for p in pool_matches
                    }
                    pcol1, pcol2 = st.columns([3, 1])
                    with pcol1:
                        pick_player_id = st.selectbox(
                            "Pool", options=list(pool_options), format_func=lambda i: pool_options[i],
                            key="draft_tab_pick_select", label_visibility="collapsed",
                        )
                    with pcol2:
                        if st.button(
                            "Draft", key="draft_tab_pick_btn", type="primary", disabled=is_read_only
                        ):
                            try:
                                core.submit_draft_pick(conn, draft["id"], pick_player_id)
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

        if user["is_admin"] and picks and st.button("↩️ Undo last pick", key="draft_tab_undo"):
            try:
                core.undo_last_pick(conn, draft["id"])
                st.rerun()
            except ValueError as e:
                st.error(str(e))

        with st.expander(f"Draft order ({len(order)} teams)"):
            st.write(" → ".join(o["team_name"] for o in order) + " → (reverses each round)")

        with st.expander(f"Pick history ({len(picks)})", expanded=True):
            if not picks:
                st.caption("No picks yet.")
            else:
                for p in picks:
                    nickname_part = f' "{p["nickname"]}"' if p["nickname"] else ""
                    st.write(
                        f"**#{p['pick_number']}** (Rd {p['round']}) — {p['team_name']}: "
                        f"{p['player_name']}{nickname_part}"
                    )

        if user["is_admin"]:
            st.divider()
            draft_delete_confirm_key = f"confirm_delete_draft_{draft['id']}"
            if st.button("🗑️ Delete this draft", key=f"delete_draft_{draft['id']}"):
                st.session_state[draft_delete_confirm_key] = True
                st.rerun()
            if st.session_state.get(draft_delete_confirm_key):
                st.warning(
                    "Delete this draft's order and pick history? Players already drafted stay on "
                    "their team's roster — this only resets the draft itself so it can be re-run."
                )
                dcol1, dcol2 = st.columns(2)
                with dcol1:
                    if st.button("Yes, delete", key=f"confirm_yes_draft_{draft['id']}", type="primary"):
                        core.delete_draft(conn, draft["id"])
                        st.session_state.pop(draft_delete_confirm_key, None)
                        st.rerun()
                with dcol2:
                    if st.button("Cancel", key=f"confirm_no_draft_{draft['id']}"):
                        st.session_state.pop(draft_delete_confirm_key, None)
                        st.rerun()


def render_evaluation_progress_chart(evaluations: list[dict]):
    """A small line+point chart of a player's graded evaluation history
    across every division/season they've been rated in — so progress (or
    regression) is visible at a glance instead of scanning a popover list.
    Only evaluations whose grade is one of the league's actual skill tiers
    (GRADE_TIERS) are plotted; a position note like "Goalie" or a status
    like "New" isn't a point on a skill axis and is left off, though it's
    still visible in the Evaluations popover's full list. Shows a single
    point for one graded season (no trend line yet, but still worth
    seeing), or an explanatory caption instead of just going silent when
    there's nothing gradeable to plot."""
    tiered = [e for e in evaluations if e["grade"] and e["grade"].strip().upper() in GRADE_TIERS]
    if not tiered:
        st.caption(
            "No graded evaluations (A/B/C/D) on file yet — add one in the Evaluations popover above."
        )
        return

    rows = [
        {
            "season_label": f"{e['year']} {e['season']}",
            "sort_key": (e["year"], SEASON_ORDER.get(e["season"], 99)),
            "grade": e["grade"].strip().upper(),
            "division": f"{e['year']} {e['season']} — {division_label(e['age_group'])}",
        }
        for e in tiered
    ]
    rows.sort(key=lambda r: r["sort_key"])
    # Dedupe consecutive same-season entries (e.g. a grade edited more than
    # once) down to the latest, keeping this a one-point-per-season trend.
    by_season = {r["season_label"]: r for r in rows}
    df = pd.DataFrame(by_season.values())
    season_order = list(by_season)

    if len(by_season) == 1:
        st.caption("Evaluation grade (only one graded season so far — add another to see a trend)")
    else:
        st.caption("Evaluation progress over time")
    chart = (
        alt.Chart(df)
        .mark_line(point={"size": 80, "filled": True}, strokeWidth=2, color="#FFC72C")
        .encode(
            x=alt.X("season_label:N", sort=season_order, title=None, axis=alt.Axis(labelAngle=-30)),
            y=alt.Y(
                "grade:O", sort=GRADE_TIERS, scale=alt.Scale(domain=GRADE_TIERS), title=None,
                # Ordinal axes don't grid by default the way numeric ones do —
                # turn it on so a horizontal line runs behind each grade tier,
                # making it easy to trace a point straight across to its A/B/C/D.
                axis=alt.Axis(grid=True, gridDash=[3, 3]),
            ),
            tooltip=[alt.Tooltip("division:N", title="Division"), alt.Tooltip("grade:N", title="Grade")],
        )
        .properties(height=180)
    )
    st.altair_chart(chart, width="stretch")


def render_player_stats_summary(conn, player_id: int):
    """A compact per-division stat line (goals/assists/points/PIM/
    shootout) for every division this player has game-derived stats in —
    shown alongside the evaluation chart so grades and on-ice performance
    read together instead of needing a trip to the Player Stats tab.
    Evaluation-only divisions (rated but never rostered onto a team with
    games — e.g. an off-season tryout cycle) simply have no stats here,
    same as they'd have none in Player Stats. One division needs no tabs;
    more than one gets a tab per division."""
    history = core.player_division_history(conn, player_id)
    if not history:
        return

    per_division = []
    for h in history:
        row = next(
            (s for s in core.get_player_stats(conn, h["division_id"]) if s["player_id"] == player_id), None
        )
        if row is not None:
            per_division.append((h, row))
    if not per_division:
        return

    def _render_stat_row(row: dict):
        cols = st.columns(6)
        values = [
            ("Goals", row["goals"]), ("Assists", row["assists"]), ("Points", row["points"]),
            ("PIM", row["penalties"]), ("SO Made", row["shootout_goals"]), ("SO Missed", row["shootout_misses"]),
        ]
        for col, (label, value) in zip(cols, values):
            col.metric(label, value)

    st.caption("Stats by division")
    if len(per_division) == 1:
        h, row = per_division[0]
        st.write(f"{h['year']} {h['season']} — {division_label(h['age_group'])} ({h['team_name']} #{h['number']})")
        _render_stat_row(row)
    else:
        stat_tabs = st.tabs([f"{h['year']} {h['season']}" for h, _ in per_division])
        for stat_tab, (h, row) in zip(stat_tabs, per_division):
            with stat_tab:
                st.write(f"{division_label(h['age_group'])} — {h['team_name']} #{h['number']}")
                _render_stat_row(row)


def render_player_panel(
    conn, player_id: int, division_name_by_id: dict, all_divisions: list[dict], key_prefix: str,
    nav_ids: list[int] | None = None, nav_pending_key: str | None = None,
):
    """The full player profile editor — name/dob/current division/contacts,
    save/soft-delete, and an Evaluations popover. Reused both by the Players
    tab (picked from a dropdown) and by a "Player Panel" action opened
    inline from a linked Player Stats row, so there's one implementation of
    "the player panel" regardless of where it's opened from. key_prefix
    keeps widget keys unique between those two call sites.

    nav_ids/nav_pending_key add Previous/Next buttons alongside Save/Delete
    for browsing a caller-supplied ordered list of player ids (the Players
    tab's alphabetical list) — omitted where that doesn't apply (the
    Player Stats tab's single-row "Player Panel" popover). Clicking one
    can't write directly to the caller's own selectbox session_state key,
    since that widget has already been instantiated earlier in this same
    script run by the time this function is called — Streamlit forbids
    modifying a widget's state after it's created in the same run. Instead
    it stashes the target id under nav_pending_key, a plain (non-widget)
    session key, which the caller applies to its selectbox's key *before*
    creating that widget on the next run."""
    player = core.get_player(conn, player_id)
    if player is None:
        st.error("Player not found.")
        return

    st.subheader(f'{player["name"]} "{player["nickname"]}"' if player["nickname"] else player["name"])
    ecol1, ecol2, ecol2b = st.columns(3)
    edit_first_name = ecol1.text_input(
        "First name", value=player["first_name"] or "", key=f"{key_prefix}_first_{player_id}",
        disabled=is_read_only,
    )
    edit_last_name = ecol2.text_input(
        "Last name", value=player["last_name"] or "", key=f"{key_prefix}_last_{player_id}",
        disabled=is_read_only,
    )
    edit_nickname = ecol2b.text_input(
        "Nickname", value=player["nickname"] or "", key=f"{key_prefix}_nickname_{player_id}",
        disabled=is_read_only,
    )
    edit_dob = st.text_input(
        "Birth date", value=player["birth_date"] or "", key=f"{key_prefix}_dob_{player_id}",
        placeholder="YYYY-MM-DD", disabled=is_read_only,
    )
    division_ids = [None] + list(division_name_by_id)
    current_idx = (
        division_ids.index(player["current_division_id"]) if player["current_division_id"] in division_ids else 0
    )
    dcol1, dcol2 = st.columns(2)
    edit_division = dcol1.selectbox(
        "Current division", options=division_ids,
        format_func=lambda i: "(none)" if i is None else division_name_by_id[i],
        index=current_idx, key=f"{key_prefix}_division_{player_id}", disabled=is_read_only,
    )
    if player["current_division_id"] is not None:
        current_team_entries = [
            h for h in core.player_division_history(conn, player_id) if h["division_id"] == player["current_division_id"]
        ]
        team_number_display = (
            ", ".join(f"#{h['number']} ({h['team_name']})" for h in current_team_entries)
            if current_team_entries else "Not on a roster yet"
        )
    else:
        team_number_display = "—"
    dcol2.text_input(
        "Current Team Number", value=team_number_display, key=f"{key_prefix}_team_number_{player_id}",
        disabled=True, help="Derived from Team Rosters — link this player to a roster row there to set it.",
    )
    if player["current_division_id"] is not None and current_team_entries:
        for h in current_team_entries:
            position_input(
                conn, player_id, player["current_division_id"], h["team_id"],
                key=f"{key_prefix}_position_{player_id}_{h['team_id']}",
                help=f"Position on {h['team_name']} for this division." if len(current_team_entries) > 1 else None,
                disabled=is_read_only,
            )

    with st.expander("📇 Contact info"):
        ecol3, ecol4 = st.columns(2)
        edit_cfn = ecol3.text_input(
            "Contact first name", value=player["contact_first_name"] or "", key=f"{key_prefix}_cfn_{player_id}",
            disabled=is_read_only,
        )
        edit_cln = ecol4.text_input(
            "Contact last name", value=player["contact_last_name"] or "", key=f"{key_prefix}_cln_{player_id}",
            disabled=is_read_only,
        )
        if hide_contact_details:
            st.caption("🔒 Phone/email are hidden for your role.")
        else:
            ecol5, ecol6 = st.columns(2)
            edit_cph = ecol5.text_input(
                "Contact phone", value=player["contact_phone"] or "", key=f"{key_prefix}_cph_{player_id}",
                disabled=is_read_only,
            )
            edit_cem = ecol6.text_input(
                "Contact email", value=player["contact_email"] or "", key=f"{key_prefix}_cem_{player_id}",
                disabled=is_read_only,
            )

    with st.expander("👨‍👩‍👧 Parent / Siblings"):
        # parent_id is auto-matched from contact info (see
        # get_or_create_parent) whenever it's saved above -- this is the
        # verification/correction step for when that guess needs fixing,
        # e.g. two kids whose parent used a different phone/email on each
        # registration, or one household sharing a parent by coincidence
        # that isn't actually siblings.
        siblings = core.list_siblings(conn, player_id)
        if player["parent_id"]:
            parent = next((p for p in core.list_parents(conn) if p["id"] == player["parent_id"]), None)
            if parent:
                parent_detail = " — ".join(v for v in (parent["phone"], parent["email"]) if v)
                st.write(f"Parent on file: **{parent['name']}**" + (f" ({parent_detail})" if parent_detail else ""))
            if siblings:
                st.caption("Siblings: " + ", ".join(s["name"] for s in siblings))
            else:
                st.caption("No siblings linked.")
            if st.button("Unlink parent", key=f"{key_prefix}_unlink_parent_{player_id}", disabled=is_read_only):
                core.set_player_parent(conn, player_id, None)
                st.rerun()
        else:
            st.caption("No parent linked yet.")

        st.markdown("**Link to a different parent**")
        other_parents = [p for p in core.list_parents(conn) if p["id"] != player["parent_id"]]
        if not other_parents:
            st.caption("No other parents on file yet.")
        else:
            parent_search = st.text_input(
                "Search by name", key=f"{key_prefix}_parent_search_{player_id}"
            )
            needle = parent_search.strip().lower()
            matches = [p for p in other_parents if not needle or needle in p["name"].lower()]
            if not matches:
                st.caption("No matching parents.")
            else:
                match_options = {
                    p["id"]: p["name"] + (f" ({p['phone']})" if p["phone"] else "") for p in matches
                }
                pcol1, pcol2 = st.columns([3, 1])
                with pcol1:
                    pick_parent_id = st.selectbox(
                        "Matches", options=list(match_options), format_func=lambda i: match_options[i],
                        key=f"{key_prefix}_parent_pick_{player_id}", label_visibility="collapsed",
                    )
                with pcol2:
                    if st.button("Link", key=f"{key_prefix}_link_parent_{player_id}", disabled=is_read_only):
                        core.set_player_parent(conn, player_id, pick_parent_id)
                        st.rerun()

    if working_division_id is None:
        st.caption("Select a Working Division above to set this player's Season Grade.")
    else:
        current_working_grade = core.get_season_grade(conn, player_id, working_division_id)
        sg_col1, sg_col2 = st.columns([3, 1])
        with sg_col1:
            season_grade_input(
                conn, player_id, working_division_id, None, key=f"{key_prefix}_season_grade_{player_id}",
                prefetched=current_working_grade,
                help="This player's grade for the current Working Division — editing it here updates "
                     "their most recent evaluation for that division instead of adding a new one to its "
                     "history. Use New Grade instead to start a fresh evaluation.",
                disabled=is_read_only,
            )
        with sg_col2:
            if st.button(
                "➕ New Grade", key=f"{key_prefix}_new_grade_{player_id}",
                disabled=is_read_only or not current_working_grade,
                help="Starts a fresh evaluation for the current Working Division, keeping the existing "
                     "one in this player's history instead of overwriting it.",
            ):
                core.add_evaluation(conn, player_id, working_division_id, None, "")
                st.rerun()

    show_nav = nav_ids is not None and nav_pending_key is not None
    nav_idx = nav_ids.index(player_id) if show_nav and player_id in nav_ids else None
    if show_nav:
        prev_col, save_col, delete_col, eval_col, next_col = st.columns(5)
        with prev_col:
            can_prev = nav_idx is not None and nav_idx > 0
            if st.button(
                "◀ Previous", key=f"{key_prefix}_nav_prev_{player_id}", disabled=not can_prev, width="stretch"
            ):
                st.session_state[nav_pending_key] = nav_ids[nav_idx - 1]
                st.rerun()
    else:
        save_col, delete_col, eval_col = st.columns(3)
    with save_col:
        if st.button(
            "Save changes", key=f"{key_prefix}_save_{player_id}", type="primary", disabled=is_read_only
        ):
            save_fields = {
                "first_name": edit_first_name.strip(), "last_name": edit_last_name.strip() or None,
                "nickname": edit_nickname.strip() or None, "birth_date": edit_dob.strip() or None,
                "current_division_id": edit_division,
                "contact_first_name": edit_cfn.strip() or None, "contact_last_name": edit_cln.strip() or None,
            }
            # Phone/email aren't even rendered (so there's nothing to read
            # them from) when hide_contact_details hides them — leave those
            # two columns untouched rather than saving over them.
            if not hide_contact_details:
                save_fields["contact_phone"] = edit_cph.strip() or None
                save_fields["contact_email"] = edit_cem.strip() or None
            core.update_player(conn, player_id, **save_fields)
            st.success("Saved.")
            st.rerun()
    with delete_col:
        if st.button("🗑️ Delete player", key=f"{key_prefix}_delete_{player_id}", disabled=is_read_only):
            confirm_delete_player_dialog(key_prefix, player_id, player["name"])
    with eval_col:
        with st.popover("📋 Evaluations"):
            history = core.player_division_history(conn, player_id)
            if history:
                st.caption("Divisions played:")
                for h in history:
                    st.write(
                        f"- {h['year']} {h['season']} — {division_label(h['age_group'])} "
                        f"({h['team_name']} #{h['number']})"
                    )

            evaluations = core.list_evaluations(conn, player_id)
            if evaluations:
                st.caption("Past evaluations:")
                for ev in evaluations:
                    evcol1, evcol2 = st.columns([4, 1])
                    with evcol1:
                        team_part = f" — {ev['team_name']}" if ev["team_name"] else ""
                        st.write(f"{ev['year']} {ev['season']} {ev['age_group']}{team_part}: **{ev['grade']}**")
                    with evcol2:
                        if st.button("✕", key=f"{key_prefix}_delete_eval_{ev['id']}", disabled=is_read_only):
                            core.delete_evaluation(conn, ev["id"])
                            st.rerun()
            else:
                st.caption("No evaluations yet.")

            st.divider()
            st.caption("Add an evaluation")
            if not all_divisions:
                st.write("No divisions yet — add one in the Teams tab → Divisions.")
            else:
                eval_division_id = st.selectbox(
                    "Division", options=list(division_name_by_id), format_func=lambda i: division_name_by_id[i],
                    key=f"{key_prefix}_eval_division_{player_id}",
                )
                eval_teams = core.list_teams(conn, eval_division_id)
                eval_coaches_by_team = core.list_team_coaches_for_division(conn, eval_division_id)
                eval_team_options = {None: "(none)"} | {
                    t["id"]: team_label_with_coaches(t, eval_coaches_by_team) for t in eval_teams
                }
                eval_team_id = st.selectbox(
                    "Team", options=list(eval_team_options), format_func=lambda i: eval_team_options[i],
                    key=f"{key_prefix}_eval_team_{player_id}",
                )
                eval_grade = st.text_input(
                    "Grade", key=f"{key_prefix}_eval_grade_{player_id}", disabled=is_read_only
                )
                if st.button(
                    "Add evaluation", key=f"{key_prefix}_add_eval_{player_id}", type="primary",
                    disabled=is_read_only,
                ):
                    if eval_grade.strip():
                        core.add_evaluation(conn, player_id, eval_division_id, eval_team_id, eval_grade.strip())
                        st.rerun()
                    else:
                        st.error("Grade is required.")

    if show_nav:
        with next_col:
            can_next = nav_idx is not None and nav_idx < len(nav_ids) - 1
            if st.button(
                "Next ▶", key=f"{key_prefix}_nav_next_{player_id}", disabled=not can_next, width="stretch"
            ):
                st.session_state[nav_pending_key] = nav_ids[nav_idx + 1]
                st.rerun()

    render_evaluation_progress_chart(evaluations)
    render_player_stats_summary(conn, player_id)


# ---------------------------------------------------------------------------
# Sidebar: shared settings
# ---------------------------------------------------------------------------

theme_mode = st.sidebar.radio("Theme", ["Dark", "Light"], horizontal=True, key="theme_mode")
inject_theme_css(theme_mode)
render_sidebar_logo(theme_mode)

st.sidebar.title("Settings")

# Rendered early (before any of the st.stop() calls below, e.g. when no
# database is selected yet) so it's always visible regardless of app state —
# a version marker that disappears in some states isn't very useful. Full
# pixel-pinning to the sidebar's visual bottom edge would require overriding
# Streamlit's internal flex layout, which turned out to corrupt other
# sidebar widgets' rendering, so this settles for "always visible, near the
# top" over a real display-breaking hack.
st.sidebar.caption(f"v{APP_VERSION}")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    _pg_host = os.environ.get("PGHOST")
    _pg_dbname = os.environ.get("PGDATABASE")
    _pg_user = os.environ.get("PGUSER")
    if _pg_host and _pg_dbname and _pg_user:
        DATABASE_URL = (
            f"postgresql://{_pg_user}:{os.environ.get('PGPASSWORD', '')}@"
            f"{_pg_host}:{os.environ.get('PGPORT', '5432')}/{_pg_dbname}"
            f"?sslmode={os.environ.get('PGSSLMODE', 'require')}"
        )

st.sidebar.markdown("**Database**")
if not DATABASE_URL:
    st.sidebar.error("No database configured.")
    st.info(
        "Set the `DATABASE_URL` environment variable (a full Postgres connection "
        "string) — or `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD`/`PGPORT`/"
        "`PGSSLMODE` individually — before running the app."
    )
    st.stop()

# One connection per browser session, reused across every rerun (Streamlit
# reruns the whole script on every interaction) rather than reconnecting to
# Postgres every time — cheap for a local SQLite file, but each reconnect
# against a remote server pays a real network+TLS handshake cost. Kept in
# session_state (not st.cache_resource) so concurrent sessions never share
# one psycopg2 connection object across threads.
if "conn" not in st.session_state:
    # Aiven (and similar managed-Postgres providers) let a service be
    # powered off to save cost between uses — connecting to one while it's
    # off just times out with no useful hint why. If the three AIVEN_*
    # variables are set, check first and power it back on if needed, so the
    # first sign-in after a quiet period gets a clear status message and a
    # working connection instead of a confusing timeout. Skipped entirely
    # (falling straight through to init_db, exactly as before this existed)
    # when they're not set — this is opt-in, not a new requirement.
    aiven_api_token = os.environ.get("AIVEN_API_TOKEN")
    aiven_project_name = os.environ.get("AIVEN_PROJECT_NAME")
    aiven_service_name = os.environ.get("AIVEN_SERVICE_NAME")
    aiven_configured = bool(aiven_api_token and aiven_project_name and aiven_service_name)
    if aiven_configured:
        aiven_status = st.empty()
        try:
            state = aiven_service.get_service_state(aiven_api_token, aiven_project_name, aiven_service_name)
            if state != aiven_service.RUNNING_STATE:
                aiven_status.info(f"Database service is **{state}** — starting it back up...")

                def _report_aiven_progress(state: str, elapsed: float):
                    aiven_status.info(
                        f"Waiting for the database service to come up — currently **{state}** "
                        f"({int(elapsed)}s elapsed)..."
                    )

                aiven_service.ensure_service_running(
                    aiven_api_token, aiven_project_name, aiven_service_name, on_poll=_report_aiven_progress
                )
                aiven_status.success("Database service is up.")
        except aiven_service.AivenServiceError as e:
            # Don't block startup on this check failing (e.g. a stale
            # token) — the service may well already be reachable; fall
            # through and let the actual connection attempt below be the
            # real test.
            aiven_status.warning(f"Couldn't confirm the database service is running: {e}")

        # This app's own database access is restricted to Streamlit Community
        # Cloud's published IPs (see scripts/aiven_ip_filter.py) — a list
        # Streamlit's own docs warn "may change at any time without notice",
        # with no API to watch for that, only this same periodic scrape. A
        # missing IP here means a real, looming risk: this app could stop
        # being able to reach its own database the moment Streamlit starts
        # serving it from a new one. Non-blocking (a stale/unreachable check
        # shouldn't stop the app from starting) and doesn't flag "extra"
        # entries — an admin's own IP is an expected, intentional extra, not
        # a problem worth a warning on every single startup.
        try:
            ip_diff = aiven_service.diff_ip_filter_against_streamlit(
                aiven_api_token, aiven_project_name, aiven_service_name
            )
            if ip_diff["missing"]:
                st.sidebar.warning(
                    f"Aiven's ip_filter is missing {len(ip_diff['missing'])} of Streamlit Cloud's current "
                    "IPs — this app may lose database access without warning. Run "
                    "`scripts/aiven_ip_filter.py sync --yes` (with your usual --extra-cidr flags) to fix it."
                )
        except aiven_service.AivenServiceError:
            pass  # best-effort — don't block or warn on startup over this check itself failing

    try:
        st.session_state["conn"] = core.init_db(DATABASE_URL)
    except Exception as e:
        st.error(f"Couldn't connect to the database: {e}")
        if aiven_configured:
            try:
                ip_diff = aiven_service.diff_ip_filter_against_streamlit(
                    aiven_api_token, aiven_project_name, aiven_service_name
                )
                if ip_diff["missing"]:
                    st.warning(
                        "Likely cause: Aiven's ip_filter doesn't allow this connection. It's currently "
                        f"missing {len(ip_diff['missing'])} of Streamlit Cloud's current IPs:\n\n"
                        + "\n".join(f"- `{cidr}`" for cidr in ip_diff["missing"])
                        + "\n\nAn admin can fix this with `scripts/aiven_ip_filter.py sync --yes` "
                          "(pass your usual --extra-cidr flags for any direct/admin access to preserve)."
                    )
            except aiven_service.AivenServiceError:
                pass  # the connection error above is still shown either way
        st.stop()
conn = st.session_state["conn"]

_db_host = urllib.parse.urlparse(DATABASE_URL).hostname
_db_name = urllib.parse.urlparse(DATABASE_URL).path.lstrip("/")
st.sidebar.caption(f"Connected to `{_db_name}` on `{_db_host}`")

# ---------------------------------------------------------------------------
# Authentication — every session must sign in with an email/password account.
# Accounts are created by an admin in the User Management tab; the very
# first admin has to be bootstrapped with scripts/manage_users.py, since
# nobody can reach User Management before at least one admin exists. Gates
# everything below this point — the sidebar bits above (theme, version, DB
# connection status) are harmless to show a signed-out visitor.
# ---------------------------------------------------------------------------

if "user" not in st.session_state:
    st.markdown("### Team Pittsburgh Ball Hockey")
    st.subheader("Sign In")
    with st.form("login_form"):
        login_email = st.text_input("Email")
        login_password = st.text_input("Password", type="password")
        login_submitted = st.form_submit_button("Log In", type="primary")
    if login_submitted:
        logged_in_user = core.verify_login(conn, login_email, login_password)
        if logged_in_user:
            st.session_state["user"] = logged_in_user
            st.rerun()
        else:
            st.error("Incorrect email or password.")
            # A wrong password is very often just Caps Lock — Python has no
            # way to read that (it's purely a client-side keyboard state),
            # so this injects a live warning that appears under the
            # password field the moment it's on, the next time they type.
            # Same iframe-reaching pattern as the Home page's card-click
            # wiring below, and for the same reason: Streamlit itself can't
            # attach a plain <script> via st.markdown.
            components.html(
                """
                <script>
                (function() {
                    function wire() {
                        var doc = window.parent.document;
                        doc.querySelectorAll('input[type="password"]').forEach(function(input) {
                            if (input.getAttribute('data-capslock-wired')) return;
                            input.setAttribute('data-capslock-wired', '1');

                            var warning = doc.createElement('div');
                            warning.textContent = '⚠️ Caps Lock is on';
                            warning.style.color = '#e8a33d';
                            warning.style.fontSize = '0.85rem';
                            warning.style.marginTop = '0.25rem';
                            warning.style.display = 'none';
                            input.insertAdjacentElement('afterend', warning);

                            function checkCapsLock(e) {
                                var isOn = e.getModifierState && e.getModifierState('CapsLock');
                                warning.style.display = isOn ? 'block' : 'none';
                            }
                            input.addEventListener('keydown', checkCapsLock);
                            input.addEventListener('keyup', checkCapsLock);
                        });
                    }
                    wire();
                    new MutationObserver(wire).observe(window.parent.document.body, {childList: true, subtree: true});
                })();
                </script>
                """,
                height=0,
            )
    st.stop()

user = st.session_state["user"]
# Admins aren't limited by their role at all — they always get every page
# (including User Management, which non-admins can never be granted), can
# always write, and always see full contact details.
visible_pages = list(core.PAGES) if user["is_admin"] else [p for p in core.PAGES if p in user["pages"]]
is_read_only = (not user["is_admin"]) and user["read_only"]
hide_contact_details = (not user["is_admin"]) and user["hide_contact_details"]

with st.sidebar:
    st.markdown(f"**Signed in:** {user['display_name'] or user['email']}" + (" (admin)" if user["is_admin"] else ""))
    if st.button("Log out"):
        del st.session_state["user"]
        st.rerun()

    with st.expander("🔑 API Tokens"):
        st.caption(
            "For scripts/integrations using api.py — a token authenticates as you, "
            "without sending your actual password on every request."
        )
        new_token_key = "new_api_token_result"
        if new_token_key in st.session_state:
            new_id, new_name, new_raw = st.session_state.pop(new_token_key)
            st.success(f'Created "{new_name}". Copy it now — it won\'t be shown again:')
            st.code(new_raw, language=None)

        new_token_name = st.text_input("New token name", key="new_api_token_name")
        if st.button("Create token", key="create_api_token_btn"):
            if new_token_name.strip():
                token_id, raw_token = core.create_api_token(conn, user["id"], new_token_name.strip())
                st.session_state[new_token_key] = (token_id, new_token_name.strip(), raw_token)
                st.rerun()
            else:
                st.error("Name is required (e.g. \"My Laptop\" or \"Zapier\").")

        existing_tokens = core.list_api_tokens(conn, user["id"])
        active_tokens = [t for t in existing_tokens if t["revoked_at"] is None]
        if not active_tokens:
            st.caption("No active tokens.")
        else:
            for t in active_tokens:
                trow1, trow2 = st.columns([3, 1])
                last_used = f"last used {t['last_used_at']}" if t["last_used_at"] else "never used"
                trow1.write(f"**{t['name']}**  \n*{last_used}*")
                with trow2:
                    if st.button("Revoke", key=f"revoke_token_{t['id']}"):
                        core.revoke_api_token(conn, t["id"], user["id"])
                        st.rerun()

api_key = st.sidebar.text_input(
    "ANTHROPIC_API_KEY",
    value=os.environ.get("ANTHROPIC_API_KEY", ""),
    type="password",
    help="Defaults to the ANTHROPIC_API_KEY environment variable.",
)

if fitz is None:
    st.sidebar.warning("pymupdf isn't installed — PDF previews will be unavailable.")

# Resolved here (before the Working Division selectbox widget itself is
# rendered further down) so the sidebar export button — which appears
# earlier in the script — can use it too. Reading/defaulting
# st.session_state["working_division_id"] before its widget is instantiated
# is fine; only writing to it *after* instantiation is disallowed.
all_divisions = core.list_divisions(conn)
if all_divisions:
    valid_division_ids = {d["id"] for d in all_divisions}
    if "working_division_id" not in st.session_state:
        # First render of a new session — recall what was picked last time
        # (persisted in the database itself, since session state doesn't
        # survive a restart) instead of always defaulting back to the first.
        saved = core.get_setting(conn, "working_division_id")
        saved_id = int(saved) if saved and saved.isdigit() else None
        st.session_state["working_division_id"] = (
            saved_id if saved_id in valid_division_ids else all_divisions[0]["id"]
        )
    elif st.session_state["working_division_id"] not in valid_division_ids:
        st.session_state["working_division_id"] = all_divisions[0]["id"]
    working_division_id = st.session_state["working_division_id"]
    core.set_setting(conn, "working_division_id", str(working_division_id))
    st.session_state["working_division_age_group"] = next(
        d["age_group"] for d in all_divisions if d["id"] == working_division_id
    )
else:
    working_division_id = None

if working_division_id is not None:
    # st.download_button needs its data ready upfront, unlike a plain
    # button — so calling export_workbook() directly here would rebuild
    # the whole workbook (several sheets, each its own query) on *every*
    # rerun just to keep the button armed, whether or not it's ever
    # clicked. Cached per division instead: built once, on request, and
    # reused until the division changes or a rebuild is asked for.
    _export_cache_key = f"export_workbook_{working_division_id}"
    if _export_cache_key not in st.session_state:
        if st.sidebar.button("Prepare Excel export", width="stretch"):
            st.session_state[_export_cache_key] = core.export_workbook(conn, working_division_id)
            st.rerun()
    else:
        st.sidebar.download_button(
            "Export to Excel",
            data=st.session_state[_export_cache_key],
            file_name="hockey_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Downloads the working division's Games, Standings, Player Stats, and Rosters as sheets in "
                 "one .xlsx file. Built as of when you clicked Prepare — click it again for a fresh copy.",
        )
        if st.sidebar.button("↻ Rebuild export", help="Data changed since this was built? Regenerate it."):
            del st.session_state[_export_cache_key]
            st.rerun()

with st.sidebar.expander("🔧 Utilities"):
    st.caption(
        "Split multi-page PDFs into individual single-page files — usually unnecessary, since "
        "Import Scoresheets already auto-splits on upload. Use this to just download the split "
        "pages without processing them."
    )

    split_uploaded = st.file_uploader(
        "Upload PDF(s) to split", type=["pdf"], accept_multiple_files=True, key="split_uploader"
    )

    if split_uploaded:
        results: list[tuple[str, bytes]] = []
        for f in split_uploaded:
            data = f.getvalue()
            pages = core.split_pdf_bytes(data)
            stem = Path(f.name).stem

            if len(pages) == 1:
                st.write(f"**{f.name}** — single page, no split needed.")
                results.append((f.name, pages[0]))
                continue

            st.write(f"**{f.name}** — {len(pages)} pages, split.")
            results.extend((f"{stem}_p{i}.pdf", p) for i, p in enumerate(pages, start=1))

        if results:
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                for name, data in results:
                    zf.writestr(name, data)
            st.download_button(
                "Save all as ZIP", data=zip_buf.getvalue(), file_name="split_pages.zip",
                mime="application/zip", type="primary",
            )
            if st.button("Load into Import Scoresheets", type="primary"):
                for key in list(st.session_state.keys()):
                    if key.startswith(("data_", "proc_", "replace_target_")):
                        del st.session_state[key]
                st.session_state.upload_key = (
                    "__from_split_pdf__", tuple(sorted((n, len(d)) for n, d in results))
                )
                st.session_state.queue = [
                    {"label": name, "bytes": data, "mime": "application/pdf", "status": "pending"}
                    for name, data in results
                ]
                st.session_state.queue_index = 0
                st.session_state["games_subpage_pending"] = "📄 Import Scoresheets"
                st.success("Loaded — switch to the **Games → Import Scoresheets** tab to continue.")

# ---------------------------------------------------------------------------
# Global working division — stays selected for the whole session, wherever
# you are in the tabs. Used to default the Division field for new sheets.
# ---------------------------------------------------------------------------

title_col, division_col = st.columns([3, 1])
with title_col:
    st.markdown("### Team Pittsburgh Ball Hockey")
with division_col:
    if all_divisions:
        div_labels = {
            d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions
        }
        st.selectbox(
            "Working Division", options=list(div_labels), format_func=lambda i: div_labels[i],
            key="working_division_id",
        )

if not all_divisions:
    st.info("No divisions in the database yet — create one to get started.")
    with st.popover("➕ Create your first division"):
        render_add_division_form(conn, key_prefix="top_")

# Home is always first and visible to everyone regardless of role — it's
# just a static tutorial/overview, not a data page, so it's never worth
# restricting (someone with only one or two pages granted still needs
# somewhere to learn what the app does at all). The primary tabs after it
# each hold a sub-menu (a radio acting as a second nav level, since
# Streamlit's own tabs don't nest cleanly) grouping several related pages —
# a signed-in user without permission for a given sub-page just sees an
# access-restricted message inside it (each sub-page's
# `if page_key not in visible_pages:` guard below) rather than it
# disappearing outright. User Management is different: it's only ever
# meaningful for admins (granting page access requires already having
# it), so it's the one tab that's actually absent for non-admins.
_tab_labels = ["🏠 Home", "🏒 Games", "📊 Stats & Standings", "📋 Teams"]
if user["is_admin"]:
    _tab_labels.append("🔑 User Management")

_tabs = st.tabs(_tab_labels)
tab_home, tab_games, tab_stats_standings, tab_teams_group = _tabs[:4]
tab_users = _tabs[4] if user["is_admin"] else None

# Sub-menu page-key groupings, used by the Home page's card-lock check and
# by each primary tab's own sub-nav radio below.
GAMES_SUBPAGES = {
    "📅 Schedule & Results": "schedule",
    "📄 Import Scoresheets": "process",
    "✏️ Manage Games": "edit",
}
STATS_STANDINGS_PAGE_KEYS = ["standings", "stats"]
TEAMS_SUBPAGES = {
    "📋 Teams": "teams",
    "👥 Team Rosters": "rosters",
    "🧑 Players": "players",
    "🧑‍🏫 Coaches": "coaches",
    "🗓️ Divisions": "divisions",
    "🎯 Draft": "draft",
}


def render_subnav(radio_key: str, options: dict[str, str]) -> str:
    """A horizontal radio acting as a second-level nav inside a primary tab.
    Supports the same "pending select" pattern used elsewhere in this app
    (e.g. players_tab_pending_select) so a button elsewhere can jump to a
    specific sub-page: set st.session_state[f"{radio_key}_pending"] to the
    target label and rerun, before this widget is instantiated again."""
    pending_key = f"{radio_key}_pending"
    if pending_key in st.session_state:
        st.session_state[radio_key] = st.session_state.pop(pending_key)
    return st.radio(
        "Section", list(options), horizontal=True, key=radio_key, label_visibility="collapsed"
    )

# ---------------------------------------------------------------------------
# Tab 0: home (tutorial/overview landing page)
# ---------------------------------------------------------------------------

with tab_home:
    st.header("Welcome to Team Pittsburgh Ball Hockey")
    st.caption(
        "This app turns handwritten game sheets into stored stats, standings, and rosters — "
        "here's what each tab does."
    )

    if all_divisions:
        st.info(
            "👉 **Start here:** pick your **Working Division** from the selector at the top of "
            "the page (next to the app title). It controls which season's games, standings, "
            "stats, and rosters you see everywhere else in the app."
        )
    else:
        st.warning(
            "👉 **Start here:** no divisions exist yet. Create one under **Teams → Divisions** "
            "before doing anything else — everything else in the app (games, standings, stats, "
            "rosters) is scoped to a division."
        )

    st.subheader("What this app does, in short")
    st.markdown(
        "1. You upload a scanned or photographed game sheet.\n"
        "2. Claude (Anthropic's AI) reads the handwriting and extracts the teams, score, goals, "
        "assists, penalties, and shootout rounds.\n"
        "3. You review and correct that extraction before anything is saved — nothing is written "
        "to the database without your confirmation.\n"
        "4. From there, standings, player stats, and rosters are all computed automatically from "
        "the games you've entered — no separate data entry.\n\n"
        "Everything is scoped to a **Working Division** (one season's instance of an age group, "
        "e.g. \"2026 Summer Penguin\") — pick it from the selector near the top of the page, and "
        "it applies across every tab below."
    )

    st.subheader("Page guide")
    st.caption("Click a card to jump straight to that tab, then pick a section inside it.")

    # Card order matches _tab_labels' order below exactly — index 0 is Home
    # (this tab), so these start at 1. Whole-card navigation isn't something
    # st.tabs() supports natively (Streamlit tabs are visual-only, no
    # server-side "switch to tab N" API), so a card click is wired up via a
    # tiny injected script that clicks the real tab button at that index —
    # see the components.html call below. Fragile in the sense that it
    # depends on Streamlit's tab buttons keeping the `data-testid="stTab"`
    # attribute, but that's been stable across recent versions. Each card
    # here maps to one *primary* tab (which itself holds a sub-menu of
    # several pages) rather than one page 1:1, since sub-pages live inside
    # a primary tab's body and aren't separately clickable this way.
    _card_colors = THEME_COLORS[theme_mode]
    st.markdown(
        f"""
        <style>
        .home-nav-card {{
            border: 1px solid {_card_colors['secondary_bg']};
            background-color: {_card_colors['panel_bg']};
            border-radius: 10px;
            padding: 0.9rem 1.1rem;
            margin-bottom: 0.6rem;
            cursor: pointer;
            transition: border-color 0.15s ease;
        }}
        .home-nav-card:hover {{ border-color: {_card_colors['primary']}; }}
        .home-nav-card .home-nav-title {{ font-weight: 700; margin-bottom: 0.2rem; color: {_card_colors['text']}; }}
        .home-nav-card .home-nav-desc {{ font-size: 0.85rem; color: {_card_colors['text']}; opacity: 0.85; }}
        .home-nav-card .home-nav-lock {{ font-size: 0.8rem; margin-top: 0.4rem; color: {_card_colors['text']}; opacity: 0.8; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    page_guides = [
        ("🏒 Games", list(GAMES_SUBPAGES.values()),
         "Schedule & Results (with a button to import scoresheets), Import Scoresheets, and "
         "Manage Games — everything about the Working Division's games in one tab."),
        ("📊 Stats & Standings", STATS_STANDINGS_PAGE_KEYS,
         "Live standings at the top, every player's stats below — filterable by team — all for "
         "the Working Division."),
        ("📋 Teams", list(TEAMS_SUBPAGES.values()),
         "Teams, Team Rosters, Players, Coaches, Divisions (including importing a division's "
         "schedule), and the Draft — everything about how teams and people are organized."),
    ]
    for tab_index, (title, page_keys, description) in enumerate(page_guides, start=1):
        locked = not any(k in visible_pages for k in page_keys) and not user["is_admin"]
        lock_html = (
            '<div class="home-nav-lock">🔒 You don\'t currently have access to this page.</div>'
            if locked else ""
        )
        st.markdown(
            f"""
            <div class="home-nav-card" data-tab-index="{tab_index}">
                <div class="home-nav-title">{title}</div>
                <div class="home-nav-desc">{description}</div>
                {lock_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    if user["is_admin"]:
        st.markdown(
            f"""
            <div class="home-nav-card" data-tab-index="{len(page_guides) + 1}">
                <div class="home-nav-title">🔑 User Management <span style="font-weight:400;">(admins only)</span></div>
                <div class="home-nav-desc">Create accounts and control who can see which pages above, using
                    named roles (e.g. "Coach") instead of picking pages one by one for every person.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Wires up card clicks to the real tab buttons. Runs inside its own
    # (same-origin) iframe, which is why it reaches into window.parent to
    # touch the main app's DOM — st.markdown's raw HTML can't run <script>
    # tags itself. The MutationObserver re-wires after every rerun, since
    # Streamlit replaces these elements' DOM nodes each time.
    components.html(
        """
        <script>
        (function() {
            function wire() {
                var doc = window.parent.document;
                doc.querySelectorAll('.home-nav-card').forEach(function(card) {
                    if (card.getAttribute('data-wired')) return;
                    card.setAttribute('data-wired', '1');
                    card.addEventListener('click', function() {
                        var idx = parseInt(card.getAttribute('data-tab-index'), 10);
                        var tabs = doc.querySelectorAll('[data-testid="stTab"]');
                        if (tabs[idx]) { tabs[idx].click(); }
                    });
                });
            }
            wire();
            new MutationObserver(wire).observe(window.parent.document.body, {childList: true, subtree: true});
        })();
        </script>
        """,
        height=0,
    )

    st.divider()
    st.caption(f"Team Pittsburgh Ball Hockey Game Sheet Scanner — v{APP_VERSION}")

# ---------------------------------------------------------------------------
# Tab: games (schedule & results, importing scoresheets, managing games)
# ---------------------------------------------------------------------------

with tab_games:
    games_subpage = render_subnav("games_subpage", GAMES_SUBPAGES)
    st.divider()

    if games_subpage == "📄 Import Scoresheets":
        if "process" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        elif is_read_only:
            st.info(
                "Your account is read-only, and this page is entirely about creating new data — "
                "there's nothing here for a read-only account to view."
            )
        else:
            st.header("Import Scoresheets")
            if working_division_id is None:
                st.warning("No division selected. Add one in the Teams tab → Divisions first.")
            # Keyed with a version counter so Clear/Cancel below can force the
            # uploader widget itself to reset (bumping the key makes Streamlit treat
            # it as a brand-new widget) — otherwise the browser keeps showing the
            # previously-picked files even after the queue/session state is cleared.
            st.session_state.setdefault("sheet_uploader_version", 0)
            uploaded = st.file_uploader(
                "Upload game sheet scan(s) (PDF or image)",
                type=["pdf", "png", "jpg", "jpeg", "webp", "gif"],
                accept_multiple_files=True,
                key=f"sheet_uploader_{st.session_state.sheet_uploader_version}",
            )

            if uploaded or st.session_state.get("queue"):
                if uploaded:
                    upload_key = tuple(sorted((f.name, f.size) for f in uploaded))
                if uploaded and st.session_state.get("upload_key") != upload_key:
                    # New batch — clear any per-item state left over from the last one.
                    for key in list(st.session_state.keys()):
                        if key.startswith(("data_", "proc_", "replace_target_")):
                            del st.session_state[key]

                    queue = []
                    for f in uploaded:
                        data = f.getvalue()
                        mime = core.guess_mime(f.name)
                        pages = core.split_pdf_bytes(data) if mime == "application/pdf" else [data]
                        stem = Path(f.name).stem

                        if len(pages) == 1:
                            queue.append({"label": f.name, "bytes": pages[0], "mime": mime, "status": "pending"})
                            continue

                        for i, page_bytes in enumerate(pages, start=1):
                            queue.append({
                                "label": f"{stem}_p{i}.pdf", "bytes": page_bytes,
                                "mime": "application/pdf", "status": "pending",
                            })

                    st.session_state.upload_key = upload_key
                    st.session_state.queue = queue
                    st.session_state.queue_index = 0

                queue = st.session_state.queue

                duplicates = [
                    (idx, item, core.find_game_by_source_file(conn, item["label"]))
                    for idx, item in enumerate(queue) if item["status"] == "pending"
                ]
                duplicates = [
                    (idx, item, g) for idx, item, g in duplicates
                    if g is not None and f"replace_target_{idx}" not in st.session_state
                ]

                if duplicates and len(duplicates) == len(queue):
                    # Every uploaded file already exists in the database — nothing to
                    # review, so skip the per-item panel and just offer to clear the
                    # batch instead of making the user click through each one.
                    with st.container(border=True, key="dup_panel"):
                        st.subheader("⚠️ All Sheets Are Duplicates")
                        st.caption(
                            f"All {len(queue)} uploaded file(s) already exist in the database — "
                            "nothing new to process."
                        )
                        if st.button("Clear", key="dup_clear_all", type="primary"):
                            for key in list(st.session_state.keys()):
                                if key.startswith(("data_", "proc_", "replace_target_")):
                                    del st.session_state[key]
                            for key in ("queue", "upload_key", "queue_index"):
                                st.session_state.pop(key, None)
                            st.session_state.sheet_uploader_version += 1
                            st.rerun()
                elif duplicates:
                    # An embedded "window": a bordered, tinted panel that gates the
                    # rest of the queue until every duplicate has a decision, instead
                    # of scattering warnings across a summary list and each item again
                    # later. key="dup_panel" is targeted by the .st-key-dup_panel CSS
                    # rule above to tint it lighter than the plain page background.
                    with st.container(border=True, key="dup_panel"):
                        st.subheader("⚠️ Duplicates Uploaded")
                        st.caption("These files match a game already in the database.")
                        for idx, item, g in duplicates:
                            tcol, rcol1, rcol2, rcol3 = st.columns([1, 4, 1, 1])
                            with tcol:
                                st.image(render_preview_png(item["bytes"], item["mime"]), width=64)
                            with rcol1:
                                st.write(
                                    f"“{item['label']}” — game_id={g['id']}: "
                                    f"{g['home_team']} {g['home_final_score']}–{g['away_final_score']} "
                                    f"{g['away_team']} ({g['game_date']})"
                                )
                            with rcol2:
                                if st.button("Replace", key=f"dup_replace_{idx}", type="primary"):
                                    st.session_state[f"replace_target_{idx}"] = g["id"]
                                    st.rerun()
                            with rcol3:
                                if st.button("Skip", key=f"dup_skip_{idx}"):
                                    queue[idx]["status"] = "skipped"
                                    st.rerun()

                            with st.expander(f"🔍 View larger — {item['label']}"):
                                st.image(render_preview_png(item["bytes"], item["mime"]), width="stretch")
                                vcol1, vcol2 = st.columns(2)
                                with vcol1:
                                    if st.button("Replace", key=f"dup_replace_big_{idx}", type="primary"):
                                        st.session_state[f"replace_target_{idx}"] = g["id"]
                                        st.rerun()
                                with vcol2:
                                    if st.button("Skip", key=f"dup_skip_big_{idx}"):
                                        queue[idx]["status"] = "skipped"
                                        st.rerun()

                            st.divider()

                        bcol1, bcol2 = st.columns(2)
                        with bcol1:
                            if st.button("Cancel", key="dup_cancel"):
                                for key in list(st.session_state.keys()):
                                    if key.startswith(("data_", "proc_", "replace_target_")):
                                        del st.session_state[key]
                                for key in ("queue", "upload_key", "queue_index"):
                                    st.session_state.pop(key, None)
                                st.session_state.sheet_uploader_version += 1
                                st.rerun()
                        with bcol2:
                            if st.button("Skip All", key="dup_skip_all", type="primary"):
                                for idx, _, _ in duplicates:
                                    queue[idx]["status"] = "skipped"
                                st.rerun()
                else:
                    idx = st.session_state.queue_index
                    while idx < len(queue) and queue[idx]["status"] != "pending":
                        idx += 1
                    st.session_state.queue_index = idx

                    done = sum(1 for item in queue if item["status"] != "pending")
                    st.progress(done / len(queue) if queue else 0, text=f"{done}/{len(queue)} sheets handled")

                    if idx >= len(queue):
                        st.success("All uploaded sheets have been handled.")
                    else:
                        item = queue[idx]
                        st.subheader(item["label"])
                        replace_key = f"replace_target_{idx}"

                        col1, col2 = st.columns(2)

                        # Fixed-height, independently-scrolling panes (a real Streamlit
                        # layout feature, not a CSS position hack) — the image pane
                        # never moves as the user scrolls through the (usually much
                        # longer) form beside it, since each pane scrolls within its
                        # own box instead of the page scrolling past both.
                        PREVIEW_PANE_HEIGHT = 750

                        with col1:
                            with st.container(height=PREVIEW_PANE_HEIGHT, border=True):
                                preview_png = render_preview_png(item["bytes"], item["mime"])
                                fit_width = display_width_for_height(preview_png, PREVIEW_PANE_HEIGHT - 40)
                                st.image(preview_png, width=fit_width)

                        with col2:
                            with st.container(height=PREVIEW_PANE_HEIGHT, border=True):
                                data_key = f"data_{idx}"
                                if data_key not in st.session_state:
                                    extract_col, presskip_col = st.columns(2)
                                    with extract_col:
                                        if st.button("Extract with Claude", key=f"extract_{idx}", type="primary"):
                                            client = get_client(api_key)
                                            if client is None:
                                                st.error("Set your ANTHROPIC_API_KEY in the sidebar first.")
                                            else:
                                                with st.spinner("Reading handwriting..."):
                                                    try:
                                                        content_block = core.build_content_block(item["bytes"], item["mime"])
                                                        st.session_state[data_key] = core.extract_game_sheet(client, content_block)
                                                        st.rerun()
                                                    except Exception as e:
                                                        st.error(f"Extraction failed: {e}")
                                    with presskip_col:
                                        if st.button("Skip this sheet", key=f"preskip_{idx}"):
                                            queue[idx]["status"] = "skipped"
                                            st.rerun()
                                else:
                                    merged = render_game_form(f"proc_{idx}", st.session_state[data_key], conn, working_division_id)

                                    accept_col, skip_col = st.columns(2)
                                    with accept_col:
                                        if st.button("Accept & Save", key=f"accept_{idx}", type="primary"):
                                            form_error = game_form_error(merged)
                                            if form_error:
                                                st.error(form_error)
                                            else:
                                                try:
                                                    replace_target = st.session_state.get(replace_key)
                                                    if replace_target:
                                                        core.update_game(conn, replace_target, merged, working_division_id)
                                                        game_id = replace_target
                                                        msg = f"Replaced game_id={game_id} with “{item['label']}”."
                                                    else:
                                                        game_id, already_existed = core.insert_game(
                                                            conn, merged, source_file=item["label"],
                                                            working_division_id=working_division_id,
                                                        )
                                                        msg = f"Saved “{item['label']}” as game_id={game_id}."
                                                        if already_existed:
                                                            msg += " (Game already existed — stats were re-inserted.)"
                                                    queue[idx]["status"] = "done"
                                                    st.session_state.setdefault("messages", []).append(msg)
                                                    st.rerun()
                                                except ValueError as e:
                                                    st.error(str(e))
                                    with skip_col:
                                        if st.button("Skip this sheet", key=f"skip_{idx}"):
                                            queue[idx]["status"] = "skipped"
                                            st.rerun()

                for msg in st.session_state.get("messages", []):
                    st.info(msg)

    elif games_subpage == "✏️ Manage Games":
        if "edit" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Manage Games")
            if working_division_id is None:
                st.warning("No division selected. Add one in the Teams tab → Divisions first.")
                rows = []
            else:
                rows = core.list_games(conn, working_division_id)

            if working_division_id is not None:
                with st.expander("➕ Add Game Manually"):
                    st.caption(
                        "For a game whose sheet is missing, lost, or never scanned — enter its "
                        "stats by hand instead of processing a sheet."
                    )
                    manual_merged = render_game_form("add_new", {}, conn, working_division_id)
                    if st.button("Save new game", key="add_new_save", type="primary", disabled=is_read_only):
                        form_error = game_form_error(manual_merged)
                        if form_error:
                            st.error(form_error)
                        else:
                            try:
                                new_game_id, already_existed = core.insert_game(
                                    conn, manual_merged, source_file="(manual entry)",
                                    working_division_id=working_division_id,
                                )
                                for key in list(st.session_state.keys()):
                                    if key.startswith("add_new_"):
                                        del st.session_state[key]
                                st.success(
                                    f"Added game_id={new_game_id} manually."
                                    + (" (Matching game already existed — stats were re-inserted.)"
                                       if already_existed else "")
                                )
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

            if not rows:
                st.write("No games in the database yet.")
            else:
                st.dataframe(zebra_style(pd.DataFrame(rows)), width="stretch", hide_index=True)

                game_id = st.selectbox("Select a game to edit", options=[r["id"] for r in rows])
                if st.button("Load for editing"):
                    # Drop any leftover widget/row state from a previous load of this game.
                    for key in list(st.session_state.keys()):
                        if key.startswith(f"edit_{game_id}_"):
                            del st.session_state[key]
                    data, source_file = core.load_game(conn, game_id)
                    st.session_state.edit_game_id = game_id
                    st.session_state.edit_source_file = source_file
                    st.session_state.edit_data = data

                if st.session_state.get("edit_game_id") == game_id and "edit_data" in st.session_state:
                    merged = render_game_form(f"edit_{game_id}", st.session_state.edit_data, conn, working_division_id)
                    delete_confirm_key = f"confirm_delete_game_{game_id}"
                    save_col, delete_col = st.columns(2)
                    with save_col:
                        if st.button("Save changes", type="primary", disabled=is_read_only):
                            form_error = game_form_error(merged)
                            if form_error:
                                st.error(form_error)
                            else:
                                try:
                                    core.update_game(conn, game_id, merged, working_division_id)
                                    st.success(f"Updated game_id={game_id}.")
                                    st.rerun()
                                except ValueError as e:
                                    st.error(str(e))
                    with delete_col:
                        if st.button("🗑️ Delete this game", key=f"delete_game_{game_id}", disabled=is_read_only):
                            st.session_state[delete_confirm_key] = True
                            st.rerun()

                    if st.session_state.get(delete_confirm_key):
                        st.warning(
                            f"Permanently delete game_id={game_id} "
                            f"({merged['home_team'] or 'Home'} vs {merged['away_team'] or 'Away'})? "
                            "This can't be undone — its goals, penalties, and shootout attempts go with it."
                        )
                        confirm_col, cancel_col = st.columns(2)
                        with confirm_col:
                            if st.button(
                                "Yes, delete", key=f"confirm_yes_delete_game_{game_id}", type="primary",
                                disabled=is_read_only,
                            ):
                                core.delete_game(conn, game_id)
                                st.session_state.pop(delete_confirm_key, None)
                                for key in ("edit_game_id", "edit_source_file", "edit_data"):
                                    st.session_state.pop(key, None)
                                st.success(f"Deleted game_id={game_id}.")
                                st.rerun()
                        with cancel_col:
                            if st.button("Cancel", key=f"confirm_no_delete_game_{game_id}"):
                                st.session_state.pop(delete_confirm_key, None)
                                st.rerun()

    else:
        if "schedule" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            header_col, import_col = st.columns([4, 1])
            with header_col:
                st.header("Schedule & Results")
            with import_col:
                st.write("")
                if st.button("📄 Import Scoresheets", width="stretch"):
                    st.session_state["games_subpage_pending"] = "📄 Import Scoresheets"
                    st.rerun()
            st.caption(
                "The season schedule for the Working Division, compared against games actually "
                "entered — showing the score for every game that's been played. Upload or manage a "
                "division's schedule from **Teams → Divisions**."
            )
            if working_division_id is None:
                st.warning("No division selected. Add one in the Teams tab → Divisions first.")
            else:
                schedule = core.list_schedule(conn, working_division_id)
                if not schedule:
                    st.info("No schedule uploaded yet for this division — add one from Teams → Divisions.")
                else:
                    total = len(schedule)
                    done = sum(1 for r in schedule if r["accounted_for"])
                    st.progress(done / total if total else 0, text=f"{done}/{total} scheduled games played")

                    def _schedule_result_text(r):
                        res = r.get("result")
                        if not res:
                            return "—"
                        return f"{res['home_team']} {res['home_score']}\u2013{res['away_score']} {res['away_team']}"

                    display_df = pd.DataFrame([
                        {
                            "Round": r["round"], "Date": r["game_date"], "Away Team": r["away_team"],
                            "Home Team": r["home_team"], "Start Time": r["start_time"], "Location": r["location"],
                            "Result": _schedule_result_text(r),
                        }
                        for r in schedule
                    ])
                    st.dataframe(zebra_style(display_df), width="stretch", hide_index=True)

                    unaccounted = [r for r in schedule if not r["accounted_for"]]
                    if unaccounted:
                        # A stored game between the same two teams, filed under a
                        # date that doesn't match any scheduled date for that
                        # matchup, is almost always this same game with a
                        # mis-transcribed date (most often the year) rather than an
                        # actual gap — flag it so the real fix (correct that game's
                        # date in Manage Games) can be found instead of assumed.
                        flagged = [r for r in unaccounted if r["possible_matches"]]
                        if flagged:
                            st.caption(
                                "Some of these may already be entered under the wrong date (a mis-typed "
                                "year is the usual culprit) rather than genuinely missing:"
                            )
                            for r in flagged:
                                for m in r["possible_matches"]:
                                    st.warning(
                                        f"**{r['away_team']} @ {r['home_team']}**, scheduled for "
                                        f"{r['game_date']} — game #{m['id']} between these two teams is on "
                                        f"file dated **{m['game_date']}** instead. Check/fix its date in "
                                        f"**Manage Games**; it'll show as accounted for here automatically "
                                        f"once corrected."
                                    )
                    else:
                        st.success("Every scheduled game has been entered.")


# ---------------------------------------------------------------------------
# Tab: stats & standings (standings on top, player stats below)
# ---------------------------------------------------------------------------

with tab_stats_standings:
    can_view_standings = "standings" in visible_pages
    can_view_stats = "stats" in visible_pages
    if not can_view_standings and not can_view_stats:
        st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
    else:
        if can_view_standings:
            st.header("Standings")
            st.caption(
                "3 pts for a regulation win, 2 for an OT/shootout win, 1 for an OT/shootout loss, 0 for a "
                "regulation loss. Ties broken by: head-to-head record, goal differential, regulation wins, "
                "OT wins, goals against, goals for."
            )
            table = core.standings_table(conn, working_division_id) if working_division_id is not None else []
            if not table:
                st.write("No completed games yet.")
            else:
                st.dataframe(zebra_style(pd.DataFrame(table)), width="stretch", hide_index=True)

        if can_view_standings and can_view_stats:
            st.divider()

        if can_view_stats:
            st.header("Player Stats")
            stats = core.get_player_stats(conn, working_division_id) if working_division_id is not None else []
            if not stats:
                st.write("No stats collected for this division yet.")
            else:
                stats = sorted(stats, key=lambda s: (-s["points"], -s["goals"]))
                all_divisions_for_stats = core.list_divisions(conn)
                division_name_by_id_stats = {
                    d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions_for_stats
                }

                team_display_names = sorted({core.display_text(s["team"]) for s in stats})
                stats_team_filter = st.selectbox(
                    "Filter by team", options=["All Teams"] + team_display_names, key="stats_team_filter",
                )
                if stats_team_filter != "All Teams":
                    stats = [s for s in stats if core.display_text(s["team"]) == stats_team_filter]

                col_widths = [1.3, 0.5, 1.5, 0.5, 0.5, 0.5, 0.5, 0.8, 0.9, 1.2]
                headers = st.columns(col_widths)
                for col, label in zip(headers, ["Team", "#", "Name", "G", "A", "PTS", "PIM", "SO Made", "SO Missed", ""]):
                    col.markdown(f"**{label}**")

                # A player's full panel (used to sit inline in a per-row
                # st.popover here) is now a button that just remembers which
                # player was picked — st.popover's body runs on every rerun
                # regardless of whether it's open, same as a tab's, so putting
                # the panel (with its evaluation chart and per-division stats
                # queries) inside one for every row meant rendering ~18 players'
                # full panels, unopened, on every single interaction anywhere
                # in the app. Rendering the (at most one) selected player's
                # panel once, below the table, does the same job for a fraction
                # of the queries.
                for stats_idx, s in enumerate(stats):
                    row_key = f"{s['team_id']}_{s['number']}"
                    with highlighted_row(s.get("player_id"), row_key=f"stats_{row_key}", index=stats_idx):
                        c = st.columns(col_widths)
                        c[0].write(core.display_text(s["team"]))
                        c[1].write(s["number"])
                        c[2].write(core.display_text(s["name"]))
                        c[3].write(s["goals"])
                        c[4].write(s["assists"])
                        c[5].write(s["points"])
                        c[6].write(s["penalties"])
                        c[7].write(s["shootout_goals"])
                        c[8].write(s["shootout_misses"])
                        with c[9]:
                            if s.get("player_id") is None:
                                render_create_player_popover(
                                    conn, f"stats_{row_key}", s["team_id"], s["team"], s["number"], working_division_id
                                )
                            elif st.button("👤 Player Panel", key=f"stats_panel_btn_{row_key}"):
                                st.session_state["stats_selected_player_id"] = s["player_id"]
                                st.rerun()

                selected_stats_player_id = st.session_state.get("stats_selected_player_id")
                if selected_stats_player_id is not None:
                    st.divider()
                    close_col, _spacer = st.columns([1, 5])
                    with close_col:
                        if st.button("✕ Close panel", key="stats_panel_close"):
                            del st.session_state["stats_selected_player_id"]
                            st.rerun()
                    with st.container(border=True):
                        render_player_panel(
                            conn, selected_stats_player_id, division_name_by_id_stats, all_divisions_for_stats,
                            key_prefix="stats_panel_selected",
                        )


# ---------------------------------------------------------------------------
# Tab: teams (overview, rosters, players, coaches, divisions, draft)
# ---------------------------------------------------------------------------

with tab_teams_group:
    teams_subpage = render_subnav("teams_subpage", TEAMS_SUBPAGES)
    st.divider()

    if teams_subpage == "👥 Team Rosters":
        if "rosters" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Team Rosters")
            st.caption(
                "Add player names/numbers per team so goals, assists, and penalties can be attributed by name. "
                "Teams belong to the Working Division picked above — the same team name in a different division "
                "is a separate team with its own roster."
            )

            if working_division_id is None:
                st.warning("No division selected. Add one in the Teams tab → Divisions first.")
            else:
                with st.expander("Add a new team"):
                    new_team_name = st.text_input("Team name", key="new_team_name", disabled=is_read_only)
                    if st.button("Add team", disabled=is_read_only):
                        if new_team_name.strip():
                            core.add_team(conn, working_division_id, new_team_name.strip())
                            st.rerun()
                        else:
                            st.error("Team name is required.")

                teams = core.list_teams(conn, working_division_id)
                if not teams:
                    st.write("No teams yet in this division — process a game sheet, or add one above.")
                else:
                    team_options = {t["id"]: t["name"] for t in teams}
                    roster_coaches_by_team = core.list_team_coaches_for_division(conn, working_division_id)
                    team_select_labels = {t["id"]: team_label_with_coaches(t, roster_coaches_by_team) for t in teams}
                    roster_team_id = st.selectbox(
                        "Select a team", options=list(team_options), format_func=lambda i: team_select_labels[i]
                    )
                    st.caption("Click a column header to sort. Edit cells directly, or use the blank bottom row to add a player.")

                    roster = core.list_roster(conn, roster_team_id)
                    roster_df = (
                        pd.DataFrame(roster, columns=["number", "name"]) if roster else pd.DataFrame(columns=["number", "name"])
                    )
                    roster_df = roster_df.rename(columns={"number": "Number", "name": "Name"})

                    edited_df = st.data_editor(
                        roster_df,
                        width="stretch",
                        hide_index=True,
                        num_rows="dynamic",
                        disabled=is_read_only,
                        column_config={
                            "Number": st.column_config.TextColumn("Number", required=True),
                            "Name": st.column_config.TextColumn("Name", required=True),
                        },
                        key=f"roster_editor_{roster_team_id}",
                    )

                    if st.button("Save roster", type="primary", disabled=is_read_only):
                        new_entries = [
                            {"number": str(row["Number"]).strip(), "name": str(row["Name"]).strip()}
                            for _, row in edited_df.iterrows()
                            if str(row["Number"]).strip() or str(row["Name"]).strip()
                        ]
                        core.replace_roster(conn, roster_team_id, new_entries)
                        st.success("Roster saved.")
                        st.rerun()

                    st.divider()
                    with st.expander(f"{team_options[roster_team_id]} — Player Details", expanded=True):
                        st.caption(
                            "Position and Season Grade for the Working Division above — edits here update that "
                            "player's position on this team, and their most recent evaluation for this division "
                            "(see the player's Evaluations popover for full grade history)."
                        )
                        # Reuses `roster` (already fetched above for the data
                        # editor) instead of a second list_roster() round trip.
                        roster_rows = roster
                        if not roster_rows:
                            st.caption("No players on this roster yet.")
                        else:
                            # Batch-fetched once for the whole grid instead of one
                            # get_position()/get_season_grade() round trip per row —
                            # over a remote connection that N+1 pattern was slow
                            # enough to make every interaction anywhere in the app
                            # feel sluggish, since every tab's body runs every rerun.
                            positions_by_player = core.get_positions_for_team(conn, working_division_id, roster_team_id)
                            grades_by_player = core.get_season_grades_for_division(conn, working_division_id)

                            detail_cols = st.columns([1, 3, 1.5, 1.5, 0.8])
                            detail_cols[0].markdown("**Number**")
                            detail_cols[1].markdown("**Name**")
                            detail_cols[2].markdown("**Position**")
                            detail_cols[3].markdown("**Season Grade**")
                            detail_cols[4].markdown("**Move**")
                            for roster_idx, entry in enumerate(roster_rows):
                                with highlighted_row(
                                    entry["player_id"], row_key=f"roster_{roster_team_id}_{entry['id']}",
                                    index=roster_idx,
                                ):
                                    row_cols = st.columns([1, 3, 1.5, 1.5, 0.8])
                                    row_cols[0].write(entry["number"])
                                    row_cols[1].write(entry["name"])
                                    if entry["player_id"] is None:
                                        with row_cols[2]:
                                            render_create_player_popover(
                                                conn, f"roster_{roster_team_id}_{entry['id']}", roster_team_id,
                                                team_options[roster_team_id], entry["number"], working_division_id,
                                            )
                                        continue
                                    with row_cols[2]:
                                        position_input(
                                            conn, entry["player_id"], working_division_id, roster_team_id,
                                            key=f"roster_position_{roster_team_id}_{entry['id']}",
                                            prefetched=positions_by_player.get(entry["player_id"]),
                                            label_visibility="collapsed", disabled=is_read_only,
                                        )
                                    with row_cols[3]:
                                        season_grade_input(
                                            conn, entry["player_id"], working_division_id, roster_team_id,
                                            key=f"roster_season_grade_{roster_team_id}_{entry['id']}",
                                            prefetched=grades_by_player.get(entry["player_id"]),
                                            label_visibility="collapsed", disabled=is_read_only,
                                        )
                                    with row_cols[4]:
                                        with st.popover("↔️"):
                                            st.caption(f"Move {entry['name']} to a different team")
                                            other_teams = {
                                                t["id"]: t["name"] for t in teams if t["id"] != roster_team_id
                                            }
                                            if not other_teams:
                                                st.caption("No other teams in this division yet.")
                                            else:
                                                move_team_id = st.selectbox(
                                                    "Team", options=list(other_teams),
                                                    format_func=lambda i: other_teams[i],
                                                    key=f"move_team_{roster_team_id}_{entry['id']}",
                                                    label_visibility="collapsed",
                                                )
                                                move_note = None
                                                if user["is_admin"]:
                                                    move_note = st.text_area(
                                                        "Reason (admin only — coaches won't see this)",
                                                        key=f"move_note_{roster_team_id}_{entry['id']}",
                                                    )
                                                if st.button(
                                                    "Move", key=f"move_btn_{roster_team_id}_{entry['id']}",
                                                    type="primary", disabled=is_read_only,
                                                ):
                                                    try:
                                                        core.move_player_to_team(
                                                            conn, entry["player_id"], working_division_id,
                                                            move_team_id, note=move_note,
                                                        )
                                                        st.rerun()
                                                    except ValueError as e:
                                                        st.error(str(e))
                                            if user["is_admin"]:
                                                move_history = core.list_player_move_notes(
                                                    conn, entry["player_id"], working_division_id
                                                )
                                                if move_history:
                                                    st.divider()
                                                    st.caption("Move history (admin only)")
                                                    for m in move_history:
                                                        st.write(
                                                            f"- {m['from_team'] or '?'} → {m['to_team'] or '?'}: "
                                                            f"{m['note'] or '(no reason given)'}"
                                                        )

                    st.divider()
                    st.subheader(f"{team_options[roster_team_id]} — Coaches")
                    render_team_coach_manager(conn, roster_team_id, team_options[roster_team_id], key_prefix="rosters_tab")

                    st.divider()
                    st.subheader(f"{team_options[roster_team_id]} — Player Stats")
                    team_name = team_options[roster_team_id]
                    team_stats = [
                        s for s in core.get_player_stats(conn, working_division_id) if s["team"] == core.normalize_text(team_name)
                    ]
                    if not team_stats:
                        st.write("No stats recorded yet for this team.")
                    else:
                        stats_df = pd.DataFrame([
                            {
                                "#": s["number"], "Name": core.display_text(s["name"]),
                                "G": s["goals"], "A": s["assists"], "PTS": s["points"], "PIM": s["penalties"],
                                "SO Made": s["shootout_goals"], "SO Missed": s["shootout_misses"],
                            }
                            for s in team_stats
                        ])
                        # Explicit height sized to every row (Streamlit's default
                        # caps at a fixed box and scrolls beyond it) so the whole
                        # team's stats are visible at once, never clipped.
                        st.dataframe(
                            zebra_style(stats_df), width="stretch", hide_index=True,
                            height=(len(team_stats) + 1) * 35 + 3,
                        )

    elif teams_subpage == "🧑 Players":
        if "players" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Players")
            st.caption(
                "Player profiles are global — the same player keeps one profile across every division/season "
                "they play in. Link a profile to a roster row (jersey number) in Stats & Standings or Team "
                "Rosters to attribute stats to a name."
            )

            all_divisions_for_players = core.list_divisions(conn)
            division_name_by_id = {
                d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions_for_players
            }

            with st.expander("➕ Add a new player"):
                pncol1, pncol2, pncol3 = st.columns(3)
                pn_first = pncol1.text_input("First name", key="new_player_first", disabled=is_read_only)
                pn_last = pncol2.text_input("Last name", key="new_player_last", disabled=is_read_only)
                pn_nickname = pncol3.text_input("Nickname", key="new_player_nickname", disabled=is_read_only)
                pn_dob = st.text_input(
                    "Birth date", key="new_player_dob", placeholder="YYYY-MM-DD", disabled=is_read_only
                )
                pn_division = st.selectbox(
                    "Current division", options=[None] + list(division_name_by_id),
                    format_func=lambda i: "(none)" if i is None else division_name_by_id[i],
                    key="new_player_division", disabled=is_read_only,
                )
                pcol1, pcol2 = st.columns(2)
                pn_cfn = pcol1.text_input("Contact first name", key="new_player_cfn", disabled=is_read_only)
                pn_cln = pcol2.text_input("Contact last name", key="new_player_cln", disabled=is_read_only)
                pn_cph = pn_cem = ""
                if hide_contact_details:
                    st.caption("🔒 Phone/email are hidden for your role.")
                else:
                    pcol3, pcol4 = st.columns(2)
                    pn_cph = pcol3.text_input("Contact phone", key="new_player_cph", disabled=is_read_only)
                    pn_cem = pcol4.text_input("Contact email", key="new_player_cem", disabled=is_read_only)
                if st.button("Add player", key="add_player_btn", type="primary", disabled=is_read_only):
                    if pn_first.strip():
                        core.add_player(
                            conn, pn_first.strip(), pn_last.strip() or None, pn_nickname.strip() or None,
                            birth_date=pn_dob.strip() or None, current_division_id=pn_division,
                            contact_first_name=pn_cfn.strip() or None, contact_last_name=pn_cln.strip() or None,
                            contact_phone=pn_cph.strip() or None, contact_email=pn_cem.strip() or None,
                        )
                        st.rerun()
                    else:
                        st.error("First name is required.")

            # "Sub"/"SUB" placeholder players (one per team, created by linking
            # a roster's generic "Sub" jersey row rather than identifying a
            # real person) aren't a real, pickable individual — exclude them
            # from this list. They still exist and keep their stats/roster
            # link; they just don't clutter or get selected from here.
            players_list = [p for p in core.list_players(conn) if p["name"].strip().lower() != "sub"]
            if not players_list:
                st.write("No players yet — add one above.")
            else:
                filter_col1, filter_col2, filter_col3 = st.columns([2, 1.5, 1.5])
                with filter_col1:
                    name_filter = st.text_input("Search by name", key="players_tab_name_filter")
                with filter_col2:
                    division_filter = st.selectbox(
                        "Current division", options=[None] + list(division_name_by_id),
                        format_func=lambda i: "All divisions" if i is None else division_name_by_id[i],
                        key="players_tab_division_filter",
                    )
                with filter_col3:
                    st.caption("Has an evaluation for")
                    eval_filter = []
                    with st.popover("Filter by division", width="stretch"):
                        st.caption(
                            "Matches only players rated in every division checked, not just any one of them."
                        )
                        for division_id in division_name_by_id:
                            checked = st.checkbox(
                                division_name_by_id[division_id], key=f"players_tab_eval_cb_{division_id}"
                            )
                            if checked:
                                eval_filter.append(division_id)

                filtered_players = players_list
                if name_filter.strip():
                    needle = name_filter.strip().lower()
                    filtered_players = [
                        p for p in filtered_players
                        if needle in p["name"].lower() or needle in (p["nickname"] or "").lower()
                    ]
                if division_filter is not None:
                    filtered_players = [p for p in filtered_players if p["current_division_id"] == division_filter]
                if eval_filter:
                    # Intersection, not union — only players evaluated in every
                    # selected division, not just any one of them.
                    evaluated_ids: set[int] | None = None
                    for division_id in eval_filter:
                        ids = core.list_evaluated_player_ids(conn, division_id)
                        evaluated_ids = ids if evaluated_ids is None else evaluated_ids & ids
                    filtered_players = [p for p in filtered_players if p["id"] in evaluated_ids]

                st.caption(f"Showing {len(filtered_players)} of {len(players_list)} players.")

                if not filtered_players:
                    st.write("No players match these filters.")
                else:
                    histories = core.player_division_histories(conn, [p["id"] for p in filtered_players])
                    player_options = {
                        p["id"]: player_label(conn, p, histories.get(p["id"], [])) for p in filtered_players
                    }
                    player_ids = list(player_options)

                    # Previous/Next live inside render_player_panel, alongside Save/
                    # Delete/Evaluations, but that runs *after* this selectbox — so
                    # they can't write "players_tab_select" directly (Streamlit forbids
                    # touching a widget's session_state after it's been instantiated
                    # this run). They stash the target id here instead; applying it to
                    # the real widget key must happen before that widget is created.
                    pending_key = "players_tab_pending_select"
                    if pending_key in st.session_state:
                        st.session_state["players_tab_select"] = st.session_state.pop(pending_key)

                    selected_player_id = st.selectbox(
                        "Select a player", options=player_ids, format_func=lambda i: player_options[i],
                        key="players_tab_select",
                    )
                    render_player_panel(
                        conn, selected_player_id, division_name_by_id, all_divisions_for_players,
                        key_prefix="players_tab", nav_ids=player_ids, nav_pending_key=pending_key,
                    )

            deleted_players = core.list_players(conn, include_deleted=True)
            deleted_players = [p for p in deleted_players if p["deleted_at"]]
            if deleted_players:
                with st.expander(f"🗑️ Deleted Players ({len(deleted_players)})"):
                    for p in deleted_players:
                        dpcol1, dpcol2 = st.columns([4, 1])
                        with dpcol1:
                            st.write(p["name"])
                        with dpcol2:
                            if st.button("Restore", key=f"restore_player_{p['id']}", disabled=is_read_only):
                                core.restore_player(conn, p["id"])
                                st.rerun()

    elif teams_subpage == "🧑‍🏫 Coaches":
        if "coaches" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Coaches")
            st.caption(
                "Coach profiles are global — the same coach keeps one profile across every division/season "
                "they coach in. A coach can coach one team per division (still multiple teams across a "
                "season's different divisions, e.g. U10 Summer and U13 Summer), but not two teams in the "
                "same division."
            )

            all_divisions_for_coaches = core.list_divisions(conn)
            coach_division_name_by_id = {
                d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions_for_coaches
            }

            with st.expander("➕ Add a new coach"):
                cncol1, cncol2, cncol3 = st.columns(3)
                cn_first = cncol1.text_input("First name", key="new_coach_first_tab", disabled=is_read_only)
                cn_last = cncol2.text_input("Last name", key="new_coach_last_tab", disabled=is_read_only)
                cn_nickname = cncol3.text_input("Nickname", key="new_coach_nickname_tab", disabled=is_read_only)
                cn_phone = cn_email = ""
                if hide_contact_details:
                    st.caption("🔒 Phone/email are hidden for your role.")
                else:
                    cncol4, cncol5 = st.columns(2)
                    cn_phone = cncol4.text_input("Phone", key="new_coach_phone_tab", disabled=is_read_only)
                    cn_email = cncol5.text_input("Email", key="new_coach_email_tab", disabled=is_read_only)
                if st.button("Add coach", key="add_coach_btn_tab", type="primary", disabled=is_read_only):
                    if cn_first.strip():
                        core.add_coach(
                            conn, cn_first.strip(), cn_last.strip() or None, cn_nickname.strip() or None,
                            phone=cn_phone.strip() or None, email=cn_email.strip() or None,
                        )
                        st.rerun()
                    else:
                        st.error("First name is required.")

            coaches_list = core.list_coaches(conn)
            if not coaches_list:
                st.write("No coaches yet — add one above.")
            else:
                coach_filter_col1, coach_filter_col2 = st.columns([2, 1.5])
                with coach_filter_col1:
                    coach_name_filter = st.text_input("Search by name", key="coaches_tab_name_filter")
                with coach_filter_col2:
                    children_filter = st.checkbox("Has a registered child", key="coaches_tab_children_filter")

                filtered_coaches = coaches_list
                if coach_name_filter.strip():
                    needle = coach_name_filter.strip().lower()
                    filtered_coaches = [
                        c for c in filtered_coaches
                        if needle in c["name"].lower() or needle in (c["nickname"] or "").lower()
                    ]
                if children_filter:
                    with_children = core.coach_ids_with_children(conn)
                    filtered_coaches = [c for c in filtered_coaches if c["id"] in with_children]

                st.caption(f"Showing {len(filtered_coaches)} of {len(coaches_list)} coaches.")

                if not filtered_coaches:
                    st.write("No coaches match these filters.")
                else:
                    coach_options = {c["id"]: coach_label(c) for c in filtered_coaches}
                    coach_ids = list(coach_options)

                    coach_pending_key = "coaches_tab_pending_select"
                    if coach_pending_key in st.session_state:
                        st.session_state["coaches_tab_select"] = st.session_state.pop(coach_pending_key)

                    selected_coach_id = st.selectbox(
                        "Select a coach", options=coach_ids, format_func=lambda i: coach_options[i],
                        key="coaches_tab_select",
                    )
                    render_coach_panel(
                        conn, selected_coach_id, coach_division_name_by_id, all_divisions_for_coaches,
                        key_prefix="coaches_tab", nav_ids=coach_ids, nav_pending_key=coach_pending_key,
                    )

            deleted_coaches = core.list_coaches(conn, include_deleted=True)
            deleted_coaches = [c for c in deleted_coaches if c["deleted_at"]]
            if deleted_coaches:
                with st.expander(f"🗑️ Deleted Coaches ({len(deleted_coaches)})"):
                    for c in deleted_coaches:
                        dccol1, dccol2 = st.columns([4, 1])
                        with dccol1:
                            st.write(coach_label(c))
                        with dccol2:
                            if st.button("Restore", key=f"restore_coach_{c['id']}", disabled=is_read_only):
                                core.restore_coach(conn, c["id"])
                                st.rerun()

    elif teams_subpage == "🗓️ Divisions":
        if "divisions" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            header_col, recycle_col = st.columns([4, 1])
            with header_col:
                st.header("Divisions")
            with recycle_col:
                with st.popover("♻️ Recycle Bin"):
                    deleted_divisions = core.list_deleted_divisions(conn)
                    st.caption("Divisions — permanently deleted 30 days after removal, unless restored first.")
                    if not deleted_divisions:
                        st.write("No deleted divisions.")
                    else:
                        for d in deleted_divisions:
                            rbcol1, rbcol2 = st.columns([4, 1])
                            with rbcol1:
                                st.write(
                                    f"{d['year']} {d['season']} — {division_label(d['age_group'])} "
                                    f"· {d['days_left']} days left"
                                )
                            with rbcol2:
                                if st.button("Restore", key=f"restore_div_{d['id']}", disabled=is_read_only):
                                    core.restore_division(conn, d["id"])
                                    st.rerun()

                    st.divider()
                    deleted_teams = core.list_deleted_teams(conn)
                    st.caption("Teams — no auto-purge, kept until restored.")
                    if not deleted_teams:
                        st.write("No deleted teams.")
                    else:
                        for t in deleted_teams:
                            rtcol1, rtcol2 = st.columns([4, 1])
                            with rtcol1:
                                st.write(
                                    f"{t['name']} ({t['year']} {t['season']} — {division_label(t['age_group'])})"
                                )
                            with rtcol2:
                                if st.button("Restore", key=f"restore_team_{t['id']}", disabled=is_read_only):
                                    core.restore_team(conn, t["id"])
                                    st.rerun()

            st.caption(
                "A division is one season's instance of an age group, e.g. 2026 Summer Penguin (U10). "
                "The same age group recurs as a new division every season. The Working Division picker "
                "at the top right applies across the whole session and defaults new sheets' Division field. "
                "Expand a division below to manage its teams — name, jersey color, and assigned coach(es)."
            )

            divisions = core.list_divisions(conn)
            if not divisions:
                st.write("No divisions yet.")
            else:
                for d in divisions:
                    division_title = f"{d['year']} {d['season']} — {division_label(d['age_group'])}"
                    with st.expander(division_title):
                        # Fetched once per division and reused by the Teams section's
                        # per-team grade breakdown, the Players section's Grade column
                        # and table below, and the coach-carryover panel's child-name
                        # matching/picker, instead of separate round trips for each.
                        # get_latest_grades falls back to a player's most recent
                        # evaluation from any division when this one doesn't have one
                        # yet, so a fresh or returning player still shows a useful
                        # reference grade instead of a blank cell.
                        division_players = core.list_players_in_division(conn, d["id"])
                        division_grades_by_player = core.get_latest_grades(
                            conn, d["id"], [p["id"] for p in division_players]
                        )

                        st.subheader("Teams")
                        teams = core.list_teams(conn, d["id"])
                        if not teams:
                            st.caption("No teams yet in this division.")
                        else:
                            div_team_col_widths = [1.8, 1.1, 2.2, 2.2, 0.7]
                            head1, head2, head3, head4, _head5 = st.columns(div_team_col_widths)
                            head1.markdown("**Team**")
                            head2.markdown("**Color**")
                            head3.markdown("**Coach(es)**")
                            head4.markdown("**Players**")
                            for team_idx, t in enumerate(teams):
                                with highlighted_row(None, row_key=f"div_team_row_{t['id']}", index=team_idx):
                                    trow1, trow2, trow3, trow4, trow5 = st.columns(div_team_col_widths)
                                    trow1.write(t["name"])
                                    with trow2:
                                        render_color_swatch(t["color"])
                                    team_coaches = core.list_team_coaches(conn, t["id"])
                                    trow3.write(
                                        ", ".join(coach_label(c) for c in team_coaches) if team_coaches else "—"
                                    )
                                    with trow4:
                                        team_roster = core.list_roster(conn, t["id"])
                                        if not team_roster:
                                            st.write("No players yet")
                                        else:
                                            team_tiered_grades = [
                                                grade.strip().upper() for entry in team_roster
                                                if entry["player_id"] is not None
                                                and (grade := division_grades_by_player.get(entry["player_id"]))
                                                and grade.strip().upper() in GRADE_TIERS
                                            ]
                                            st.write(f"{len(team_roster)} added · {len(team_tiered_grades)} graded")
                                            if team_tiered_grades:
                                                st.caption(", ".join(
                                                    f"{tier}: {team_tiered_grades.count(tier)}"
                                                    for tier in GRADE_TIERS if tier in team_tiered_grades
                                                ))
                                    with trow5:
                                        with st.popover("⚙️"):
                                            st.caption(f"Manage {t['name']}")
                                            mcol1, mcol2 = st.columns(2)
                                            edit_team_name = mcol1.text_input(
                                                "Name", value=t["name"], key=f"div_team_name_{t['id']}",
                                                disabled=is_read_only,
                                            )
                                            default_team_color = (
                                                t["color"] if t["color"] and _HEX_COLOR_RE.match(t["color"])
                                                else "#CCCCCC"
                                            )
                                            edit_team_color = mcol2.color_picker(
                                                "Color", value=default_team_color, key=f"div_team_color_{t['id']}",
                                                disabled=is_read_only,
                                            )
                                            if st.button(
                                                "Save", key=f"div_team_save_{t['id']}", type="primary",
                                                disabled=is_read_only,
                                            ):
                                                try:
                                                    core.update_team(
                                                        conn, t["id"], name=edit_team_name.strip(),
                                                        color=edit_team_color,
                                                    )
                                                    st.rerun()
                                                except ValueError as e:
                                                    st.error(str(e))

                                            st.divider()
                                            st.caption("Coaches")
                                            render_team_coach_manager(
                                                conn, t["id"], t["name"], key_prefix=f"div_{d['id']}"
                                            )

                                            st.divider()
                                            team_confirm_key = f"confirm_delete_team_{t['id']}"
                                            if st.button(
                                                "🗑️ Delete team", key=f"delete_team_{t['id']}", disabled=is_read_only
                                            ):
                                                st.session_state[team_confirm_key] = True
                                                st.rerun()
                                            if st.session_state.get(team_confirm_key):
                                                st.warning(
                                                    f"Delete {t['name']}? Its roster and coach assignments go with "
                                                    "it (recoverable from the Recycle Bin above)."
                                                )
                                                tccol1, tccol2 = st.columns(2)
                                                with tccol1:
                                                    if st.button(
                                                        "Yes, delete", key=f"confirm_yes_team_{t['id']}",
                                                        type="primary", disabled=is_read_only,
                                                    ):
                                                        core.soft_delete_team(conn, t["id"])
                                                        st.session_state.pop(team_confirm_key, None)
                                                        st.rerun()
                                                with tccol2:
                                                    if st.button("Cancel", key=f"confirm_no_team_{t['id']}"):
                                                        st.session_state.pop(team_confirm_key, None)
                                                        st.rerun()

                        with st.popover("➕ Add a team"):
                            new_div_team_count = st.number_input(
                                "How many teams?", min_value=1, max_value=20, value=1, step=1,
                                key=f"new_div_team_count_{d['id']}", disabled=is_read_only,
                            )
                            if new_div_team_count == 1:
                                new_div_team_name = st.text_input(
                                    "Team name", key=f"new_div_team_name_{d['id']}", disabled=is_read_only
                                )
                            else:
                                st.caption(
                                    f"Creates {int(new_div_team_count)} teams named "
                                    f"Team1, Team2, ... Team{int(new_div_team_count)} — rename them "
                                    "individually afterward (⚙️ on each team above)."
                                )
                            if st.button(
                                "Add team" if new_div_team_count == 1 else "Add teams",
                                key=f"add_div_team_btn_{d['id']}", disabled=is_read_only,
                            ):
                                if new_div_team_count == 1:
                                    if new_div_team_name.strip():
                                        core.add_team(conn, d["id"], new_div_team_name.strip())
                                        st.rerun()
                                    else:
                                        st.error("Team name is required.")
                                else:
                                    for team_num in range(1, int(new_div_team_count) + 1):
                                        core.add_team(conn, d["id"], f"Team{team_num}")
                                    st.rerun()

                        st.divider()
                        st.subheader("Coaches")
                        division_coaches_by_team = core.list_team_coaches_for_division(conn, d["id"])
                        coach_rows = [
                            {"Coach": coach_label(c), "Team": t["name"]}
                            for t in teams for c in division_coaches_by_team.get(t["id"], [])
                        ]
                        if not coach_rows:
                            st.caption("No coaches assigned to any team in this division yet.")
                        else:
                            st.dataframe(zebra_style(pd.DataFrame(coach_rows)), width="stretch", hide_index=True)

                        if not teams:
                            st.caption("Add a team above first, then a coach can be assigned to it.")
                        else:
                            coach_team_options = {t["id"]: t["name"] for t in teams}
                            coach_team_id = st.selectbox(
                                "Manage coaches for", options=list(coach_team_options),
                                format_func=lambda i: coach_team_options[i], key=f"div_coach_team_pick_{d['id']}",
                            )
                            render_team_coach_manager(
                                conn, coach_team_id, coach_team_options[coach_team_id], key_prefix=f"div_coaches_{d['id']}"
                            )

                        with st.expander("📅 Assign Coaches From Last Division"):
                            previous_division = core.find_previous_division(conn, d["id"])
                            if previous_division is None:
                                st.caption("No earlier division found for this age group yet.")
                            elif not teams:
                                st.caption("Add a team above first, then a coach can be assigned to it.")
                            else:
                                previous_label = (
                                    f"{previous_division['year']} {previous_division['season']} — "
                                    f"{division_label(previous_division['age_group'])}"
                                )
                                previous_coaches_by_team = core.list_team_coaches_for_division(
                                    conn, previous_division["id"]
                                )
                                previous_teams = core.list_teams(conn, previous_division["id"])
                                carry_rows = [
                                    (pt, c) for pt in previous_teams for c in previous_coaches_by_team.get(pt["id"], [])
                                ]
                                if not carry_rows:
                                    st.caption(f"No coaches were assigned to a team in {previous_label}.")
                                else:
                                    st.caption(
                                        f"Coaches from {previous_label} — team names aren't carried over, so pick "
                                        "this season's team for each."
                                    )
                                    current_coaches_by_team = core.list_team_coaches_for_division(conn, d["id"])
                                    current_team_by_coach_id = {
                                        c["id"]: tid for tid, cs in current_coaches_by_team.items() for c in cs
                                    }
                                    current_team_options = {t["id"]: t["name"] for t in teams}
                                    # Seeded with each coach's *current* team (if any) so a
                                    # team already coached isn't offered to anyone else, then
                                    # claimed further as each row below picks one, so two
                                    # coaches can't be pointed at the same team in one batch.
                                    claimed_team_by_team_id: dict[int, int] = {
                                        team_id: coach_id for coach_id, team_id in current_team_by_coach_id.items()
                                    }
                                    pending_picks: dict[int, int] = {}
                                    for previous_team, coach in carry_rows:
                                        # A coach almost always has a kid playing here -- that's
                                        # usually why they were coaching. find_coach_children_in_division
                                        # checks the explicit link first, then falls back to a
                                        # name match against this division's players (auto-linking
                                        # it if found). Only when *neither* finds anything do we
                                        # grey the row out and ask directly who their child is,
                                        # rather than silently guessing.
                                        coach_children_here = core.find_coach_children_in_division(
                                            conn, coach["id"], d["id"]
                                        )
                                        is_relevant = bool(coach_children_here)

                                        crow1, crow2, crow3 = st.columns([2, 2.2, 1.6])
                                        label_text = f"{coach_label(coach)}  \n*was {previous_team['name']}*"
                                        if is_relevant:
                                            crow1.write(label_text)
                                        else:
                                            crow1.caption(label_text + "  \n*no player registered in this division*")
                                            with crow1.popover("❓ Who is their child?"):
                                                st.caption(
                                                    f"Which player in this division is "
                                                    f"{coach_label(coach)}'s child?"
                                                )
                                                child_needle = st.text_input(
                                                    "Search by name",
                                                    key=f"carry_child_search_{d['id']}_{coach['id']}",
                                                )
                                                needle_norm = child_needle.strip().lower()
                                                child_matches = [
                                                    p for p in division_players
                                                    if not needle_norm or needle_norm in p["name"].lower()
                                                ]
                                                if not child_matches:
                                                    st.caption("No matching players.")
                                                else:
                                                    child_options = {p["id"]: p["name"] for p in child_matches}
                                                    child_pick_id = st.selectbox(
                                                        "Player", options=list(child_options),
                                                        format_func=lambda i: child_options[i],
                                                        key=f"carry_child_pick_{d['id']}_{coach['id']}",
                                                        label_visibility="collapsed",
                                                    )
                                                    if st.button(
                                                        "Link as child",
                                                        key=f"carry_child_link_{d['id']}_{coach['id']}",
                                                        type="primary", disabled=is_read_only,
                                                    ):
                                                        core.link_coach_child(conn, coach["id"], child_pick_id)
                                                        st.rerun()
                                        current_team_id_for_coach = current_team_by_coach_id.get(coach["id"])
                                        # A team already claimed by *another* coach (currently,
                                        # or by an earlier row's pick in this same pass) is left
                                        # out of the options entirely, so two coaches can't be
                                        # pointed at the same team in one batch -- except this
                                        # coach's own current team, which stays offered to them.
                                        selectable_team_ids = [
                                            t_id for t_id in current_team_options
                                            if claimed_team_by_team_id.get(t_id, coach["id"]) == coach["id"]
                                        ]
                                        team_pick_options = {None: "— Select a team —"} | {
                                            t_id: current_team_options[t_id] for t_id in selectable_team_ids
                                        }
                                        default_idx = (
                                            list(team_pick_options).index(current_team_id_for_coach)
                                            if current_team_id_for_coach in team_pick_options else 0
                                        )
                                        with crow2:
                                            pick_team_id = st.selectbox(
                                                "Team", options=list(team_pick_options),
                                                format_func=lambda i: team_pick_options[i], index=default_idx,
                                                key=f"carry_coach_team_{d['id']}_{coach['id']}",
                                                label_visibility="collapsed",
                                                disabled=is_read_only or not is_relevant,
                                            )
                                        if pick_team_id is not None:
                                            claimed_team_by_team_id[pick_team_id] = coach["id"]
                                            if pick_team_id != current_team_id_for_coach:
                                                pending_picks[coach["id"]] = pick_team_id
                                        with crow3:
                                            if current_team_id_for_coach is not None and st.button(
                                                "Remove", key=f"carry_coach_remove_{d['id']}_{coach['id']}",
                                                disabled=is_read_only,
                                            ):
                                                core.remove_coach_from_team(
                                                    conn, current_team_id_for_coach, coach["id"]
                                                )
                                                st.rerun()

                                    st.divider()
                                    if not pending_picks:
                                        st.caption("Pick a team above for at least one coach to assign them.")
                                    else:
                                        st.caption(f"{len(pending_picks)} coach(es) ready to assign.")
                                        if st.button(
                                            "💾 Assign All", key=f"carry_assign_all_{d['id']}",
                                            type="primary", disabled=is_read_only,
                                        ):
                                            assign_errors = []
                                            for coach_id, team_id in pending_picks.items():
                                                prior_team_id = current_team_by_coach_id.get(coach_id)
                                                try:
                                                    if prior_team_id is not None and prior_team_id != team_id:
                                                        core.remove_coach_from_team(conn, prior_team_id, coach_id)
                                                    core.assign_coach_to_team(conn, team_id, coach_id)
                                                except ValueError as e:
                                                    assign_errors.append(str(e))
                                            if assign_errors:
                                                st.error(" / ".join(assign_errors))
                                            else:
                                                st.rerun()

                        st.divider()
                        st.subheader("Players")
                        if not division_players:
                            st.caption("No players signed up or rostered in this division yet.")
                        else:
                            players_tiered_grades = [
                                g.strip().upper() for p in division_players
                                if (g := division_grades_by_player.get(p["id"])) and g.strip().upper() in GRADE_TIERS
                            ]
                            summary_bits = [f"{len(division_players)} player(s)"]
                            if players_tiered_grades:
                                summary_bits.append(f"{len(players_tiered_grades)} graded")
                                summary_bits.append(", ".join(
                                    f"{tier}: {players_tiered_grades.count(tier)}"
                                    for tier in GRADE_TIERS if tier in players_tiered_grades
                                ))
                            st.caption(" · ".join(summary_bits))
                            experience_notes = core.player_experience_notes(
                                conn, d["id"], [p["id"] for p in division_players]
                            )
                            st.dataframe(
                                style_player_notes(pd.DataFrame([
                                    {
                                        "Name": p["name"],
                                        "Grade": division_grades_by_player.get(p["id"]) or "—",
                                        "Birth Date": p["birth_date"] or "—",
                                        "Team(s)": ", ".join(p["teams"]) if p["teams"] else "—",
                                        "Parent": core.full_name(p["contact_first_name"], p["contact_last_name"]) or "—",
                                        "Note": experience_notes.get(p["id"], ""),
                                    }
                                    for p in division_players
                                ])),
                                width="stretch", hide_index=True,
                            )

                        plan_key = f"player_import_plan_{d['id']}"
                        filename_key = f"player_import_filename_{d['id']}"
                        result_msg_key = f"player_import_result_{d['id']}"
                        with st.expander(
                            "📥 Import Players",
                            expanded=bool(st.session_state.get(plan_key) or st.session_state.get(result_msg_key)),
                        ):
                            if result_msg_key in st.session_state:
                                st.success(st.session_state.pop(result_msg_key))

                            st.caption(
                                "Upload a player list (CSV, Excel, or ODS) for this division. Matches "
                                "existing player profiles by name — using birth date too, when given, to "
                                "tell same-named players apart or flag a possible mismatch — and creates "
                                "new profiles otherwise. A Team column also assigns each player to that "
                                "team's roster (with a jersey number, if given); a Coach column assigns "
                                "that coach to the team too."
                            )
                            import_file = st.file_uploader(
                                "Upload player list", type=["csv", "xlsx", "xls", "ods"],
                                key=f"player_import_uploader_{d['id']}", disabled=is_read_only,
                            )

                            if (
                                import_file is not None and not is_read_only
                                and st.session_state.get(filename_key) != import_file.name
                            ):
                                try:
                                    import_df = read_uploaded_table(import_file)
                                    import_columns = core.detect_player_import_columns(list(import_df.columns))
                                    has_name = "name" in import_columns or (
                                        "first_name" in import_columns and "last_name" in import_columns
                                    )
                                    if not has_name:
                                        st.error(
                                            "Couldn't find a name column (e.g. \"Player Name\", \"Name\", or "
                                            "separate \"First Name\"/\"Last Name\" columns) — can't match or "
                                            "create players without one."
                                        )
                                    else:
                                        import_rows = import_df.to_dict("records")
                                        st.session_state[plan_key] = core.build_player_import_plan(
                                            conn, d["id"], import_rows, import_columns
                                        )
                                        st.session_state[filename_key] = import_file.name
                                except ValueError as e:
                                    st.error(str(e))

                            plan = st.session_state.get(plan_key)
                            if plan:
                                counts: dict[str, int] = {}
                                for entry in plan:
                                    counts[entry["status"]] = counts.get(entry["status"], 0) + 1
                                summary_bits = []
                                if counts.get("create"):
                                    summary_bits.append(f"{counts['create']} new")
                                if counts.get("update"):
                                    summary_bits.append(f"{counts['update']} matched to an existing profile")
                                if counts.get("ambiguous"):
                                    summary_bits.append(f"{counts['ambiguous']} ambiguous")
                                if counts.get("conflict"):
                                    summary_bits.append(f"{counts['conflict']} need review (birth date mismatch)")
                                if counts.get("invalid"):
                                    summary_bits.append(f"{counts['invalid']} skipped (no name)")
                                st.info(f"{len(plan)} row(s) in file: {', '.join(summary_bits)}.")

                                needs_review = [e for e in plan if e["status"] in ("ambiguous", "conflict")]
                                if needs_review:
                                    st.warning(f"{len(needs_review)} row(s) need your input before they'll be imported:")
                                    for entry in needs_review:
                                        st.markdown(f"**Row {entry['row_number']}: {entry['name']}**")
                                        options = {"__unresolved__": "— Choose one —"}
                                        if entry["status"] == "conflict":
                                            detail = entry["conflict_detail"]
                                            options["use_existing"] = (
                                                f"Same person — update the existing profile (birth date "
                                                f"{detail['existing']} → {detail['incoming']})"
                                            )
                                            options["create"] = "Different person — create a new profile"
                                        else:
                                            for c in entry["candidates"]:
                                                options[f"use:{c['id']}"] = (
                                                    f"{c['name']} (born {c['birth_date'] or 'unknown'})"
                                                )
                                            options["create"] = "None of these — create a new profile"

                                        choice = st.radio(
                                            "Resolution", list(options), format_func=lambda k: options[k],
                                            key=f"player_import_choice_{d['id']}_{entry['row_number']}",
                                            label_visibility="collapsed",
                                        )
                                        if choice == "__unresolved__":
                                            entry["resolved_action"], entry["resolved_player_id"] = None, None
                                        elif choice == "create":
                                            entry["resolved_action"], entry["resolved_player_id"] = "create", None
                                        elif choice == "use_existing":
                                            entry["resolved_action"] = "use_existing"
                                            entry["resolved_player_id"] = entry["matched_player_id"]
                                        elif choice.startswith("use:"):
                                            entry["resolved_action"] = "use_existing"
                                            entry["resolved_player_id"] = int(choice.split(":", 1)[1])
                                        st.divider()

                                still_unresolved = sum(1 for e in needs_review if e["resolved_action"] is None)
                                if still_unresolved:
                                    st.caption(
                                        f"{still_unresolved} row(s) above still need a choice — they'll be "
                                        "skipped (not guessed at) if you import now."
                                    )

                                import_apply_col, import_cancel_col = st.columns(2)
                                with import_apply_col:
                                    if st.button(
                                        "Apply Import", key=f"apply_import_{d['id']}", type="primary",
                                        disabled=is_read_only,
                                    ):
                                        result = core.apply_player_import_plan(conn, d["id"], plan)
                                        st.session_state.pop(plan_key, None)
                                        st.session_state.pop(filename_key, None)
                                        msg = (
                                            f"Created {result['created']}, updated {result['updated']}, "
                                            f"added to a roster {result['rostered']}, assigned "
                                            f"{result['coached']} coach(es), skipped {result['skipped']}."
                                        )
                                        for w in result["warnings"]:
                                            msg += f"\n- ⚠️ {w}"
                                        # Stashed for the *next* render rather than shown directly here —
                                        # st.rerun() immediately below would otherwise wipe out a message
                                        # shown via st.success() in this run before anyone sees it, and
                                        # the sections above (Teams/Coaches/Players) need that rerun to
                                        # stop showing stale pre-import data.
                                        st.session_state[result_msg_key] = msg
                                        st.rerun()
                                with import_cancel_col:
                                    if st.button("Cancel", key=f"cancel_import_{d['id']}"):
                                        st.session_state.pop(plan_key, None)
                                        st.session_state.pop(filename_key, None)
                                        st.rerun()

                        st.divider()
                        st.subheader("Schedule")
                        st.caption(
                            "Upload this division's official schedule (a CSV with Date/Home Team/Away "
                            "Team columns). Full results are shown on the Games tab."
                        )
                        sched_upload_col, sched_clear_col = st.columns([4, 1])
                        with sched_upload_col:
                            division_schedule_csv = st.file_uploader(
                                "Upload schedule CSV", type=["csv"],
                                key=f"division_schedule_csv_{d['id']}", disabled=is_read_only,
                            )
                        with sched_clear_col:
                            st.write("")
                            with st.popover("🗑️ Clear"):
                                st.caption("Removes this division's entire saved schedule.")
                                if st.button(
                                    "Clear", key=f"clear_schedule_btn_{d['id']}", type="primary",
                                    disabled=is_read_only,
                                ):
                                    core.clear_schedule(conn, d["id"])
                                    st.rerun()

                        if division_schedule_csv is not None and not is_read_only:
                            division_schedule_df = None
                            try:
                                division_schedule_df = pd.read_csv(division_schedule_csv)
                                division_schedule_df.columns = [c.strip() for c in division_schedule_df.columns]
                            except Exception as e:
                                st.error(f"Couldn't read that CSV: {e}")

                            if division_schedule_df is not None:
                                required_cols = {"Date", "Home Team", "Away Team"}
                                missing_cols = required_cols - set(division_schedule_df.columns)
                                if missing_cols:
                                    st.error(f"CSV is missing expected column(s): {', '.join(sorted(missing_cols))}")
                                else:
                                    division_schedule_rows = [
                                        {
                                            "order": row.get("Order"), "round": row.get("Round"),
                                            "game_date": row.get("Date"), "home_team": row.get("Home Team"),
                                            "away_team": row.get("Away Team"), "start_time": row.get("Start Time"),
                                            "end_time": row.get("End Time"), "location": row.get("Location"),
                                            "field": row.get("Field"),
                                        }
                                        for row in division_schedule_df.to_dict("records")
                                    ]
                                    saved = core.import_schedule(conn, d["id"], division_schedule_rows)
                                    st.success(f"Saved {saved} scheduled game(s) to the database.")

                        existing_schedule = core.list_schedule(conn, d["id"])
                        if existing_schedule:
                            sched_total = len(existing_schedule)
                            sched_done = sum(1 for r in existing_schedule if r["accounted_for"])
                            st.caption(f"{sched_done}/{sched_total} scheduled games played so far.")
                        else:
                            st.caption("No schedule uploaded yet for this division.")

                        st.divider()
                        remove_all_result_key = f"remove_all_players_result_{d['id']}"
                        if remove_all_result_key in st.session_state:
                            st.success(st.session_state.pop(remove_all_result_key))
                        bulk_remove_key = f"confirm_remove_all_players_{d['id']}"
                        if st.button(
                            "🧹 Remove all players from this division",
                            key=f"remove_all_players_{d['id']}", disabled=is_read_only,
                        ):
                            st.session_state[bulk_remove_key] = True
                            st.rerun()
                        if st.session_state.get(bulk_remove_key):
                            st.warning(
                                f"Remove every player from {division_title}? Clears their roster spot, "
                                "evaluations, position, and move notes for this division only — player "
                                "profiles stay, and any other division they're in is untouched. Useful "
                                "for undoing a bad import."
                            )
                            rcol1, rcol2 = st.columns(2)
                            with rcol1:
                                if st.button(
                                    "Yes, remove all", key=f"confirm_yes_remove_all_{d['id']}",
                                    type="primary", disabled=is_read_only,
                                ):
                                    removed = core.remove_all_players_from_division(conn, d["id"])
                                    st.session_state.pop(bulk_remove_key, None)
                                    st.session_state[remove_all_result_key] = (
                                        f"Removed {removed} player(s) from {division_title}."
                                    )
                                    st.rerun()
                            with rcol2:
                                if st.button("Cancel", key=f"confirm_no_remove_all_{d['id']}"):
                                    st.session_state.pop(bulk_remove_key, None)
                                    st.rerun()

                        st.divider()
                        confirm_key = f"confirm_delete_div_{d['id']}"
                        if st.button("🗑️ Delete division", key=f"delete_div_{d['id']}", disabled=is_read_only):
                            st.session_state[confirm_key] = True
                            st.rerun()
                        if st.session_state.get(confirm_key):
                            st.warning(
                                f"Delete {division_title}? It goes to the recycle bin for 30 days before being "
                                "permanently removed."
                            )
                            ccol1, ccol2 = st.columns(2)
                            with ccol1:
                                if st.button(
                                    "Yes, delete", key=f"confirm_yes_div_{d['id']}", type="primary",
                                    disabled=is_read_only,
                                ):
                                    core.soft_delete_division(conn, d["id"])
                                    st.session_state.pop(confirm_key, None)
                                    st.rerun()
                            with ccol2:
                                if st.button("Cancel", key=f"confirm_no_div_{d['id']}"):
                                    st.session_state.pop(confirm_key, None)
                                    st.rerun()

            with st.popover("➕ Add Division"):
                render_add_division_form(conn)

    elif teams_subpage == "🎯 Draft":
        if "draft" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Draft")
            st.caption(
                "A live, snake-order draft of a division's registered-but-unrostered players onto its teams. "
                "Each team's coach (or an admin) submits that team's pick when it's their turn; a drafted "
                "player is added straight to that team's roster (jersey number left as a placeholder for the "
                "coach to fill in later). The board below refreshes itself every few seconds, so every open "
                "Draft tab picks up picks made elsewhere automatically. Or skip the pick-by-pick flow "
                "entirely with 🤖 Auto-Draft below, which assigns the whole pool at once, balanced by rank "
                "and roster size — undo it (or just run it again) to re-draft from scratch."
            )

            draft_divisions = core.list_divisions(conn)
            if not draft_divisions:
                st.write("No divisions yet — add one in the Teams tab → Divisions first.")
            else:
                draft_division_options = {
                    d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in draft_divisions
                }
                default_idx = (
                    list(draft_division_options).index(working_division_id)
                    if working_division_id in draft_division_options else 0
                )
                draft_division_id = st.selectbox(
                    "Division", options=list(draft_division_options), format_func=lambda i: draft_division_options[i],
                    index=default_idx, key="draft_tab_division",
                )

                render_draft_live(conn, draft_division_id)

    else:
        if "teams" not in visible_pages:
            st.info("You don't have access to this page. Ask an admin to grant it in User Management.")
        else:
            st.header("Teams")
            st.caption(
                "Every team in a division at a glance: coach(es), whether players have been added, and "
                "whether they've been graded — with an average rating and A/B/C/D breakdown once they have."
            )

            teams_divisions = core.list_divisions(conn)
            if not teams_divisions:
                st.write("No divisions yet — add one in the Teams tab → Divisions first.")
            else:
                teams_division_options = {
                    d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in teams_divisions
                }
                teams_default_idx = (
                    list(teams_division_options).index(working_division_id)
                    if working_division_id in teams_division_options else 0
                )
                teams_division_id = st.selectbox(
                    "Division", options=list(teams_division_options), format_func=lambda i: teams_division_options[i],
                    index=teams_default_idx, key="teams_tab_division",
                )

                all_division_teams = core.list_teams(conn, teams_division_id)
                if not all_division_teams:
                    st.write("No teams yet in this division — add one in the Teams tab → Divisions.")
                else:
                    # Batch-fetched once for every team in this division, instead
                    # of one get_season_grade() round trip per player.
                    grades_by_player = core.get_season_grades_for_division(conn, teams_division_id)

                    col_widths = [1.8, 1, 2.3, 1.8, 1.3, 2.8]
                    head1, head2, head3, head4, head5, head6 = st.columns(col_widths)
                    head1.markdown("**Team**")
                    head2.markdown("**Color**")
                    head3.markdown("**Coach(es)**")
                    head4.markdown("**Players**")
                    head5.markdown("**Avg**")
                    head6.markdown("**Breakdown**")

                    for team_idx, t in enumerate(all_division_teams):
                        with highlighted_row(None, row_key=f"teams_tab_row_{t['id']}", index=team_idx):
                            c1, c2, c3, c4, c5, c6 = st.columns(col_widths)
                            c1.write(t["name"])
                            with c2:
                                render_color_swatch(t["color"])

                            team_coaches = core.list_team_coaches(conn, t["id"])
                            c3.write(", ".join(coach_label(tc) for tc in team_coaches) if team_coaches else "—")

                            roster = core.list_roster(conn, t["id"])
                            if not roster:
                                c4.write("No players yet")
                                c5.write("—")
                                c6.write("—")
                                continue

                            tiered_grades = []
                            for entry in roster:
                                if entry["player_id"] is None:
                                    continue
                                grade = grades_by_player.get(entry["player_id"])
                                if grade and grade.strip().upper() in GRADE_TIERS:
                                    tiered_grades.append(grade.strip().upper())

                            c4.write(f"{len(roster)} added · {len(tiered_grades)} graded")
                            if tiered_grades:
                                avg = sum(GRADE_VALUES[g] for g in tiered_grades) / len(tiered_grades)
                                c5.write(f"**{avg:.1f}**/{max(GRADE_VALUES.values())}")
                                c6.write(", ".join(
                                    f"{tier}: {tiered_grades.count(tier)}" for tier in GRADE_TIERS if tier in tiered_grades
                                ))
                            else:
                                c5.write("—")
                                c6.write("—")


# ---------------------------------------------------------------------------
# Tab 12: user management (admin only)
# ---------------------------------------------------------------------------

if user["is_admin"]:
    with tab_users:
        st.header("User Management")
        st.caption(
            "Admins always see every page, including this one, no matter what role they have. "
            "Everyone else sees whatever pages their assigned role grants."
        )

        roles = core.list_roles(conn)
        role_options: list[int | None] = [None] + [r["id"] for r in roles]

        def _role_label(role_id: int | None) -> str:
            if role_id is None:
                return "— No role (no page access) —"
            match = next((r for r in roles if r["id"] == role_id), None)
            return match["name"] if match else "(deleted role)"

        # -----------------------------------------------------------------
        # Roles — configure a bundle of pages once, then assign it to
        # however many users share that access level, instead of picking
        # pages one by one for every person.
        # -----------------------------------------------------------------

        st.subheader("Roles")
        if not roles:
            st.write("No roles yet — create one below, then assign it to users.")
        for role in roles:
            with st.expander(f"{role['name']} ({len(role['pages'])} page{'s' if len(role['pages']) != 1 else ''})"):
                edit_role_name = st.text_input("Role name", value=role["name"], key=f"role_name_{role['id']}")
                edit_role_pages = st.multiselect(
                    "Pages", options=list(core.PAGES), default=role["pages"],
                    format_func=lambda k: core.PAGES[k], key=f"role_pages_{role['id']}",
                )
                edit_role_read_only = st.checkbox(
                    "Read-only (can view its pages but not save/create/delete)",
                    value=role["read_only"], key=f"role_read_only_{role['id']}",
                )
                edit_role_hide_contact = st.checkbox(
                    "Hide contact details (parent name still visible, phone/email hidden)",
                    value=role["hide_contact_details"], key=f"role_hide_contact_{role['id']}",
                )
                role_save_col, role_delete_col = st.columns(2)
                with role_save_col:
                    if st.button("Save", key=f"role_save_{role['id']}"):
                        core.update_role(
                            conn, role["id"], name=edit_role_name, pages=edit_role_pages,
                            read_only=edit_role_read_only, hide_contact_details=edit_role_hide_contact,
                        )
                        st.success("Saved.")
                        st.rerun()
                with role_delete_col:
                    if st.button("Delete role", key=f"role_delete_{role['id']}"):
                        core.delete_role(conn, role["id"])
                        st.warning(f"Deleted. Anyone with the {role['name']} role now has no page access.")
                        st.rerun()

        with st.popover("➕ Add Role"):
            new_role_name = st.text_input("Role name", key="new_role_name")
            new_role_pages = st.multiselect(
                "Pages", options=list(core.PAGES), format_func=lambda k: core.PAGES[k], key="new_role_pages"
            )
            new_role_read_only = st.checkbox(
                "Read-only (can view its pages but not save/create/delete)", key="new_role_read_only"
            )
            new_role_hide_contact = st.checkbox(
                "Hide contact details (parent name still visible, phone/email hidden)",
                key="new_role_hide_contact",
            )
            if st.button("Create Role", type="primary", key="create_role_btn"):
                if not new_role_name.strip():
                    st.error("Enter a role name.")
                elif any(r["name"].lower() == new_role_name.strip().lower() for r in roles):
                    st.error("A role with that name already exists.")
                else:
                    core.add_role(
                        conn, new_role_name, pages=new_role_pages,
                        read_only=new_role_read_only, hide_contact_details=new_role_hide_contact,
                    )
                    st.success(f"Created role {new_role_name}.")
                    for _k in ("new_role_name", "new_role_pages", "new_role_read_only", "new_role_hide_contact"):
                        st.session_state.pop(_k, None)
                    st.rerun()

        st.divider()

        # -----------------------------------------------------------------
        # Users
        # -----------------------------------------------------------------

        st.subheader("Users")
        active_users = core.list_users(conn)
        deactivated_users = [u for u in core.list_users(conn, include_deleted=True) if u["deleted_at"]]
        active_admin_count = sum(1 for u in active_users if u["is_admin"])
        all_coaches_for_users = core.list_coaches(conn)
        coach_link_options = [None] + [c["id"] for c in all_coaches_for_users]
        coach_link_names = {c["id"]: coach_label(c) for c in all_coaches_for_users}

        for row_user in active_users:
            is_self = row_user["id"] == user["id"]
            last_admin = is_self and row_user["is_admin"] and active_admin_count <= 1
            label = row_user["display_name"] or row_user["email"]
            with st.expander(f"{label} — {row_user['email']}" + (" (admin)" if row_user["is_admin"] else "")):
                edit_name = st.text_input(
                    "Display name", value=row_user["display_name"] or "", key=f"user_name_{row_user['id']}"
                )
                edit_admin = st.checkbox(
                    "Admin (full access, including User Management)",
                    value=row_user["is_admin"], key=f"user_admin_{row_user['id']}",
                    disabled=last_admin,
                    help="Can't remove the last admin's own admin access." if last_admin else None,
                )
                edit_role_id = st.selectbox(
                    "Role", options=role_options, format_func=_role_label,
                    index=role_options.index(row_user["role_id"]), key=f"user_role_{row_user['id']}",
                    disabled=edit_admin,
                    help="Ignored while Admin is checked — admins get every page." if edit_admin else None,
                )
                edit_coach_id = st.selectbox(
                    "Linked coach profile", options=coach_link_options,
                    format_func=lambda i: "— None —" if i is None else coach_link_names[i],
                    index=coach_link_options.index(row_user["coach_id"]), key=f"user_coach_{row_user['id']}",
                    help="Ties this login to a coach identity so they can submit picks for their team(s) "
                         "on the Draft page.",
                )

                save_col, reset_col, deactivate_col = st.columns(3)
                with save_col:
                    if st.button("Save", key=f"user_save_{row_user['id']}"):
                        core.update_user(conn, row_user["id"], display_name=edit_name, is_admin=edit_admin)
                        core.set_user_role(conn, row_user["id"], edit_role_id)
                        core.set_user_coach(conn, row_user["id"], edit_coach_id)
                        st.success("Saved.")
                        st.rerun()
                with reset_col:
                    reset_state_key = f"user_reset_pw_{row_user['id']}"
                    if st.button("Generate new password", key=f"user_reset_btn_{row_user['id']}"):
                        st.session_state[reset_state_key] = secrets.token_urlsafe(9)
                    if st.session_state.get(reset_state_key):
                        st.code(st.session_state[reset_state_key])
                        if st.button("Apply this password", key=f"user_reset_confirm_{row_user['id']}"):
                            core.set_user_password(conn, row_user["id"], st.session_state[reset_state_key])
                            del st.session_state[reset_state_key]
                            st.success("Password updated — share it with them directly; it won't be shown again.")
                with deactivate_col:
                    if st.button("Deactivate", key=f"user_deactivate_{row_user['id']}", disabled=last_admin):
                        core.soft_delete_user(conn, row_user["id"])
                        st.rerun()

        if deactivated_users:
            with st.popover(f"♻️ Deactivated users ({len(deactivated_users)})"):
                for row_user in deactivated_users:
                    dcol1, dcol2 = st.columns([4, 1])
                    with dcol1:
                        st.write(row_user["email"])
                    with dcol2:
                        if st.button("Reactivate", key=f"user_reactivate_{row_user['id']}"):
                            core.restore_user(conn, row_user["id"])
                            st.rerun()

        st.divider()
        with st.popover("➕ Add User"):
            new_user_email = st.text_input("Email", key="new_user_email")
            new_user_name = st.text_input("Display name (optional)", key="new_user_name")

            gen_col, pw_col = st.columns([1, 3])
            with gen_col:
                st.write("")  # vertical alignment nudge next to the text input below
                if st.button("Generate", key="new_user_gen_pw"):
                    st.session_state["new_user_password"] = secrets.token_urlsafe(9)
            with pw_col:
                new_user_password = st.text_input(
                    "Temporary password", key="new_user_password",
                    help="There's no email delivery — share this with them directly. "
                         "They can be given a way to change it later.",
                )

            new_user_is_admin = st.checkbox("Admin (full access)", key="new_user_is_admin")
            new_user_role_id = st.selectbox(
                "Role", options=role_options, format_func=_role_label,
                key="new_user_role", disabled=new_user_is_admin,
            )

            if st.button("Create User", type="primary", key="create_user_btn"):
                if not new_user_email.strip() or "@" not in new_user_email:
                    st.error("Enter a valid email address.")
                elif not new_user_password:
                    st.error("Set a temporary password (or click Generate).")
                elif core.get_user_by_email(conn, new_user_email):
                    st.error("A user with that email already exists.")
                else:
                    core.add_user(
                        conn, new_user_email, new_user_password, display_name=new_user_name,
                        is_admin=new_user_is_admin, role_id=new_user_role_id,
                    )
                    st.success(f"Created {new_user_email}. Share the temporary password with them directly.")
                    for _k in ("new_user_email", "new_user_name", "new_user_password",
                               "new_user_is_admin", "new_user_role"):
                        st.session_state.pop(_k, None)
                    st.rerun()
