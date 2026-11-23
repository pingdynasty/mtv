"""HTTP server: static page, JSON API per loop, actions.

    GET  /                          the page
    GET  /static/...                bundled assets (ECharts, fonts)
    GET  /api/loops                 watched loops and their state in one line each
    GET  /api/<loop>/live           small live state, with an ETag (304 when unchanged)
    GET  /api/<loop>/{cycles,issues,cycle,task,issue,audit,audits,session,metrics,timeline,docs,log,status}
    POST /api/<loop>/action/<name>  run an allowed 10km / msm command (when actions are on)

Auth: requests from 127.0.0.1 need no token. Anything else needs
"Authorization: Bearer <token>" (or ?token= for the first page load, which
sets a cookie). With no token configured, only local requests are served,
unless the app is started with open_access (mtv --no-token).
"""
import hashlib
import hmac
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__
from .actions import build
from .data import LoopUnavailable

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


class App:
    def __init__(self, cfg, loops, runner, token=None, open_access=False):
        self.cfg = cfg
        self.loops = {l.id: l for l in loops}
        self.order = [l.id for l in loops]
        self.runner = runner
        self.token = token or None
        self.open_access = bool(open_access)
        self.allow = bool(cfg.get('allow_actions'))
        self.operator = cfg.get('operator') or ''
        self._live = {}
        self._lock = threading.Lock()

    def actions_state(self, loop=None):
        if not self.allow:
            return {'enabled': False, 'reason': 'Read-only: start mtv with --allow-actions to enable actions.'}
        ok, why = self.runner.available(loop)
        return {'enabled': ok, 'reason': why}

    def live(self, loop):
        """Cached live JSON body and ETag; rebuilt when files change or every 15 s."""
        sig = loop.signature()
        with self._lock:
            c = self._live.get(loop.id)
            if c and c['sig'] == sig and time.time() - c['t'] < 15:
                return c['body'], c['etag']
        d = loop.live()
        d['actions'] = self.actions_state(loop)
        d['loops'] = [{'id': i, 'name': self.loops[i].name} for i in self.order]
        d['version'] = __version__
        body = json.dumps(d, default=str, separators=(',', ':')).encode()
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        with self._lock:
            self._live[loop.id] = {'sig': sig, 't': time.time(), 'body': body, 'etag': etag}
        return body, etag

    def forget(self, loop):
        with self._lock:
            self._live.pop(loop.id, None)

    def summary(self):
        out = []
        for i in self.order:
            l = self.loops[i]
            e = {'id': i, 'name': l.name, 'root': l.root}
            try:
                lp = l.q1('select current_cycle, phase, stopped, reason from loop') or {}
                e.update(lp)
                e['escalations'] = (l.q1("select count(*) n from issues where type = 'escalation' "
                                         "and status = 'open' and assignee_role = 'operator'") or {}).get('n', 0)
                v, ok, missing = l.schema()
                e['schema'] = {'version': v, 'supported': ok, 'missing': missing}
            except LoopUnavailable as ex:
                e['error'] = str(ex)
            out.append(e)
        return out


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = f'mtv/{__version__}'

        def log_message(self, *a):
            pass

        # ------------------------------------------------------------ helpers
        def send(self, code, body, ctype='application/json', etag=None, extra=None):
            if not isinstance(body, bytes):
                body = (json.dumps(body, default=str, separators=(',', ':')) if ctype == 'application/json'
                        else body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text/', 'application/j')) else ''))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if etag:
                self.send_header('ETag', etag)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def local(self):
            return self.client_address[0] in ('127.0.0.1', '::1', '::ffff:127.0.0.1')

        def authed(self, q):
            if app.open_access:
                return True
            if self.local() and not app.token:
                return True
            if not app.token:
                return False
            got = ''
            h = self.headers.get('Authorization', '')
            if h.startswith('Bearer '):
                got = h[7:].strip()
            if not got:
                for part in (self.headers.get('Cookie') or '').split(';'):
                    k, _, v = part.strip().partition('=')
                    if k == 'mtv_token':
                        got = v
            got = got or q.get('token', '')
            return bool(got) and hmac.compare_digest(got, app.token)

        def same_origin(self):
            o = self.headers.get('Origin')
            if o is None:
                return True
            host = self.headers.get('Host', '')
            return urlparse(o).netloc == host

        # ------------------------------------------------------------ GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urlparse(self.path)
            a = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not self.authed(a):
                return self.send(401, {'error': 'unauthorized: pass a token'})
            extra = {}
            if a.get('token') and app.token and hmac.compare_digest(a['token'], app.token):
                extra['Set-Cookie'] = f'mtv_token={app.token}; HttpOnly; SameSite=Strict; Path=/'
            try:
                if u.path in ('/', '/index.html'):
                    with open(os.path.join(STATIC, 'index.html'), 'rb') as f:
                        return self.send(200, f.read(), 'text/html', extra=extra)
                if u.path.startswith('/static/'):
                    return self.static(u.path[len('/static/'):])
                if u.path == '/api/loops':
                    return self.send(200, {'version': __version__, 'loops': app.summary(), 'actions': app.actions_state()})
                parts = u.path.split('/')
                if len(parts) >= 4 and parts[1] == 'api':
                    loop = app.loops.get(parts[2])
                    if not loop:
                        return self.send(404, {'error': f'no loop {parts[2]!r}'})
                    return self.api(loop, '/'.join(parts[3:]), a)
                self.send(404, {'error': 'not found'})
            except LoopUnavailable as e:
                self.send(503, {'error': str(e), 'unavailable': True})
            except Exception as e:                      # keep the server up; report the failure
                self.send(500, {'error': f'{type(e).__name__}: {e}'})

        def static(self, rel):
            p = os.path.normpath(os.path.join(STATIC, rel))
            if not p.startswith(STATIC + os.sep) or not os.path.isfile(p):
                return self.send(404, {'error': 'not found'})
            ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
            with open(p, 'rb') as f:
                body = f.read()
            self.send(200, body, ctype, extra={'Cache-Control': 'public, max-age=86400'})

        def api(self, loop, path, a):
            if path == 'live':
                body, etag = app.live(loop)
                if self.headers.get('If-None-Match') == etag:
                    self.send_response(304)
                    self.send_header('ETag', etag)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                return self.send(200, body, etag=etag)
            num = lambda k: int(a.get(k) or 0) if str(a.get(k) or '0').lstrip('-').isdigit() else None
            routes = {
                'cycles': lambda: loop.cycles_page(a),
                'issues': lambda: loop.issues_page(a),
                'audits': loop.audits_list,
                'cycle': lambda: loop.cycle(num('id')),
                'task': lambda: loop.task(a.get('id')),
                'issue': lambda: loop.issue(a.get('id')),
                'audit': lambda: loop.audit(num('id')),
                'session': lambda: loop.session(a.get('name')),
                'metrics': lambda: loop.metrics(a),
                'timeline': lambda: loop.timeline(a),
                'docs': loop.docs,
                'log': lambda: loop.agent_log(a.get('session')),
                'status': lambda: app.runner.run(loop, ['10km', 'status']),
            }
            if path not in routes:
                return self.send(404, {'error': 'not found'})
            r = routes[path]()
            return self.send(200 if r is not None else 404, r if r is not None else {'error': 'not found'})

        # ------------------------------------------------------------ POST
        def do_POST(self):
            u = urlparse(self.path)
            a = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not self.authed(a):
                return self.send(401, {'error': 'unauthorized: pass a token'})
            if not self.same_origin():
                return self.send(403, {'error': 'cross-origin request refused'})
            parts = u.path.split('/')
            if len(parts) != 5 or parts[1] != 'api' or parts[3] != 'action':
                return self.send(404, {'error': 'not found'})
            loop = app.loops.get(parts[2])
            if not loop:
                return self.send(404, {'error': f'no loop {parts[2]!r}'})
            try:
                n = int(self.headers.get('Content-Length') or 0)
                p = json.loads(self.rfile.read(min(n, 1_000_000)) or b'{}')
                if not isinstance(p, dict):
                    raise ValueError
            except ValueError:
                return self.send(400, {'error': 'bad json'})
            p['operator'] = app.operator
            argv, writes = build(parts[4], p)
            if not argv:
                return self.send(400, {'error': f'unknown command or missing argument: {parts[4]}'})
            st = app.actions_state(loop)
            if writes and not st['enabled']:
                return self.send(403, {'error': st['reason'], 'argv': argv})
            r = app.runner.run(loop, argv, who=app.operator, writes=writes)
            app.forget(loop)
            self.send(200, r)

    return H


def serve(app, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
