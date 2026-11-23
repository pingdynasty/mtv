# mtv, the monkey televisor

A web console for [10km](https://github.com/pingdynasty/10km) loops: escalations, the current
cycle, sessions, repositories, history, cost and metrics, with a small set of operator actions.

mtv watches loops whose files it can read: a local folder, or a volume shared with the loop's
container. Requires Python 3.9+, standard library only. The page bundles its chart library 
(Apache ECharts) and works offline.

## Run it

    python3 -m mtv ~/devel/myloop                       # read-only, http://127.0.0.1:8710/
    python3 -m mtv ~/devel/a ~/devel/b --open           # several loops, with a switcher
    python3 -m mtv --allow-actions ~/devel/myloop

Or put the loops in a settings file, `~/.config/mtv/config.json` (or `$MTV_CONFIG`, or `--config`):

```json
{
  "loops": [
    {"name": "bruckert", "root": "~/devel/bruckert-10km"},
    {"name": "pdlang",   "root": "~/devel/pdlang-10km"}
  ],
  "allow_actions": false,
  "operator": "martin"
}
```

| Setting | Default | Meaning |
|---|---|---|
| `loops` | `[]` | Loop roots (the folder holding `.10km/`), with optional names. Command-line roots are added. |
| `tenkm` | none | Optional override: run every loop's `10km` and `msm` from this checkout instead of its own `central/loop`. |
| `host`, `port` | `127.0.0.1`, `8710` | Where to listen. |
| `allow_actions` | `false` | Enable the buttons that change a loop. |
| `operator` | `$USER` | Name recorded with each action, and appended to escalation answers. |
| `token_env` | `MTV_TOKEN` | Environment variable holding the access token for non-local requests. Generated and printed at startup if unset. |
| `state_dir` | `~/.local/state/mtv` | Where mtv keeps its action log (`actions.jsonl`). |

## Actions

With `--allow-actions`, the page can answer or resolve escalations, add backlog items, stop, kill,
start, resume, dispatch, mark a session done, and start an operator session. Each runs the
loop's own live installation, `<root>/central/loop/bin/10km` or `msm`, with the loop's `host.env`
sourced, as an operator's shell would. `LOOP_ROOT` and `MSM_DB` are set for the loop and
`LOOP_SESSION` is unset, so the commands' own checks apply (for example, resume is refused while
a blocking escalation is open). mtv's token is never passed to these commands. Every action is
logged with the operator's name.

mtv trusts a watched loop as much as it trusts itself: the loop's code runs with mtv's user and
in mtv's environment. Run mtv inside the same sandbox as the loop, not outside it (see below).

## Security

- mtv listens on 127.0.0.1 by default and serves requests from this machine without a token.
- On any other address, requests need a token: `Authorization: Bearer …`, or open the page once
  with `?token=<token>`, which sets a cookie.
  - **`$MTV_TOKEN` set** (or the variable named by `token_env`): that token is used and never printed.
  - **Not set:** mtv generates one for the run and prints it with a login link, so
    `docker logs <container>` shows it. It changes on every restart.
  - **`--no-token`:** no access control at all. Anyone who can reach the port can use mtv,
    including its actions if they're enabled. Use it only behind a proxy or network that does its
    own authentication.
- Action requests from another origin are refused.
- For HTTPS, put mtv behind a reverse proxy (for example Caddy or nginx).

## Where to run it

Because mtv runs the loop's own code, it belongs in the loop's sandbox:

- **Same VM or host, same user as the loop:** the loop's code runs with exactly the constraints
  the loop already has. This is the recommended setup. Note that anything running as that user,
  including the loop's agents, can read mtv's environment (`/proc/<pid>/environ` on Linux), so
  treat `$MTV_TOKEN` as readable by the loop.
- **Inside the loop's container** (`docker exec -d myloop python3 -m mtv … --host 0.0.0.0` with
  the port published to `127.0.0.1`): the same, plus the container's limits.
- **A sidecar container** sharing the volume is *not* the same sandbox. Commands mtv runs
  execute in the sidecar: its image, user, network and mounts, and its open port. Database-only
  actions (answer, resolve, backlog, stop, resume) work there. Actions that launch or signal
  agents (start, dispatch, kill, operator sessions with an agent) would run agents in the
  sidecar, or fail to reach the loop's processes, so don't enable actions in a sidecar unless it
  is built to the same constraints as the loop container. Read-only use is fine.

SQLite in WAL mode needs write access to `.10km/` even for reading (for its `-shm` file), so run
mtv as the loop's user (uid 1000 in the 10km image) or group.

## Development

    make test        # unit tests (standard library only)
    make smoke       # optional browser smoke test (needs: pip install playwright && playwright install chromium)

The tests build synthetic loops (`tests/fixtures.py`): imported history, Kiro credits, malformed
usage lines, sessions whose folder is missing, a loop that has never run.

Layout:

    mtv/data.py      read-only access to a loop (database, usage log, logs, repos)
    mtv/actions.py   the allowed 10km / msm commands and the action log
    mtv/server.py    HTTP server: page, JSON API per loop, auth
    mtv/cli.py       command line and settings
    mtv/static/      the page and its bundled chart library

## License

mtv: see LICENSE. Apache ECharts (`mtv/static/vendor/echarts.min.js`) is Apache-2.0.
