#!/usr/bin/env python3
"""
app.py

Streamlit web app for the Team Pittsburgh Ball Hockey game sheet pipeline:
split multi-page PDF scans, upload a sheet, review Claude's extraction in an
editable form, and save it to the SQLite database — or correct a game already
stored. Also has standings, player stats, team rosters, and an Excel export.

All extraction/DB/winner logic lives in game_sheet_core.py, which has no
Streamlit dependency, so the same core also backs the terminal scripts
(process_game_sheet.py, edit_game.py) and could back a Flask app later
without rewriting any of that logic.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    streamlit run app.py

Requires:
    pip install anthropic pypdf pymupdf streamlit pandas
"""

import base64
import os
import platform
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st
from PIL import Image

import game_sheet_core as core

DEFAULT_DB_PATH = Path(__file__).parent / "hockey.db"
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
        .st-key-db_path_button button {{
            background-color: {c['panel_bg']} !important;
            color: {c['text']} !important;
            justify-content: flex-start !important;
            text-align: left !important;
            font-weight: 400 !important;
            border: 1px solid {c['secondary_bg']} !important;
        }}
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
    Divisions tab and the top-of-page prompt shown when there are none yet.
    key_prefix keeps the two instances' widget keys from colliding."""
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        new_div_year = st.number_input(
            "Year", min_value=2000, max_value=2100, value=2026, step=1, key=f"{key_prefix}new_div_year"
        )
    with dcol2:
        new_div_season = st.selectbox(
            "Season", ["Summer", "Fall", "Winter", "Spring"], key=f"{key_prefix}new_div_season"
        )
    new_div_age_group = st.selectbox(
        "Age Group", list(core.AGE_GROUPS), key=f"{key_prefix}new_div_age_group", format_func=division_label,
    )
    st.caption(f"Category: {core.AGE_GROUPS[new_div_age_group]}")
    if st.button("Add Division", key=f"{key_prefix}add_division_btn", type="primary"):
        core.add_division(conn, int(new_div_year), new_div_season, new_div_age_group)
        st.rerun()


def get_client(api_key: str) -> anthropic.Anthropic | None:
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def gui_dialogs_available() -> bool:
    """Whether this process can pop up native dialogs (tkinter file picker,
    Windows Explorer). False on a hosted deployment like Streamlit Community
    Cloud, which runs headless on Linux with no display and no tkinter —
    installing tkinter there wouldn't help, since there's still no display
    for it to open a window on."""
    if platform.system() != "Windows":
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def browse_for_db_file() -> str | None:
    """Open a native file-picker dialog on the machine running this Streamlit
    server and return the chosen path, or None if cancelled/unavailable.
    Only meaningful for local use, where that machine is your own."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        st.sidebar.error("Browse isn't available — tkinter isn't installed.")
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Select database file",
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
        )
        root.destroy()
    except Exception as e:
        st.sidebar.error(f"Couldn't open the file picker: {e}")
        return None
    return path or None


def open_file_location(path_str: str):
    """Open Windows File Explorer at the given file's location, selecting it
    if it exists, or opening its parent folder if it doesn't yet."""
    p = Path(path_str).resolve()
    try:
        if p.exists():
            subprocess.run(["explorer", "/select,", str(p)])
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["explorer", str(p.parent)])
    except Exception as e:
        st.sidebar.error(f"Couldn't open File Explorer: {e}")


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

    return merged


