#!/bin/sh
# Adjust the `paperstand` user to PUID/PGID, then drop privileges and run the
# command it was given — `serve` (the Dockerfile's default `CMD`) or
# `organize`, for the optional second service. When the container is already
# started as a non-root user (`user:` in compose, `--user` on the command
# line) nothing is adjusted and the process simply runs as whoever it was
# started as.
set -eu

if [ "$(id -u)" != "0" ]; then
    exec paperstand "$@"
fi

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Both values reach `groupmod` and `usermod`, which run as root, so they are
# checked before anything else: decimal digits only, no option-like strings, and
# never 0 — a container serving as root would defeat the privilege drop.
# Prints the normalised id on stdout; exits the script on a bad value.
normalise_id() {
    _name="$1"
    _raw="$2"
    case "$_raw" in
        '' | *[!0-9]*)
            echo "entrypoint: $_name must be a decimal number, got '$_raw'" >&2
            exit 1
            ;;
    esac
    # Strip leading zeros so that the comparisons below are decimal.
    _value="$_raw"
    while [ "${_value#0}" != "$_value" ]; do
        _value="${_value#0}"
    done
    if [ -z "$_value" ] || [ "${#_value}" -gt 10 ]; then
        echo "entrypoint: $_name must be between 1 and 2147483647, got '$_raw'" >&2
        exit 1
    fi
    if [ "$_value" -lt 1 ] || [ "$_value" -gt 2147483647 ]; then
        echo "entrypoint: $_name must be between 1 and 2147483647, got '$_raw'" >&2
        exit 1
    fi
    echo "$_value"
}

# `set -e` also covers an assignment whose command substitution fails, so a
# rejected id ends the script here, before any account is touched.
PUID="$(normalise_id PUID "$PUID")"
PGID="$(normalise_id PGID "$PGID")"

current_gid="$(getent group paperstand | cut -d: -f3)"
current_uid="$(id -u paperstand)"

if [ "$current_gid" != "$PGID" ]; then
    groupmod -o -g "$PGID" paperstand
fi

if [ "$current_uid" != "$PUID" ]; then
    usermod -o -u "$PUID" paperstand
fi

# Chown one bind-mounted tree to $PUID:$PGID, only when it is not already
# owned correctly: a large directory should not be walked on every start.
# `-xdev` keeps the walk inside the mount, `chown -h` never follows a
# symlink out of it. Some bind mounts (a few NAS setups) refuse chown while
# staying perfectly writable, so a failure is reported and the start
# continues regardless.
chown_tree() {
    _dir="$1"
    if [ ! -d "$_dir" ]; then
        return 0
    fi
    _owner="$(stat -c '%u:%g' "$_dir")"
    if [ "$_owner" = "$PUID:$PGID" ]; then
        return 0
    fi
    chown -h "$PUID:$PGID" "$_dir" \
        || echo "entrypoint: cannot chown $_dir to $PUID:$PGID, continuing" >&2
    find "$_dir" -xdev \( ! -user "$PUID" -o ! -group "$PGID" \) \
        -exec chown -h "$PUID:$PGID" {} + \
        || echo "entrypoint: cannot chown some files under $_dir, continuing" >&2
}

# /data is the server's own writable directory; /inbox is only present for
# the optional organizer service, mounted read-write for it to move files
# out of — both have to be writable by $PUID:$PGID before privileges drop.
chown_tree /data
chown_tree /inbox

exec gosu paperstand paperstand "$@"
