"""Read-only access to one loop's state: loop.db, usage.jsonl, dispatch.json,
session agent.logs and the central/ repos. Never writes to the loop.
"""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

from . import SCHEMA_VERSIONS

ISSUE_SHORT = ('id, type, title, assigner_session, assigner_role, assignee_role, severity, status, '
               'created_at, answered_at, resolved_at')
TASK_SHORT = 'id, seq, cycle, shortname, branch, session, status, created_at, started_at, done_at, curated_at'
CYCLE_SHORT = 'id, status, planner_session, planned_at, headline, curator_session, curated_at'
LIST_CAP = 20
PAGE = 50


class LoopUnavailable(Exception):
    """The loop's database can't be read; the message says why, in plain words."""


def ts(s):
    """ISO-8601 (with Z or offset) to epoch seconds, or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def readf(p, default=''):
    try:
        with open(p, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return default


def alive(handle, wd=None):
    """True/False for a local process handle; None when it can't be known.

    No working directory on this machine means the pid can't be trusted
    (shown as 'folder missing').
    """
    if wd and not os.path.isdir(wd):
        return None
    m = re.match(r'process:(\d+)', handle or '')
    if not m:
        return None
    try:
        os.kill(int(m.group(1)), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True


class Usage:
    """Tails usage.jsonl incrementally and keeps the rows in memory."""
    KEYS = ('cycle', 'role', 'session', 'recorded_at', 'duration_ms', 'num_turns', 'input_tokens', 'output_tokens',
            'cache_read_input_tokens', 'cache_creation_input_tokens', 'total_cost_usd', 'credits', 'harness')

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.pos = 0
        self.ino = None
        self.rows = []
        self.by_session = {}

    def refresh(self):
        with self.lock:
            try:
                st = os.stat(self.path)
            except OSError:
                return
            if st.st_ino != self.ino or st.st_size < self.pos:   # rotated or truncated
                self.pos, self.ino, self.rows, self.by_session = 0, st.st_ino, [], {}
            if st.st_size == self.pos:
                return
            with open(self.path, 'rb') as f:
                f.seek(self.pos)
                data = f.read()
            end = data.rfind(b'\n') + 1          # whole lines only
            self.pos += end
            for line in data[:end].splitlines():
                try:
                    u = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(u, dict):
                    continue
                r = {k: u.get(k) for k in self.KEYS}
                for k in ('total_cost_usd', 'credits', 'duration_ms'):
                    if not isinstance(r[k], (int, float)) or isinstance(r[k], bool):
                        r[k] = None
                h = r.get('harness') or ''
                if not re.fullmatch(r'[A-Za-z0-9._-]+', str(h)):   # older loops logged e.g. 'VAR=value' here
                    r['harness'] = 'unknown'
                r['session'] = str(r['session'] or '')
                r['t'] = ts(r['recorded_at'])
                self.rows.append(r)
                self.by_session.setdefault(r['session'], []).append(r)

    def total(self):
        return sum(r['total_cost_usd'] or 0 for r in self.rows)

    def credits(self):
        return sum(r['credits'] or 0 for r in self.rows)


def _git(repo, *a):
    try:
        return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


class Loop:
    """One watched loop. Thread-safe for the HTTP server's reads."""

    def __init__(self, root, name=None, id=None, db=None, usage=None):
        self.root = os.path.abspath(root)
        self.name = name or os.path.basename(self.root)
        self.id = id or self.name
        self.dot = os.path.join(self.root, '.10km')
        self.db = db or os.path.join(self.dot, 'loop.db')
        self.usage = Usage(usage or os.path.join(self.dot, 'usage.jsonl'))
        self._repo_cache = {'t': 0, 'v': []}
        self._cols = None

    # ------------------------------------------------------------ database
    def connect(self):
        if not os.path.exists(self.db):
            raise LoopUnavailable(f'No loop database at {self.db}.')
        con = None
        try:
            con = sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=5)
            con.execute('select 1 from loop')
        except sqlite3.OperationalError as e:
            if con is not None:
                con.close()
            # WAL mode: a reader must create the -shm file beside the database.
            if 'unable to open' in str(e) or 'readonly' in str(e):
                if not os.access(os.path.dirname(self.db), os.W_OK):
                    raise LoopUnavailable(
                        f'Can\'t read {self.db}: it is in WAL mode, and mtv needs write access to '
                        f'{os.path.dirname(self.db)} for SQLite\'s -shm file. Run mtv as the loop\'s user '
                        f'or group, or mount the volume read-write.')
            if 'no such table' in str(e):
                raise LoopUnavailable(f'{self.db} has no loop table: is this a 10km loop?')
            raise LoopUnavailable(f'Can\'t read {self.db}: {e}')
        con.row_factory = sqlite3.Row
        return con

    def q(self, sql, a=()):
        con = self.connect()
        try:
            return [dict(r) for r in con.execute(sql, a)]
        finally:
            con.close()

    def q1(self, sql, a=()):
        r = self.q(sql, a)
        return r[0] if r else None

    def schema(self):
        """(user_version, supported, missing columns per table)."""
        con = self.connect()
        try:
            v = con.execute('pragma user_version').fetchone()[0]
            want = {
                'loop': ['current_cycle', 'phase', 'stopped'], 'sessions': ['name', 'role', 'status'],
                'cycles': ['id', 'status', 'headline'], 'tasks': ['id', 'cycle', 'status'],
                'issues': ['id', 'type', 'status'], 'audits': ['id', 'summary'],
            }
            missing = {}
            for t, cols in want.items():
                have = {r[1] for r in con.execute(f'pragma table_info("{t}")')}
                miss = [c for c in cols if c not in have] if have else ['(table)']
                if miss:
                    missing[t] = miss
            return v, v in SCHEMA_VERSIONS, missing
        finally:
            con.close()

    def signature(self):
        """Cheap change signature from file sizes and mtimes."""
        out = []
        for p in (self.db, self.db + '-wal', self.usage.path, os.path.join(self.dot, 'dispatch.json')):
            try:
                st = os.stat(p)
                out.append(f'{st.st_size}:{st.st_mtime_ns}')
            except OSError:
                out.append('-')
        return '|'.join(out)

    # ------------------------------------------------------------ repos
    def repos(self, max_age=30):
        if time.time() - self._repo_cache['t'] < max_age:
            return self._repo_cache['v']
        c = os.path.join(self.root, 'central')
        out = []
        for n in sorted(os.listdir(c)) if os.path.isdir(c) else []:
            p = os.path.join(c, n)
            if not os.path.exists(os.path.join(p, '.git')):
                continue
            h = _git(p, 'log', '-1', '--format=%h%x09%ct%x09%s')
            h, t, s = (h.split('\t') + ['', '', ''])[:3]
            dirty = len([l for l in _git(p, 'status', '--porcelain').splitlines() if l.strip()])
            ahead = behind = 0
            ab = _git(p, 'rev-list', '--left-right', '--count', '@{upstream}...HEAD')
            if ab:
                try:
                    behind, ahead = (int(x) for x in ab.split())
                except ValueError:
                    pass
            out.append({'name': n, 'branch': _git(p, 'rev-parse', '--abbrev-ref', 'HEAD'), 'commit': h,
                        'when': int(t) if t.isdigit() else None, 'message': s,
                        'dirty': dirty, 'ahead': ahead, 'behind': behind})
        self._repo_cache.update(t=time.time(), v=out)
        return out

    def invalidate(self):
        self._repo_cache['t'] = 0

    # ------------------------------------------------------------ views
    def _sessions(self):
        rows = self.q('select * from sessions')
        for s in rows:
            s['alive'] = alive(s.get('handle'), s.get('working_directory')) if s['status'] == 'active' else None
        return rows

    def last_session_at(self, sessions):
        """When a session last ended, from loop.db and usage.jsonl (None if never).

        Deliberately not "now" for an active session: the live state must not
        change on its own, or its ETag never matches. The page checks
        sessions for active ones itself.
        """
        ends = [r['t'] for r in self.usage.rows if r['t']] + [ts(s['updated_at']) for s in sessions]
        ends = [e for e in ends if e]
        return max(ends) if ends else None

    def live(self):
        """What the always-visible parts of the page need; small at any loop size."""
        self.usage.refresh()
        loop = self.q1('select * from loop') or {}
        cur = loop.get('current_cycle')
        sessions = self._sessions()
        counts = {f"{r['type']}:{r['status']}": r['n']
                  for r in self.q('select type, status, count(*) n from issues group by 1, 2')}
        tstat = {r['status']: r['n'] for r in self.q('select status, count(*) n from tasks group by 1')}
        U = self.usage.rows
        return {
            'id': self.id, 'root': self.root, 'name': self.name,
            'loop': loop,
            'dispatch': json.loads(readf(os.path.join(self.dot, 'dispatch.json'), '{}') or '{}'),
            'cycle': (self.q1(f'select {CYCLE_SHORT} from cycles where id = ?', (cur,))
                      or self.q1(f'select {CYCLE_SHORT} from cycles order by id desc limit 1')),
            'cycle_tasks': self.q(f'select {TASK_SHORT} from tasks where cycle = '
                                  '(select coalesce(?, max(id)) from cycles) order by seq', (cur,)),
            'sessions': sessions,
            'escalations': self.q(f"select {ISSUE_SHORT}, substr(description, 1, 600) description from issues "
                                  "where type = 'escalation' and status = 'open' "
                                  "order by case severity when 'blocking' then 0 when 'high' then 1 else 2 end, "
                                  "created_at"),
            'backlog': self.q(f"select {ISSUE_SHORT} from issues where type = 'backlog' and status = 'open' "
                              "order by created_at desc limit ?", (LIST_CAP,)),
            'audits': self.q('select id, created_at from audits order by id desc limit ?', (LIST_CAP,)),
            'counts': {
                'cycles': (self.q1('select count(*) n from cycles') or {}).get('n', 0),
                'tasks': sum(tstat.values()), 'task_status': tstat,
                'issues': sum(counts.values()), 'issue_status': counts,
                'backlog_open': counts.get('backlog:open', 0),
                'audits': (self.q1('select count(*) n from audits') or {}).get('n', 0),
                'last_audit_at': (self.q1('select max(created_at) t from audits') or {}).get('t'),
                'usage_sessions': len(U), 'cost': round(self.usage.total(), 4),
                'credits': round(self.usage.credits(), 2),
                'cycle_cost': round(sum(r['total_cost_usd'] or 0 for r in U if r['cycle'] == cur), 4),
            },
            'last_session_at': self.last_session_at(sessions),
            'repos': self.repos(),
        }

    @staticmethod
    def _page(a, default=PAGE):
        try:
            off = max(0, int(a.get('offset') or 0))
            lim = min(500, max(1, int(a.get('limit') or default)))
        except ValueError:
            off, lim = 0, default
        return off, lim

    def cycles_page(self, a):
        off, lim = self._page(a)
        where, args = [], []
        if a.get('q'):
            where.append('(headline like ? or report like ?)')
            args += ['%' + a['q'] + '%'] * 2
        w = (' where ' + ' and '.join(where)) if where else ''
        total = self.q1(f'select count(*) n from cycles{w}', args)['n']
        rows = self.q(f'select {CYCLE_SHORT}, (select count(*) from tasks t where t.cycle = c.id) ntasks '
                      f'from cycles c{w} order by id desc limit ? offset ?', args + [lim, off])
        self.usage.refresh()
        cost, cred = {}, {}
        for r in self.usage.rows:
            cost[r['cycle']] = cost.get(r['cycle'], 0) + (r['total_cost_usd'] or 0)
            cred[r['cycle']] = cred.get(r['cycle'], 0) + (r['credits'] or 0)
        for r in rows:
            r['cost'] = round(cost.get(r['id'], 0), 2)
            r['credits'] = round(cred.get(r['id'], 0), 2)
        return {'total': total, 'offset': off, 'rows': rows}

    def issues_page(self, a):
        off, lim = self._page(a)
        where, args = [], []
        for k in ('type', 'status', 'severity', 'assignee_role', 'assigner_role'):
            if a.get(k) and a[k] != 'all':
                where.append(f'{k} = ?')
                args.append(a[k])
        if a.get('q'):
            where.append('(title like ? or description like ? or resolution like ?)')
            args += ['%' + a['q'] + '%'] * 3
        w = (' where ' + ' and '.join(where)) if where else ''
        total = self.q1(f'select count(*) n from issues{w}', args)['n']
        rows = self.q(f'select {ISSUE_SHORT} from issues{w} order by created_at desc limit ? offset ?',
                      args + [lim, off])
        return {'total': total, 'offset': off, 'rows': rows}

    def audits_list(self):
        return self.q('select id, created_at, period, substr(summary, 1, 200) summary from audits order by id desc')

    def cycle(self, id_):
        c = self.q1('select * from cycles where id = ?', (id_,))
        if not c:
            return None
        c['tasks'] = self.q(f'select {TASK_SHORT} from tasks where cycle = ? order by seq', (id_,))
        self.usage.refresh()
        c['usage'] = [r for r in self.usage.rows if r['cycle'] == c['id']]
        return c

    def task(self, id_):
        return self.q1('select * from tasks where id = ?', (id_,))

    def issue(self, id_):
        return self.q1('select * from issues where id = ?', (id_,))

    def audit(self, id_):
        return self.q1('select * from audits where id = ?', (id_,))

    def session(self, name):
        self.usage.refresh()
        s = self.q1('select * from sessions where name = ?', (name,))
        if s and s['status'] == 'active':
            s['alive'] = alive(s.get('handle'), s.get('working_directory'))
        us = self.usage.by_session.get(name, [])
        t = self.q1(f'select {TASK_SHORT} from tasks where session = ?', (name,))
        if not s and not us and not t:
            return None
        return {'session': s, 'usage': us, 'task': t, 'name': name}

    def metrics(self, a):
        """Tasks and cost per cycle, hour, day or month.

        Days and months are local calendar days and months (daylight saving
        included); keys are the epoch seconds of each bucket's local start.
        """
        by = a.get('by', 'cycle')
        if by not in ('cycle', 'hour', 'day', 'month'):
            by = 'cycle'
        self.usage.refresh()
        tasks = self.q('select cycle, status, coalesce(curated_at, done_at, started_at) t from tasks')

        def fl(t):
            if t is None:
                return None
            if by == 'hour':
                return int(t // 3600 * 3600)
            d = time.localtime(t)
            return int(time.mktime((d.tm_year, d.tm_mon, 1 if by == 'month' else d.tm_mday, 0, 0, 0, 0, 0, -1)))

        key_t = (lambda r: r['cycle']) if by == 'cycle' else (lambda r: fl(ts(r['t'])))
        key_u = (lambda r: r['cycle']) if by == 'cycle' else (lambda r: fl(r['t']))
        empty = lambda: {'accepted': 0, 'failed': 0, 'progress': 0, 'cost': 0.0, 'credits': 0.0}
        b = {}
        for r in tasks:
            k = key_t(r)
            if k is None:
                continue
            e = b.setdefault(k, empty())
            e['accepted' if r['status'] == 'accepted'
              else 'failed' if r['status'] in ('expired', 'rejected') else 'progress'] += 1
        roles, rcred = {}, {}
        for r in self.usage.rows:
            c, cr = r['total_cost_usd'] or 0, r['credits'] or 0
            if r['role']:
                roles[r['role']] = roles.get(r['role'], 0) + c
                rcred[r['role']] = rcred.get(r['role'], 0) + cr
            k = key_u(r)
            if k is None:
                continue
            e = b.setdefault(k, empty())
            e['cost'] += c
            e['credits'] += cr
        keys = sorted(b)
        if by != 'cycle' and keys:            # continuous axis: fill empty hours/days/months
            full, k = [], keys[0]
            while k <= keys[-1] and len(full) < 20000:
                full.append(k)
                b.setdefault(k, empty())
                if by == 'hour':
                    k += 3600
                else:
                    d = time.localtime(k)
                    y, m, dd = d.tm_year, d.tm_mon, d.tm_mday
                    if by == 'month':
                        y, m, dd = (y + 1, 1, 1) if m == 12 else (y, m + 1, 1)
                    else:
                        dd += 1                 # mktime normalises day 32 etc.
                    k = int(time.mktime((y, m, dd, 0, 0, 0, 0, 0, -1)))
            keys = full
        return {'by': by, 'keys': keys, 'buckets': [b[k] for k in keys],
                'roles': sorted(([r, round(c, 2), round(rcred.get(r, 0), 2)] for r, c in roles.items()),
                                key=lambda x: (-(x[1] or 0), -(x[2] or 0))),
                'total': round(self.usage.total(), 2), 'credits': round(self.usage.credits(), 2),
                'sessions': len(self.usage.rows)}

    def timeline(self, a):
        """Session spans overlapping [from, to] (epoch seconds), from loop.db and usage.jsonl."""
        self.usage.refresh()
        now = time.time()
        try:
            to = float(a.get('to') or now)
            fr = float(a.get('from') or 0)
        except ValueError:
            to, fr = now, 0
        spans = {}

        def span(n, role, cycle, harness):
            return spans.setdefault(n, {'n': n, 'role': role, 'cycle': cycle, 's': None, 'e': None,
                                        'active': False, 'harness': harness})
        for r in self.usage.rows:
            if not r['t']:
                continue
            e = span(r['session'], r['role'], r['cycle'], r['harness'])
            st = r['t'] - (r['duration_ms'] or 0) / 1000
            e['s'] = st if e['s'] is None else min(e['s'], st)
            e['e'] = r['t'] if e['e'] is None else max(e['e'], r['t'])
        for x in self.q('select name, role, cycle, status, created_at, updated_at from sessions'):
            e = span(x['name'], x['role'], x['cycle'], None)
            c, u = ts(x['created_at']), ts(x['updated_at'])
            if c:
                e['s'] = c if e['s'] is None else min(e['s'], c)
            if x['status'] == 'active':
                e['active'], e['e'] = True, now
            elif u:
                e['e'] = u if e['e'] is None else max(e['e'], u)
        rows = [e for e in spans.values() if e['s'] is not None and e['e'] is not None and e['e'] >= fr and e['s'] <= to]
        rows.sort(key=lambda e: e['s'])
        start = (self.q1('select min(planned_at) t from cycles') or {}).get('t')
        cands = ([ts(start)] if start else []) + [e['s'] for e in spans.values() if e['s']]
        first = min([c for c in cands if c] or [now])
        return {'from': fr, 'to': to, 'start': first, 'total': len(rows), 'rows': rows[-3000:]}

    def docs(self):
        return {n: readf(os.path.join(self.root, 'central', 'loop', n + '.md')) for n in ('GOALS', 'PLAN', 'LEARNINGS')}

    def agent_log(self, name, tail=2_000_000):
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name or ''):
            return {'exists': False, 'events': []}
        p = os.path.join(self.root, name, 'agent.log')
        if not os.path.exists(p):
            return {'exists': False, 'events': []}
        size = os.path.getsize(p)
        with open(p, 'rb') as f:
            if size > tail:
                f.seek(size - tail)
                f.readline()
            raw = f.read().decode('utf-8', 'replace')
        out = []
        for line in raw.splitlines()[-2000:]:
            try:
                j = json.loads(line)
            except ValueError:
                if line.strip():
                    out.append({'k': 'text', 't': line[:2000]})
                continue
            if not isinstance(j, dict):
                continue
            typ = j.get('type')
            if typ == 'assistant':
                for c in (j.get('message') or {}).get('content') or []:
                    if c.get('type') == 'text' and c.get('text', '').strip():
                        out.append({'k': 'say', 't': c['text'][:4000]})
                    elif c.get('type') == 'tool_use':
                        inp = c.get('input') or {}
                        arg = inp.get('command') or inp.get('file_path') or inp.get('path') or inp.get('pattern') or ''
                        out.append({'k': 'tool', 't': f"{c.get('name')} {str(arg)[:300]}"})
            elif typ == 'result' or 'result' in j and 'session_id' in j:
                out.append({'k': 'result', 't': str(j.get('result') or '')[:8000],
                            'cost': j.get('total_cost_usd'), 'ms': j.get('duration_ms'), 'turns': j.get('num_turns')})
        return {'exists': True, 'events': out, 'truncated': size > tail}