def render_player_panel(
    conn, player_id: int, division_name_by_id: dict, all_divisions: list[dict], key_prefix: str
):
    """The full player profile editor — name/dob/current division/contacts,
    save/soft-delete, and an Evaluations popover. Reused both by the Players
    tab (picked from a dropdown) and by a "Player Panel" action opened
    inline from a linked Player Stats row, so there's one implementation of
    "the player panel" regardless of where it's opened from. key_prefix
    keeps widget keys unique between those two call sites."""
    player = core.get_player(conn, player_id)
    if player is None:
        st.error("Player not found.")
        return

    st.subheader(player["name"])
    ecol1, ecol2 = st.columns(2)
    edit_name = ecol1.text_input("Name", value=player["name"], key=f"{key_prefix}_name_{player_id}")
    edit_dob = ecol2.text_input(
        "Birth date", value=player["birth_date"] or "", key=f"{key_prefix}_dob_{player_id}",
        placeholder="YYYY-MM-DD",
    )
    division_ids = [None] + list(division_name_by_id)
    current_idx = (
        division_ids.index(player["current_division_id"]) if player["current_division_id"] in division_ids else 0
    )
    edit_division = st.selectbox(
        "Current division", options=division_ids,
        format_func=lambda i: "(none)" if i is None else division_name_by_id[i],
        index=current_idx, key=f"{key_prefix}_division_{player_id}",
    )
    ecol3, ecol4 = st.columns(2)
    edit_cfn = ecol3.text_input(
        "Contact first name", value=player["contact_first_name"] or "", key=f"{key_prefix}_cfn_{player_id}"
    )
    edit_cln = ecol4.text_input(
        "Contact last name", value=player["contact_last_name"] or "", key=f"{key_prefix}_cln_{player_id}"
    )
    ecol5, ecol6 = st.columns(2)
    edit_cph = ecol5.text_input(
        "Contact phone", value=player["contact_phone"] or "", key=f"{key_prefix}_cph_{player_id}"
    )
    edit_cem = ecol6.text_input(
        "Contact email", value=player["contact_email"] or "", key=f"{key_prefix}_cem_{player_id}"
    )

    save_col, delete_col, eval_col = st.columns(3)
    with save_col:
        if st.button("Save changes", key=f"{key_prefix}_save_{player_id}", type="primary"):
            core.update_player(
                conn, player_id, name=edit_name.strip(), birth_date=edit_dob.strip() or None,
                current_division_id=edit_division,
                contact_first_name=edit_cfn.strip() or None, contact_last_name=edit_cln.strip() or None,
                contact_phone=edit_cph.strip() or None, contact_email=edit_cem.strip() or None,
            )
            st.success("Saved.")
            st.rerun()
    with delete_col:
        confirm_del_key = f"{key_prefix}_confirm_delete_{player_id}"
        if st.button("🗑️ Delete player", key=f"{key_prefix}_delete_{player_id}"):
            st.session_state[confirm_del_key] = True
            st.rerun()
        if st.session_state.get(confirm_del_key):
            st.warning(f"Delete {player['name']}? This can be undone in the Players tab.")
            if st.button("Yes, delete", key=f"{key_prefix}_delete_confirm_{player_id}", type="primary"):
                core.soft_delete_player(conn, player_id)
                st.session_state.pop(confirm_del_key, None)
                st.rerun()
    with eval_col:
        with st.popover("📋 Evaluations"):
            history = core.player_division_history(conn, player_id)
            if history:
                st.caption("Divisions played:")
                for h in history:
                    st.write(f"- {h['year']} {h['season']} — {division_label(h['age_group'])} ({h['team_name']})")

            evaluations = core.list_evaluations(conn, player_id)
            if evaluations:
                st.caption("Past evaluations:")
                for ev in evaluations:
                    evcol1, evcol2 = st.columns([4, 1])
                    with evcol1:
                        team_part = f" — {ev['team_name']}" if ev["team_name"] else ""
                        st.write(f"{ev['year']} {ev['season']} {ev['age_group']}{team_part}: **{ev['grade']}**")
                    with evcol2:
                        if st.button("✕", key=f"{key_prefix}_delete_eval_{ev['id']}"):
                            core.delete_evaluation(conn, ev["id"])
                            st.rerun()
            else:
                st.caption("No evaluations yet.")

            st.divider()
            st.caption("Add an evaluation")
            if not all_divisions:
                st.write("No divisions yet — add one in the Divisions tab.")
            else:
                eval_division_id = st.selectbox(
                    "Division", options=list(division_name_by_id), format_func=lambda i: division_name_by_id[i],
                    key=f"{key_prefix}_eval_division_{player_id}",
                )
                eval_teams = core.list_teams(conn, eval_division_id)
                eval_team_options = {None: "(none)"} | {t["id"]: t["name"] for t in eval_teams}
                eval_team_id = st.selectbox(
                    "Team", options=list(eval_team_options), format_func=lambda i: eval_team_options[i],
                    key=f"{key_prefix}_eval_team_{player_id}",
                )
                eval_grade = st.text_input("Grade", key=f"{key_prefix}_eval_grade_{player_id}")
                if st.button("Add evaluation", key=f"{key_prefix}_add_eval_{player_id}", type="primary"):
                    if eval_grade.strip():
                        core.add_evaluation(conn, player_id, eval_division_id, eval_team_id, eval_grade.strip())
                        st.rerun()
                    else:
                        st.error("Grade is required.")


