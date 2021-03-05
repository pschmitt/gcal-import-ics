#!/usr/bin/env sh

chown -R 1099:1099 /config

# Oneshot
if [ -z "$INTERVAL" ]
then
  exec su gcal -c "/app/gcal_import.py -c '/config/credentials.json' -t '/config/token' $*"
fi

# Periodic import
while true
do
  su gcal -c "/app/gcal_import.py -c '/config/credentials.json' -t '/config/token' $*"

  echo "Sleeping for $INTERVAL"
  sleep "$INTERVAL"
done
