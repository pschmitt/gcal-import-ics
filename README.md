# CLI import of ICS files

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
  --delete \
  --clear \
  -c ./credentials/client_secret_blablablabla.apps.googleusercontent.com.json \
  -t ./credentials/gcal.token \
  -p "PROXY=socks5h://corp.acme.com:8080" \
  "$GOOGLE_CALENDAR_ID" \
  "$ICS_FILE_OR_URL"
```

# TODO

- [ ] 🚧 Fix status for some imported events (they are created with status=cancelled)
- [x] Requests proxy option
- [x] Delete unknown events
- [x] With an optional date parameter (ie only delete future events)
- [ ] All-day events
- [ ] Attendees
- [ ] Store metadata ("imported by gcal_import.py") somewhere (in source? organizer? description?)
- [x] Clear calendar before import (optional)
- [ ] Wait and retry when rate-limitted
