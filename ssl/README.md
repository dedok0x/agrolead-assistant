# SSL

Production TLS is managed automatically by Let's Encrypt in Docker volumes:

- `letsencrypt` stores issued certificates and renewal metadata.
- `certbot_www` stores temporary HTTP-01 challenge files.

Do not commit private keys, certificate dumps, or copied files from `/etc/letsencrypt`.
The legacy `ssl/fullchain.pem` and `ssl/privkey.key` files are no longer required.
