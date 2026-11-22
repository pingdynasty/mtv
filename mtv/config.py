"""Settings: a JSON file with the loops to watch and how to run 10km.

Looked up in this order: --config PATH, $MTV_CONFIG, ~/.config/mtv/config.json.
Command-line loop roots are added to the file's loops. Example:

    {
      "loops": [
        {"name": "bruckert", "root": "~/devel/bruckert-10km"},
        {"name": "pdlang",   "root": "~/devel/pdlang-10km"}
      ],
      "tenkm": "~/10km",
      "host": "127.0.0.1",
      "port": 8710,
      "allow_actions": false,
      "operator": "martin",
      "token_env": "MTV_TOKEN"
    }

"tenkm" is a 10km checkout mtv runs `10km` and `msm` from. It must not be a
loop's own central/loop, which the loop's agents can change. Without it,
actions are unavailable (read-only).
"""
import json
import os
import re

DEFAULTS = {
    'loops': [],
    'tenkm': '',
    'host': '127.0.0.1',
    'port': 8710,
    'allow_actions': False,
    'operator': '',
    'token_env': 'MTV_TOKEN',
    'state_dir': '~/.local/state/mtv',
}


class ConfigError(Exception):
    pass


def default_path():
    return os.environ.get('MTV_CONFIG') or os.path.expanduser('~/.config/mtv/config.json')


def slug(s):
    return re.sub(r'[^a-z0-9-]+', '-', s.lower()).strip('-') or 'loop'


def load(path=None, roots=(), overrides=None):
    """Return the settings dict. Missing file is fine when roots are given."""
    cfg = dict(DEFAULTS)
    p = path or default_path()
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
        except ValueError as e:
            raise ConfigError(f'{p}: not valid JSON ({e})')
        if not isinstance(data, dict):
            raise ConfigError(f'{p}: expected a JSON object')
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ConfigError(f'{p}: unknown settings: {", ".join(sorted(unknown))}')
        cfg.update(data)
    elif path:
        raise ConfigError(f'{p}: no such file')
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})

    loops = []
    for i, e in enumerate(list(cfg['loops']) + [{'root': r} for r in roots]):
        if isinstance(e, str):
            e = {'root': e}
        if not isinstance(e, dict) or not e.get('root'):
            raise ConfigError(f'loops[{i}]: needs a "root"')
        root = os.path.abspath(os.path.expanduser(e['root']))
        if any(l['root'] == root for l in loops):
            continue
        loops.append({'name': e.get('name') or os.path.basename(root), 'root': root})
    seen = {}
    for l in loops:                      # unique, URL-safe ids
        base = slug(l['name'])
        n = seen.get(base, 0)
        seen[base] = n + 1
        l['id'] = base if not n else f'{base}-{n + 1}'
    cfg['loops'] = loops
    cfg['tenkm'] = os.path.abspath(os.path.expanduser(cfg['tenkm'])) if cfg['tenkm'] else ''
    cfg['state_dir'] = os.path.abspath(os.path.expanduser(cfg['state_dir']))
    cfg['port'] = int(cfg['port'])
    for l in loops:
        if cfg['tenkm'] and os.path.realpath(cfg['tenkm']).startswith(os.path.realpath(l['root']) + os.sep):
            raise ConfigError(f'"tenkm" is inside loop {l["name"]}: use a 10km checkout the loop\'s agents can\'t change')
    return cfg
