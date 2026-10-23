#!/bin/sh
# Replaces the upstream image's gosu binary (Phase 7, task 7.4.3): gosu is built with an old Go
# standard library that image scans flag, and all it does here is drop from root to a user.
# util-linux's setpriv (part of Debian) does the same.
#
#   gosu USER[:GROUP] COMMAND [ARGS...]
set -eu
spec="$1"
shift
user="${spec%%:*}"
group="${spec#*:}"
[ "$group" = "$spec" ] && group="$(id -gn "$user")"
HOME="$(getent passwd "$user" | cut -d: -f6)"
export HOME
exec setpriv --reuid="$user" --regid="$group" --init-groups -- "$@"
