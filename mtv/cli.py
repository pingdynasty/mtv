"""mtv command line.

    mtv [LOOP_ROOT ...] [--config PATH] [--host H] [--port N]
        [--tenkm DIR] [--allow-actions] [--operator NAME] [--open]

Loop roots given here are added to those in the settings file.
The token for non-local access comes from the environment variable named by
"token_env" (default MTV_TOKEN); it is never read from a loop's files. When
mtv listens on a non-local address and no token is set, it generates one for
this run and prints a login link, so `docker logs` shows it.
"""
import argparse
import os
import secrets
import sys
import webbrowser

from . import __version__, config
from .actions import Runner
from .data import Loop, LoopUnavailable
from .server import App, serve


def main(argv=None):
    ap = argparse.ArgumentParser(prog='mtv', description='mtv, the monkey televisor: a web console for 10km loops.')
    ap.add_argument('roots', nargs='*', metavar='LOOP_ROOT', help='loop root (the folder holding .10km/)')
    ap.add_argument('--config', help='settings file (default $MTV_CONFIG or ~/.config/mtv/config.json)')
    ap.add_argument('--host', help='address to listen on (default 127.0.0.1)')
    ap.add_argument('--port', type=int, help='port (default 8710)')
    ap.add_argument('--tenkm', help='10km checkout to run 10km and msm from')
    ap.add_argument('--allow-actions', action='store_true', default=None, help='enable buttons that change a loop')
    ap.add_argument('--operator', help='name recorded with actions')
    ap.add_argument('--open', action='store_true', help='open the page in a browser')
    ap.add_argument('--version', action='version', version=f'mtv {__version__}')
    ns = ap.parse_args(argv)

    try:
        cfg = config.load(ns.config, ns.roots, {
            'host': ns.host, 'port': ns.port, 'tenkm': ns.tenkm,
            'allow_actions': ns.allow_actions, 'operator': ns.operator,
        })
    except config.ConfigError as e:
        sys.exit(f'mtv: {e}')
    if not cfg['loops']:
        sys.exit('mtv: no loops: pass a LOOP_ROOT or list "loops" in the settings file')
    cfg['operator'] = cfg['operator'] or os.environ.get('USER', '')

    loops = [Loop(l['root'], l['name'], l['id']) for l in cfg['loops']]
    for l in loops:
        try:
            v, ok, missing = l.schema()
            note = '' if ok else f'  (schema version {v} not tested with mtv {__version__})'
            if missing:
                note += f'  missing: {missing}'
            print(f'  {l.id:<20} {l.root}{note}')
        except LoopUnavailable as e:
            print(f'  {l.id:<20} {l.root}  UNAVAILABLE: {e}')

    local_only = cfg['host'] in ('127.0.0.1', '::1', 'localhost')
    token = os.environ.get(cfg['token_env']) or None
    generated = False
    if not token and not local_only:
        token = secrets.token_urlsafe(24)
        generated = True
    runner = Runner(cfg['tenkm'], cfg['state_dir'])
    app = App(cfg, loops, runner, token)
    st = app.actions_state()
    try:
        httpd = serve(app, cfg['host'], cfg['port'])
    except OSError as e:
        sys.exit(f'mtv: can\'t listen on {cfg["host"]}:{cfg["port"]}: {e}')
    url = f'http://{"127.0.0.1" if cfg["host"] in ("0.0.0.0", "::") else cfg["host"]}:{cfg["port"]}/'
    print(f'mtv {__version__}  {url}  ({"actions on" if st["enabled"] else st["reason"]})')
    if generated:
        print(f'  token (this run only; set ${cfg["token_env"]} to keep one): {token}')
        print(f'  open: {url}?token={token}')
    elif token and not local_only:
        print(f'  token from ${cfg["token_env"]}; open {url}?token=<token> once to sign in')
    sys.stdout.flush()
    if ns.open:
        webbrowser.open(url + (f'?token={token}' if generated else ''))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
