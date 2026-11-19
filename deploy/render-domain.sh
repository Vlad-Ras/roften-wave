#!/usr/bin/env bash
# Render a standalone Nginx site. Does not change DNS, certificates or running nginx.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 wave.example.com" >&2
  exit 2
fi
domain="$1"
if [[ ! "$domain" =~ ^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$ ]]; then
  echo "Invalid domain" >&2
  exit 2
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output="${here}/nginx.${domain}.conf"
sed "s/wave\.roften\.ru/${domain}/g" "${here}/nginx.conf.example" > "$output"
echo "Generated $output"
echo "Before enabling: configure DNS A/AAAA, obtain an SSL certificate for $domain, review site, run sudo nginx -t."
echo "Install as a NEW site, not by overwriting existing AzuraCast nginx configuration."
echo "After enabling the new domain, use Roften Wave Settings to update the displayed public URLs."
