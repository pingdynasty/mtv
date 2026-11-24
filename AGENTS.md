# AGENTS.md

Guidance for agents working on mtv, the web console for [10km](https://github.com/pingdynasty/10km)
loops. [README.md](README.md) describes what it does, its settings and its security model.

- Python 3.9+, standard library only, by default. These can be relaxed (a dependency, a newer
  Python) when keeping to them costs more than it gives; say so in your report so the operator
  is told, without stopping the loop. The page (`mtv/static/`) bundles its libraries and works
  offline; no CDNs.
- Run `make test` before every commit. `make smoke` (a browser test) needs Playwright and is
  optional.
- Layout: `mtv/data.py` reads a loop, `mtv/actions.py` runs the allowed 10km and msm commands,
  `mtv/server.py` serves the page and the JSON API, `mtv/cli.py` handles the command line and
  settings. Tests build synthetic loops in `tests/fixtures.py`.
- Never let a test touch a real loop: tests use fixture loops in temporary directories, and must
  not inherit `LOOP_ROOT`, `MSM_DB` or `LOOP_SESSION`, or call a `10km` or `msm` on `PATH`.
- mtv changes a loop only through 10km and msm commands, never by writing its files or database.
- Security defaults stay as they are: listen on 127.0.0.1, require a token on other addresses,
  refuse cross-origin actions, keep actions off unless enabled. Raise any change to these with
  the operator rather than making it.
- Examples, fixtures and docs use generic names (loops `alpha` and `beta` under `~/loops`,
  operator `alice`), never real projects, paths or usernames.
- Update `CHANGELOG.md` and `README.md` when behaviour or settings change.
