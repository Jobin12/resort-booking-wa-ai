import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app
import logging

from app.utils.resort_info import ACTIVE_STATUSES


def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=current_app.config['PG_HOST'],
            port=current_app.config['PG_PORT'],
            user=current_app.config['PG_USER'],
            password=current_app.config['PG_PASS'],
            dbname=current_app.config['PG_DBNAME']
        )
        return conn
    except Exception as e:
        logging.error(f"Error connecting to database: {e}")
        return None


def _serialize_booking(row):
    """Normalize a bookings row for JSON serialization."""
    if not row:
        return row
    if row.get('booking_id') is not None:
        row['booking_id'] = str(row['booking_id'])
    if row.get('total_amount') is not None:
        row['total_amount'] = float(row['total_amount'])
    for date_field in ('check_in_date', 'check_out_date', 'created_at', 'updated_at'):
        if row.get(date_field) is not None:
            row[date_field] = row[date_field].isoformat()
    return row


# ---------------------------------------------------------------------------
# Media assets (images)
# ---------------------------------------------------------------------------

def _get_images_db(asset_type, related_item):
    conn = get_db_connection()
    if not conn:
        return []

    query = (
        "SELECT image_url FROM media_assets "
        "WHERE asset_type = %s AND related_item = %s "
        "ORDER BY display_order"
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (asset_type, related_item))
            rows = cur.fetchall()
            return [r['image_url'] for r in rows if r.get('image_url')]
    except Exception as e:
        logging.error(f"Error fetching {asset_type} images for '{related_item}': {e}")
        return []
    finally:
        conn.close()


def get_hut_images_db(hut_category):
    """Image URLs for a hut category, ordered by display_order."""
    return _get_images_db("HUT", hut_category)


def get_amenity_images_db(amenity):
    """Image URLs for an amenity, ordered by display_order."""
    return _get_images_db("AMENITY", amenity)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def get_occupied_hut_numbers_db(hut_category, check_in, check_out, exclude_booking_id=None):
    """
    Return the set of specific hut_numbers in a category that are occupied by an
    active (inventory-consuming) booking overlapping the requested date range.
    Uses the standard half-open overlap test:
        existing.check_in_date < requested.check_out
        AND existing.check_out_date > requested.check_in
    When re-checking availability for an existing booking being modified, pass its
    booking_id as exclude_booking_id so it doesn't count against itself.
    Returns a set of hut_number strings, or None on DB error (so callers fail safe).
    """
    conn = get_db_connection()
    if not conn:
        return None

    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    query = (
        "SELECT DISTINCT hut_number FROM bookings "
        "WHERE hut_category = %s "
        f"AND booking_status IN ({placeholders}) "
        "AND check_in_date < %s AND check_out_date > %s"
    )
    params = [hut_category, *ACTIVE_STATUSES, check_out, check_in]
    if exclude_booking_id:
        query += " AND booking_id <> %s"
        params.append(exclude_booking_id)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return {r['hut_number'] for r in cur.fetchall() if r.get('hut_number')}
    except Exception as e:
        logging.error(f"Error fetching occupied hut numbers: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bookings CRUD
# ---------------------------------------------------------------------------

def create_booking_db(
    guest_name,
    guest_phone,
    hut_category,
    hut_number,
    check_in_date,
    check_out_date,
    number_of_guests,
    total_amount,
    guest_email=None,
    special_requests=None,
):
    """Insert a CONFIRMED booking and return the serialized row (with booking_id)."""
    conn = get_db_connection()
    if not conn:
        return None

    query = (
        "INSERT INTO bookings "
        "(guest_name, guest_phone, guest_email, hut_category, hut_number, check_in_date, "
        " check_out_date, number_of_guests, booking_status, total_amount, special_requests) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CONFIRMED', %s, %s) "
        "RETURNING *"
    )
    params = (
        guest_name,
        guest_phone,
        guest_email,
        hut_category,
        hut_number,
        check_in_date,
        check_out_date,
        number_of_guests,
        total_amount,
        special_requests,
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            conn.commit()
            return _serialize_booking(row)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating booking: {e}")
        return None
    finally:
        conn.close()


def get_bookings_db(guest_phone, booking_id=None):
    """
    Retrieve bookings for a guest, scoped by guest_phone so a caller can only ever
    see their own bookings. Optionally narrow to a single booking_id.
    """
    conn = get_db_connection()
    if not conn:
        return []

    query = "SELECT * FROM bookings WHERE guest_phone = %s"
    params = [guest_phone]
    if booking_id:
        query += " AND booking_id = %s"
        params.append(booking_id)
    query += " ORDER BY created_at DESC"

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [_serialize_booking(r) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"Error retrieving bookings: {e}")
        return []
    finally:
        conn.close()


def update_booking_db(booking_id, guest_phone, fields):
    """
    Update allowed fields on a booking, scoped by booking_id AND guest_phone so a
    caller can never modify another customer's booking. `fields` is a dict of
    column -> value. Returns the updated serialized row, or None if not found.
    """
    if not fields:
        return None

    conn = get_db_connection()
    if not conn:
        return None

    set_clause = ", ".join(f"{col} = %s" for col in fields)
    query = (
        f"UPDATE bookings SET {set_clause}, updated_at = NOW() "
        "WHERE booking_id = %s AND guest_phone = %s "
        "RETURNING *"
    )
    params = [*fields.values(), booking_id, guest_phone]
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            conn.commit()
            return _serialize_booking(row)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error updating booking {booking_id}: {e}")
        return None
    finally:
        conn.close()


def cancel_booking_db(booking_id, guest_phone):
    """
    Mark a booking CANCELLED (never delete), scoped by booking_id AND guest_phone.
    Returns the updated serialized row, or None if not found / on error.
    """
    conn = get_db_connection()
    if not conn:
        return None

    query = (
        "UPDATE bookings SET booking_status = 'CANCELLED', updated_at = NOW() "
        "WHERE booking_id = %s AND guest_phone = %s "
        "RETURNING *"
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (booking_id, guest_phone))
            row = cur.fetchone()
            conn.commit()
            return _serialize_booking(row)
    except Exception as e:
        conn.rollback()
        logging.error(f"Error cancelling booking {booking_id}: {e}")
        return None
    finally:
        conn.close()
