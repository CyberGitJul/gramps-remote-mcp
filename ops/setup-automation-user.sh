#!/usr/bin/env bash
# One-time creation of a persistent, role-restricted automation user for the
# Gramps Remote MCP server (default EDITOR / role=3; set GRAMPS_ROLE=4 for OWNER,
# required by the import/backup tools). Run this ONCE, directly on the host that
# runs your Gramps Web container -- it uses `docker exec` against that container.
# The generated password is printed ONCE: copy it into your MCP server's .env
# (GRAMPS_USERNAME / GRAMPS_PASSWORD). It is never stored or logged anywhere else.
set -euo pipefail

# Target container name is overridable. It defaults to "grampsweb", the standard
# Gramps Web docker-compose service name. For a differently named container
# (e.g. a local dev stack) pass it via GRAMPS_CONTAINER; optionally give the new
# user a custom username as the first argument:
#   GRAMPS_CONTAINER=my-grampsweb-1 ops/setup-automation-user.sh mcp-automation
GRAMPS_CONTAINER="${GRAMPS_CONTAINER:-grampsweb}"
USERNAME="${1:-mcp-automation}"
# Role for the automation user: 3=EDITOR (default), 4=OWNER (needed for
# gramps_import_file / batch delete). See README "Backup / Restore".
GRAMPS_ROLE="${GRAMPS_ROLE:-3}"

helper_local="$(mktemp)"
username_local="$(mktemp)"
helper_remote="/tmp/gramps-setup-automation-user-$$.py"
username_remote="/tmp/gramps-setup-automation-username-$$.txt"

cleanup() {
  docker exec "$GRAMPS_CONTAINER" rm -f "$helper_remote" "$username_remote" 2>/dev/null || true
  rm -f "$helper_local" "$username_local"
}
trap cleanup EXIT

printf '%s' "$USERNAME" > "$username_local"

cat > "$helper_local" <<'PYEOF'
import secrets
import sqlite3
import subprocess
import sys

with open(sys.argv[1]) as f:
    username = f.read()
password = secrets.token_urlsafe(32)

conn = sqlite3.connect("/app/users/users.sqlite")
tree_id = [row[0] for row in conn.execute("SELECT id FROM trees")][0]

role = sys.argv[2]
result = subprocess.run(
    ["python3", "-m", "gramps_webapi", "user", "add", username, password,
     "--role", role, "--tree", tree_id, "--fullname", "MCP Automation"],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print("USER_ADD_FAILED", result.stderr[-500:], file=sys.stderr)
    sys.exit(1)

print("GRAMPS_USERNAME=" + username)
print("GRAMPS_PASSWORD=" + password)
PYEOF

docker cp "$helper_local" "${GRAMPS_CONTAINER}:${helper_remote}"
docker cp "$username_local" "${GRAMPS_CONTAINER}:${username_remote}"

echo "Creating persistent automation user (role=$GRAMPS_ROLE) -- credentials are shown only once:"
docker exec "$GRAMPS_CONTAINER" sh -c \
  "export SECRET_KEY=\"\$(cat /app/secret/secret)\"; python3 '$helper_remote' '$username_remote' '$GRAMPS_ROLE'"
