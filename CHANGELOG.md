# Changelog

## 0.1.0 (unreleased)

First release as mtv (previously the 10km console).

- Watches one or more loops in local folders or shared volumes, with a loop switcher.
- Dashboard with escalations first, loop controls, current cycle, repositories and sessions.
- History of cycles and issues (paged, searchable), audits, goals and plan, status report.
- Cost and metrics per month, day, hour or cycle; Kiro credits shown beside dollars; sessions
  timeline from the last hour to project start.
- Actions through mtv's own 10km checkout, with an action log.
- Token for non-local access, generated and printed at startup when $MTV_TOKEN isn't set; settings in JSON.
- Charts with Apache ECharts, bundled for offline use (replaces Highcharts).
