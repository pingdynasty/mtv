"""Actions: a fixed list of 10km / msm commands, run from mtv's own 10km checkout.

mtv never runs a loop's central/loop/bin or sources its host.env: the loop's
agents can change both. The checkout is the "tenkm" setting; LOOP_ROOT and
MSM_DB are set explicitly, and LOOP_SESSION is unset (msm treats a set
LOOP_SESSION as an agent's session). Every call is appended to an action log
in mtv's state directory with who asked, what ran and the result.
"""
import json
import os
import subprocess
import threading
import time

# name -> (needs write access, argv builder)
_LOCK = threading.Lock()


def _s(p, k):
    v = p.get(k, '')
    return '' if v is None else str(v)


def build(cmd, p):
    """Return (argv, writes) for an allowed command, or (None, None)."""
    s = lambda k: _s(p, k)
    if cmd == 'status':
        return ['10km', 'status'], False
    if cmd == 'doctor':
        return ['10km', 'doctor'], False
    if cmd == 'metrics':
        return ['10km', 'metrics'] + (['--cycle', s('cycle')] if s('cycle').isdigit() else []), False
    if cmd == 'pause':
        return (['10km', 'pause'] + (['--now'] if p.get('now') else []) + ([s('reason')] if s('reason') else [])), True
    if cmd == 'resume':
        return ['10km', 'resume'], True
    if cmd == 'stop':
        return ['10km', 'stop'] + (['--now'] if p.get('now') else []), True
    if cmd == 'dispatch':
        return ['10km', 'dispatch'], True
    if cmd == 'session-done':
        return (['10km', 'session', 'done', s('name')], True) if s('name') else (None, None)
    if cmd == 'session-start':
        # v0.1: operator sessions without an agent only (OPERATOR_AGENT lives in host.env).
        user = s('user')
        if not user:
            return None, None
        return ['10km', 'session', 'start', user, '--no-agent'], True
    if cmd in ('answer', 'resolve'):
        if not s('id') or not s('resolution').strip():
            return None, None
        status = 'answered' if cmd == 'answer' else 'resolved'
        res = s('resolution')
        if p.get('operator'):
            res = f'{res}\n\n(by {p["operator"]} via mtv)'
        return ['msm', 'issue', 'update', s('id'), json.dumps({'status': status, 'resolution': res})], True
    if cmd == 'backlog':
        if not s('title').strip():
            return None, None
        return ['msm', 'issue', 'create', json.dumps({
            'type': 'backlog', 'title': s('title'), 'description': s('description'),
            'assignee_role': 'planner', 'severity': s('severity') if s('severity') in ('low', 'high', 'blocking') else 'low',
        })], True
    return None, None


class Runner:
    def __init__(self, tenkm, state_dir, timeout=120):
        self.tenkm = tenkm
        self.state_dir = state_dir
        self.timeout = timeout

    def available(self):
        """(ok, reason)."""
        if not self.tenkm:
            return False, 'No 10km checkout configured ("tenkm" setting): actions are off.'
        b = os.path.join(self.tenkm, 'bin')
        for exe in ('10km', 'msm'):
            if not os.access(os.path.join(b, exe), os.X_OK):
                return False, f'{os.path.join(b, exe)} not found or not executable.'
        return True, ''

    def env(self, loop):
        env = {k: v for k, v in os.environ.items() if k not in ('LOOP_SESSION', 'MSM_DB', 'LOOP_ROOT')}
        env['LOOP_ROOT'] = loop.root
        env['MSM_DB'] = loop.db
        env['PATH'] = os.path.join(self.tenkm, 'bin') + os.pathsep + env.get('PATH', '')
        return env

    def run(self, loop, argv, who='', writes=False):
        ok, why = self.available()
        if not ok:
            return {'code': None, 'out': '', 'err': why, 'argv': argv}
        exe = os.path.join(self.tenkm, 'bin', argv[0])
        t0 = time.time()
        try:
            r = subprocess.run([exe] + argv[1:], cwd=loop.root, env=self.env(loop),
                               capture_output=True, text=True, timeout=self.timeout)
            res = {'code': r.returncode, 'out': r.stdout[-20000:], 'err': r.stderr[-6000:], 'argv': argv}
        except subprocess.TimeoutExpired:
            res = {'code': None, 'out': '', 'err': f'timed out after {self.timeout}s', 'argv': argv}
        except OSError as e:
            res = {'code': None, 'out': '', 'err': str(e), 'argv': argv}
        if writes:
            self.log(loop, who, argv, res, time.time() - t0)
        loop.invalidate()
        return res

    def log(self, loop, who, argv, res, secs):
        rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'loop': loop.id, 'root': loop.root,
               'operator': who or None, 'argv': argv, 'code': res.get('code'), 'secs': round(secs, 2),
               'err': (res.get('err') or '')[-500:] or None}
        with _LOCK:
            os.makedirs(self.state_dir, exist_ok=True)
            with open(os.path.join(self.state_dir, 'actions.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec) + '\n')
