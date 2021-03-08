# 📅 Import ICS files to your Google Calendar, without the web interface.

[![Build](https://github.com/pschmitt/gcal-import-ics/actions/workflows/build.yaml/badge.svg)](https://github.com/pschmitt/gcal-import-ics/actions/workflows/build.yaml)

# TL;DR

```shell
docker run -it --rm \
  -e "TZ=Europe/Berlin" \
  -v "$PWD/config:/config" \
  -v "$PWD/data:/data:ro" \
  pschmitt/gcal-import-ics:latest \
    c_randomCalendarId@group.calendar.google.com \
    /data/calendar.ics
```

and the main star of the show is [🌟 gcal_import.py](./gcal_import.py)

# Installation

```shell
git clone https://github.com/pschmitt/gcal-import-ics
cd gcal-import-ics

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

# Setup

You will need the following:

- Your calendar ID
- a JSON credentials file. See [here](https://google-calendar-simple-api.readthedocs.io/en/latest/getting_started.html#credentials) for instructions.

# Usage

```shell
python3 gcal_import.py \
  --debug \
  -c ./credentials/client_secret_blablablabla.apps.googleusercontent.com.json \
  -t ./credentials/gcal.token \
  -p "PROXY=socks5h://corp.acme.com:8080" \
  "$GOOGLE_CALENDAR_ID" \
  "$ICS_FILE_OR_URL"
```

# But why?

https://groups.google.com/g/google-apps-manager/c/tgIAB35I5EE?pli=1

# TODO

- [ ] 🚧 Fix status for some imported events (they are created with status=cancelled)
- [x] Requests proxy option
- [x] Delete unknown events
- [x] With an optional date parameter (ie only delete future events)
- [x] All-day events
- [ ] Attendees
- [ ] Reminders
- [ ] Store metadata ("imported by gcal_import.py") somewhere (in source? organizer? description?)
- [x] Clear calendar before import (optional)
- [ ] Wait and retry when rate-limitted
- [x] Optionally ignore SEQUENCE and always update
- [x] Allow passing in the calendar name, instead of the ID
- [x] Support recurring event *instances*
