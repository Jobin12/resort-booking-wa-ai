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

def search_cars_in_db(filters):
    """
    Search cars based on provided filters.
    Filters is a dictionary that might contain:
    manufacturer, model_name, vehicle_type, fuel_type, transmission, 
    max_price, min_year, max_kms
    """
    conn = get_db_connection()
    if not conn:
        return []

    query = "SELECT id, manufacturer, model_name, vehicle_type, fuel_type, transmission, year_of_manufacture, total_kms, price, color, description FROM cars WHERE 1=1"
    params = []

    if filters.get("manufacturer"):
        query += " AND manufacturer ILIKE %s"
        params.append(f"%{filters['manufacturer']}%")
    
    if filters.get("model_name"):
        query += " AND model_name ILIKE %s"
        params.append(f"%{filters['model_name']}%")

    if filters.get("vehicle_type"):
        query += " AND vehicle_type = %s"
        params.append(filters["vehicle_type"].lower())
        
    if filters.get("fuel_type"):
        query += " AND fuel_type = %s"
        params.append(filters["fuel_type"].lower())

    if filters.get("transmission"):
        query += " AND transmission = %s"
        params.append(filters["transmission"].lower())

    if filters.get("max_price"):
        query += " AND price <= %s"
        params.append(filters["max_price"])

    if filters.get("min_year"):
        query += " AND year_of_manufacture >= %s"
        params.append(filters["min_year"])

    if filters.get("max_kms"):
        query += " AND total_kms <= %s"
        params.append(filters["max_kms"])
        
    query += " LIMIT 5" # limit to 5 to avoid overwhelming the LLM

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            results = cur.fetchall()
            # Convert UUID to string for JSON serialization
            for row in results:
                row['id'] = str(row['id'])
                # convert decimal to float
                row['price'] = float(row['price'])
            return results
    except Exception as e:
        logging.error(f"Error executing search query: {e}")
        return []
    finally:
        conn.close()

def get_car_details_db(car_id):
    conn = get_db_connection()
    if not conn:
        return None

    query = "SELECT * FROM cars WHERE id = %s"
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (car_id,))
            result = cur.fetchone()
            if result:
                result['id'] = str(result['id'])
                result['price'] = float(result['price'])
                # format dates
                if result.get('created_at'):
                    result['created_at'] = result['created_at'].isoformat()
                if result.get('updated_at'):
                    result['updated_at'] = result['updated_at'].isoformat()
            return result
    except Exception as e:
        logging.error(f"Error getting car details: {e}")
        return None
    finally:
        conn.close()

def get_car_images_db(car_id):
    conn = get_db_connection()
    if not conn:
        return []

    query = "SELECT image_urls FROM cars WHERE id = %s"
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (car_id,))
            result = cur.fetchone()
            if result and result.get('image_urls'):
                return result['image_urls']
            return []
    except Exception as e:
        logging.error(f"Error getting car images: {e}")
        return []
    finally:
        conn.close()
