#!/bin/sh
set -eu

domain="${TLS_DOMAIN:-artemshtodin.ru}"
live_dir="/etc/letsencrypt/live/${domain}"

if [ ! -s "${live_dir}/fullchain.pem" ] || [ ! -s "${live_dir}/privkey.pem" ]; then
  echo "Creating temporary self-signed certificate for ${domain}"
  mkdir -p "${live_dir}"
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "${live_dir}/privkey.pem" \
    -out "${live_dir}/fullchain.pem" \
    -subj "/CN=${domain}" >/dev/null 2>&1
fi
