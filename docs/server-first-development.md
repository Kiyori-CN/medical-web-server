# Server-First Development

This repository is developed from the Tencent Cloud server workspace:

```text
/srv/projects/medical-web-server/repo
```

Local Windows checkouts are backup mirrors. Normal code changes, validation, commits, and pushes happen on the server workspace.

## Production Boundary

Production runtime is separate from the Git workspace:

```text
/srv/projects/medical-web-server/app
/srv/projects/medical-web-server/public
/etc/medical-exam.env
/srv/projects/medical-web-server/knowledge-base
```

Do not edit runtime files as a Git working tree. Deploy source/static updates with an explicit artifact through:

```bash
projectsctl update medical-web-server <artifact.tar.gz>
```

The production environment file and knowledge base are runtime assets and must be preserved across deployments.

## Never Commit

Do not commit `.env` files, `/etc/medical-exam.env`, `knowledge-base`, `.ikbs_cache`, logs, caches, generated indexes, databases, API keys, certificates, or private keys.

## Minimum Checks

Before committing or deploying:

```bash
git status --short --branch
git diff --check
projectsctl health medical-web-server
```

Before deployment also record the service state, rollback target, health result, and available disk space.
