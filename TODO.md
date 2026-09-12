# To-do / backlog

Deferred work — not started, revisit when prioritized.

## Social login (Google / Apple / Facebook / Amazon / Yahoo / Microsoft)

Add "Sign in with X" alongside (or instead of) the current email/password
login, for the accounts an admin has already created in User Management.

**Easy — Streamlit's built-in `st.login()` is OIDC-based (authlib), so these
are just developer-console setup + `secrets.toml` config, no custom code:**
- Google
- Microsoft
- Yahoo (also standard OIDC, just less commonly integrated)

**Doable but has ongoing maintenance cost — Apple:**
- OIDC-compliant so it fits the same mechanism, but needs a paid ($99/yr)
  Apple Developer account, a verified domain, and a client "secret" that's
  actually a JWT derived from a private key — **expires every 6 months**
  and has to be regenerated/redeployed indefinitely.

**Harder — not standard OIDC, need a hand-rolled OAuth2 flow outside
`st.login()`:**
- Facebook — Meta App Review needed to request the email permission
  (days–weeks, sometimes business verification)
- Amazon (Login with Amazon) — own console/quirks, less tooling/precedent

**Open design question before building any of it:** the app is invite-only
(admins create every account, no self-signup). Need to decide how a social
login reconciles with that — e.g. only works if an admin already created a
matching account by that email, vs. letting anyone in and leaving them in a
no-role/pending state until an admin assigns one.

**Recommended approach when we pick this up:** start with Google only,
see whether it's actually what people want to use, then decide whether the
rest (especially Apple/Facebook/Amazon) are worth their added cost.

Each provider also requires *you* (not something I can do) to register a
developer account/app in that provider's console and hand over the
resulting client id/secret.