# ---------------------------------------------------------------------------
# Sidebar: shared settings
# ---------------------------------------------------------------------------

theme_mode = st.sidebar.radio("Theme", ["Dark", "Light"], horizontal=True, key="theme_mode")
inject_theme_css(theme_mode)
render_sidebar_logo(theme_mode)

st.markdown(
    f"""
    <div style="position: fixed; bottom: 6px; left: 10px; z-index: 1000;
                font-size: 0.7rem; color: rgba(150, 150, 150, 0.6); pointer-events: none;">
        v{APP_VERSION}
    </div>
    """,
    unsafe_allow_html=True,
)

st.sidebar.title("Settings")
if "db_path_input" not in st.session_state:
    st.session_state["db_path_input"] = str(DEFAULT_DB_PATH)

db_path = st.session_state["db_path_input"]

st.sidebar.markdown("**Database file**")
if gui_dialogs_available():
    if st.sidebar.button(
        f"📂 {db_path or '(click to choose a database file)'}", key="db_path_button",
        help="Click to browse for an existing database file", width="stretch",
    ):
        picked = browse_for_db_file()
        if picked:
            st.session_state["db_path_input"] = picked
            st.rerun()

    open_col, create_col = st.sidebar.columns(2)
    with open_col:
        if st.button("📁 Locate", key="open_loc_btn", help="Open this file's folder in File Explorer"):
            if db_path.strip():
                open_file_location(db_path)
            else:
                st.sidebar.error("No database file set.")
    with create_col:
        with st.popover("🆕 New"):
            new_db_name = st.text_input("New database filename or path", value="hockey.db", key="new_db_filename")
            if st.button("Create", key="create_db_btn", type="primary"):
                new_path = new_db_name.strip() or "hockey.db"
                st.session_state["db_path_input"] = new_path
                st.session_state["db_just_created"] = str(Path(new_path).resolve())
                st.rerun()
else:
    typed_path = st.sidebar.text_input(
        "Database path", value=db_path, key="db_path_text",
        help="No native file browser on a hosted deployment — type a path, "
             "or use Upload/Download below.",
    )
    if typed_path != db_path:
        st.session_state["db_path_input"] = typed_path
        st.rerun()
    if st.sidebar.button("🆕 Create", key="create_db_btn"):
        new_path = typed_path.strip() or "hockey.db"
        st.session_state["db_path_input"] = new_path
        st.session_state["db_just_created"] = str(Path(new_path).resolve())
        st.rerun()

with st.sidebar.expander("☁️ Load / Save database file"):
    st.caption(
        "For a hosted deployment (no local file browsing there): upload a "
        "previously-downloaded database to work from it, and download it "
        "again afterward to keep your changes — this app only works for one "
        "person at a time this way, since there's no merging of edits from "
        "two people working from separate copies."
    )
    uploaded_db = st.file_uploader("Upload a database file (.db)", type=["db"], key="db_upload")
    if uploaded_db is not None:
        upload_marker = (uploaded_db.name, uploaded_db.size)
        if st.session_state.get("db_upload_marker") != upload_marker:
            upload_path = (Path(__file__).parent / "uploaded_hockey.db").resolve()
            upload_path.write_bytes(uploaded_db.getvalue())
            st.session_state["db_upload_marker"] = upload_marker
            st.session_state["db_path_input"] = str(upload_path)
            st.rerun()
    download_db_placeholder = st.empty()

