#!/usr/bin/env bash
# deploy.sh — Deploy ev_charger AppDaemon script to Home Assistant
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Config ────────────────────────────────────────────────────────────────────
SSH_HOST="ha"
HA_APPS_DIR="/addon_configs/a0d7b954_appdaemon/apps"
HA_BACKUP_DIR="/addon_configs/a0d7b954_appdaemon/_backups"
ADDON_SLUG="a0d7b954_appdaemon"
MAX_BACKUPS=10
RESTART_WAIT=20
ROLLBACK_WAIT=15

LOCAL_FILES=(
    "appdaemon/apps/ev_charger.py"
    "appdaemon/apps.yaml"
)
REMOTE_FILES=(
    "${HA_APPS_DIR}/ev_charger.py"
    "${HA_APPS_DIR}/apps.yaml"
)

BACKUP_PATH=""   # recorded in step 3, used in rollback

# ── Flags ─────────────────────────────────────────────────────────────────────
FORCE=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --force)    FORCE=true ;;
        --dry-run)  DRY_RUN=true ;;
        *) printf "${RED}Unknown argument: %s${NC}\n" "$arg" >&2; exit 1 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { printf "${YELLOW}  %s${NC}\n" "$*"; }
ok()    { printf "${GREEN}  %s${NC}\n" "$*"; }
err()   { printf "${RED}  %s${NC}\n" "$*" >&2; }
step()  { printf "\n${BOLD}${CYAN}%s${NC}\n" "$*"; }
dry()   { printf "${YELLOW}  [DRY-RUN] %s${NC}\n" "$*"; }

rc()    { ssh "$SSH_HOST" "$@"; }        # remote command via SSH alias
put()   { scp "$1" "${SSH_HOST}:$2"; }   # upload single file

countdown() {
    local total=$1 label=${2:-"Waiting"} interval=5
    while [[ $total -gt 0 ]]; do
        info "${label}... ${total}s"
        sleep $interval
        total=$(( total - interval ))
    done
}

# Always run from repo root so relative paths work
cd "$(dirname "${BASH_SOURCE[0]}")"

# ─────────────────────────────────────────────────────────────────────────────
step "[0/6] Checking SSH connection to '${SSH_HOST}'..."
# ─────────────────────────────────────────────────────────────────────────────

if ! rc echo ok &>/dev/null; then
    err "Cannot connect to HA via SSH (host: ${SSH_HOST})"
    err "Check ~/.ssh/config — expected alias '${SSH_HOST}' pointing to HA."
    exit 1
fi
ok "SSH OK"

# ─────────────────────────────────────────────────────────────────────────────
step "[1/6] Validating local files..."
# ─────────────────────────────────────────────────────────────────────────────

for f in "${LOCAL_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        err "File not found: $f"
        exit 1
    fi
done
ok "All local files present"

if python3 -c "import sys" &>/dev/null 2>&1; then
    info "Checking syntax: appdaemon/apps/ev_charger.py"
    if ! python3 -m py_compile appdaemon/apps/ev_charger.py 2>&1; then
        err "Syntax error in ev_charger.py — aborting. Fix before deploying."
        exit 1
    fi
    ok "Syntax OK"
else
    info "Warning: python3 not available — syntax check skipped"
fi

# ─────────────────────────────────────────────────────────────────────────────
step "[2/6] Deployment plan:"
# ─────────────────────────────────────────────────────────────────────────────

for i in "${!LOCAL_FILES[@]}"; do
    printf "  ${BOLD}%-42s${NC} → ha:%s\n" "${LOCAL_FILES[$i]}" "${REMOTE_FILES[$i]}"
done

if [[ "$DRY_RUN" == true ]]; then
    printf "\n"
    dry "Dry-run mode — nothing will be executed."
    dry "Would run: backup → upload ${#LOCAL_FILES[@]} files → restart AppDaemon → verify logs"
    exit 0
fi

if [[ "$FORCE" == false ]]; then
    printf "\n${BOLD}Deploy to HA? [y/N]${NC} "
    read -r answer || true
    if [[ "${answer:-}" != "y" && "${answer:-}" != "Y" ]]; then
        info "Aborted."
        exit 0
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
step "[3/6] Creating backup on HA..."
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_PATH="${HA_BACKUP_DIR}/${TIMESTAMP}"

rc "mkdir -p '${BACKUP_PATH}'"

