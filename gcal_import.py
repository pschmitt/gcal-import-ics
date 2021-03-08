#!/usr/bin/env python3

import argparse
import logging
import re
import sys
from datetime import datetime
from pprint import pformat

import coloredlogs
import icalendar
import requests
from gcsa.event import Event as GoogleCalendarEvent
from gcsa.google_calendar import GoogleCalendar
from googleapiclient.errors import HttpError as GoogleHttpError

LOGGER = logging.getLogger("gcal-ics-import")


def gcal_get_event(gcal, ical_uid):
    res = list(
        gcal.get_events(
            iCalUID=ical_uid,
            time_min=datetime(1970, 1, 1),
            time_max=datetime(3000, 1, 1),
            single_events=False,
        )
    )
    return res[0] if res else None


def gcal_clear(gcal, dry_run=False):
    res = list(
        gcal.get_events(
            time_min=datetime(1970, 1, 1),
            time_max=datetime(3000, 1, 1),
            single_events=False,
        )
    )
    deleted = 0
    for event in res:
        if dry_run:
            LOGGER.info(f'Dry run: Would have deleted event "{event.summary}"')
        else:
            try:
                gcal.delete_event(event)
            except GoogleHttpError as exc:
                # Ignore 'Resource has been deleted' exceptions
                if (
                    exc.resp["status"] != "410"
                ):  # 410: Gone -> "Resource has been deleted"
                    LOGGER.error(
                        "Exception caught while deleting:"
                        f"{exc.error_details}\n{exc}"
                    )
                    raise exc
            deleted = deleted + 1
    return deleted


def gcal_compare(event1, event2, ignore_sequence=False):
    for prop in [
        "summary",
        "description",
        "location",
        "start",
        "end",
        "recurrence",
        "transparency",
    ]:
        p1 = getattr(event1, prop)
        p2 = getattr(event2, prop)
        if p1 in ["", None] and p2 in ["", None]:
            # Consider empty string equal to None
            continue
        elif (
            prop == "transparency"
            and p1 in ["", None, "opaque"]
            and p2 in ["", None, "opaque"]
        ):
            # "opaque" is the default transparency
            continue
        elif prop == "recurrence":
            if not isinstance(p1, list) or not isinstance(p2, list):
                LOGGER.error(
                    "Recurrence is supposed to be a list, got:"
                    f"{type(p1)} and {type(p2)})"
                )
                return False
            if len(p1) != len(p2):
                return False

            # Sort both, so that we compare apples to apples
            p1.sort()
            p2.sort()
            i = 0
            LOGGER.debug(f"Comparing {p1} to {p2}")
            while i < len(p1):
                # Remove RRULE: and split
                rrule1 = set(
                    re.sub(
                        r"^{0}".format(re.escape("RRULE:")), "", p1[i]
                    ).split(";")
                )
                rrule2 = set(
                    re.sub(
                        r"^{0}".format(re.escape("RRULE:")), "", p2[i]
                    ).split(";")
                )
                if rrule1 != rrule2:
                    return False
                i += 1
        elif p1 != p2:
            LOGGER.warning(f"The events differ by {prop}: {p1} != {p2}")
            return False

    # Check the "other" dict
    other_keys = ["status"]
    if not ignore_sequence:
        other_keys.append("sequence")

    for prop in other_keys:
        p1 = event1.other.get(prop)
        p2 = event2.other.get(prop)
        if p1 in ["", None] and p2 in ["", None]:
            # Consider empty string equal to None
            continue
        elif (
            prop == "status"
            and p1 in ["", None, "confirmed"]
            and p2 in ["", None, "confirmed"]
        ):
            # "confirmed" is the default status
            continue
        elif p1 != p2:
            LOGGER.warning(f"The events differ by {prop}: {p1} != {p2}")
            return False

    return True


def read_ics(file, proxy=None):
    if re.match("https?://.*", file):
        rq_proxies = (
            {
                "http": proxy,
                "https": proxy,
            }
            if proxy
            else {}
        )

        LOGGER.info(f"Fetching ICS file from {file} (proxy: {proxy})")
        ics_text = requests.get(file, proxies=rq_proxies).text
    else:
        with open(file) as f:
            ics_text = icalendar.Calendar.from_ical(f.read())
    return icalendar.Calendar.from_ical(ics_text)