if not db_path.strip():
    st.sidebar.caption("No database selected.")
    st.info("Click the database field above to browse, or use New to create one.")
    st.stop()

just_created = st.session_state.pop("db_just_created", None)
resolved_db_path = Path(db_path).resolve()

if not just_created and not resolved_db_path.exists():
    st.sidebar.caption(f"Not found: {resolved_db_path}")
    st.info(
        f"No database exists yet at:\n\n{resolved_db_path}\n\n"
        "Use **New** (or type a path and hit **Create**) to create it, "
        "Browse to pick a different existing file, or Upload one below."
    )
    st.stop()

if just_created:
    st.sidebar.success(f"Created database at:\n\n{resolved_db_path}")

resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
st.sidebar.caption(f"Using: {resolved_db_path}")
api_key = st.sidebar.text_input(
    "ANTHROPIC_API_KEY",
    value=os.environ.get("ANTHROPIC_API_KEY", ""),
    type="password",
    help="Defaults to the ANTHROPIC_API_KEY environment variable.",
)

if fitz is None:
    st.sidebar.warning("pymupdf isn't installed — PDF previews will be unavailable.")

conn = core.init_db(db_path)

if Path(db_path).exists():
    download_db_placeholder.download_button(
        "💾 Download current database", data=Path(db_path).read_bytes(),
        file_name=Path(db_path).name or "hockey.db", mime="application/x-sqlite3",
        help="Save your current database file locally so you can upload it again next time.",
    )

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
    st.sidebar.download_button(
        "Export to Excel",
        data=core.export_workbook(conn, working_division_id),
        file_name="hockey_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Downloads the working division's Games, Standings, Player Stats, and Rosters as sheets in one .xlsx file.",
    )

