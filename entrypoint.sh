#!/usr/bin/env bash

if [[ -n "$DEBUG" ]]
then
  set -x
fi

CREDENTIALS_PATH="${CREDENTIALS_PATH:-/config/credentials.json}"
TOKEN_PATH="${TOKEN_PATH:-/config/token}"
CALENDAR="${CALENDAR}"
ICS_URL="${ICS_URL}"
PROXY="${PROXY}"

CLEAR="${CLEAR}"
DELETE="${DELETE}"

GCAL_UID="$(id -u gcal)"
GCAL_GID="$(id -g gcal)"
chown -R "${GCAL_UID}:${GCAL_GID}" /config

IMPORT_CMD=(/app/gcal_import.py -c "$CREDENTIALS_PATH" -t "$TOKEN_PATH")

if [[ -n "$DEBUG" ]]
then
  IMPORT_CMD+=(--debug)
fi

if [[ -n "$PROXY" ]]
then
  IMPORT_CMD+=(--proxy "$PROXY")
fi

if [[ -n "$CLEAR" ]]
then
  IMPORT_CMD+=(--clear)
fi

if [[ -n "$DELETE" ]]
then
  IMPORT_CMD+=(--delete)
fi

# Confluence settings
if [[ -n "$CONFLUENCE_URL" ]]
then
  IMPORT_CMD+=(--confluence-url "$CONFLUENCE_URL")
fi

if [[ -n "$CONFLUENCE_USERNAME" ]]
then
  IMPORT_CMD+=(--confluence-username "$CONFLUENCE_USERNAME")
fi

if [[ -n "$CONFLUENCE_PASSWORD" ]]
then
  IMPORT_CMD+=(--confluence-password "$CONFLUENCE_PASSWORD")
fi

if [[ -n "$CONFLUENCE_CALENDAR_PREFIX" ]]
then
  IMPORT_CMD+=(--confluence-calendar-prefix "$CONFLUENCE_CALENDAR_PREFIX")
fi

# CONFLUENCE_CALENDARS is a comma-separated list
if [[ -n "$CONFLUENCE_CALENDARS" ]]
then
  readarray -d ',' -t CONFLUENCE_CALENDARS_ARRAY <<< "$CONFLUENCE_CALENDARS"
  for c in "${CONFLUENCE_CALENDARS_ARRAY[@]}"
  do
    IMPORT_CMD+=(--confluence-calendars "$c")
  done
fi

# Append remaining args
IMPORT_CMD+=("$@")

if [[ -n "$CALENDAR" ]]
then
  IMPORT_CMD+=("$CALENDAR")
fi

if [[ -n "$ICS_URL" ]]
then
  IMPORT_CMD+=("$ICS_URL")
fi

# Oneshot
if [[ -z "$INTERVAL" ]]
then
  exec sudo -u gcal "${IMPORT_CMD[@]}"
fi

# Periodic import
while true
do
  sudo -u gcal "${IMPORT_CMD[@]}"

  echo "Sleeping for $INTERVAL"
  sleep "$INTERVAL"
done
