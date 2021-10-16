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

from atlassian import Confluence
from gcsa.event import Event as GoogleCalendarEvent
from gcsa.google_calendar import GoogleCalendar
from googleapiclient.errors import HttpError as GoogleHttpError

LOGGER = logging.getLogger("gcal-ics-import")


def gcal_get_event(gcal, ical_uid, single_events=False):
    res = list(
        gcal.get_events(
            iCalUID=ical_uid,
            time_min=datetime(1970, 1, 1),
            time_max=datetime(3000, 1, 1),
            single_events=single_events,
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
                LOGGER.debug(f"Events differ by RRULE: {p1} != {p2}")
                return False

            # Sort both, so that we compare apples to apples
            p1.sort()
            p2.sort()

            LOGGER.debug(f"RRULE: Comparing {p1} to {p2}")

            i = 0
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
                    LOGGER.debug(
                        f"Events differ by RRULE: {rrule1} != {rrule2}"
                    )
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


def read_ics(file, proxy=None, auth=None):
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
        ics_text = requests.get(file, proxies=rq_proxies, auth=auth).text
    else:
        with open(file) as f:
            ics_text = f.read()

    ical = icalendar.Calendar.from_ical(ics_text)
    events = []
    recurrent_event_instances = []

    for item in ical.walk():
        # Skip non-events
        if item.name != "VEVENT":
            LOGGER.debug(f"Not an event ({item.name}). Skip this ical item.")
            continue
        elif "RECURRENCE-ID" in item:
            recurrent_event_instances.append(item)
        else:
            events.append(item)
    # Append the recurent event instances at the end so that they get
    # processed last. Otherwise we'd end up trying to fetch a recurring
    # event instance before we import the "parent" recurring event.
    return events + recurrent_event_instances


def ics_to_gcal(ical_event):
    # Metadata
    ical_uid = str(ical_event.get("UID"))
    # sequence = int(ical_event.get("SEQUENCE", 0))
    transparency = (
        str(ical_event.get("TRANSP")).lower()
        if "TRANSP" in ical_event
        else "opaque"
    )

    summary = (
        ical_event.decoded("SUMMARY").decode("utf-8").strip()
        if "SUMMARY" in ical_event
        else ""
    )
    description = (
        ical_event.decoded("DESCRIPTION").decode("utf-8")
        if "DESCRIPTION" in ical_event
        else ""
    )
    status = (
        ical_event.decoded("STATUS").decode("utf-8").lower()
        if "STATUS" in ical_event
        else "confirmed"
    )
    location = (
        ical_event.decoded("LOCATION").decode("utf-8")
        if "LOCATION" in ical_event
        else ""
    )

    start = ical_event.decoded("DTSTART")
    end = ical_event.decoded("DTEND")
    rrule = (
        "RRULE:" + ical_event.get("RRULE").to_ical().decode("utf-8")
        if "RRULE" in ical_event
        else None
    )

    # Create a new Event object with the ICS data
    gcal_event = GoogleCalendarEvent(
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
        LOGGER.warning(f"NEW EVENT RRULE=[{rrule}]")
        gcal_event.recurrence = [rrule]
    if status:
        gcal_event.other["status"] = status
    # if sequence:
    #     gcal_ics_event.other["sequence"] = sequence
    return gcal_event


def import_events(gcal, file, proxy=None, auth=None, dry_run=False):
    gcal_changes = {
        "updated": [],
        "created": [],
        "untouched": [],
        "duplicates": [],
        "unsupported": [],
        "failed": [],
    }
    processed_uids = []

    for ical_event in read_ics(file, proxy, auth):
        # Create a new Event object with the ICS data
        gcal_ics_event = ics_to_gcal(ical_event)
        ical_uid = gcal_ics_event.other.get("iCalUID")
        status = gcal_ics_event.other.get("status")

        LOGGER.info(f'Processing ICS event "{gcal_ics_event.summary}"\n')
        LOGGER.debug(f"UID: {ical_uid}")

        # Check if this is an instance of a recurring event
        if "RECURRENCE-ID" in ical_event:
            LOGGER.debug("This event is an occurence of a recurring event")

            # Fetch event instance
            gcal_parent_event = gcal_get_event(gcal, ical_uid)
            try:
                gcal_events = list(
                    gcal.get_instances(
                        recurring_event=gcal_parent_event,
                        time_min=gcal_ics_event.start,
                        time_max=gcal_ics_event.end,
                        maxResults=2,
                    )
                )
            except Exception as exc:
                LOGGER.error(
                    f"Failed to find event instances for event {ical_uid}: {exc}"
                )
                gcal_changes.get("failed").append(ical_uid)
                continue

            LOGGER.debug(
                f'Recurring event: "{gcal_parent_event.summary}" '
                f"(event ID: {gcal_parent_event.event_id})"
            )
            LOGGER.debug(f"Number of matching instances: {len(gcal_events)}")
            if gcal_events:
                LOGGER.debug(f"Event instance ID: {gcal_events[0].event_id}")

            if not gcal_events:
                LOGGER.error(
                    "Could not find recurrent event instance for this timeframe"
                )
                gcal_changes.get("failed").append(ical_uid)
                continue
            elif len(gcal_events) > 1:
                LOGGER.error(
                    "Found more than one event instance. "
                    "This shouldn't happen."
                )
                gcal_changes.get("failed").append(ical_uid)
                continue

            gcal_event = gcal_events[0]

        # Check if we already processed this iCalUID
        elif ical_uid in processed_uids:
            LOGGER.info("Duplicate iCalUID detected. Skip item.")
            gcal_changes.get("duplicates").append(ical_uid)
            continue
        else:
            gcal_event = gcal_get_event(gcal, ical_uid)

        processed_uids.append(ical_uid)

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
                        gcal_changes["updated"].append(updated_event)
                    else:
                        LOGGER.error("❗ Event did not update correctly")
                        gcal_changes["failed"].append(updated_event)
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
            LOGGER.debug(
                f"New event: {gcal_ics_event} (RRULE: {gcal_ics_event.recurrence})"
            )
            try:
                res = gcal.import_event(gcal_ics_event)
            except Exception as exc:
                LOGGER.error(f"Failed to import event {gcal_ics_event}: {exc}")
                gcal_changes["failed"].append(gcal_ics_event)
                continue

            # FIXME Why does this even happen?
            # Some recurring events are created with status=cancelled 🤷
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


def get_confluence_calendar_info(url: str, username: str, password: str):
    confluence_client = Confluence(url, username=username, password=password)
    cal_metadata = []
    for c in confluence_client.team_calendars_get_sub_calendars().get(
        "payload"
    ):
        cal = c.get("subCalendar")
        cal_id = cal.get("id")
        cal_name = cal.get("name")
        cal_tz = cal.get("timeZoneId")
        ics_url = f"{url}/rest/calendar-services/1.0/calendar/export/subcalendar/{cal_id}.ics?os_authType=basic&isSubscribe=true"
        LOGGER.info(f"{cal_name} (ID: {cal_id}): {ics_url}")
        cal_metadata.append(
            {"id": cal_id, "name": cal_name, "tz": cal_tz, "url": ics_url}
        )
    return cal_metadata


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
        "-c",
        "--credentials",
        required=True,
        help="Path to Google Calendar credentials file",
    )
    parser.add_argument(
        "-t",
        "--token",
        required=True,
        help="Path to Google Calendar token file",
    )
    parser.add_argument(
        "--confluence-url", required=False, help="Confluence URL"
    )
    parser.add_argument(
        "--confluence-username", required=False, help="Confluence Username"
    )
    parser.add_argument(
        "--confluence-password", required=False, help="Confluence Password"
    )
    parser.add_argument(
        "--confluence-calendars",
        required=False,
        nargs="*",
        default=[],
        help="Confluence calendar to sync",
    )
    parser.add_argument(
        "--confluence-calendar-prefix",
        required=False,
        default="",
        help="Confluence Calendar Prefix",
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
    parser.add_argument(
        "CALENDAR", nargs="?", help="Google Calendar ID or name"
    )
    parser.add_argument("ICS_FILE", nargs="?", help="File path or URL")
    return parser.parse_args()


def import_ics(
    credentials,
    token_path,
    calendar_name,
    ics_file,
    proxy=None,
    auth=None,
    clear=False,
    delete=False,
    dry_run=False,
):
    gcal = GoogleCalendar(
        credentials_path=credentials,
        token_path=token_path,
    )

    # Set calendar ID
    if re.match(".+@group.calendar.google.com", calendar_name):
        calendar_id = calendar_name
    else:
        # Find calendar ID
        calendars = [
            x.get("id")
            for x in gcal.service.calendarList().list().execute().get("items")
            if x.get("summary") == calendar_name
        ]
        if not calendars:
            LOGGER.warning(
                f"Could not find any calendar named {calendar_name}. "
                f"Creating a new calendar named {calendar_name}"
            )
            # Create new calendar
            new_calendar = {
                "summary": calendar_name,
                # 'timeZone': 'Europe/Berlin'
            }
            res = gcal.service.calendars().insert(body=new_calendar).execute()
            calendar_id = res["id"]
        else:
            calendar_id = calendars[0]

    gcal.calendar = calendar_id
    LOGGER.debug(f"CALENDAR ID: {calendar_id}")

    # FIXME Check the ICS file/url first.
    # Clear?
    if clear:
        deleted = gcal_clear(gcal, dry_run)
        LOGGER.warning(f"✂️ Deleted {deleted} events")

    # Import
    events = import_events(
        gcal,
        ics_file,
        proxy=proxy,
        auth=auth,
        dry_run=dry_run,
    )
    LOGGER.info(
        f"ℹ️ Imported {len(events['created'])} and "
        f"updated {len(events['updated'])} events."
    )
    LOGGER.info(f"👌 {len(events['untouched'])} events were left untouched.")
    LOGGER.info(f"👯 Duplicates count: {len(events['duplicates'])}")
    LOGGER.info(f"🤷 Unsupported items: {len(events['unsupported'])}")
    LOGGER.info(f"😞 Failed items: {len(events['failed'])} ")
    LOGGER.debug(f"Failed items:\n{pformat(events['failed'])}")

    if delete:
        if events or dry_run:
            deleted = delete_other_events(gcal, events, dry_run=dry_run)
            LOGGER.warning(f"✂️ Deleted {deleted} fringe events")
        else:
            LOGGER.error(
                "🚨 No event was imported."
                "Deletion of fringe events was skipped."
            )
            return

    return events


def main():
    args = parse_args()

    logging.getLogger("googleapiclient.discovery_cache").setLevel(
        logging.CRITICAL
    )
    coloredlogs.install(
        level="DEBUG" if args.debug else "INFO",
        logger=LOGGER,
        fmt="[%(asctime)s] %(levelname)s %(message)s",
    )
    if args.confluence_url:
        confluence_calendars = get_confluence_calendar_info(
            args.confluence_url,
            args.confluence_username,
            args.confluence_password,
        )
        LOGGER.debug(f"{confluence_calendars}")
        res = []
        for ccal in confluence_calendars:
            if args.confluence_calendars and ccal.get("name").lower() not in [
                x.lower() for x in args.confluence_calendars
            ]:
                LOGGER.warning(f"Skipping calendar {ccal.get('name')}")
                continue
            res = import_ics(
                credentials=args.credentials,
                token_path=args.token,
                calendar_name=f"{args.confluence_calendar_prefix}{ccal.get('name')}",
                ics_file=ccal.get("url"),
                proxy=args.proxy,
                auth=(
                    args.confluence_username,
                    args.confluence_password,
                ),
                clear=args.clear,
                delete=args.delete,
                dry_run=args.dry_run,
            )
        return res
    else:
        return import_ics(
            credentials=args.credentials,
            token_path=args.token,
            calendar_name=args.CALENDAR,
            ics_file=args.ICS_FILE,
            proxy=args.proxy,
            clear=args.clear,
            delete=args.delete,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