with st.sidebar.expander("🔧 Utilities"):
    st.caption(
        "Split multi-page PDFs into individual single-page files — usually unnecessary, since "
        "Process New Sheets already auto-splits on upload. Use this to just download the split "
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
            if st.button("Load into Process New Sheets", type="primary"):
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
                st.success("Loaded — switch to the **Process New Sheets** tab to continue.")

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

tab_process, tab_edit, tab_standings, tab_stats, tab_rosters, tab_players, tab_divisions = st.tabs(
    ["Process New Sheets", "Games", "Standings", "Player Stats",
     "Team Rosters", "Players", "Divisions"]
)

# ---------------------------------------------------------------------------
# Tab 1: process new sheets
# ---------------------------------------------------------------------------

with tab_process:
    st.header("Process New Sheets")
    if working_division_id is None:
        st.warning("No division selected. Add one in the Divisions tab first.")
    uploaded = st.file_uploader(
        "Upload game sheet scan(s) (PDF or image)",
        type=["pdf", "png", "jpg", "jpeg", "webp", "gif"],
        accept_multiple_files=True,
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
                                    if merged["winner"] == "tie":
                                        st.error(
                                            "Games can't end in a tie — fix the score, add shootout "
                                            "results, or pick a winner below before saving."
                                        )
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

# ---------------------------------------------------------------------------
# Tab 2: edit existing games
# ---------------------------------------------------------------------------

with tab_edit:
    st.header("Games")
    if working_division_id is None:
        st.warning("No division selected. Add one in the Divisions tab first.")
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
            if st.button("Save new game", key="add_new_save", type="primary"):
                if manual_merged["winner"] == "tie":
                    st.error(
                        "Games can't end in a tie — fix the score, add shootout results, "
                        "or pick a winner below before saving."
                    )
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
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

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
            if st.button("Save changes", type="primary"):
                if merged["winner"] == "tie":
                    st.error(
                        "Games can't end in a tie — fix the score, add shootout results, "
                        "or pick a winner below before saving."
                    )
                else:
                    try:
                        core.update_game(conn, game_id, merged, working_division_id)
                        st.success(f"Updated game_id={game_id}.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

# ---------------------------------------------------------------------------
# Tab 3: standings
# ---------------------------------------------------------------------------

with tab_standings:
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
        st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 4: player stats
# ---------------------------------------------------------------------------

with tab_stats:
    st.header("Player Stats")
    stats = core.get_player_stats(conn, working_division_id) if working_division_id is not None else []
    if not stats:
        st.write("No players in the roster yet — add some in the Team Rosters tab.")
    else:
        stats = sorted(stats, key=lambda s: (-s["points"], -s["goals"]))
        all_divisions_for_stats = core.list_divisions(conn)
        division_name_by_id_stats = {
            d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions_for_stats
        }

        col_widths = [1.3, 0.5, 1.5, 0.5, 0.5, 0.5, 0.5, 0.8, 0.9, 1.2]
        headers = st.columns(col_widths)
        for col, label in zip(headers, ["Team", "#", "Name", "G", "A", "PTS", "PIM", "SO Made", "SO Missed", ""]):
            col.markdown(f"**{label}**")

        for s in stats:
            row_key = f"{s['team_id']}_{s['number']}"
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
                    with st.popover("➕ Create Player"):
                        st.caption(f"Linking {core.display_text(s['team'])} #{s['number']}")
                        new_name = st.text_input("Player name", key=f"stats_new_name_{row_key}")
                        new_dob = st.text_input(
                            "Birth date", key=f"stats_new_dob_{row_key}", placeholder="YYYY-MM-DD"
                        )
                        ncol1, ncol2 = st.columns(2)
                        new_cfn = ncol1.text_input("Contact first name", key=f"stats_new_cfn_{row_key}")
                        new_cln = ncol2.text_input("Contact last name", key=f"stats_new_cln_{row_key}")
                        ncol3, ncol4 = st.columns(2)
                        new_cph = ncol3.text_input("Contact phone", key=f"stats_new_cph_{row_key}")
                        new_cem = ncol4.text_input("Contact email", key=f"stats_new_cem_{row_key}")
                        if st.button("Create & Link", key=f"stats_create_link_{row_key}", type="primary"):
                            if not new_name.strip():
                                st.error("Player name is required.")
                            else:
                                new_player_id = core.add_player(
                                    conn, new_name.strip(), birth_date=new_dob.strip() or None,
                                    current_division_id=working_division_id,
                                    contact_first_name=new_cfn.strip() or None, contact_last_name=new_cln.strip() or None,
                                    contact_phone=new_cph.strip() or None, contact_email=new_cem.strip() or None,
                                )
                                entry = conn.execute(
                                    "SELECT id FROM roster_entries WHERE team_id = ? AND number = ?",
                                    (s["team_id"], s["number"]),
                                ).fetchone()
                                if entry:
                                    core.link_roster_entry_to_player(conn, entry[0], new_player_id)
                                st.success(f"Created and linked {new_name.strip()}.")
                                st.rerun()
                else:
                    with st.popover("👤 Player Panel"):
                        render_player_panel(
                            conn, s["player_id"], division_name_by_id_stats, all_divisions_for_stats,
                            key_prefix=f"stats_panel_{row_key}",
                        )

# ---------------------------------------------------------------------------
# Tab 5: team rosters
# ---------------------------------------------------------------------------

with tab_rosters:
    st.header("Team Rosters")
    st.caption(
        "Add player names/numbers per team so goals, assists, and penalties can be attributed by name. "
        "Teams belong to the Working Division picked above — the same team name in a different division "
        "is a separate team with its own roster."
    )

    if working_division_id is None:
        st.warning("No division selected. Add one in the Divisions tab first.")
    else:
        with st.expander("Add a new team"):
            new_team_name = st.text_input("Team name", key="new_team_name")
            if st.button("Add team"):
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
            roster_team_id = st.selectbox(
                "Select a team", options=list(team_options), format_func=lambda i: team_options[i]
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
                column_config={
                    "Number": st.column_config.TextColumn("Number", required=True),
                    "Name": st.column_config.TextColumn("Name", required=True),
                },
                key=f"roster_editor_{roster_team_id}",
            )

            if st.button("Save roster", type="primary"):
                new_entries = [
                    {"number": str(row["Number"]).strip(), "name": str(row["Name"]).strip()}
                    for _, row in edited_df.iterrows()
                    if str(row["Number"]).strip() or str(row["Name"]).strip()
                ]
                core.replace_roster(conn, roster_team_id, new_entries)
                st.success("Roster saved.")
                st.rerun()

            st.divider()
            st.subheader(f"{team_options[roster_team_id]} — Coaches")
            assigned = core.list_team_coaches(conn, roster_team_id)
            if assigned:
                st.write(", ".join(c["name"] for c in assigned))
            else:
                st.caption("No coaches assigned to this team yet.")
            all_coaches = core.list_coaches(conn)
            assigned_ids = {c["id"] for c in assigned}
            available_coaches = {c["id"]: c["name"] for c in all_coaches if c["id"] not in assigned_ids}
            acol1, acol2 = st.columns([3, 1])
            with acol1:
                coach_to_assign = st.selectbox(
                    "Assign coach", options=list(available_coaches), format_func=lambda i: available_coaches[i],
                    key=f"assign_coach_pick_{roster_team_id}",
                ) if available_coaches else None
            with acol2:
                if available_coaches and st.button("Assign", key=f"assign_coach_btn_{roster_team_id}"):
                    core.assign_coach_to_team(conn, roster_team_id, coach_to_assign)
                    st.rerun()
            if assigned:
                assigned_names = {c["id"]: c["name"] for c in assigned}
                remove_col1, remove_col2 = st.columns([3, 1])
                with remove_col1:
                    coach_to_remove = st.selectbox(
                        "Remove coach", options=list(assigned_ids), format_func=lambda i: assigned_names[i],
                        key=f"remove_coach_pick_{roster_team_id}",
                    )
                with remove_col2:
                    if st.button("Remove", key=f"remove_coach_btn_{roster_team_id}"):
                        core.remove_coach_from_team(conn, roster_team_id, coach_to_remove)
                        st.rerun()
            with st.popover("➕ New coach"):
                new_coach_name = st.text_input("Coach name", key=f"new_coach_name_{roster_team_id}")
                if st.button("Create coach", key=f"create_coach_btn_{roster_team_id}"):
                    if new_coach_name.strip():
                        new_coach_id = core.add_coach(conn, new_coach_name.strip())
                        core.assign_coach_to_team(conn, roster_team_id, new_coach_id)
                        st.rerun()
                    else:
                        st.error("Coach name is required.")

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
                st.dataframe(stats_df, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 6: players (global profiles, persisting across every division/season)
# ---------------------------------------------------------------------------

with tab_players:
    st.header("Players")
    st.caption(
        "Player profiles are global — the same player keeps one profile across every division/season "
        "they play in. Link a profile to a roster row (jersey number) in the Player Stats tab or Team "
        "Rosters to attribute stats to a name."
    )

    all_divisions_for_players = core.list_divisions(conn)
    division_name_by_id = {
        d["id"]: f"{d['year']} {d['season']} — {division_label(d['age_group'])}" for d in all_divisions_for_players
    }

    with st.expander("➕ Add a new player"):
        pn_name = st.text_input("Name", key="new_player_name")
        pn_dob = st.text_input("Birth date", key="new_player_dob", placeholder="YYYY-MM-DD")
        pn_division = st.selectbox(
            "Current division", options=[None] + list(division_name_by_id),
            format_func=lambda i: "(none)" if i is None else division_name_by_id[i],
            key="new_player_division",
        )
        pcol1, pcol2 = st.columns(2)
        pn_cfn = pcol1.text_input("Contact first name", key="new_player_cfn")
        pn_cln = pcol2.text_input("Contact last name", key="new_player_cln")
        pcol3, pcol4 = st.columns(2)
        pn_cph = pcol3.text_input("Contact phone", key="new_player_cph")
        pn_cem = pcol4.text_input("Contact email", key="new_player_cem")
        if st.button("Add player", key="add_player_btn", type="primary"):
            if pn_name.strip():
                core.add_player(
                    conn, pn_name.strip(), birth_date=pn_dob.strip() or None,
                    current_division_id=pn_division,
                    contact_first_name=pn_cfn.strip() or None, contact_last_name=pn_cln.strip() or None,
                    contact_phone=pn_cph.strip() or None, contact_email=pn_cem.strip() or None,
                )
                st.rerun()
            else:
                st.error("Name is required.")

    players_list = core.list_players(conn)
    if not players_list:
        st.write("No players yet — add one above.")
    else:
        player_options = {p["id"]: p["name"] for p in players_list}
        selected_player_id = st.selectbox(
            "Select a player", options=list(player_options), format_func=lambda i: player_options[i],
            key="players_tab_select",
        )
        render_player_panel(
            conn, selected_player_id, division_name_by_id, all_divisions_for_players, key_prefix="players_tab"
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
                    if st.button("Restore", key=f"restore_player_{p['id']}"):
                        core.restore_player(conn, p["id"])
                        st.rerun()

# ---------------------------------------------------------------------------
# Tab 7: divisions
# ---------------------------------------------------------------------------

with tab_divisions:
    header_col, recycle_col = st.columns([4, 1])
    with header_col:
        st.header("Divisions")
    with recycle_col:
        with st.popover("♻️ Recycle Bin"):
            deleted_divisions = core.list_deleted_divisions(conn)
            if not deleted_divisions:
                st.write("Recycle bin is empty.")
            else:
                st.caption("Permanently deleted 30 days after removal, unless restored first.")
                for d in deleted_divisions:
                    rbcol1, rbcol2 = st.columns([4, 1])
                    with rbcol1:
                        st.write(
                            f"{d['year']} {d['season']} — {division_label(d['age_group'])} "
                            f"· {d['days_left']} days left"
                        )
                    with rbcol2:
                        if st.button("Restore", key=f"restore_div_{d['id']}"):
                            core.restore_division(conn, d["id"])
                            st.rerun()

    st.caption(
        "A division is one season's instance of an age group, e.g. 2026 Summer Penguin (U10). "
        "The same age group recurs as a new division every season. The Working Division picker "
        "at the top right applies across the whole session and defaults new sheets' Division field."
    )

    divisions = core.list_divisions(conn)
    if not divisions:
        st.write("No divisions yet.")
    else:
        for d in divisions:
            confirm_key = f"confirm_delete_div_{d['id']}"
            dcol1, dcol2 = st.columns([5, 1])
            with dcol1:
                st.write(f"**{d['year']} {d['season']}** — {division_label(d['age_group'])}")
            with dcol2:
                if st.button("🗑️ Delete", key=f"delete_div_{d['id']}"):
                    st.session_state[confirm_key] = True
                    st.rerun()
            if st.session_state.get(confirm_key):
                st.warning(
                    f"Delete {d['year']} {d['season']} — {division_label(d['age_group'])}? "
                    "It goes to the recycle bin for 30 days before being permanently removed."
                )
                ccol1, ccol2 = st.columns(2)
                with ccol1:
                    if st.button("Yes, delete", key=f"confirm_yes_div_{d['id']}", type="primary"):
                        core.soft_delete_division(conn, d["id"])
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                with ccol2:
                    if st.button("Cancel", key=f"confirm_no_div_{d['id']}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
            st.divider()

    with st.popover("➕ Add Division"):
        render_add_division_form(conn)
