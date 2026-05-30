-- Resort Booking WhatsApp Assistant - schema + seed data
-- Bootstrap a local database with:  psql "<connection string>" -f schema.sql

-- ---------------------------------------------------------------------------
-- media_assets: stores hut and amenity image URLs (S3).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_assets (
    asset_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_type    VARCHAR(20)  NOT NULL,   -- 'HUT' or 'AMENITY'
    related_item  VARCHAR(100) NOT NULL,   -- e.g. 'Luxury', 'Swimming Pool'
    image_url     TEXT         NOT NULL,
    display_order INTEGER      DEFAULT 1,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_media_assets_lookup
    ON media_assets (asset_type, related_item, display_order);

-- ---------------------------------------------------------------------------
-- bookings: one row per reservation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bookings (
    booking_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_name       VARCHAR(100) NOT NULL,
    guest_phone      VARCHAR(20)  NOT NULL,
    guest_email      VARCHAR(100),
    hut_category     VARCHAR(20)  NOT NULL,   -- 'Economy' | 'Deluxe' | 'Luxury'
    hut_number       VARCHAR(20)  NOT NULL,   -- e.g. 'E001', 'D003', 'L002'
    check_in_date    DATE NOT NULL,
    check_out_date   DATE NOT NULL,
    number_of_guests INTEGER NOT NULL,
    booking_status   VARCHAR(20) NOT NULL DEFAULT 'CONFIRMED',  -- 'CONFIRMED' | 'CANCELLED'
    total_amount     DECIMAL(10, 2),
    special_requests TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bookings_phone   ON bookings (guest_phone);
CREATE INDEX IF NOT EXISTS idx_bookings_overlap ON bookings (hut_category, hut_number, booking_status, check_in_date, check_out_date);

-- ---------------------------------------------------------------------------
-- Seed: hut images
-- ---------------------------------------------------------------------------
INSERT INTO media_assets (asset_type, related_item, image_url, display_order) VALUES
    ('HUT', 'Economy', 'https://s3.amazonaws.com/resort/economy1.jpg', 1),
    ('HUT', 'Economy', 'https://s3.amazonaws.com/resort/economy2.jpg', 2),
    ('HUT', 'Deluxe',  'https://s3.amazonaws.com/resort/deluxe1.jpg',  1),
    ('HUT', 'Deluxe',  'https://s3.amazonaws.com/resort/deluxe2.jpg',  2),
    ('HUT', 'Luxury',  'https://s3.amazonaws.com/resort/luxury1.jpg',  1),
    ('HUT', 'Luxury',  'https://s3.amazonaws.com/resort/luxury2.jpg',  2),
    ('HUT', 'Luxury',  'https://s3.amazonaws.com/resort/luxury3.jpg',  3);

-- ---------------------------------------------------------------------------
-- Seed: amenity images
-- ---------------------------------------------------------------------------
INSERT INTO media_assets (asset_type, related_item, image_url, display_order) VALUES
    ('AMENITY', 'Swimming Pool', 'https://s3.amazonaws.com/resort/pool1.jpg',       1),
    ('AMENITY', 'Swimming Pool', 'https://s3.amazonaws.com/resort/pool2.jpg',       2),
    ('AMENITY', 'Play Area',     'https://s3.amazonaws.com/resort/playarea1.jpg',   1),
    ('AMENITY', 'Restaurant',    'https://s3.amazonaws.com/resort/restaurant1.jpg', 1),
    ('AMENITY', 'Camp Fire',     'https://s3.amazonaws.com/resort/campfire1.jpg',   1);
