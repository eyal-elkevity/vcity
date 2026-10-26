#!/usr/bin/env bash
set -euo pipefail

PROJECT=vcity-512721

cd "$(dirname "$0")/.."

add_version() {
  gcloud secrets versions add "$1" --data-file=- --project "$PROJECT"
}

unquote() {
  local value=$1
  if [[ ${#value} -ge 2 ]]; then
    local first=${value:0:1} last=${value: -1}
    if [[ ( $first == '"' || $first == "'" ) && $first == "$last" ]]; then
      value=${value:1:${#value}-2}
    fi
  fi
  printf %s "$value"
}

for name in ANTHROPIC_API_KEY VOYAGE_API_KEY DEEPGRAM_API_KEY LINEAR_API_KEY GAP_LOG_SALT TWILIO_AUTH_TOKEN TRANSFER_TO; do
  value=$(unquote "$(grep -m1 "^${name}=" .env | cut -d= -f2- || true)")
  if [[ -z "$value" ]]; then
    echo "Skipping ${name}: not in .env"
    continue
  fi
  printf %s "$value" | add_version "$name"
done

read -rsp "Access code: " code
echo
if [[ -z "$code" ]]; then
  echo "Skipping ACCESS_CODE: empty"
  exit 0
fi
printf %s "$code" | add_version ACCESS_CODE
