#!/usr/bin/env bash
# Scrape the Next Gen Stats API for a season range and commit + push per season.
#
# Stages, in build order (schedules first -- every other stage enumerates from
# the schedule parquet it writes):
#   01 schedules  02 teams  03 statboard  04 leaders  05 gamecenter
#
# Usage: bash scripts/daily_ngs_scraper.sh [-s YYYY] [-e YYYY] [-r true|false]
#   -s/-e  seasons, STARTING calendar year (2025 = the 2025-26 season);
#          default = the current season (rolls over after Labor Day, not 1 Jan)
#   -r     rescrape payloads already valid on disk. Default TRUE: the daily
#          run must refresh the in-progress week's leaderboards, which change
#          after every game. Pass -r false for a backfill (skips complete
#          weeks / FINAL games already banked; incomplete weeks are refetched
#          regardless -- finality is read from the schedule, not from a file
#          existing).
#
# Commit subject is LOAD-BEARING: nfl_ngs_data_trigger.yml forwards it to
# nfl-ngs-data, whose workflow parses the years with `Start:\s*\K[0-9]{4}`.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

START_YEAR=""; END_YEAR=""; RESCRAPE="true"
while getopts s:e:r: flag; do
  case "${flag}" in
    s) START_YEAR=${OPTARG};;
    e) END_YEAR=${OPTARG};;
    r) RESCRAPE=${OPTARG};;
    *) echo "usage: $0 [-s YYYY] [-e YYYY] [-r true|false]" >&2; exit 2;;
  esac
done

export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
mkdir -p logs

# Commit + push, surviving a remote that moved while the scrape was running.
# Stage and commit FIRST so the tree is clean, then reconcile. `rebase --merge`,
# never `pull --rebase`: the am backend base64-encodes every blob it replays.
sdv_commit_push() {
  local msg="$1"; shift
  git add -- "$@" >/dev/null 2>&1 || true
  if git diff --cached --quiet; then
    echo "nothing to commit for: $msg"
    return 0
  fi
  git commit -q -m "$msg" || { echo "::warning ::commit failed: $msg"; return 1; }
  local attempt
  for attempt in 1 2 3; do
    if git push -q origin HEAD; then
      echo "pushed: $msg (attempt $attempt)"
      return 0
    fi
    echo "push rejected (attempt $attempt); syncing with origin"
    git fetch --quiet origin main || true
    if ! git rebase --merge origin/main >/dev/null 2>&1; then
      git rebase --abort >/dev/null 2>&1 || true
      echo "::error ::cannot rebase onto origin/main for: $msg"
      return 1
    fi
  done
  echo "::error ::push still rejected after 3 attempts: $msg"
  return 1
}

# Resolve the interpreter INSIDE the logging block so a resolver FATAL lands
# in the log rather than leaving an empty logs/ that reads as "never ran".
RC=0
{
  # shellcheck source=scripts/_venv.sh
  . "$REPO/scripts/_venv.sh"
  PY="$SDV_PY"
  echo "[$(date -u '+%F %T')Z] interpreter: $PY"
  sdv_preflight sportsdataverse.dl_utils polars ngs_raw

  if [ -z "$START_YEAR" ]; then
    START_YEAR=$(PYTHONPATH=python "$PY" -c 'from ngs_raw.cli import current_season; print(current_season())')
  fi
  END_YEAR=${END_YEAR:-$START_YEAR}
  echo "[$(date -u '+%F %T')Z] ngs raw scrape start: seasons ${START_YEAR}-${END_YEAR} rescrape=${RESCRAPE}"

  git config --local user.email "action@github.com"
  git config --local user.name "Github Action"
  git fetch --quiet origin main || echo "WARN: fetch failed; push may be rejected"
  if [ -z "$(git status --porcelain)" ] && ! git rebase --merge origin/main >/dev/null 2>&1; then
    git rebase --abort >/dev/null 2>&1 || true
    echo "[$(date -u '+%F %T')Z] WARN: rebase onto origin/main failed; continuing on local HEAD"
  fi
} 2>&1 | tee -a "logs/daily_ngs_$(date -u +%Y%m%d).log"

for i in $(seq "${START_YEAR}" "${END_YEAR}"); do
  LOGFILE="logs/nfl_ngs_raw_logfile_${i}.log"
  TMPLOG=$(mktemp "/tmp/nfl_ngs_raw_${i}.XXXXXX.log")
  SEASON_RC=0
  {
    echo "=== season $i  $(date -u '+%F %T')Z ==="
    for stage in ngs_01_schedules_scrape ngs_02_teams_scrape ngs_03_statboard_scrape ngs_04_leaders_scrape ngs_05_gamecenter_scrape; do
      t0=$(date +%s)
      # Gamecenter is per FINAL game and never changes once banked: a daily
      # refresh must not re-download every game of the season.
      r="$RESCRAPE"; [ "$stage" = "ngs_05_gamecenter_scrape" ] && r="false"
      PYTHONPATH=python "$PY" "python/${stage}.py" -s "$i" -e "$i" -r "$r" || { rc=$?; echo "::warning ::$stage $i rc=$rc"; SEASON_RC=$rc; }
      echo "stage $stage elapsed=$(( $(date +%s) - t0 ))s"
    done
    echo "season $i EXIT=$SEASON_RC"
    # Commit whatever landed even if a stage failed -- partial output is usable
    # and the failure is carried in the exit code, never hidden.
    sdv_commit_push "NGS Raw Update (Start: $i End: $i)" ngs || SEASON_RC=1
  } 2>&1 | tee "$TMPLOG"
  cp "$TMPLOG" "$LOGFILE"; rm -f "$TMPLOG"
  sdv_commit_push "NGS Raw log update (Start: $i End: $i)" "$LOGFILE" || SEASON_RC=1
  [ "$SEASON_RC" -eq 0 ] || RC=1
done

echo "[$(date -u '+%F %T')Z] ngs raw scrape done EXIT=$RC" | tee -a "logs/daily_ngs_$(date -u +%Y%m%d).log"
exit "$RC"
