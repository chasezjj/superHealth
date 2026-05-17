#!/usr/bin/env bash
# Manage SuperHealth services using the native service manager.
#
# Usage:
#   bash scripts/manage_service.sh start   <dashboard|vitals_receiver|daily_pipeline>
#   bash scripts/manage_service.sh stop    <dashboard|vitals_receiver|daily_pipeline>
#   bash scripts/manage_service.sh status  <dashboard|vitals_receiver|daily_pipeline>
#   bash scripts/manage_service.sh schedule daily_pipeline [hour] [minute]
#
# Environment:
#   DASHBOARD_PORT      Dashboard port (default: 8505)
#   SUPERHEALTH_PYTHON  Python executable (default: current python3)

set -euo pipefail

ACTION="${1:-}"
TARGET="${2:-}"
HOUR="${3:-7}"
MINUTE="${4:-5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_BIN="${SUPERHEALTH_PYTHON:-$(command -v python3)}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8505}"
LOG_DIR="${HOME}/.superhealth/logs/services"

if [[ -z "$ACTION" || -z "$TARGET" ]]; then
  echo "Usage: $0 <start|stop|status|schedule> <dashboard|vitals_receiver|daily_pipeline> [hour] [minute]" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# macOS — launchd
# ---------------------------------------------------------------------------

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s' "$value"
}

plist_string() {
  local indent="$1"
  local value="$2"
  printf '%s<string>%s</string>\n' "$indent" "$(xml_escape "$value")"
}

mac_label() {
  case "$TARGET" in
    dashboard)       echo "com.superhealth.dashboard" ;;
    vitals_receiver) echo "com.superhealth.vitals-receiver" ;;
    daily_pipeline)  echo "com.superhealth.daily-pipeline" ;;
    *) echo "Unsupported target: $TARGET" >&2; exit 2 ;;
  esac
}

mac_command() {
  case "$TARGET" in
    dashboard)
      echo "cd '$PROJECT_DIR' && exec '$PYTHON_BIN' -m superhealth dashboard --server.port='$DASHBOARD_PORT'"
      ;;
    vitals_receiver)
      echo "cd '$PROJECT_DIR' && exec '$PYTHON_BIN' -m superhealth.api.vitals_receiver"
      ;;
    daily_pipeline)
      echo "cd '$PROJECT_DIR' && exec '$PYTHON_BIN' -m superhealth.daily_pipeline"
      ;;
    *) echo "Unsupported target: $TARGET" >&2; exit 2 ;;
  esac
}

mac_log_prefix() {
  case "$TARGET" in
    dashboard)       echo "dashboard" ;;
    vitals_receiver) echo "vitals_receiver" ;;
    daily_pipeline)  echo "daily_pipeline" ;;
  esac
}

write_launchd_plist() {
  local label="$1" command="$2" plist="$3" mode="$4"
  local prefix
  prefix="$(mac_log_prefix)"

  {
    printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>'
    printf '%s\n' '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    printf '%s\n' '<plist version="1.0"><dict>'
    printf '%s\n' '  <key>Label</key>'; plist_string "  " "$label"
    printf '%s\n' '  <key>WorkingDirectory</key>'; plist_string "  " "$PROJECT_DIR"
    printf '%s\n' '  <key>EnvironmentVariables</key><dict>'
    printf '%s\n' '    <key>PYTHONPATH</key>'; plist_string "    " "${PROJECT_DIR}/src"
    printf '%s\n' '    <key>PATH</key>'
    plist_string "    " "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    printf '%s\n' '  </dict>'
    printf '%s\n' '  <key>ProgramArguments</key><array>'
    plist_string "    " "/bin/bash"; plist_string "    " "-lc"; plist_string "    " "$command"
    printf '%s\n' '  </array>'
    printf '%s\n' '  <key>StandardOutPath</key>'; plist_string "  " "${LOG_DIR}/${prefix}.out.log"
    printf '%s\n' '  <key>StandardErrorPath</key>'; plist_string "  " "${LOG_DIR}/${prefix}.err.log"
    printf '%s\n' '  <key>ThrottleInterval</key><integer>20</integer>'
    if [[ "$mode" == "daemon" ]]; then
      printf '%s\n' '  <key>KeepAlive</key><true/>'
      printf '%s\n' '  <key>RunAtLoad</key><true/>'
    elif [[ "$mode" == "timer" ]]; then
      printf '%s\n' '  <key>KeepAlive</key><dict>'
      printf '%s\n' '    <key>SuccessfulExit</key><false/>'
      printf '%s\n' '  </dict>'
      printf '%s\n' '  <key>StartCalendarInterval</key><dict>'
      printf '    <key>Hour</key><integer>%s</integer>\n' "$HOUR"
      printf '    <key>Minute</key><integer>%s</integer>\n' "$MINUTE"
      printf '%s\n' '  </dict>'
    fi
    printf '%s\n' '</dict></plist>'
  } >"$plist"
}

