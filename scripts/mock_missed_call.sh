#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
if [[ $# -lt 1 ]]; then
  echo "usage: $0 <caller-phone>" >&2
  exit 2
fi
PHONE="$1"
SECRET="${MISSED_CALL_SHARED_SECRET:-}"

ARGS=(-sS -X POST "$BASE_URL/missed-call" -H "Content-Type: application/json")
if [[ -n "$SECRET" ]]; then
  ARGS+=(-H "X-Webhook-Secret: $SECRET")
fi

curl "${ARGS[@]}" -d "{\"event\":\"missed_call\",\"caller_phone\":\"$PHONE\"}"
echo