def import_events(gcal, file, proxy=None, dry_run=False):
    gcal_changes = {"updated": [], "created": [], "untouched": []}
    ical = read_ics(file, proxy)

    for ical_event in ical.walk():
        # Skip non-events
        if ical_event.name != "VEVENT":
            LOGGER.debug(
                f"Not an event ({ical_event.name}). Skip this ical item."
            )
            continue

        # Metadata
        ical_uid = str(ical_event.get("UID"))
        # sequence = int(ical_event.get("SEQUENCE", 0))
        transparency = str(ical_event.get("TRANSP")).lower()

        summary = ical_event.decoded("SUMMARY").decode("utf-8").strip()
        description = ical_event.decoded("DESCRIPTION").decode("utf-8")
        status = ical_event.decoded("STATUS").decode("utf-8").lower()
        location = ical_event.decoded("LOCATION").decode("utf-8")

        start = ical_event.decoded("DTSTART")
        end = ical_event.decoded("DTEND")
        rrule = (
            "RRULE:" + ical_event.get("RRULE").to_ical().decode("utf-8")
            if "RRULE" in ical_event
            else None
        )

        LOGGER.info(f'Processing ICS event "{summary}"\n')
        LOGGER.debug(f"UID: {ical_uid}\nRRULE: {rrule}")

        # Create a new Event object
        gcal_ics_event = GoogleCalendarEvent(
            iCalUID=ical_uid,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            default_reminders=True,
            transparency=transparency,
        )

        if rrule:
            gcal_ics_event.recurrence = [rrule]
        if status:
            gcal_ics_event.other["status"] = status
        # if sequence:
        #     gcal_ics_event.other["sequence"] = sequence

        gcal_event = gcal_get_event(gcal, ical_uid)

        if gcal_event:
            # Update event
            LOGGER.info(f'Found matching gcal event: "{gcal_event.summary}"')
            if gcal_compare(gcal_event, gcal_ics_event, ignore_sequence=True):
                LOGGER.info("⏩ Same event data. Skip.")
                gcal_changes["untouched"].append(gcal_event)
            else:

                # Copy event data
                gcal_event.summary = gcal_ics_event.summary
                gcal_event.description = gcal_ics_event.description
                gcal_event.location = gcal_ics_event.location
                gcal_event.transparency = gcal_ics_event.transparency

                gcal_event.start = gcal_ics_event.start
                gcal_event.end = gcal_ics_event.end
                gcal_event.recurrence = gcal_ics_event.recurrence
                if status:
                    gcal_event.other["status"] = status

                if dry_run:
                    LOGGER.info(
                        "Dry run: Would have updated event "
                        f'"{gcal_event.summary}"'
                    )
                else:
                    updated_event = gcal.update_event(gcal_event)
                    if gcal_compare(
                        gcal_ics_event, updated_event, ignore_sequence=True
                    ):
                        LOGGER.info("✅🆙 Event successfully updated")
                    else:
                        LOGGER.error("❗ Event did not update correctly")
                    gcal_changes["updated"].append(updated_event)
            continue

        # Create new event
        LOGGER.info("No gcal event found. Creating a new one")

        if dry_run:
            LOGGER.info(
                "Dry run: Would have created event"
                f'"{gcal_ics_event.summary}"'
            )
            continue
        try:
            res = gcal.import_event(gcal_ics_event)

            if not gcal_compare(gcal_ics_event, res, ignore_sequence=True):
                LOGGER.warning(
                    "❗ The event was not created as intended. "
                    "Let's update it."
                )
                LOGGER.debug(f"Original (ICS) event status: {status}")
                LOGGER.debug(
                    "GOOGLE CALENDAR API RESULT (w/o description):\n"
                    + pformat(
                        {
                            k: v
                            for (k, v) in vars(res).items()
                            if k != "description"
                        }
                    )
                )

                # Copy event data
                res.summary = gcal_ics_event.summary
                res.description = gcal_ics_event.description
                res.transparency = gcal_ics_event.transparency
                res.location = gcal_ics_event.location
                res.start = gcal_ics_event.start
                res.end = gcal_ics_event.end
                res.recurrence = gcal_ics_event.recurrence
                # res.other["sequence"] = gcal_ics_event.sequence
                if gcal_ics_event.other.get("status"):
                    res.other["status"] = gcal_ics_event.other["status"]
                res = gcal.update_event(res)

                # We need to ignore the sequence here since updating the
                # event does increase it
                if not gcal_compare(gcal_ics_event, res, ignore_sequence=True):
                    LOGGER.critical("💥 Even updating did not help.")
                    LOGGER.debug(f"Original (ICS) event status: {status}")
                    LOGGER.debug(
                        "GOOGLE CALENDAR API RESULT (w/o description):\n"
                        + pformat(
                            {
                                k: v
                                for (k, v) in vars(res).items()
                                if k != "description"
                            }
                        )
                    )
                else:
                    LOGGER.info("✅ Created event sucessfully")
            else:
                LOGGER.info("✅ Created event sucessfully")
            gcal_changes["created"].append(res)
        except Exception as exc:
            LOGGER.error(f"🚨 Failed to create event\n{exc}")
            raise exc

    return gcal_changes


