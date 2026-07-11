# AGENTS.md

This repository is the source and deployment-script workspace for
`D:\10_Project\medical-web-server`.

Production runs under `/srv/projects/medical-web-server` on the `tencent-cloud`
SSH host. Use the local Tencent Cloud wrapper for remote operations:
`C:\Users\admin\.codex\skills\tencent-cloud\scripts\tencent-cloud.ps1`.

Keep production secrets and mutable data out of Git:

- `/etc/medical-exam.env`
- `.env` and `.env.*`
- `/srv/projects/medical-web-server/knowledge-base`
- `.ikbs_cache`, logs, caches, SQLite files, and generated Python bytecode

The GitHub repository should contain only source, static assets, documentation,
configuration templates, and maintenance scripts.

Before publishing or deploying, run:

```powershell
git status --short --branch
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\ikbs_pysearch.py app\backend\main.py app\backend\incremental_index.py app\backend\update_index.py
```

Server health checks:

```bash
projectsctl health medical-web-server
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS -X POST http://127.0.0.1:8080/api/search \
  -H 'Content-Type: application/json' \
  --data '{"query":"心","top_k":3,"search_mode":"keyword"}'
```
