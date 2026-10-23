# Headers sent only over HTTPS (included by nginx.tls.conf; never over plain HTTP).
# Browsers refuse plain HTTP to this host for two years (task 7.2.1).
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
# A second policy, enforced alongside the main one: fetch every sub-resource over HTTPS.
add_header Content-Security-Policy "upgrade-insecure-requests" always;
