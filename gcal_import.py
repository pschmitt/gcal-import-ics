#!/usr/bin/env python3

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pprint import pformat, pprint

import coloredlogs
import icalendar
import requests
from gcsa.event import Event as GoogleCalendarEvent
from gcsa.google_calendar import GoogleCalendar

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


def gcal_clear(gcal):
    res = list(
        gcal.get_events(
            time_min=datetime(1970, 1, 1),
            time_max=datetime(3000, 1, 1),
            single_events=False,
        )
    )
    deleted = 0
    for event in res:
        try:
            gcal.delete_event(event)
        except Exception as exc:
            # Ignore 'Resource has been deleted' exceptions
            if exc.resp["status"] != "410":  # 410: Gone -> "Resource has been deleted"
                LOGGER.error(
                    f"Exception caught while deleting: {exc.error_details}\n{exc}"
                )
                raise exc
        deleted = deleted + 1
    return deleted


def gcal_compare(event1, event2, ignore_sequence=False):
    for prop in ["summary", "description", "location", "start", "end"]:
        p1 = getattr(event1, prop)
        p2 = getattr(event2, prop)
        if p1 in ["", None] and p2 in ["", None]:
            # Consider empty string equal to None
            continue
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
        elif p1 != p2:
            LOGGER.warning(f"The events differ by {prop}: {p1} != {p2}")
            return False

    return True


def import_events(gcal, file, proxy=None):
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
        ical = icalendar.Calendar.from_ical(requests.get(file, proxies=rq_proxies).text)
    else:
        with open(file) as f:
            ical = icalendar.Calendar.from_ical(f.read())

    gcal_imported_events = []

    for ical_event in ical.walk():
        # Skip non-events
        if ical_event.name != "VEVENT":
            LOGGER.debug("Not an event. Skip this ical item.")
            continue

        # Metadata
        ical_uid = str(ical_event.get("UID"))
        sequence = int(ical_event.get("SEQUENCE", 0))

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

        LOGGER.info(
            f'Processing ICS event "{summary}"\nUID: {ical_uid}\nRRULE: {rrule}'
        )

        gcal_event = gcal_get_event(gcal, ical_uid)

        if gcal_event:
            LOGGER.info(f"Found matching gcal event: {gcal_event.summary}")
            gcal_sequence = gcal_event.other.get("sequence")

            LOGGER.debug(f"SEQUENCE ICS: {sequence} - GCAL: {gcal_sequence}")

            if sequence > gcal_sequence:
                LOGGER.info("😱 SEQUENCE incremented. Event will be updated.")

                gcal_event.other["sequence"] = sequence
                gcal_event.other["status"] = status

                gcal_event.summary = summary
                gcal_event.description = description
                gcal_event.location = location

                gcal_event.start = start
                gcal_event.end = end

                if rrule:
                    gcal_event.recurrence = [rrule]

                updated_event = gcal.update_event(gcal_event)
                if gcal_compare(gcal_event, updated_event, ignore_sequence=True):
                    LOGGER.info(f"✅ Event updated successfully")
                else:
                    LOGGER.warning(f"❗ Event did not update correctly")
                gcal_imported_events.append(updated_event)
            elif gcal_sequence > sequence:
                LOGGER.info(f"⏩ The Google Calendar entry has a higher SEQUENCE. Skip.")
                gcal_imported_events.append(gcal_event)
            else:
                LOGGER.info(f"⏩ Same sequence number ({sequence}). Skip.")
                gcal_imported_events.append(gcal_event)
        else:
            LOGGER.info(f"No gcal event found. Creating a new one")
            # TODO Create event
            gcal_event = GoogleCalendarEvent(
                iCalUID=ical_uid,
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                default_reminders=True,
            )
            gcal_event.other["status"] = status
            gcal_event.other["sequence"] = sequence

            if rrule:
                gcal_event.recurrence = [rrule]

            try:
                res = gcal.import_event(gcal_event)

                if not gcal_compare(gcal_event, res):
                    LOGGER.warning(
                        "❗ The event was not created as intended. Let's update it."
                    )
                    LOGGER.debug(
                        "GOOGLE CALENDAR API RESULT:\n"
                        + pformat(
                            {k: v for (k, v) in vars(res).items() if k != "description"}
                        )
                    )
                    res.summary = summary
                    res.description = description
                    res.location = location
                    res.start = start
                    res.end = end
                    res.other["sequence"] = sequence
                    res.other["status"] = status
                    res = gcal.update_event(res)
                    # We need to ignore the sequence here since updating the
                    # event does increase it
                    if not gcal_compare(gcal_event, res, ignore_sequence=True):
                        LOGGER.critical("💥 Even updating did not help.")
                        LOGGER.debug(
                            "GOOGLE CALENDAR API RESULT:\n"
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
                gcal_imported_events.append(res)
            except:
                LOGGER.error(f"🚨 Failed to create event")
                raise

    return gcal_imported_events


def delete_other_events(gcal, imported_events, include_past_events=False):
    deleted_count = 0
    # Fetch all future events
    events = list(
        gcal.get_events(time_min=datetime.now(), time_max=datetime(3000, 1, 1))
    )

    imported_uids = [x.other["iCalUID"] for x in imported_events]

    for ev in events:
        LOGGER.debug(f"Event ID: {ev.other['iCalUID']}")
        if ev.other["iCalUID"] not in imported_uids:
            LOGGER.warning(f"🕵️  Fringe event found: {ev.summary}. Deleting it!")
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
    parser.add_argument("-t", "--token", required=True, help="Path to token file")
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
    parser.add_argument("CALENDAR_ID")
    parser.add_argument("ICS_FILE")
    return parser.parse_args()


def main():
    args = parse_args()

    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.CRITICAL)
    coloredlogs.install(
        level="DEBUG" if args.debug else "INFO",
        logger=LOGGER,
        fmt="[%(asctime)s] %(name)s %(levelname)s %(message)s",
    )

    gcal = GoogleCalendar(
        calendar=args.CALENDAR_ID,
        credentials_path=args.credentials,
        token_path=args.token,
    )

    # FIXME Check the ICS file/url first.
    # Clear?
    if args.clear:
        deleted = gcal_clear(gcal)
        LOGGER.warning(f"✂️ Deleted {deleted} events")

    # Import
    events = import_events(gcal, args.ICS_FILE, proxy=args.proxy)
    LOGGER.info(f"ℹ️ Imported/updated {len(events)} events")

    if args.delete:
        if events:
            deleted = delete_other_events(gcal, events)
            LOGGER.warning(f"✂️ Deleted {deleted} fringe events")
        else:
            LOGGER.error(f"🚨 No event was imported. Deletion of fringe events was skipped.")
    return events


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
