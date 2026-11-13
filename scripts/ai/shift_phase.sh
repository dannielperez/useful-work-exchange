#!/usr/bin/env bash
# =============================================================================
# shift_phase.sh — lightweight per-cycle throughput instrumentation
# =============================================================================
#
# Records WHERE an unattended shift spends its time, so the next throughput
# review is MEASURED, not modeled (docs/ai/investigations/shift-throughput-
# architecture-review.md §1 named the absence of per-cycle timing as the reason
# every time-split there is an estimate). It is a thin, dependency-free, append-
# only event log — NOT a scheduler and NOT a gate.
#
# It writes ONLY to docs/ai/runlogs/shift.timeline.jsonl (gitignored, local
# operational state). It performs NO git/gh/network/vendor actions, reads no
# secrets, and CANNOT cross or unblock a hard safety gate — those stay the role
# prompt's + .claude/settings.json's job. One event per line, append-only.
#
# Phases (the vocabulary the report buckets by):
#   bootstrap   — context load / reading memory/board/handoffs at shift or cycle start
#   discovery   — GitHub/PR/issue discovery (gh pr list / gh pr view / gh issue list)
#   planning    — choosing the next task, scoring, designing the change
#   execution   — runner-owned agent runtime not yet refined by a tool hook
#   implementation — editing code/tests
#   validation  — pytest / ruff / mypy / manage.py check / pre-commit
#   review      — self-review / reviewer federation / PR comment
#   handoff     — board reconcile / handoff + memory + heartbeat updates
#   blocked     — waiting on a blocker (failing CI, missing dep, ambiguity)
#   gated       — stopped at a hard safety gate (owner action required)
#   idle        — backlog-gen / nothing eligible
#
# Usage:
#   shift_phase.sh classify                # PostToolUse hook: AUTO-stamp phase
#                                          # from the tool call (no agent needed)
#   shift_phase.sh log <phase> [note...]   # append one timeline event (manual)
#   shift_phase.sh report [timeline.jsonl] # per-phase totals + cycle count
#   shift_phase.sh phases                  # print the valid phase vocabulary
#
# DETERMINISTIC by default: the runner stamps `execution` before every engine
# launch and `cycle`/`idle` around runner-owned boundaries. Claude's
# `PostToolUse` hook (.claude/settings.json) then refines that coarse runtime
# into task phases. Codex therefore remains measurable even though it has no
# Claude hook. `log` remains as a manual override; the agent need not call it.
#
# Companion: scripts/ai/run_shift_worker.sh (shift markers), stop_continue.sh
# (per-cycle boundary stamp), docs/ai/CLAUDE_FIRST_UNATTENDED_SHIFT.md §7.4.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNLOGS="${REPO_ROOT}/docs/ai/runlogs"
# SHIFT_TIMELINE_FILE: test/override seam — defaults to the gitignored runlogs.
TIMELINE="${SHIFT_TIMELINE_FILE:-${RUNLOGS}/shift.timeline.jsonl}"
# Last auto-classified phase (so `classify` only stamps on a TRANSITION). Derived
# from TIMELINE so a test override keeps them together.
PHASE_STATE="${SHIFT_PHASE_STATE:-${TIMELINE%.jsonl}.phase}"
# Active-shift sentinel: `classify` is inert outside a shift (no marker) so it
# never touches an interactive session. Overridable for tests.
PHASE_DEADLINE="${SHIFT_DEADLINE_FILE:-${RUNLOGS}/shift.deadline}"
RUN_ID_FILE="${SHIFT_RUN_ID_FILE:-${RUNLOGS}/shift.run-id}"
RUNTIME_FILE="${SHIFT_RUNTIME_FILE:-${RUNLOGS}/shift.runtime.tsv}"

VALID_PHASES="bootstrap discovery planning execution implementation validation review handoff blocked gated idle cycle"

now_utc()   { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
now_epoch() { date -u +%s; }
die() { printf 'shift_phase: ERROR: %s\n' "$*" >&2; exit 2; }

# JSON-escape a single value (no jq dependency; notes are single-line).
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\t'/ }"
  s="${s//$'\n'/ }"
  printf '%s' "${s}"
}

is_valid_phase() {
  local p="$1" v
  for v in ${VALID_PHASES}; do [[ "${p}" == "${v}" ]] && return 0; done
  return 1
}

