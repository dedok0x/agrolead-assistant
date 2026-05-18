#!/bin/sh
set -eu

(
  while :; do
    sleep 6h
    nginx -s reload >/dev/null 2>&1 || true
  done
) &
