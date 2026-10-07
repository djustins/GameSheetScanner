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

## Pages

The sidebar has the league-wide pages (Home, Divisions, All Players, All Coaches,
All Parents, and User Management for admins) and, under **Working Division**, the
pages scoped to the division picked at the top of the sidebar:

| Page | What's there |
|---|---|
| Team Rosters | Each team's roster, coaches and stats; trades between teams |
| Evals | This season's evaluations |
| Games | Schedule & Results, Import Scoresheets (the game sheet scanner), Manage Games |
| Stats & Standings | Standings and player stats, computed from stored games |
| Draft | The draft and Auto-Draft, with play-with Requests as a second tab |

What a user sees follows their role's pages and read-only setting, the same as in
the Streamlit app (`src/auth/access.tsx`).

### Team roster table

- Opens sorted by last name, then first name.
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
