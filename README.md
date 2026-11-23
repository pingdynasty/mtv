# mtv, the monkey televisor

A web console for [10km](https://github.com/pingdynasty/10km) loops: escalations, the current
cycle, sessions, repositories, history, cost and metrics, with a small set of operator actions.

mtv watches loops whose files it can read: a local folder, or a folder bind-mounted into the
loop's container. Python 3.9+, standard library only. The page bundles its chart library
(Apache ECharts) and works offline. Demo: [tenkm.org](https://tenkm.org/).

## Install

    pip install git+https://github.com/pingdynasty/mtv     # or: python3 -m pip install .
    mtv --help

Or run it from a checkout without installing: `python3 -m mtv`.

## Run it

    mtv ~/loops/myloop                       # read-only, http://127.0.0.1:8710/
    mtv ~/loops/a ~/loops/b --open           # several loops, with a switcher
    mtv --allow-actions ~/loops/myloop

Or put the loops in a settings file, `~/.config/mtv/config.json` (or `$MTV_CONFIG`, or `--config`):

```json
{
  "loops": [
    {"name": "alpha", "root": "~/loops/alpha"},
    {"name": "beta",  "root": "~/loops/beta"}
  ],
  "allow_actions": false,
  "operator": "alice"
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
- **Inside the loop's container** (`docker exec -d myloop mtv /home/monkey/loop --host 0.0.0.0`,
  with the port published to `127.0.0.1`): the same, plus the container's limits.
- **On the host, for a container loop:** bind-mount the loop folder
  (`-v ~/loops/myloop:/home/monkey/loop`) so mtv can read it, and keep actions off: commands
  would run on the host, not in the container. Reading and acting through `docker exec`, with
  no bind mount, is planned (see the roadmap).
- **A separate sidecar container** is a different sandbox from the loop's. Keep it read-only.

SQLite in WAL mode needs write access to `.10km/` even for reading (for its `-shm` file), so run
mtv as the loop's user (uid 1000 in the 10km image) or group.

## Development

    make test        # unit tests (standard library only)
    make smoke       # optional browser smoke test (needs: pip install playwright && playwright install chromium)

## Roadmap

- `10km mcp`: each loop serves its own state and operator actions over MCP; mtv becomes an MCP
  client, local loops and container loops (`docker exec -i … 10km mcp`) alike.
- Operator sessions with an agent, started from the page.

The tests build synthetic loops (`tests/fixtures.py`): imported history, Kiro credits, malformed
usage lines, sessions whose folder is missing, a loop that has never run.

Layout:

    mtv/data.py      read-only access to a loop (database, usage log, logs, repos)
    mtv/actions.py   the allowed 10km / msm commands and the action log
    mtv/server.py    HTTP server: page, JSON API per loop, auth
    mtv/cli.py       command line and settings
    mtv/static/      the page and its bundled chart library

## License

mtv is MIT licensed: see LICENSE. Apache ECharts (`mtv/static/vendor/echarts.min.js`) is
Apache-2.0: see `mtv/static/vendor/ECHARTS-LICENSE` and `ECHARTS-NOTICE`.
