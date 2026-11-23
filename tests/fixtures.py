"""Synthetic loops for tests. No real project data.

make_loop(root, kind) builds a loop root with .10km/loop.db (WAL mode),
usage.jsonl and dispatch.json. Kinds cover what real loops showed:
  'basic'    a few cycles, an open escalation, Claude usage
  'mixed'    imported history cycles, Kiro credits, a broken harness field,
             an active session whose folder doesn't exist here
  'fresh'    created but never run: one loop row, no cycles
"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE loop (id INTEGER PRIMARY KEY CHECK (id = 1), current_cycle INTEGER NOT NULL, last_audit INTEGER NOT NULL,
  empty_cycles INTEGER NOT NULL, phase TEXT NOT NULL CHECK (phase IN ('plan','execute','curate')), stopped INTEGER NOT NULL,
  reason TEXT, updated_at TEXT NOT NULL);
CREATE TABLE sessions (name TEXT PRIMARY KEY, role TEXT NOT NULL, cycle INTEGER, status TEXT NOT NULL, handle TEXT,
  working_directory TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE cycles (id INTEGER PRIMARY KEY, status TEXT NOT NULL, planner_session TEXT, planned_at TEXT NOT NULL,
  headline TEXT, report TEXT, curator_session TEXT, curated_at TEXT);
CREATE TABLE tasks (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, cycle INTEGER NOT NULL, shortname TEXT NOT NULL,
  instructions TEXT NOT NULL, branch TEXT NOT NULL, session TEXT, report TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
  started_at TEXT, done_at TEXT, curated_at TEXT);
CREATE TABLE issues (id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT, assigner_session TEXT,
  assigner_role TEXT NOT NULL, assignee_role TEXT, severity TEXT NOT NULL DEFAULT 'low', urgent_if TEXT, status TEXT NOT NULL,
  branch TEXT, created_at TEXT NOT NULL, answered_at TEXT, resolved_at TEXT, resolution TEXT);
CREATE TABLE audits (id INTEGER PRIMARY KEY, period TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
PRAGMA user_version = 2;
"""


