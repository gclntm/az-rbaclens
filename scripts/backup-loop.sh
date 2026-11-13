#!/bin/sh
# Automatic database backups for the prod stack (the `backup` service in
# docker/docker-compose.prod.yml, image postgres:18-alpine).
#
# Once a day, from BACKUP_HOUR (container time, TZ) on, writes
# $BACKUP_DIR/<prefix>-YYYYMMDD-HHMMSS-auto.dump with pg_dump -Fc (consistent
# while the app runs), checks it can be read back, then keeps the newest
# BACKUP_KEEP automatic dumps. Manual `make backup prod` files are never
# removed. A day missed while the machine slept is caught up on wake.
#
# Connection: the standard PGHOST / PGUSER / PGPASSWORD / PGDATABASE.
# `backup-loop.sh --now` takes one backup immediately and exits.
set -u

DIR=${BACKUP_DIR:-/backups}
HOUR=${BACKUP_HOUR:-2}
KEEP=${BACKUP_KEEP:-14}
PREFIX=${BACKUP_PREFIX:-prod}
CHECK_EVERY=${BACKUP_CHECK_SECONDS:-300}
RETRY_AFTER=${BACKUP_RETRY_SECONDS:-1800}
umask 077 # the dumps hold the whole database

log() { echo "$(date '+%Y-%m-%d %H:%M:%S %Z') backup: $*"; }

is_int() { case $1 in '' | *[!0-9]*) return 1 ;; esac; }
is_int "$HOUR" && [ "$HOUR" -le 23 ] || { log "BACKUP_HOUR must be 0-23, got '$HOUR'"; exit 1; }
is_int "$KEEP" && [ "$KEEP" -ge 1 ] || { log "BACKUP_KEEP must be 1 or more, got '$KEEP'"; exit 1; }
case $PREFIX in '' | *[!A-Za-z0-9_-]*) log "BACKUP_PREFIX may hold letters, digits, - and _ only"; exit 1 ;; esac
mkdir -p "$DIR"

prune() {
  # Names sort by time; drop all but the newest $KEEP automatic dumps.
  ls -1 "$DIR"/"$PREFIX"-*-auto.dump 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" |
    while IFS= read -r old; do
      rm -f -- "$old" && log "removed $(basename "$old")"
    done
}

backup_now() {
  out="$DIR/$PREFIX-$(date +%Y%m%d-%H%M%S)-auto.dump"
  tmp="$out.partial"
  if pg_dump -Fc -f "$tmp" && pg_restore -l "$tmp" >/dev/null; then
    mv -- "$tmp" "$out"
    log "written $(basename "$out") ($(du -h "$out" | cut -f1))"
    prune
  else
    rm -f -- "$tmp"
    log "FAILED: no backup written"
    return 1
  fi
}

done_today() { ls "$DIR"/"$PREFIX"-"$(date +%Y%m%d)"-*-auto.dump >/dev/null 2>&1; }

if [ "${1:-}" = "--now" ]; then
  backup_now
  exit $?
fi

rm -f -- "$DIR"/"$PREFIX"-*-auto.dump.partial # left by a stop mid-dump
log "daily from $(printf %02d "$HOUR"):00, keeping the newest $KEEP in $DIR"
while :; do
  hour=$(date +%H) # "00".."23"; ${hour#0} drops one leading zero
  if [ "${hour#0}" -ge "$HOUR" ]; then
    if ! done_today; then
      backup_now || { sleep "$RETRY_AFTER"; continue; }
    fi
  fi
  sleep "$CHECK_EVERY"
done