manage_macos() {
  local label plist user_id command mode
  label="$(mac_label)"
  plist="${HOME}/Library/LaunchAgents/${label}.plist"
  user_id="$(id -u)"
  command="$(mac_command)"
  mode="daemon"
  [[ "$TARGET" == "daily_pipeline" ]] && mode="timer"

  case "$ACTION" in
    start|schedule)
      mkdir -p "${HOME}/Library/LaunchAgents"
      write_launchd_plist "$label" "$command" "$plist" "$mode"
      launchctl bootout "gui/${user_id}" "$plist" >/dev/null 2>&1 || true
      launchctl bootstrap "gui/${user_id}" "$plist"
      launchctl enable "gui/${user_id}/${label}"
      if [[ "$TARGET" != "daily_pipeline" ]]; then
        launchctl kickstart -k "gui/${user_id}/${label}"
      fi
      echo "Managed ${TARGET} with launchd (${label})"
      ;;
    stop)
      launchctl bootout "gui/${user_id}" "$plist" >/dev/null 2>&1 || true
      rm -f "$plist"
      echo "Stopped ${TARGET} launchd service"
      ;;
    status)
      launchctl print "gui/${user_id}/${label}"
      ;;
    *) echo "Unsupported action: $ACTION" >&2; exit 2 ;;
  esac
}

# ---------------------------------------------------------------------------
# Linux — systemd
# ---------------------------------------------------------------------------

systemd_unit_name() {
  case "$TARGET" in
    dashboard)       echo "superhealth-dashboard.service" ;;
    vitals_receiver) echo "superhealth-vitals-receiver.service" ;;
    daily_pipeline)  echo "superhealth-daily-pipeline.service" ;;
    *) echo "Unsupported target: $TARGET" >&2; exit 2 ;;
  esac
}

write_user_systemd_units() {
  local unit_dir="${HOME}/.config/systemd/user"
  local service timer
  mkdir -p "$unit_dir"
  service="$(systemd_unit_name)"

  if [[ "$TARGET" == "dashboard" ]]; then
    cat >"${unit_dir}/${service}" <<EOF
[Unit]
Description=SuperHealth dashboard
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}/src
ExecStart=${PYTHON_BIN} -m superhealth dashboard --server.port=${DASHBOARD_PORT}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  elif [[ "$TARGET" == "vitals_receiver" ]]; then
    cat >"${unit_dir}/${service}" <<EOF
[Unit]
Description=SuperHealth vitals receiver
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}/src
ExecStart=${PYTHON_BIN} -m superhealth.api.vitals_receiver
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  else
    timer="superhealth-daily-pipeline.timer"
    cat >"${unit_dir}/${service}" <<EOF
[Unit]
Description=SuperHealth daily pipeline
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}/src
ExecStart=${PYTHON_BIN} -m superhealth.daily_pipeline
EOF
    cat >"${unit_dir}/${timer}" <<EOF
[Unit]
Description=Run SuperHealth daily pipeline every day

[Timer]
OnCalendar=*-*-* ${HOUR}:${MINUTE}:00
Persistent=true
Unit=${service}

[Install]
WantedBy=timers.target
EOF
  fi
}

manage_linux() {
  local service timer
  service="$(systemd_unit_name)"
  timer="superhealth-daily-pipeline.timer"
  write_user_systemd_units
  systemctl --user daemon-reload

  case "$ACTION" in
    start)
      systemctl --user enable --now "$service"
      echo "Managed ${TARGET} with systemd user service (${service})"
      ;;
    schedule)
      systemctl --user enable --now "$timer"
      echo "Managed ${TARGET} with systemd user timer (${timer})"
      ;;
    stop)
      if [[ "$TARGET" == "daily_pipeline" ]]; then
        systemctl --user disable --now "$timer" >/dev/null 2>&1 || true
      fi
      systemctl --user disable --now "$service" >/dev/null 2>&1 || true
      echo "Stopped ${TARGET} systemd user service"
      ;;
    status)
      if [[ "$TARGET" == "daily_pipeline" ]]; then
        systemctl --user --no-pager --full status "$timer" || true
      fi
      systemctl --user --no-pager --full status "$service" || true
      ;;
    *) echo "Unsupported action: $ACTION" >&2; exit 2 ;;
  esac
}

# ---------------------------------------------------------------------------

case "$(uname -s)" in
  Darwin) manage_macos ;;
  Linux)  manage_linux ;;
  *) echo "Unsupported platform: $(uname -s)" >&2; exit 1 ;;
esac
