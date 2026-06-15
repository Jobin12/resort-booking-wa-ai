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

HUTS = {
    "Mist Habitat": {
        "max_guests": 3,
        "view": "Nature View",
        "amenities": ["Comfortable Bed", "Cozy Nature Stay"],
        "price_per_night": 5000,
        "hut_numbers": ["MH01", "MH02", "MH03", "MH04", "MH05"],
        "min_price_per_night": 5000 - MAX_DISCOUNT_PER_NIGHT,
        "recommended_for": "Couples, Solo travelers, Small families",
        "description": "A comfortable premium room option suitable for guests seeking a cozy nature stay."
    },
    "Mist Premium Suite": {
        "max_guests": 4,
        "view": "Nature View",
        "amenities": ["Additional Space", "Premium Comfort"],
        "price_per_night": 7000,
        "hut_numbers": ["MPS01", "MPS02", "MPS03", "MPS04"],
        "min_price_per_night": 7000 - MAX_DISCOUNT_PER_NIGHT,
        "recommended_for": "Couples, Honeymooners, Small families",
        "description": "A larger suite offering additional space and comfort compared to standard rooms."
    },
    "Mist Haven Cottage": {
        "max_guests": 3,
        "view": "Secluded View",
        "amenities": ["Private Cottage", "Secluded Experience"],
        "price_per_night": 8000,
        "hut_numbers": ["MHC01", "MHC02", "MHC03"],
        "min_price_per_night": 8000 - MAX_DISCOUNT_PER_NIGHT,
        "recommended_for": "Couples, Honeymooners, Guests seeking privacy",
        "description": "A private cottage-style accommodation ideal for guests looking for a more secluded experience."
    },
    "Mist Villa": {
        "max_guests": 6,
        "view": "Nature View",
        "amenities": ["Two Bedrooms", "Attached Bathrooms", "Living Space", "Balcony"],
        "price_per_night": 15000,
        "hut_numbers": ["MV01", "MV02"],
        "min_price_per_night": 15000 - MAX_DISCOUNT_PER_NIGHT,
        "recommended_for": "Families, Two families traveling together, Groups of friends",
        "description": "A spacious villa with two bedrooms, attached bathrooms, living space, and balcony."
    },
    "Mist Presidential Suite": {
        "max_guests": 4,
        "view": "Panoramic View",
        "amenities": ["Most Luxurious", "Premium Service"],
        "price_per_night": 20000,
        "hut_numbers": ["PR01"],
        "min_price_per_night": 20000 - MAX_DISCOUNT_PER_NIGHT,
        "recommended_for": "Luxury travelers, Special occasions, Guests wanting the most premium accommodation",
        "description": "The largest and most luxurious accommodation category available at the resort."
    }
}

# Inventory size per category, derived from the physical hut numbers.
for _info in HUTS.values():
    _info["inventory"] = len(_info["hut_numbers"])

# Valid amenity names - must match media_assets.related_item for asset_type='AMENITY'.
AMENITIES = [
    "Swimming Pool",
    "Restaurant",
    "Nature Walks",
    "Trekking Experiences",
    "Family-Friendly Activities",
    "Relaxation Amidst Nature"
]


def normalize_hut(name):
    """
    Map a free-text hut name to its canonical catalog key, or None if unknown.
    """
    if not name:
        return None
    cleaned = name.strip().lower()
    for key in HUTS:
        # Flexible matching
        if key.lower() in cleaned or cleaned in key.lower() or key.lower().replace("mist ", "") in cleaned:
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
