"""Unit tests: data layer, config, actions, HTTP server. Standard library only.

    python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures import make_loop                                     # noqa: E402
from mtv import config                                              # noqa: E402
from mtv.actions import Runner, build                               # noqa: E402
from mtv.data import Loop, LoopUnavailable, Usage, alive            # noqa: E402
from mtv.server import App, serve                                   # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='mtv-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def loop(self, kind='basic', name=None):
        root = make_loop(os.path.join(self.tmp, name or kind), kind)
        return Loop(root, name or kind, name or kind)


class TestData(Tmp):
    def test_live_basic(self):
        L = self.loop().live()
        self.assertEqual(L['loop']['current_cycle'], 3)
        self.assertEqual(L['cycle']['id'], 3)
        self.assertEqual([t['shortname'] for t in L['cycle_tasks']], ['t3a', 't3b'])
        self.assertEqual(len(L['escalations']), 1)
        self.assertEqual(L['escalations'][0]['severity'], 'blocking')
        self.assertEqual(L['counts']['backlog_open'], 1)
        self.assertAlmostEqual(L['counts']['cost'], 3 * (2.5 + 5.0 + 0.5))
        self.assertEqual(L['counts']['credits'], 0)
        self.assertEqual(L['dispatch']['result'], 'ok')
        self.assertTrue(L['last_session_at'])                # an active session: now
        self.assertNotIn('instructions', L['cycle_tasks'][0])   # live state stays small
        self.assertLess(len(json.dumps(L, default=str)), 20000)

    def test_live_mixed(self):
        L = self.loop('mixed').live()
        s = [x for x in L['sessions'] if x['status'] == 'active'][0]
        self.assertIsNone(s['alive'])                        # folder missing: can't be checked
        self.assertAlmostEqual(L['counts']['credits'], 35.5)
        self.assertAlmostEqual(L['counts']['cost'], 0.58 + 1.0 + 0.3)

    def test_fresh_loop(self):
        lp = self.loop('fresh')
        L = lp.live()
        self.assertIsNone(L['cycle'])
        self.assertEqual(L['cycle_tasks'], [])
        self.assertEqual(lp.metrics({'by': 'day'})['keys'], [])
        self.assertEqual(lp.timeline({})['rows'], [])

    def test_usage_parsing(self):
        lp = self.loop('mixed')
        lp.usage.refresh()
        h = {r['session']: r['harness'] for r in lp.usage.rows}
        self.assertEqual(h['cycle11-curator-k'], 'unknown')     # 'VAR=value' harness
        self.assertEqual(h['cycle11-worker1-k'], 'kiro-cli')
        self.assertEqual(len(lp.usage.rows), 5)                 # partial last line ignored

    def test_usage_incremental_and_truncation(self):
        p = os.path.join(self.tmp, 'u.jsonl')
        with open(p, 'w') as f:
            f.write(json.dumps({'session': 'a', 'total_cost_usd': 1}) + '\n')
        u = Usage(p)
        u.refresh()
        with open(p, 'a') as f:
            f.write(json.dumps({'session': 'b', 'total_cost_usd': 2}) + '\n')
        u.refresh()
        self.assertEqual(u.total(), 3)
        with open(p, 'w') as f:                                  # rotated: start over
            f.write(json.dumps({'session': 'c', 'total_cost_usd': 5}) + '\n')
        u.refresh()
        self.assertEqual(u.total(), 5)

    def test_metrics_all_groupings_agree(self):
        lp = self.loop()
        tot = {}
        for by in ('cycle', 'hour', 'day', 'month'):
            m = lp.metrics({'by': by})
            self.assertEqual(m['by'], by)
            self.assertEqual(len(m['keys']), len(m['buckets']))
            tot[by] = (sum(b['accepted'] for b in m['buckets']), round(sum(b['cost'] for b in m['buckets']), 2))
        self.assertEqual(len(set(tot.values())), 1, tot)
        self.assertEqual(tot['cycle'], (4, 24.0))    # 6 tasks: one active, one rejected
        self.assertEqual(lp.metrics({'by': 'bogus'})['by'], 'cycle')
        days = lp.metrics({'by': 'day'})['keys']
        self.assertEqual(len(days), 3)                           # Nov 18, 19, 20, gaps filled
        for k in days:
            self.assertEqual(time.localtime(k).tm_hour, 0)       # local midnight
        m = lp.metrics({'by': 'month'})
        self.assertEqual(len(m['keys']), 1)
        self.assertEqual(time.localtime(m['keys'][0]).tm_mday, 1)

    def test_metrics_credits_by_role(self):
        m = self.loop('mixed').metrics({'by': 'cycle'})
        roles = {r[0]: r for r in m['roles']}
        self.assertEqual(roles['worker'][2], 35.5)
        self.assertEqual(roles['worker'][1], 0)

    def test_pages_and_search(self):
        lp = self.loop('mixed')
        p = lp.cycles_page({'limit': '5'})
        self.assertEqual((p['total'], len(p['rows']), p['rows'][0]['id']), (12, 5, 12))
        self.assertEqual(lp.cycles_page({'offset': '10', 'limit': '5'})['rows'][-1]['id'], 1)
        self.assertEqual(lp.cycles_page({'q': 'real work'})['total'], 2)
        self.assertEqual(lp.cycles_page({'offset': 'x'})['offset'], 0)
        b = self.loop()
        self.assertEqual(b.issues_page({'type': 'backlog', 'status': 'open'})['total'], 1)
        self.assertEqual(b.issues_page({'q': 'license'})['total'], 1)
        self.assertEqual(b.issues_page({'type': 'all', 'status': 'all'})['total'], 3)

    def test_details(self):
        lp = self.loop()
        c = lp.cycle(2)
        self.assertEqual(len(c['tasks']), 2)
        self.assertEqual(len(c['usage']), 3)
        self.assertIsNone(lp.cycle(99))
        self.assertIn('instructions', lp.task('1-1-0000-0000'))
        self.assertEqual(lp.issue('e1-0000')['severity'], 'blocking')
        self.assertIn('GREEN', lp.audit(1)['summary'])
        s = lp.session('cycle1-worker1-cccc')                    # only in usage.jsonl (removed)
        self.assertIsNone(s['session'])
        self.assertEqual(len(s['usage']), 1)
        self.assertIsNone(lp.session('nope'))

    def test_timeline(self):
        lp = self.loop()
        t = lp.timeline({})
        names = [r['n'] for r in t['rows']]
        self.assertIn('cycle3-worker1-cccc', names)
        act = [r for r in t['rows'] if r['n'] == 'cycle3-worker1-cccc'][0]
        self.assertTrue(act['active'])
        w = [r for r in t['rows'] if r['n'] == 'cycle1-worker1-cccc'][0]
        self.assertAlmostEqual(w['e'] - w['s'], 3000)            # from duration_ms
        recent = lp.timeline({'from': str(time.time() - 60)})
        self.assertEqual([r['n'] for r in recent['rows']], ['cycle3-worker1-cccc'])

    def test_agent_log(self):
        lp = self.loop()
        r = lp.agent_log('cycle3-worker1-cccc')
        self.assertTrue(r['exists'])
        self.assertEqual([e['k'] for e in r['events']], ['text', 'say', 'tool', 'result'])
        self.assertFalse(lp.agent_log('../etc')['exists'])
        self.assertFalse(lp.agent_log('nope')['exists'])

    def test_schema(self):
        v, ok, missing = self.loop().schema()
        self.assertEqual((v, ok, missing), (2, True, {}))

    def test_unavailable(self):
        lp = Loop(os.path.join(self.tmp, 'nothing'))
        with self.assertRaises(LoopUnavailable):
            lp.live()
        bad = os.path.join(self.tmp, 'bad', '.10km')
        os.makedirs(bad)
        import sqlite3
        sqlite3.connect(os.path.join(bad, 'loop.db')).execute('create table x (a)').connection.commit()
        with self.assertRaises(LoopUnavailable) as e:
            Loop(os.path.dirname(bad)).live()
        self.assertIn('10km loop', str(e.exception))

    def test_alive(self):
        self.assertIsNone(alive('process:1:x', '/nonexistent/dir'))
        self.assertIsNone(alive(None, self.tmp))
        self.assertTrue(alive(f'process:{os.getpid()}:x', self.tmp))
        self.assertFalse(alive('process:999999999:x', self.tmp))


class TestConfig(Tmp):
    def test_load_and_roots(self):
        p = os.path.join(self.tmp, 'c.json')
        with open(p, 'w') as f:
            json.dump({'loops': [{'name': 'My Loop', 'root': '/x/a'}, '/x/b'], 'port': '9000'}, f)
        cfg = config.load(p, roots=['/x/b', '/x/c'])
        self.assertEqual([l['id'] for l in cfg['loops']], ['my-loop', 'b', 'c'])   # dedupe, slug
        self.assertEqual(cfg['port'], 9000)

    def test_errors(self):
        p = os.path.join(self.tmp, 'c.json')
        for body, msg in (('{', 'not valid JSON'), ('[]', 'JSON object'), ('{"lops": []}', 'unknown settings'),
                          ('{"loops": [{"name": "x"}]}', 'needs a "root"')):
            with open(p, 'w') as f:
                f.write(body)
            with self.assertRaises(config.ConfigError) as e:
                config.load(p)
            self.assertIn(msg, str(e.exception))
        with self.assertRaises(config.ConfigError):
            config.load(os.path.join(self.tmp, 'missing.json'))

    def test_tenkm_inside_loop_refused(self):
        root = os.path.join(self.tmp, 'L')
        os.makedirs(os.path.join(root, 'central', 'loop'))
        with self.assertRaises(config.ConfigError):
            config.load(None, [root], {'tenkm': os.path.join(root, 'central', 'loop')})

    def test_duplicate_names(self):
        cfg = config.load(None, [os.path.join(self.tmp, 'a', 'x'), os.path.join(self.tmp, 'b', 'x')])
        self.assertEqual([l['id'] for l in cfg['loops']], ['x', 'x-2'])


class TestActions(Tmp):
    def test_allow_list(self):
        self.assertEqual(build('resume', {}), (['10km', 'resume'], True))
        self.assertEqual(build('status', {}), (['10km', 'status'], False))
        self.assertEqual(build('pause', {'now': 1, 'reason': 'x'}), (['10km', 'pause', '--now', 'x'], True))
        self.assertEqual(build('rm -rf', {}), (None, None))
        self.assertEqual(build('answer', {'id': 'e1'}), (None, None))         # needs a resolution
        argv, w = build('answer', {'id': 'e1', 'resolution': 'Yes', 'operator': 'martin'})
        self.assertEqual(argv[:4], ['msm', 'issue', 'update', 'e1'])
        body = json.loads(argv[4])
        self.assertEqual(body['status'], 'answered')
        self.assertIn('by martin via mtv', body['resolution'])
        self.assertEqual(build('session-start', {'user': 'm'})[0], ['10km', 'session', 'start', 'm', '--no-agent'])
        self.assertEqual(json.loads(build('backlog', {'title': 't', 'severity': 'weird'})[0][3])['severity'], 'low')

    def fake_tenkm(self):
        d = os.path.join(self.tmp, '10km', 'bin')
        os.makedirs(d)
        for exe in ('10km', 'msm'):
            p = os.path.join(d, exe)
            with open(p, 'w') as f:
                f.write('#!/bin/sh\necho "$0 $* root=$LOOP_ROOT db=$MSM_DB session=${LOOP_SESSION:-none}"\n')
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        return os.path.dirname(d)

    def test_runner_env_and_log(self):
        lp = self.loop()
        state = os.path.join(self.tmp, 'state')
        r = Runner(self.fake_tenkm(), state)
        os.environ['LOOP_SESSION'] = 'agent-session'
        try:
            res = r.run(lp, ['10km', 'resume'], who='martin', writes=True)
        finally:
            del os.environ['LOOP_SESSION']
        self.assertEqual(res['code'], 0)
        self.assertIn(f'root={lp.root}', res['out'])
        self.assertIn(f'db={lp.db}', res['out'])
        self.assertIn('session=none', res['out'])               # LOOP_SESSION is never passed on
        self.assertIn('/10km/bin/10km', res['out'])             # mtv's checkout, not the loop's
        with open(os.path.join(state, 'actions.jsonl')) as f:
            rec = json.loads(f.readline())
        self.assertEqual((rec['operator'], rec['argv'], rec['code']), ('martin', ['10km', 'resume'], 0))

    def test_runner_unavailable(self):
        self.assertFalse(Runner('', self.tmp).available()[0])
        self.assertFalse(Runner(os.path.join(self.tmp, 'none'), self.tmp).available()[0])
        res = Runner('', self.tmp).run(self.loop(), ['10km', 'status'])
        self.assertIsNone(res['code'])


class TestServer(Tmp):
    def start(self, token=None, allow=False, tenkm=''):
        loops = [self.loop('basic'), self.loop('mixed'), Loop(os.path.join(self.tmp, 'gone'), 'gone', 'gone')]
        cfg = {'allow_actions': allow, 'operator': 'tester'}
        self.app = App(cfg, loops, Runner(tenkm, os.path.join(self.tmp, 'state')), token)
        self.httpd = serve(self.app, '127.0.0.1', 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        if getattr(self, 'httpd', None):
            self.httpd.shutdown()
            self.httpd.server_close()
        super().tearDown()

    def req(self, path, data=None, headers=None, method=None):
        r = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_pages_and_api(self):
        self.start()
        code, h, body = self.req('/')
        self.assertEqual(code, 200)
        self.assertIn(b'/static/vendor/echarts.min.js', body)
        self.assertNotIn(b'highcharts', body.lower())
        self.assertEqual(self.req('/static/vendor/echarts.min.js')[0], 200)
        self.assertEqual(self.req('/static/../server.py')[0], 404)
        loops = json.loads(self.req('/api/loops')[2])['loops']
        self.assertEqual([l['id'] for l in loops], ['basic', 'mixed', 'gone'])
        self.assertEqual(loops[0]['escalations'], 1)
        self.assertIn('error', loops[2])
        for p in ('cycles', 'issues?type=backlog', 'audits', 'cycle?id=2', 'task?id=1-1-0000-0000', 'issue?id=e1-0000',
                  'audit?id=1', 'session?name=cycle3-worker1-cccc', 'metrics?by=month', 'timeline', 'docs',
                  'log?session=cycle3-worker1-cccc'):
            code, _, body = self.req('/api/basic/' + p)
            self.assertEqual(code, 200, p)
            json.loads(body)
        self.assertEqual(self.req('/api/basic/cycle?id=99')[0], 404)
        self.assertEqual(self.req('/api/basic/cycle?id=abc')[0], 404)
        self.assertEqual(self.req('/api/nope/live')[0], 404)
        self.assertEqual(self.req('/api/basic/nope')[0], 404)

    def test_live_etag(self):
        self.start()
        code, h, body = self.req('/api/basic/live')
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d['actions']['enabled'], False)
        self.assertEqual([l['id'] for l in d['loops']], ['basic', 'mixed', 'gone'])
        self.assertEqual(self.req('/api/basic/live', headers={'If-None-Match': h['ETag']})[0], 304)

    def test_unavailable_loop(self):
        self.start()
        code, _, body = self.req('/api/gone/live')
        self.assertEqual(code, 503)
        self.assertTrue(json.loads(body)['unavailable'])

    def test_actions_off_and_allow_list(self):
        self.start()
        post = lambda name, body, h=None: self.req(f'/api/basic/action/{name}', json.dumps(body).encode(),
                                                   dict({'Content-Type': 'application/json'}, **(h or {})))
        self.assertEqual(post('resume', {})[0], 403)                         # read-only
        self.assertEqual(post('rm', {})[0], 400)
        self.assertEqual(post('resume', {}, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.req('/api/basic/action/resume', b'not json')[0], 400)

    def test_actions_on(self):
        tenkm = os.path.join(self.tmp, 'tk')
        os.makedirs(os.path.join(tenkm, 'bin'))
        for exe in ('10km', 'msm'):
            p = os.path.join(tenkm, 'bin', exe)
            with open(p, 'w') as f:
                f.write('#!/bin/sh\necho ok "$@"\n')
            os.chmod(p, 0o755)
        self.start(allow=True, tenkm=tenkm)
        code, _, body = self.req('/api/basic/action/answer', json.dumps({'id': 'e1-0000', 'resolution': 'MIT'}).encode(),
                                 {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertEqual(code, 200)
        r = json.loads(body)
        self.assertEqual(r['code'], 0)
        self.assertIn('by tester via mtv', r['out'])

    def test_token(self):
        self.start(token='s3cret')
        self.assertEqual(self.req('/api/loops')[0], 401)
        self.assertEqual(self.req('/api/loops', headers={'Authorization': 'Bearer nope'})[0], 401)
        self.assertEqual(self.req('/api/loops', headers={'Authorization': 'Bearer s3cret'})[0], 200)
        code, h, _ = self.req('/?token=s3cret')
        self.assertEqual(code, 200)
        self.assertIn('mtv_token=s3cret', h.get('Set-Cookie', ''))
        self.assertEqual(self.req('/api/loops', headers={'Cookie': 'mtv_token=s3cret'})[0], 200)


if __name__ == '__main__':
    unittest.main()
