# Spec: `10km mcp`, and mtv as its client

Status: proposal, for a 10km loop to build. Owner: the operator. Targets 10km and mtv together.

## Goal

Each 10km loop serves its own state and its operator actions over MCP. mtv stops reading loop
files and becomes an MCP client of every loop it watches, local or in a container, through one
code path. The same server lets any MCP-capable agent (Claude Code, Kiro, …) observe or operate a
loop.

This is a clean break: no migration of existing loops, no fallback to mtv's current data layer.
mtv 0.2 requires loops on a 10km that has `10km mcp`; older loops update by merging
`origin/main` as usual.

## Non-goals

- Network transport (HTTP/SSE) in 10km. The server speaks stdio only; reaching it is the client's
  job (`docker exec -i`, `ssh`, or a local process).
- Authentication inside 10km. Who may start the process is who may use it.
- Writing to the loop by any path other than msm and the 10km commands.

## 1. 10km: `10km mcp`

### Command

    10km mcp [--read-only]

Starts an MCP server on stdin/stdout for the loop at `LOOP_ROOT`. Logs go to stderr. Exits when
stdin closes. Standard library only (10km's rule), Python 3.9+.

Typical invocations:

| Loop | Command |
|---|---|
| local | `cd <root> && . ./host.env && 10km mcp` |
| container | `docker exec -i <name> sh -c 'cd /home/monkey/loop && . ./host.env && exec 10km mcp'` |
| remote | `ssh <host> docker exec -i <name> …` (same) |

### Protocol

- MCP over stdio: newline-delimited JSON-RPC 2.0, `initialize`, `tools/list`, `tools/call`,
  `ping`, and `notifications/initialized`.
- Protocol version: the current stable MCP revision at implementation time. The server answers
  with the version it supports; it rejects unknown major behaviour rather than guessing.
- `serverInfo`: `{"name": "10km", "version": <10km version>}`, plus in `instructions` a one-line
  description of the loop (name, root).
- Results use `structuredContent` (the JSON object) with `outputSchema` declared per tool. The
  text `content` item carries a short human-readable summary, not a second copy of the JSON, to
  keep payloads small. (Clients that need the data read `structuredContent`; mtv does.)
- Concurrency: requests may be pipelined; replies carry the request `id`. A single-threaded server
  answering in order is acceptable for the first version.

### Identity and permissions

The server's caller is whoever started it, with the environment it was started in:

- `LOOP_SESSION` unset → the **operator**. All tools available.
- `LOOP_SESSION` set → that **agent session**. Write tools run msm as that session, so msm's own
  rules apply (an agent can't resume a loop or answer the operator's escalations). The server
  adds no rules of its own.
- `--read-only` → write tools are not listed.

Writes are implemented by running msm / the 10km command in a subprocess with the same
environment, never by writing the database directly. Every write returns the command's exit code,
stdout and stderr.

### Change notification

- Tool `loop.changes(since)` returns `{token, changed: bool}`. `token` is opaque, cheap to compute
  (file sizes and mtimes of loop.db, its WAL, usage.jsonl, dispatch.json) and changes whenever any
  of them does. Clients poll it every few seconds and fetch `loop.live` only when it changes.
- Optional later: resource subscriptions (`resources/subscribe` on `loop://live`) with
  `notifications/resources/updated`. Not required for mtv 0.2.

### Tools

Names are dotted by area. Every read tool takes optional paging/size parameters so the same tool
serves mtv (everything) and agents (small). Defaults suit agents; mtv passes explicit limits.

Read:

| Tool | Params | Returns |
|---|---|---|
| `loop.live` | — | loop row, dispatch, current cycle + tasks, sessions (with `alive`), open escalations (first 600 chars of description), open backlog (first 20), audits (first 20), counts, cost and credits totals, first/last session times, repos summary |
| `loop.changes` | `since?` | `{token, changed}` |
| `loop.schema` | — | `{user_version, supported, missing}` |
| `cycles.list` | `q?, offset=0, limit=20` | `{total, offset, rows[]}` with ntasks, cost, credits |
| `cycles.get` | `id` | cycle with tasks and usage rows |
| `tasks.get` | `id` | task, full instructions and report |
| `issues.list` | `type?, status?, assignee_role?, assigner_role?, q?, offset=0, limit=20` | `{total, offset, rows[]}` |
| `issues.get` | `id` | issue, full description and resolution |
| `audits.list` | `limit=20` | rows with a 200-char summary |
| `audits.get` | `id` | audit |
| `sessions.get` | `name` | session row, usage rows, task |
| `sessions.log` | `name, tail_bytes=65536` | parsed agent.log events (`say`, `tool`, `result`, `text`), `truncated` |
| `sessions.timeline` | `from?, to?, limit=500` | session spans `{n, role, cycle, s, e, active, harness}`, `start` |
| `metrics.buckets` | `by=cycle\|hour\|day\|month` | keys, buckets (accepted, failed, progress, cost, credits), roles |
| `docs.get` | `name=GOALS\|PLAN\|LEARNINGS` | markdown text |
| `repos.status` | — | per repo in central/: branch, commit, when, message, dirty, ahead, behind |
| `status.report` | — | `10km status` output |

Write (operator; or as the calling session, under msm's rules):

| Tool | Runs |
|---|---|
| `issues.answer` | `msm issue update <id> {"status":"answered","resolution":…}` |
| `issues.resolve` | `msm issue update <id> {"status":"resolved","resolution":…}` |
| `issues.create_backlog` | `msm issue create {"type":"backlog",…}` |
| `loop.stop` | `10km pause [reason]` |
| `loop.kill` | `10km pause --now [reason]` |
| `loop.resume` | `10km resume` |
| `loop.start` | `10km start` |
| `loop.dispatch` | `10km dispatch` |
| `sessions.start_operator` | `10km session start <user> --no-agent` (agent sessions: later) |
| `sessions.done` | `10km session done <name>` |

Each write tool's description says plainly what it does and what it can't undo. `loop.kill`
names the consequence (running agents stopped, sessions expired, unfinished work not merged).

Errors: tool failures return `isError: true` with the message (for example "Can't read loop.db:
…"). Protocol errors use JSON-RPC codes (-32601 unknown tool, -32602 bad params).

### Data rules (carried over from mtv, now owned by 10km)

These handle what real loops contain; each needs a test in 10km's suite:

- usage.jsonl read incrementally from a remembered offset; a partial last line is ignored; a
  rotated or truncated file restarts the read.
- `credits` (Kiro) kept separate from `total_cost_usd` (Claude); never added together.
- A `harness` value that isn't a plain name (older loops logged `VAR=value`) reported as
  `unknown`.
- Sessions removed from loop.db still appear from usage.jsonl.
- `alive` is `null` (unknown) when a session's working directory doesn't exist here.
- Cycles imported from project history (no planner session) are flagged `imported: true`.
- Days and months are local calendar dates; hours are UTC-aligned.
- WAL: open read-only; if the `-shm` file can't be created, say so plainly.
- agent.log: Claude `stream-json` and single-result JSON, Kiro output, and plain text lines.

### Tests (10km)

- An MCP conformance test: start `10km mcp` as a subprocess, `initialize`, `tools/list`, call
  every read tool against a fixture loop, check `outputSchema` validation of each result.
- Write tools against a fixture database with a stub msm: argv, environment (LOOP_SESSION passed
  through or unset), exit codes.
- The data rules above, each with a fixture.
- `--read-only` hides writes.

## 2. mtv 0.2: an MCP client

### Changes

- Remove `mtv/data.py` and direct file reads. Add `mtv/client.py`: an MCP stdio client (standard
  library) that starts the loop's `10km mcp` with the loop's `exec` prefix, does `initialize`,
  checks the tool list, and exposes the read and write tools as methods.
- Settings per loop:

  ```json
  {"name": "bruckert", "root": "~/devel/bruckert-10km"}
  {"name": "myloop", "exec": ["docker", "exec", "-i", "myloop"], "root": "/home/monkey/loop"}
  ```

  `root` is where the loop is as seen by `exec` (inside the container for docker). Without
  `exec`, mtv runs it locally with the loop's host.env.
- Remove `actions.py`'s subprocess runner: actions become write-tool calls on the same
  connection.
- The HTTP API and the page stay as they are; server.py maps each `/api/<loop>/…` route to a tool
  call. ETag caching uses `loop.changes`.
- One client process per loop, restarted with backoff (1, 2, 4 … 60 s) when it exits; the loop
  shows as unavailable with the reason meanwhile.
- A loop whose 10km has no `mcp` command is shown as "needs a 10km update" with the command to
  merge it. No fallback reader.
- `--read-only` on mtv starts every server with `--read-only`.

### Tests (mtv)

- Client against a fake MCP server (a small Python script speaking the protocol), covering
  initialize, tool calls, `isError`, a crash and restart, and a missing tool.
- Server routes map to the right tool calls with the right limits.
- The existing browser smoke test, against fake servers for the three fixture loops.

## 3. Order of work

1. 10km: the read tools and `loop.changes`, with the data rules and tests.
2. 10km: the write tools.
3. mtv: client.py and the route mapping; delete data.py and the subprocess runner.
4. mtv: `exec` settings and container loops; docs.
5. Try against bruckert (local) and a container loop; release 10km and mtv 0.2 together.

## 4. Open questions

- Should agents' sessions get a narrower read set (for example, not other sessions' logs or the
  operator's escalation answers)? Default: same as the operator's reads.
- Agent operator sessions (`sessions.start_operator` with an agent): needs a way to run the
  operator agent detached and stream its log. Out of scope for 0.2.
- Resource subscriptions instead of polling `loop.changes`.
