#!/usr/bin/env sh

if [ -n "$DEBUG" ]
then
  set -x
fi

CREDENTIALS_PATH="${CREDENTIALS_PATH:-/config/credentials.json}"
TOKEN_PATH="${TOKEN_PATH:-/config/token}"
CALENDAR="${CALENDAR}"
ICS_URL="${ICS_URL}"

GCAL_UID="$(id -u gcal)"
GCAL_GID="$(id -g gcal)"
chown -R "${GCAL_UID}:${GCAL_GID}" /config

# Oneshot
if [ -z "$INTERVAL" ]
then
  exec su gcal -c "/app/gcal_import.py -c '${CREDENTIALS_PATH}' -t '${TOKEN_PATH}' $@"
fi

# Periodic import
while true
do
  su gcal -c "/app/gcal_import.py -c '${CREDENTIALS_PATH}' -t '${TOKEN_PATH}' $@"

  echo "Sleeping for $INTERVAL"
  sleep "$INTERVAL"
done
