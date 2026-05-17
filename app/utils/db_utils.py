import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app
import logging

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


def _serialize_item(row):
    """Normalize a jewellery_items row for JSON serialization."""
    if not row:
        return row
    row['id'] = str(row['id'])
    for numeric_field in ('price', 'making_charge', 'weight_grams'):
        if row.get(numeric_field) is not None:
            row[numeric_field] = float(row[numeric_field])
    for date_field in ('created_at', 'updated_at'):
        if row.get(date_field) is not None:
            row[date_field] = row[date_field].isoformat()
    return row


def search_jewellery_in_db(filters):
    """
    Search jewellery items based on provided filters.
    Filters is a dictionary that might contain:
    category, metal_type, karat_purity, stone_type, gender,
    max_price, max_weight_grams
    """
    logging.info(f"search_jewellery_in_db called with filters: {filters}")
    conn = get_db_connection()
    if not conn:
        return []

    query = (
        "SELECT id, category, metal_type, karat_purity, stone_type, "
        "weight_grams, gender, price, making_charge "
        "FROM public.jewellery_items WHERE 1=1"
    )
    params = []

    if filters.get("category"):
        query += " AND category = %s"
        params.append(filters["category"].lower())

    if filters.get("metal_type"):
        query += " AND metal_type = %s"
        params.append(filters["metal_type"].lower())

    if filters.get("karat_purity"):
        query += " AND karat_purity = %s"
        params.append(filters["karat_purity"])

    if filters.get("stone_type"):
        query += " AND stone_type ILIKE %s"
        params.append(f"%{filters['stone_type']}%")

    if filters.get("gender"):
        query += " AND gender = %s"
        params.append(filters["gender"].lower())

    if filters.get("max_price"):
        query += " AND price <= %s"
        params.append(filters["max_price"])

    if filters.get("max_weight_grams"):
        query += " AND weight_grams <= %s"
        params.append(filters["max_weight_grams"])

    query += " ORDER BY price ASC NULLS LAST LIMIT 5"

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            results = cur.fetchall()
            return [_serialize_item(r) for r in results]
    except Exception as e:
        logging.error(f"Error executing search query: {e}")
        return []
    finally:
        conn.close()


def get_jewellery_details_db(item_id):
    conn = get_db_connection()
    if not conn:
        return None

    query = "SELECT * FROM public.jewellery_items WHERE id = %s"
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (item_id,))
            return _serialize_item(cur.fetchone())
    except Exception as e:
        logging.error(f"Error getting jewellery details: {e}")
        return None
    finally:
        conn.close()


def get_jewellery_images_db(item_id):
    conn = get_db_connection()
    if not conn:
        return []

    query = "SELECT image_urls FROM public.jewellery_items WHERE id = %s"
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (item_id,))
            result = cur.fetchone()
            if result and result.get('image_urls'):
                return result['image_urls']
            return []
    except Exception as e:
        logging.error(f"Error getting jewellery images: {e}")
        return []
    finally:
        conn.close()
