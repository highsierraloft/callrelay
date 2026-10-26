#!/bin/sh
set -eu
CONTROL_BRIDGE_TOKEN=$(jq -er '.bridge_token' /run/secrets/control-access.json)
CONTROL_OWNER_LOGIN=$(jq -er '.owner_login' /run/secrets/control-access.json)
CONTROL_PROXY_IPS=$(jq -er '.trusted_proxy_ips | select(length > 0) | join(" ")' /run/secrets/control-access.json)
CONTROL_WS_ORIGIN=$(jq -er '.public_origin | sub("^https"; "wss")' /run/secrets/control-access.json)
export CONTROL_BRIDGE_TOKEN CONTROL_OWNER_LOGIN CONTROL_WS_ORIGIN CONTROL_PROXY_IPS
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