def _write_usage(dot, rows):
    with open(os.path.join(dot, 'usage.jsonl'), 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
        f.write('{"truncated line without newline"')   # partial last line must be ignored


def make_loop(root, kind='basic'):
    dot = os.path.join(root, '.10km')
    os.makedirs(dot, exist_ok=True)
    con = sqlite3.connect(os.path.join(dot, 'loop.db'))
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript(SCHEMA)
    x = con.execute
    usage = []
    if kind == 'fresh':
        x("insert into loop values (1, 0, 0, 0, 'plan', 1, null, '2026-11-01T10:00:00Z')")
    elif kind == 'basic':
        x("insert into loop values (1, 3, 1, 0, 'execute', 0, null, '2026-11-20T12:00:00Z')")
        for c, day in ((1, 18), (2, 19), (3, 20)):
            st = 'open' if c == 3 else 'closed'
            x("insert into cycles values (?, ?, ?, ?, ?, ?, ?, ?)",
              (c, st, f'cycle{c}-planner-aaaa', f'2026-11-{day}T09:00:00Z', f'PH{c}: cycle {c} work',
               None if st == 'open' else f'# Cycle {c} curated\n\n- **t{c}a** accepted', None if st == 'open' else f'cycle{c}-curator-bbbb',
               None if st == 'open' else f'2026-11-{day}T11:00:00Z'))
            for s in (1, 2):
                status = 'active' if c == 3 and s == 1 else ('rejected' if (c, s) == (2, 2) else 'accepted')
                x("insert into tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (f'{c}-{s}-0000-0000', s, c, f't{c}{"ab"[s - 1]}', f'Do **thing** {c}.{s}\n\n- step one\n- step two', f'cycle{c}-worker{s}',
                   f'cycle{c}-worker{s}-cccc', None if status == 'active' else 'Done; gate passed.', status,
                   f'2026-11-{day}T09:05:00Z', f'2026-11-{day}T09:10:00Z', None if status == 'active' else f'2026-11-{day}T10:00:00Z',
                   None if status == 'active' or c == 3 else f'2026-11-{day}T11:00:00Z'))
                usage.append({'cycle': c, 'role': 'worker', 'session': f'cycle{c}-worker{s}-cccc', 'harness': 'claude',
                              'recorded_at': f'2026-11-{day}T10:00:00Z', 'duration_ms': 3_000_000, 'num_turns': 40,
                              'total_cost_usd': 2.5 * s})
            usage.append({'cycle': c, 'role': 'planner', 'session': f'cycle{c}-planner-aaaa', 'harness': 'claude',
                          'recorded_at': f'2026-11-{day}T09:04:00Z', 'duration_ms': 200_000, 'total_cost_usd': 0.5})
        x("insert into sessions values ('cycle3-worker1-cccc', 'worker', 3, 'active', 'process:999999:x', ?, "
          "'2026-11-20T09:10:00Z', '2026-11-20T09:10:00Z')", (os.path.join(root, 'cycle3-worker1-cccc'),))
        os.makedirs(os.path.join(root, 'cycle3-worker1-cccc'), exist_ok=True)
        with open(os.path.join(root, 'cycle3-worker1-cccc', 'agent.log'), 'w') as f:
            f.write('[warning] plain text line\n')
            f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'Reading the task.'},
                {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'make check'}}]}}) + '\n')
            f.write(json.dumps({'type': 'result', 'result': 'Done.', 'total_cost_usd': 1.25, 'num_turns': 9}) + '\n')
        x("insert into issues values ('e1-0000', 'escalation', 'ESC1: Pick a license', 'Which license?', 'cycle2-curator-bbbb', "
          "'curator', 'operator', 'blocking', null, 'open', null, '2026-11-19T12:00:00Z', null, null, null)")
        x("insert into issues values ('e2-0000', 'escalation', 'Do the parser rewrite next', 'Planner, please.', null, "
          "'operator', 'planner', 'high', null, 'open', null, '2026-11-19T14:00:00Z', null, null, null)")
        x("insert into issues values ('b1-0000', 'backlog', 'Tidy the settings page', 'Labels are misaligned.', null, "
          "'operator', 'planner', 'low', null, 'open', null, '2026-11-19T13:00:00Z', null, null, null)")
        x("insert into issues values ('b2-0000', 'backlog', 'Old item', null, null, 'worker', 'planner', 'high', null, "
          "'resolved', null, '2026-11-18T13:00:00Z', null, '2026-11-19T08:00:00Z', 'Done in cycle 2.')")
        x("insert into audits values (1, '{\"from\": \"2026-11-18T00:00:00Z\", \"to\": \"2026-11-19T00:00:00Z\"}', "
          "'# Audit 1\n\nGREEN. Two cycles.', '2026-11-19T08:30:00Z')")
        with open(os.path.join(dot, 'dispatch.json'), 'w') as f:
            json.dump({'started_at': '2026-11-20T12:00:00Z', 'finished_at': '2026-11-20T12:00:01Z', 'result': 'ok'}, f)
    elif kind == 'mixed':
        x("insert into loop values (1, 12, 0, 0, 'execute', 0, null, '2026-11-21T12:00:00Z')")
        for c in range(1, 11):        # imported history: no sessions, no usage, no timings
            x("insert into cycles values (?, 'closed', null, ?, ?, null, null, ?)",
              (c, f'2026-10-{10 + c}T10:00:00Z', f'Imported from project history: w{c}', f'2026-10-{10 + c}T10:00:00Z'))
            x("insert into tasks values (?, 1, ?, ?, 'imported', 'main', null, 'ok', 'accepted', ?, null, null, null)",
              (f'imp-{c}', c, f'w{c}', f'2026-10-{10 + c}T10:00:00Z'))
        for c in (11, 12):
            x("insert into cycles values (?, ?, ?, ?, ?, null, null, null)",
              (c, 'open' if c == 12 else 'closed', f'cycle{c}-planner-k', f'2026-11-2{c - 10}T08:00:00Z', f'DEL{c}: real work'))
            x("insert into tasks values (?, 1, ?, 'real', 'x', 'b', ?, null, ?, ?, ?, ?, null)",
              (f'r-{c}', c, f'cycle{c}-worker1-k', 'active' if c == 12 else 'done', f'2026-11-2{c - 10}T08:05:00Z',
               f'2026-11-2{c - 10}T08:06:00Z', None if c == 12 else f'2026-11-2{c - 10}T09:00:00Z'))
        usage += [
            {'cycle': 11, 'role': 'worker', 'session': 'cycle11-worker1-k', 'harness': 'kiro-cli', 'credits': 35.5,
             'duration_ms': 1_800_000, 'recorded_at': '2026-11-21T09:00:00Z'},
            {'cycle': 11, 'role': 'curator', 'session': 'cycle11-curator-k', 'harness': 'SOME_VAR=12345',
             'total_cost_usd': 0.58, 'duration_ms': 43_000, 'recorded_at': '2026-11-21T09:30:00Z'},
            {'cycle': 11, 'role': 'planner', 'session': 'cycle11-planner-k', 'harness': 'claude', 'total_cost_usd': 1.0,
             'recorded_at': '2026-11-21T08:04:00Z'},                         # no duration
            {'cycle': None, 'role': 'auditor', 'session': 'auditor1-x', 'harness': 'claude', 'total_cost_usd': 0.3,
             'recorded_at': '2026-11-21T07:00:00Z'},                          # no cycle
            {'cycle': 12, 'role': 'worker', 'session': 'cycle12-worker1-k', 'harness': 'kiro-cli',
             'recorded_at': '2026-11-22T08:30:00Z'},                          # neither dollars nor credits
        ]
        x("insert into sessions values ('cycle12-worker1-k', 'worker', 12, 'active', 'process:1:x', "
          "'/nonexistent/elsewhere/cycle12-worker1-k', '2026-11-22T08:06:00Z', '2026-11-22T08:06:00Z')")
    con.commit()
    con.close()
    _write_usage(dot, usage)
    return root
