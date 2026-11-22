"""Browser smoke test (optional): opens every view against synthetic loops and
fails on any script error or empty chart.

    pip install playwright && playwright install chromium
    python3 tests/smoke_browser.py            # or: make smoke

Exits 0 with a note when Playwright isn't installed, so it can sit in CI
without being required for a build.
"""
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('smoke: playwright not installed, skipped')
    sys.exit(0)

from fixtures import make_loop          # noqa: E402
from mtv.actions import Runner          # noqa: E402
from mtv.data import Loop               # noqa: E402
from mtv.server import App, serve       # noqa: E402

VIEWS = ['#/', '#/status', '#/goals', '#/metrics', '#/cycles', '#/issues', '#/audits', '#/settings', '#/launch',
         '#/cycle/2', '#/cycle/3', '#/task/1-1-0000-0000', '#/issue/e1-0000', '#/audit/1',
         '#/session/cycle3-worker1-cccc', '#/session/cycle1-worker1-cccc']
MIXED = ['#/', '#/metrics', '#/cycles', '#/cycle/1', '#/cycle/11', '#/session/cycle12-worker1-k']


def main():
    tmp = tempfile.mkdtemp(prefix='mtv-smoke-')
    try:
        loops = [Loop(make_loop(os.path.join(tmp, 'basic'), 'basic'), 'basic', 'basic'),
                 Loop(make_loop(os.path.join(tmp, 'mixed'), 'mixed'), 'mixed', 'mixed'),
                 Loop(make_loop(os.path.join(tmp, 'fresh'), 'fresh'), 'fresh', 'fresh')]
        app = App({'allow_actions': False}, loops, Runner('', os.path.join(tmp, 'state')))
        httpd = serve(app, '127.0.0.1', 0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        fails = []
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: m.type == 'error' and errors.append(m.text))

            def visit(loop, h):
                page.evaluate(f"localStorage.setItem('mtv-loop', '{loop}')") if page.url.startswith('http') else None
                page.goto(f'http://127.0.0.1:{port}/{h}')
                page.wait_for_selector('#tree .it, main h1', timeout=10000)
                page.wait_for_timeout(600)
                bad = page.evaluate("""() => {
                    const m = document.querySelector('main');
                    const empty = [...m.querySelectorAll('[id^=ch]')].filter(d => !d.querySelector('canvas')).map(d => d.id);
                    return {loading: m.innerText.includes('Loading…'), empty, h1: (m.querySelector('h1') || {}).textContent};
                }""")
                where = f'{loop} {h}'
                if errors:
                    fails.append(f'{where}: {errors[:]}')
                    errors.clear()
                if bad['loading'] or bad['empty']:
                    fails.append(f'{where}: {bad}')
                print(f'  ok  {where:40} {bad["h1"]!r:.60}' if not (bad['loading'] or bad['empty']) else f'  FAIL {where}')

            page.goto(f'http://127.0.0.1:{port}/')
            for h in VIEWS:
                visit('basic', h)
            for by in ('month', 'day', 'hour', 'cycle'):
                page.evaluate(f"setMB('{by}')")
                page.wait_for_timeout(800)
                if errors:
                    fails.append(f'metrics {by}: {errors[:]}')
                    errors.clear()
            for h in MIXED:
                visit('mixed', h)
            visit('fresh', '#/')
            visit('fresh', '#/metrics')
            b.close()
        httpd.shutdown()
        if fails:
            print('smoke: FAILED')
            for f in fails:
                print('  ' + f)
            sys.exit(1)
        print('smoke: all views ok')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
