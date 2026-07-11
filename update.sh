#!/usr/bin/env bash
set -Eeuo pipefail
exec /usr/local/sbin/projectsctl update medical-web-server "$@"
