#!/bin/bash
# Source this in systemd EnvironmentFile or docker-compose env
# Secret path lives in the secrets manager (see secrets-audit.sh for the roster).
SECRET_PATH="rmi/jwt_secret"
JWT_SECRET=""
JWT_SECRET="$(gopass show "$SECRET_PATH" 2>/dev/null)"
if [ -z "$JWT_SECRET" ]; then
  echo "WARNING: JWT_SECRET not found in secrets manager" >&2
fi
