#!/usr/bin/env sh

chown -R 1000:1000 /config

exec su gcal -c "/app/gcal_import.py -c '/config/credentials.json' -t '/config/token' $*"
