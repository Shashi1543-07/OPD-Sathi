# Proxy settings for every /api/ location (included by nginx.conf and nginx.tls.conf).
#
# The API is served from the web app's own address under /api, so a phone that scans the
# token QR code needs to reach only this one port. The prefix is stripped with a rewrite
# because a regex location cannot carry a URI in proxy_pass.
rewrite ^/api/(.*)$ /$1 break;
proxy_pass http://api:8000;
proxy_http_version 1.1;
proxy_set_header Host $host;
# Overwrite (never append) so a client cannot fake its address for rate limits.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
# Kiosk audio WebSocket.
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
# Live queue and patient status use server-sent events.
proxy_buffering off;
proxy_read_timeout 1h;
# The API scopes the staff refresh cookie to /auth; here that path is /api/auth.
proxy_cookie_path /auth /api/auth;
