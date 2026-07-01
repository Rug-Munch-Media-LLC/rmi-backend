#!/bin/bash
# T08 — GlitchTip admin user + project structure (server-side ops)
#
# Run on netcup to create/update the GlitchTip admin user and verify
# the 4 project structure (rmi-backend, rmi-frontend, rmi-mcp, rmi-x402).
#
# GlitchTip on netcup uses apps.users.models.User (custom auth model)
# with email as the lookup key. No rest_framework installed (lite build),
# so we use Django ORM directly instead of Token API.
#
# Usage: bash scripts/ops/t08_glitchtip_admin.sh
# Idempotent: safe to re-run.

set -e
CONTAINER="${GLITCHTIP_CONTAINER:-rmi-glitchtip}"
EMAIL="${GLITCHTIP_ADMIN_EMAIL:-ops@rugmunch.io}"
PASSWORD="${GLITCHTIP_ADMIN_PASSWORD:-GlitchTip-RMI-2026-ProD}"

echo "=== T08: GlitchTip admin + projects ==="
echo "container: $CONTAINER"
echo "email: $EMAIL"
echo ""

echo "=== Step 1: Ensure admin superuser ==="
docker exec -i $CONTAINER python manage.py shell << PYEOF
from apps.users.models import User
if not User.objects.filter(email='$EMAIL').exists():
    User.objects.create_superuser(email='$EMAIL', password='$PASSWORD')
    print("CREATED admin")
else:
    u = User.objects.get(email='$EMAIL')
    u.set_password('$PASSWORD')
    u.is_superuser = True
    u.is_staff = True
    u.save()
    print("UPDATED admin")
admins = User.objects.filter(is_superuser=True)
print(f"Total superusers: {admins.count()}")
PYEOF

echo ""
echo "=== Step 2: Save creds to /root/.rmi-creds/ ==="
mkdir -p /root/.rmi-creds
cat > /root/.rmi-creds/glitchtip_admin.txt << CRED
email=$EMAIL
password=$PASSWORD
url=https://glitchtip.rugmunch.io
CRED
chmod 600 /root/.rmi-creds/glitchtip_admin.txt
echo "saved to /root/.rmi-creds/glitchtip_admin.txt"

echo ""
echo "=== Step 3: Verify 4 projects exist ==="
docker exec $CONTAINER python manage.py shell -c "
from django.apps import apps
Project = apps.get_model('projects', 'Project')
existing = sorted(Project.objects.values_list('name', flat=True))
print(f'Projects ({len(existing)}):')
for p in existing:
    print(f'  - {p}')
" 2>&1 | tail -10

echo ""
echo "=== Step 4: Verify admin can authenticate ==="
docker exec $CONTAINER python manage.py shell -c "
from apps.users.models import User
u = User.objects.get(email='$EMAIL')
ok = u.check_password('$PASSWORD')
print(f'Login test for {u.email}: {\"OK\" if ok else \"FAIL\"}')
print(f'  superuser={u.is_superuser} staff={u.is_staff} active={u.is_active}')
" 2>&1 | tail -3

echo ""
echo "=== T08 COMPLETE ==="
echo "Login at: https://glitchtip.rugmunch.io"
echo "Email:    $EMAIL"
echo "Password: $PASSWORD"