cmd_log() {
  local phase="${1:-}"; shift || true
  [[ -n "${phase}" ]] || die "log: phase required (one of: ${VALID_PHASES})"
  is_valid_phase "${phase}" || die "log: unknown phase '${phase}' (valid: ${VALID_PHASES})"
  local note="$*"
  local branch run_id engine source event
  branch="$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  run_id="$(head -1 "${RUN_ID_FILE}" 2>/dev/null || true)"
  engine="${SHIFT_PHASE_ENGINE:-}"
  [[ -n "${engine}" ]] || engine="$(awk -F '\t' 'NR==1 {print $1; exit}' "${RUNTIME_FILE}" 2>/dev/null || true)"
  source="${SHIFT_PHASE_SOURCE:-manual}"
  mkdir -p "${RUNLOGS}" "$(dirname "${TIMELINE}")" "$(dirname "${PHASE_STATE}")" 2>/dev/null || true
  # Keep the transition state aligned with every explicit boundary. In
  # particular, runner `execution` and Stop-hook `cycle` events must make the
  # next Claude tool event transition back into its refined task phase.
  printf '%s' "${phase}" > "${PHASE_STATE}" 2>/dev/null || true
  printf -v event '{"ts":"%s","epoch":%s,"run_id":"%s","source":"%s","engine":"%s","phase":"%s","branch":"%s","note":"%s"}' \
    "$(now_utc)" "$(now_epoch)" "$(json_escape "${run_id}")" \
    "$(json_escape "${source}")" "$(json_escape "${engine}")" "${phase}" \
    "$(json_escape "${branch}")" "$(json_escape "${note}")"
  printf '%s\n' "${event}" >> "${TIMELINE}" 2>/dev/null || true
  # Keep the authoritative mobile snapshot synchronized with hook and runner
  # transitions. This is local, best-effort, and cannot affect phase logging.
  if [[ -x "${SCRIPT_DIR}/worker_status.sh" ]]; then
    UNIQUEOS_WORKER_RUNLOGS="$(dirname "${TIMELINE}")" UNIQUEOS_WORKER_PHASE_EVENT="${event}" \
      "${SCRIPT_DIR}/worker_status.sh" write auto >/dev/null 2>&1 || true
  fi
}

