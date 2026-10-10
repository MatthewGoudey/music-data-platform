# The document worker as a Claude Code routine (Phase 6 Block G)

The worker writes requested deep dives with no terminal open: a **routine** at claude.ai/code runs a
cloud session on Matt's plan four times a day, clones this repo, and follows
`.claude/skills/album-companion/SKILL.md` against the API. Companion spec section 8.2 is the design;
this file is the setup. Sources: code.claude.com/docs/en/routines and
code.claude.com/docs/en/cloud-environments (checked 2026-10-10; routines are in research preview).

## What Matt sets up (once, in the browser)

### 1. A cloud environment

1. Open **claude.ai/code**. Above the message box, select the cloud icon with the environment name,
   then **Add cloud environment**. Name it `musicdata-worker`.
2. **Network access**: choose **Custom**. In **Allowed domains** enter one per line:
   ```
   musicdata-dev.fly.dev
   musicdata-prod.fly.dev
   ```
   Tick **Also include default list of common package managers**.
3. **Network secrets** → **Add secret**: credential type **Bearer**; **Allowed websites**
   `musicdata-dev.fly.dev`; paste the value of `API_TOKEN` from `.env.dev` into the Authorization
   row; **Connect**. The proxy adds the header on every call to that host, and the session never
   sees the token. (A second secret for `musicdata-prod.fly.dev` with `.env.prod`'s `API_TOKEN`
   comes when the worker moves to prod.)
4. Leave **Environment variables** and **Setup script** empty: the worker needs only `curl`, `git`
   and Python's standard library, which the cloud image has.

### 2. The Firecrawl connector

The worker's web searches use the claude.ai **Firecrawl** connector Matt already has; routines include
connected connectors by default. Page fetches go through the API (`POST /fetches`), which holds its own
Firecrawl key, so no Firecrawl key goes into the environment.

### 3. The routine

1. Open **claude.ai/code/routines** → **New routine**.
2. **Name**: `musicdata document worker`.
3. **Repository**: `MatthewGoudey/music-data-platform` (public; default branch `main`).
4. **Environment**: `musicdata-worker`.
5. **Connectors**: keep **Firecrawl**; remove the rest.
6. **Prompt**: the block below, as written.
7. **Trigger**: **Daily** at 09:07 for now. For the full schedule, run `/schedule update` in Claude Code
   on the laptop and set the cron to `7 9,13,17,21 * * *`; then open the routine and confirm the next
   run shows 09:07, 13:07, 17:07 or 21:07 Central.
8. **Create**, then use **Run now** for the first, watched run (Block G's manual test).

Prompt:

```
Write my requested documents.

You are the music-data-platform document worker. Follow .claude/skills/album-companion/SKILL.md
exactly, from section 1 to section 6, for every requested document.

The API is https://musicdata-dev.fly.dev. The environment's network secret adds the bearer token to
every call to that host: call it with curl and no Authorization header. Use the Firecrawl connector's
search for research; have the API fetch every page you cite (POST /fetches). Launch a separate agent
for the reader and another for the fact-checker, as the skill says.

When no document is requested, say so and finish. End with a short report: each document's album,
word count, pages added, Firecrawl credits, and fact-check tally.
```

## What Block G checks

The first **Run now** proves the four needs of companion spec 8.2 at once:

| Need | Proved when |
| --- | --- |
| no approval prompts | the run finishes without waiting (routines run autonomously) |
| the API token | `GET /documents?status=requested` answers 200, not 401 |
| network to the API and Firecrawl | `POST /fetches` and a connector search both succeed |
| agents | the reader and the fact-checker run as separate agents |

Then Matt presses **Request deep dive** on an album page, and the next scheduled run writes it. Each run
counts against the plan's usage; an idle run asks once for requests and ends.
