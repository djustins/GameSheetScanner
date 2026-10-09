# Team Pittsburgh Team Manager — React app

The React front end for this project: the same league data as the Streamlit app
(`../app.py`), reached through the HTTP API (`../api.py`) instead of talking to the
database directly. See the [root README](../README.md) for the project as a whole,
the API, and the database.

Built with React, TypeScript and Vite, using [Mantine](https://mantine.dev) for
components, TanStack Query for data fetching, and React Router for pages.

## Running it locally

You need Node.js and the API running (see "Running the API" in the root README).

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Other scripts: `npm run build` (type-check, then build into `dist/`), `npm run lint`
(oxlint), `npm run preview` (serve the built app).

### Which API it talks to

`VITE_API_BASE_URL` decides, and Vite picks the file by mode:

| File | Used by | Value |
|---|---|---|
| `.env.development` | `npm run dev` | `http://localhost:8000` |
| `.env.production` | `npm run build` | the deployed API on Render |
| `.env.local` | overrides both, not committed | whatever you need locally |

Sign in with the same email and password as the Streamlit app.

## Deploying

The app is hosted on Vercel, which builds this folder on every push to `main`.
`vercel.json` rewrites every path to `index.html` so page refreshes and deep links
work with client-side routing. The API deploys separately on Render and usually
finishes a little later than the front end, so for a minute or two after a push the
new front end can be talking to the old API.

### Analytics

[Vercel Web Analytics](https://vercel.com/docs/analytics) counts page views and
visitors (`<Analytics />` in `src/main.tsx`). It only reports from the deployed
site, not from `npm run dev`, and it has to be switched on once for the project in
the Vercel dashboard under **Analytics**.

The numbers to trust are on the app's own **Usage** page (admins only, in the
sidebar): the API records each sign-in and each page opened, so nothing in the
browser can block it, and it shows who, not just how many. `AppLayout.tsx` reports
each page change to `POST /me/page-views`; `GET /usage` summarises the log per
person, per page and per day. Vercel Analytics still adds what the server can't
see (country, device, referrer).

## Pages

The sidebar has the league-wide pages (Home, Divisions, All Players, All Coaches,
All Parents, and User Management for admins) and, under **Working Division**, the
pages scoped to the division picked at the top of the sidebar:

| Page | What's there |
|---|---|
| Team Rosters | A tab per team (alphabetical, the first one open), each showing its players as cards with their stats, for everyone; admins get a **Bulk edit team** button that opens the roster editor (numbers, positions, grades, adding and moving players, coaches). Also everyone registered in the division. |
| Evals | This season's evaluations |
| Games | Schedule & Results, Import Scoresheets (the game sheet scanner), Manage Games |
| Stats & Standings | Standings (with a Team balance section below them) and player stats, computed from stored games |
| Roster Management | Draft (live or Auto-Draft), Requests (play-with requests), Trades (plus the move history, for admins), Unplaced Players |

What a user sees follows their role's pages and read-only setting, the same as in
the Streamlit app (`src/auth/access.tsx`).

### My Account

Clicking your name at the top right opens **My Account** (`/account`): your sign-in
email and access, your display name, changing your password (the current one is
required), and your API tokens. Admins also get a shortcut there, and an **Email**
link in the header, to the page for emailing parents and coaches.

### Player photos

A player's profile (the drawer that opens from any player list) has **Upload
photo** / **Replace photo** / **Remove** for anyone who can edit. The photo shows
there and on the Team Rosters cards; players without one show their
initials. `src/components/PlayerPhoto.tsx` shrinks the chosen image to 400px and a
JPEG in the browser before uploading, so any phone photo works and what's stored is
small. Photos live in the database (`player_photos`), are served only to signed-in
users (`GET /players/{id}/photo`), and are deleted with the player.

### The draft

The Draft section of Roster Management has two modes, switched at the top:

- **Live draft** is the real one: each pick puts the player on that team's roster.
  Auto-Draft, its results and the draft notes are here too.
- **Mock draft** is a practice run for testing settings or training coaches. Its
  picks are never saved to a roster, its pool is everyone registered in the
  division (on a team already or not), anyone who can edit picks for every team,
  and **Auto-pick** fills the current pick with the best player left. It runs
  alongside the real draft without touching it; end it and start again freely.

Either way a running draft shows as a draft room (`src/components/DraftRoom.tsx`):
who is on the clock and who is next, the round-by-team board, the player pool
(search, filter by position, sort by grade, name or age, with each player's
play-with requests), every team's picks and balance, and the pick history. It
refreshes every five seconds.

Each draft has its own settings, chosen when it starts and changeable from **Draft
settings** while it runs (`src/components/DraftSetup.tsx`):

| Setting | Choices |
|---|---|
| Order | Snake (each round reverses) or linear. Fixed once a pick has been made. |
| Rounds | A number, or blank to draft until the pool is empty |
| Pick clock | Off, or a countdown everyone sees. It prompts; it never picks for anyone. |
| Who makes picks | Each team's own coach, or admins only (real drafts; admins can always pick) |

### Team roster table

- This is the admin-only editor behind **Bulk edit team**. It opens sorted by last name, then first name.
- Click a column header to sort by it, and again to reverse: **#** sorts numerically
  with placeholders like `TBD3` last; **Name** by last then first name; **Position**
  and **Grade** with blanks at the bottom.
- To swap two players' numbers, edit one player and type the other's number. The
  dialog names who has it and the button becomes **Swap numbers**.

## Theme

Team Pittsburgh's black and gold, matching the Streamlit app's `THEME_COLORS`, with
a **Dark / Light** switch at the top of the sidebar. It opens in Dark and remembers
the choice in that browser.

- `src/theme.ts` — the colors, the Mantine theme, and the CSS variables for each
  mode. Change a color here, not in components.
- `src/index.css` — global styles; headings take their gold from the theme.
- `public/logo.png` is the sidebar and login logo; `public/favicon.png` is the
  browser tab icon (the logo on a black tile, so it reads on light tabs too).

Use Mantine's color props and the `--app-*` variables rather than hard-coded
colors, so both modes keep working.

## Layout of `src/`

| Folder | Contents |
|---|---|
| `api/` | One module per API area, plus `client.ts` (auth header, errors) and `types.ts` |
| `auth/` | Sign-in state, route guards, and the page/read-only access helpers |
| `components/` | Shared pieces: roster view, game form, import panels, trade panel |
| `context/` | The Working Division selection |
| `layout/` | The app shell: header, sidebar, theme switch |
| `pages/` | One component per route (routes are in `App.tsx`) |
