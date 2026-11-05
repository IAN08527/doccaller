#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
if [[ $# -lt 1 ]]; then
  echo "usage: $0 <caller-phone> [text] [message-id]" >&2
  exit 2
fi
PHONE="$1"
TEXT="${2:-Rahul Verma, root canal, 4 Nov morning}"
MESSAGE_ID="${3:-$(date +%s)-$$}"

curl -sS -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"phone\":\"$PHONE\",\"message_id\":\"$MESSAGE_ID\",\"text\":\"$TEXT\"}"
echo