def delete_other_events(
    gcal, imported_events, include_past_events=False, dry_run=False
):
    LOGGER.warning("Searching for fringe events")
    deleted_count = 0
    min = datetime(1970, 1, 1) if include_past_events else datetime.now()
    # Fetch all events
    events = list(gcal.get_events(time_min=min, time_max=datetime(3000, 1, 1)))

    imported_uids = [
        x.other["iCalUID"]
        for x in imported_events.get("updated")
        + imported_events.get("created")
        + imported_events.get("untouched")
    ]

    for ev in events:
        if ev.other["iCalUID"] not in imported_uids:
            if dry_run:
                LOGGER.info(
                    f"Dry run: Would have deleted fringe event {ev.summary}"
                )
            else:
                LOGGER.warning(
                    f"🕵️  Fringe event found: {ev.summary}. Deleting it!"
                )
                gcal.delete_event(ev)
                deleted_count = deleted_count + 1
    return deleted_count


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d",
        "--debug",
        required=False,
        action="store_true",
        default=False,
        help="Debug output",
    )
    parser.add_argument(
        "-c", "--credentials", required=True, help="Path to credentials file"
    )
    parser.add_argument(
        "-t", "--token", required=True, help="Path to token file"
    )
    parser.add_argument(
        "-p", "--proxy", required=False, help="PROXY to use to fetch the ICS"
    )
    parser.add_argument(
        "-C",
        "--clear",
        required=False,
        action="store_true",
        default=False,
        help="❗ DELETE ALL EVENTS FROM CALENDAR BEFORE IMPORTING",
    )
    parser.add_argument(
        "-D",
        "--delete",
        required=False,
        action="store_true",
        default=False,
        help="Delete future events that are not in the provided ICS file",
    )
    parser.add_argument(
        "-k",
        "--dry-run",
        required=False,
        action="store_true",
        default=False,
        help="Dry-run. Do not add/remove/update any events",
    )
    parser.add_argument("CALENDAR", help="Google Calendar ID or name")
    parser.add_argument("ICS_FILE", help="File path or URL")
    return parser.parse_args()


def main():
    args = parse_args()

    logging.getLogger("googleapiclient.discovery_cache").setLevel(
        logging.CRITICAL
    )
    coloredlogs.install(
        level="DEBUG" if args.debug else "INFO",
        logger=LOGGER,
        fmt="[%(asctime)s] %(name)s %(levelname)s %(message)s",
    )

    gcal = GoogleCalendar(
        credentials_path=args.credentials,
        token_path=args.token,
    )

    # Set calendar ID
    if re.match(".+@group.calendar.google.com", args.CALENDAR):
        calendar_id = args.CALENDAR
    else:
        # Find calendar ID
        calendars = [
            x.get("id")
            for x in gcal.service.calendarList().list().execute().get("items")
            if x.get("summary") == args.CALENDAR
        ]
        if not calendars:
            LOGGER.critical(
                f"Could not find any calendar named {args.CALENDAR}"
            )
            sys.exit(1)
        calendar_id = calendars[0]

    gcal.calendar = calendar_id
    LOGGER.debug(f"CALENDAR ID: {calendar_id}")

    # FIXME Check the ICS file/url first.
    # Clear?
    if args.clear:
        deleted = gcal_clear(gcal, args.dry_run)
        LOGGER.warning(f"✂️ Deleted {deleted} events")

    # Import
    events = import_events(
        gcal,
        args.ICS_FILE,
        proxy=args.proxy,
        dry_run=args.dry_run,
    )
    LOGGER.info(
        f"ℹ️ Imported {len(events['created'])} and "
        f"updated {len(events['updated'])} events. "
        f"Left {len(events['untouched'])} events untouched"
    )

    if args.delete:
        if events or args.dry_run:
            deleted = delete_other_events(gcal, events, dry_run=args.dry_run)
            LOGGER.warning(f"✂️ Deleted {deleted} fringe events")
        else:
            LOGGER.error(
                "🚨 No event was imported."
                "Deletion of fringe events was skipped."
            )
            return
    return events


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