# Summarise per-phase wall-clock: each event's duration = gap to the NEXT event.
# The final (open) event has no successor, so it contributes 0 (still in flight).
# Pure awk over the JSONL; no jq. Tolerant of hand-edits/blank lines.
cmd_report() {
  local file="${1:-${TIMELINE}}"
  [[ -f "${file}" ]] || { printf 'shift_phase report: no timeline yet (%s)\n' "${file}"; return 0; }
  awk '
    function num(s){ return s+0 }
    {
      # extract epoch and phase from the JSONL line (field order is fixed by us)
      e=""; p=""; g="";
      if (match($0, /"epoch":[0-9]+/))   { e=substr($0,RSTART+8,RLENGTH-8) }
      if (match($0, /"phase":"[^"]*"/))  { p=substr($0,RSTART+9,RLENGTH-10) }
      if (match($0, /"engine":"[^"]*"/)) { g=substr($0,RSTART+10,RLENGTH-11) }
      if (g=="") g="unknown"
      if (e=="" || p=="") next
      n++; ep[n]=num(e); ph[n]=p; en[n]=g;
    }
    END {
      if (n==0){ print "shift_phase report: timeline is empty"; exit }
      total=0; cycles=0;
      for(i=1;i<n;i++){ d=ep[i+1]-ep[i]; if(d<0)d=0; sec[ph[i]]+=d; cnt[ph[i]]++; engsec[en[i]]+=d; engcnt[en[i]]++; total+=d; if(ph[i]=="cycle")cycles++ }
      cnt[ph[n]]++; if(ph[n]=="cycle")cycles++;   # count the open event, 0 duration
      engcnt[en[n]]++;
      span = ep[n]-ep[1]; if(span<0)span=0;
      printf "Shift timeline report  (%d events, %d cycle marks, span %dm %ds)\n", n, cycles, span/60, span%60;
      printf "%-14s %10s %8s %7s\n", "phase", "time", "events", "share";
      printf "%-14s %10s %8s %7s\n", "-----", "----", "------", "-----";
      # stable, readable order
      split("bootstrap discovery planning execution implementation validation review handoff blocked gated idle cycle", ord, " ");
      for(k=1;k<=12;k++){ p=ord[k]; if(p in cnt){ s=sec[p]+0; pct=(total>0)?100*s/total:0;
        printf "%-14s %7dm%02ds %8d %6.1f%%\n", p, s/60, s%60, cnt[p], pct } }
      printf "%-14s %7dm%02ds\n", "TOTAL(closed)", total/60, total%60;
      printf "\nEngine coverage\n";
      printf "%-14s %10s %8s %7s\n", "engine", "time", "events", "share";
      printf "%-14s %10s %8s %7s\n", "------", "----", "------", "-----";
      for(g in engcnt){ s=engsec[g]+0; pct=(total>0)?100*s/total:0;
        printf "%-14s %7dm%02ds %8d %6.1f%%\n", g, s/60, s%60, engcnt[g], pct }
    }
  ' "${file}"
}

# --- DETERMINISTIC phase classification (the agent-cooperation-free path) -----
# Map a tool call to a phase by pattern. Returns a phase, or nothing for a
# "neutral" signal (ls/cat/git status/log/diff/echo) that should leave the
# current phase unchanged. Pure function (no I/O) so it is unit-testable.
classify_phase() {
  local tool="$1" inp="$2"
  case "${tool}" in
    Edit | Write | MultiEdit | NotebookEdit)
      printf 'implementation'
      return
      ;;
  esac
  case "${inp}" in
    *pytest* | *"just test"* | *"just preflight"* | *"manage.py check"* | *"manage.py test"* | *"pre-commit run"* | *ruff* | *mypy* | *coverage*)
      printf 'validation'
      ;;
    *"gh pr diff"* | *"gh pr create"* | *"gh pr ready"* | *"gh pr comment"* | *"gh pr review"* | *federate_review* | *review_pr*)
      printf 'review'
      ;;
    *"gh pr list"* | *"gh pr view"* | *"gh pr status"* | *"gh pr checks"* | *"gh issue"* | *"gh run "* | *"git fetch"*)
      printf 'discovery'
      ;;
    *task_queue*blocked*)
      printf 'blocked'
      ;;
    *task_queue* | *merge_packet* | *promote_mergeable* | *queue_reconcile* | *IMPLEMENTATION_FIRST_SCORING*)
      printf 'planning'
      ;;
    *"git commit"* | *"git add "* | *"git push"*)
      printf 'implementation'
      ;;
    *AGENT_ORCHESTRATION_BOARD* | *shift-memory* | *"docs/ai/handoffs"* | *CLAUDE_FIRST_UNATTENDED*)
      printf 'bootstrap'
      ;;
    *)
      case "${tool}" in
        Read | Grep | Glob) printf 'discovery' ;;
        *) printf '' ;;
      esac
      ;;
  esac
}

# PostToolUse hook entry. The HARNESS fires this after every tool call, so phase
# measurement no longer depends on the agent voluntarily stamping — "the agent
# forgot to stamp" is now impossible. Inert outside an active shift; emits an
# event ONLY on a phase transition (durations stay meaningful); always exit 0 so
# it can never disturb a tool result.
cmd_classify() {
  local payload flat tool inp phase last
  payload="$(cat 2>/dev/null || true)"   # always drain stdin so the pipe never blocks
  [[ -f "${PHASE_DEADLINE}" ]] || return 0   # not in a shift → inert
  flat="$(printf '%s' "${payload}" | tr '\n\t' '  ')"
  tool="$(printf '%s' "${flat}" | grep -oE '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/' || true)"
  inp="${flat%%'"tool_response"'*}"          # match the command, not the tool OUTPUT
  phase="$(classify_phase "${tool}" "${inp}")"
  [[ -n "${phase}" ]] || return 0            # neutral signal → leave phase as-is
  last="$(cat "${PHASE_STATE}" 2>/dev/null || true)"
  [[ "${phase}" == "${last}" ]] && return 0  # no transition → no event
  SHIFT_PHASE_SOURCE=hook cmd_log "${phase}" "auto:${tool:-tool}"
  return 0
}

main() {
  local sub="${1:-}"; shift || true
  case "${sub}" in
    log)     cmd_log "$@" ;;
    classify) cmd_classify ;;
    report)  cmd_report "$@" ;;
    phases)  printf '%s\n' "${VALID_PHASES}" ;;
    ""|-h|--help|help)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown command '${sub}' (use: log | report | phases)" ;;
  esac
}

# Run only when executed directly; sourcing (e.g. a test) loads the functions.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
