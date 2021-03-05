#!/usr/bin/env sh

chown -R 1099:1099 /config

exec su gcal -c "/app/gcal_import.py -c '/config/credentials.json' -t '/config/token' $*"
