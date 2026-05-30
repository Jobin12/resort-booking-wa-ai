"""
Static resort knowledge - the single source of truth for non-image facts.

Per the spec, ONLY images come from the database (media_assets). Everything else
(hut categories, capacities, views, amenities, prices, inventory counts, and
negotiation limits) is fixed resort data and lives here so the agent and the
server-side validators share one definition and never invent values.
"""
from datetime import date, datetime

# Maximum discount the bot may grant per night before it must escalate to a human.
MAX_DISCOUNT_PER_NIGHT = 1000

# Booking statuses that consume inventory (an active booking occupies a hut).
ACTIVE_STATUSES = ("CONFIRMED",)

# Canonical hut catalog. Keys match media_assets.related_item for asset_type='HUT'
# and the value stored in bookings.hut_category. `hut_numbers` is the physical
# inventory - each booking is assigned one specific hut number, and inventory size
# is simply the count of hut numbers.
HUTS = {
    "Economy": {
        "max_guests": 2,
        "view": "Garden View",
        "amenities": ["Garden View", "Free WiFi"],
        "price_per_night": 3000,
        "hut_numbers": ["E001", "E002", "E003", "E004", "E005"],
        "min_price_per_night": 3000 - MAX_DISCOUNT_PER_NIGHT,  # 2000
    },
    "Deluxe": {
        "max_guests": 3,
        "view": "Pool View",
        "amenities": ["Pool View", "Free WiFi", "Complimentary Breakfast"],
        "price_per_night": 5000,
        "hut_numbers": ["D001", "D002", "D003", "D004"],
        "min_price_per_night": 5000 - MAX_DISCOUNT_PER_NIGHT,  # 4000
    },
    "Luxury": {
        "max_guests": 4,
        "view": "Pool View",
        "amenities": [
            "Private Balcony",
            "Pool View",
            "Complimentary Breakfast",
            "Premium Amenities",
        ],
        "price_per_night": 8000,
        "hut_numbers": ["L001", "L002", "L003"],
        "min_price_per_night": 8000 - MAX_DISCOUNT_PER_NIGHT,  # 7000
    },
}

# Inventory size per category, derived from the physical hut numbers.
for _info in HUTS.values():
    _info["inventory"] = len(_info["hut_numbers"])

# Valid amenity names - must match media_assets.related_item for asset_type='AMENITY'.
AMENITIES = [
    "Swimming Pool",
    "Play Area",
    "Restaurant",
    "Camp Fire",
    "Kids Zone",
]


def normalize_hut(name):
    """
    Map a free-text hut name to its canonical catalog key, or None if unknown.
    Accepts e.g. "economy", "Economy Hut", "luxury hut" -> "Economy"/"Luxury".
    """
    if not name:
        return None
    cleaned = name.strip().lower().replace("hut", "").strip()
    for key in HUTS:
        if key.lower() == cleaned:
            return key
    return None


def normalize_amenity(name):
    """Map a free-text amenity name to its canonical catalog value, or None."""
    if not name:
        return None
    cleaned = name.strip().lower()
    for amenity in AMENITIES:
        if amenity.lower() == cleaned:
            return amenity
    return None


def _to_date(value):
    """Parse an ISO date string (YYYY-MM-DD) or pass through a date/datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def nights(check_in, check_out):
    """Number of nights between two dates (accepts date objects or ISO strings)."""
    return (_to_date(check_out) - _to_date(check_in)).days
