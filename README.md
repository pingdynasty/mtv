# mtv, the monkey televisor

A web console for [10km](https://github.com/pingdynasty/10km) loops: escalations, the current
cycle, sessions, repositories, history, cost and metrics, with a small set of operator actions.

mtv watches loops whose files it can read: a local folder, or a volume shared with the loop's
container. Requires Python 3.9+, standard library only. The page bundles its chart library 
(Apache ECharts) and works offline.

## Run it

    python3 -m mtv ~/devel/myloop                       # read-only, http://127.0.0.1:8710/
    python3 -m mtv ~/devel/a ~/devel/b --open           # several loops, with a switcher
    python3 -m mtv --tenkm ~/10km --allow-actions ~/devel/myloop

Or put the loops in a settings file, `~/.config/mtv/config.json` (or `$MTV_CONFIG`, or `--config`):

```json
{
  "loops": [
    {"name": "bruckert", "root": "~/devel/bruckert-10km"},
    {"name": "pdlang",   "root": "~/devel/pdlang-10km"}
  ],
  "tenkm": "~/10km",
  "allow_actions": false,
  "operator": "martin"
}
```

| Setting | Default | Meaning |
|---|---|---|
| `loops` | `[]` | Loop roots (the folder holding `.10km/`), with optional names. Command-line roots are added. |
| `tenkm` | none | A 10km checkout mtv runs `10km` and `msm` from. Without it, mtv is read-only. |
| `host`, `port` | `127.0.0.1`, `8710` | Where to listen. |
| `allow_actions` | `false` | Enable the buttons that change a loop. |
| `operator` | `$USER` | Name recorded with each action, and appended to escalation answers. |
| `token_env` | `MTV_TOKEN` | Environment variable holding the access token for non-local requests. Generated and printed at startup if unset. |
| `state_dir` | `~/.local/state/mtv` | Where mtv keeps its action log (`actions.jsonl`). |

## Actions

With `--allow-actions` and a `tenkm` checkout, the page can answer or resolve escalations, add
backlog items, pause, resume, stop, dispatch, mark a session done, and start an operator session
(without an agent). Each runs `10km` or `msm` from **mtv's own 10km checkout**, with `LOOP_ROOT`
and `MSM_DB` set and `LOOP_SESSION` unset, so the commands' own checks apply (for example, resume
is refused while a blocking escalation is open). Every action is logged with the operator's name.

mtv never runs a loop's `central/loop/bin` or sources its `host.env`: the loop's agents can change
both. `tenkm` must not be inside a loop; mtv refuses to start if it is.

## Security

- mtv listens on 127.0.0.1 by default and serves requests from this machine without a token.
- On any other address, requests need a token: `Authorization: Bearer …`, or open the page once
  with `?token=…`, which sets a cookie. The token comes from `$MTV_TOKEN` (or `token_env`). If
  none is set, mtv generates one at startup and prints it with a login link, so
  `docker logs <container>` shows it. A generated token lasts for that run only; set
  `$MTV_TOKEN` to keep the same one across restarts.
- Action requests from another origin are refused.
- For HTTPS, put mtv behind a reverse proxy (for example Caddy or nginx).

## As a sidecar

Run mtv in its own container that mounts the loop's volume. The agents' container should hold
neither mtv's token nor network access to it.

```sh
docker build -t mtv .
docker run -d --name myloop-mtv -v myloop-home:/home/monkey:rw \
  -p 127.0.0.1:8710:8710 mtv /home/monkey/loop --host 0.0.0.0
docker logs myloop-mtv      # shows the token and a login link
```

Pass `-e MTV_TOKEN=…` to use a fixed token instead.

SQLite in WAL mode needs write access to `.10km/` even for reading (for its `-shm` file), so run
mtv with the loop's user ID (1000 in the 10km image) or group, and mount the volume read-write.
For actions, mount or bake in a 10km checkout and pass `--tenkm` and `--allow-actions`.

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