for i in "${!REMOTE_FILES[@]}"; do
    filename=$(basename "${REMOTE_FILES[$i]}")
    rc "cp '${REMOTE_FILES[$i]}' '${BACKUP_PATH}/${filename}' 2>/dev/null || true"
done
ok "Backup created: ${BACKUP_PATH}"

# Keep only MAX_BACKUPS newest backups, delete older ones
rc "ls -dt '${HA_BACKUP_DIR}'/[0-9]* 2>/dev/null \
    | tail -n +$(( MAX_BACKUPS + 1 )) \
    | xargs -r rm -rf -- 2>/dev/null" || true
ok "Old backups pruned (keeping last ${MAX_BACKUPS})"

# ─────────────────────────────────────────────────────────────────────────────
step "[4/6] Uploading files..."
# ─────────────────────────────────────────────────────────────────────────────

for i in "${!LOCAL_FILES[@]}"; do
    info "Uploading: ${LOCAL_FILES[$i]}"
    put "${LOCAL_FILES[$i]}" "${REMOTE_FILES[$i]}"
    ok "→ ha:${REMOTE_FILES[$i]}"
done

# ─────────────────────────────────────────────────────────────────────────────
step "[5/6] Restarting AppDaemon..."
# ─────────────────────────────────────────────────────────────────────────────

rc "ha apps restart ${ADDON_SLUG}"
ok "Restart command sent"
countdown $RESTART_WAIT "Waiting for AppDaemon"

# ─────────────────────────────────────────────────────────────────────────────
step "[6/6] Verifying..."
# ─────────────────────────────────────────────────────────────────────────────

LOG=$(rc "ha apps logs ${ADDON_SLUG} 2>&1 | tail -80" || true)

SUCCESS=false
HAS_ERROR=false
echo "$LOG" | grep -q  "EV Charger Control startuje" && SUCCESS=true   || true
echo "$LOG" | grep -qE "ERROR|Traceback"              && HAS_ERROR=true || true

# ── Rollback function ─────────────────────────────────────────────────────────
do_rollback() {
    step "[ROLLBACK] Restoring previous version..."
    if [[ -z "$BACKUP_PATH" ]]; then
        err "No backup path recorded — cannot rollback"
        return 1
    fi
    info "Restoring from: ${BACKUP_PATH}"
    for i in "${!REMOTE_FILES[@]}"; do
        filename=$(basename "${REMOTE_FILES[$i]}")
        rc "cp '${BACKUP_PATH}/${filename}' '${REMOTE_FILES[$i]}' 2>/dev/null || true"
    done
    ok "Files restored"
    rc "ha apps restart ${ADDON_SLUG}"
    ok "AppDaemon restarting..."
    countdown $ROLLBACK_WAIT "Waiting"
    printf "\n--- Post-rollback log (last 20 lines) ---\n"
    rc "ha apps logs ${ADDON_SLUG} 2>&1 | tail -20" || true
    printf "─────────────────────────────────────────\n\n"
    printf "${GREEN}${BOLD}  ↩ Rolled back to previous version${NC}\n\n"
}

# ── Result ────────────────────────────────────────────────────────────────────
if [[ "$SUCCESS" == true && "$HAS_ERROR" == false ]]; then
    printf "\n${GREEN}${BOLD}✓ Deploy successful${NC}\n\n"
else
    printf "\n${RED}${BOLD}✗ Deploy may have failed${NC}\n"
    if [[ "$HAS_ERROR" == true ]]; then
        err "Errors detected in AppDaemon log"
    else
        err "'EV Charger Control startuje' not found in log"
    fi
    printf "\n--- Last 30 log lines ---\n"
    echo "$LOG" | tail -30
    printf "─────────────────────────────────────────\n\n"
    printf "${BOLD}Rollback to previous version? [Y/n]${NC} "
    read -r rollback_answer || true
    if [[ "${rollback_answer:-}" != "n" && "${rollback_answer:-}" != "N" ]]; then
        do_rollback
    else
        info "Rollback skipped."
        info "To check logs: ssh ${SSH_HOST} 'ha apps logs ${ADDON_SLUG} | tail -50'"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   ./deploy.sh            # normal deploy with confirmation
#   ./deploy.sh --force    # skip confirmation prompt (CI-friendly)
#   ./deploy.sh --dry-run  # show plan without making any changes
# ─────────────────────────────────────────────────────────────────────────────
