# -*- coding: utf-8 -*-
"""
City to timezone mapping.
Users type a city name, we resolve to a proper IANA timezone.
"""
import re
from typing import Optional

# Major cities mapped to IANA timezones
# Covers most of the world's population centers
CITY_TIMEZONE_MAP = {
    # Australia
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth",
    "adelaide": "Australia/Adelaide",
    "gold coast": "Australia/Brisbane",
    "canberra": "Australia/Sydney",
    "hobart": "Australia/Hobart",
    "darwin": "Australia/Darwin",
    # US
    "new york": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "houston": "America/Chicago",
    "phoenix": "America/Phoenix",
    "denver": "America/Denver",
    "seattle": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "atlanta": "America/New_York",
    "dallas": "America/Chicago",
    "austin": "America/Chicago",
    "detroit": "America/Detroit",
    "washington": "America/New_York",
    "portland": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles",
    "nashville": "America/Chicago",
    # Canada
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",
    "montreal": "America/Montreal",
    "calgary": "America/Edmonton",
    # UK & Europe
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid",
    "rome": "Europe/Rome",
    "zurich": "Europe/Zurich",
    "munich": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "barcelona": "Europe/Madrid",
    "dublin": "Europe/Dublin",
    "stockholm": "Europe/Stockholm",
    "oslo": "Europe/Oslo",
    "helsinki": "Europe/Helsinki",
    "warsaw": "Europe/Warsaw",
    "prague": "Europe/Prague",
    "vienna": "Europe/Vienna",
    "lisbon": "Europe/Lisbon",
    "athens": "Europe/Athens",
    "moscow": "Europe/Moscow",
    "istanbul": "Europe/Istanbul",
    "bucharest": "Europe/Bucharest",
    "budapest": "Europe/Budapest",
    # Asia
    "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore",
    "seoul": "Asia/Seoul",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bangkok": "Asia/Bangkok",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "jakarta": "Asia/Jakarta",
    "taipei": "Asia/Taipei",
    "manila": "Asia/Manila",
    "dubai": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai",
    "riyadh": "Asia/Riyadh",
    "karachi": "Asia/Karachi",
    "dhaka": "Asia/Dhaka",
    # South America
    "sao paulo": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "bogota": "America/Bogota",
    "santiago": "America/Santiago",
    "lima": "America/Lima",
    "mexico city": "America/Mexico_City",
    # Africa
    "johannesburg": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg",
    "lagos": "Africa/Lagos",
    "nairobi": "Africa/Nairobi",
    "cairo": "Africa/Cairo",
    "casablanca": "Africa/Casablanca",
    # Middle East
    "tel aviv": "Asia/Jerusalem",
    "jerusalem": "Asia/Jerusalem",
    "doha": "Asia/Qatar",
    # New Zealand
    "auckland": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
}

# Common abbreviations / nicknames
ALIAS_MAP = {
    "nyc": "new york",
    "la": "los angeles",
    "sf": "san francisco",
    "dc": "washington",
    "uk": "london",
    "nz": "auckland",
    "aus": "sydney",
    "oz": "sydney",
    "mel": "melbourne",
    "brissy": "brisbane",
    "brissie": "brisbane",
    "perth": "perth",
    "tokyo": "tokyo",
    "hk": "hong kong",
    "sg": "singapore",
    "kl": "kuala lumpur",
}


def resolve_timezone(city_input: str) -> Optional[str]:
    """
    Resolve a city name or timezone string to an IANA timezone.
    Returns None if unresolvable.
    """
    if not city_input:
        return None

    # Check if it's already a valid IANA timezone (contains /)
    if "/" in city_input:
        return city_input

    # Normalize input
    key = city_input.strip().lower()

    # Check aliases first
    if key in ALIAS_MAP:
        key = ALIAS_MAP[key]

    # Direct city lookup
    if key in CITY_TIMEZONE_MAP:
        return CITY_TIMEZONE_MAP[key]

    # Try partial match (e.g., "York" matches "New York")
    for city, tz in CITY_TIMEZONE_MAP.items():
        if key in city or city in key:
            return tz

    return None


def get_utc_offset_display(timezone: str) -> str:
    """Get a human-readable UTC offset for a timezone"""
    from datetime import datetime, timezone as tz
    try:
        import zoneinfo
        tz_obj = zoneinfo.ZoneInfo(timezone)
        now = datetime.now(tz_obj)
        offset = now.utcoffset()
        if offset:
            hours = int(offset.total_seconds() / 3600)
            mins = int((offset.total_seconds() % 3600) / 60)
            if mins:
                return f"UTC{hours:+d}:{mins:02d}"
            return f"UTC{hours:+d}"
    except Exception:
        pass
    return timezone


def get_popular_cities() -> list[str]:
    """Return a curated list of popular cities for the /email setup flow"""
    return [
        "Sydney", "Melbourne", "Brisbane", "Perth",
        "London", "Paris", "Berlin", "Amsterdam",
        "New York", "Los Angeles", "Chicago", "Toronto",
        "Tokyo", "Singapore", "Hong Kong", "Seoul",
        "Dubai", "Mumbai", "Shanghai", "Auckland",
    ]