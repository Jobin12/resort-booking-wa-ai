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
    hut_category     VARCHAR(50)  NOT NULL,
    hut_number       VARCHAR(20)  NOT NULL,
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
    ('HUT', 'Mist Habitat', 'https://resort-table.s3.amazonaws.com/vythiri/mist-habitat.jpg', 1),
    ('HUT', 'Mist Habitat', 'https://resort-table.s3.amazonaws.com/vythiri/g1.jpg', 2),
    ('HUT', 'Mist Premium Suite', 'https://resort-table.s3.amazonaws.com/vythiri/premium-suite.jpg', 1),
    ('HUT', 'Mist Premium Suite', 'https://resort-table.s3.amazonaws.com/vythiri/g3.jpg', 2),
    ('HUT', 'Mist Haven Cottage', 'https://resort-table.s3.amazonaws.com/vythiri/mist-haven.jpg', 1),
    ('HUT', 'Mist Haven Cottage', 'https://resort-table.s3.amazonaws.com/vythiri/g4.jpg', 2),
    ('HUT', 'Mist Villa', 'https://resort-table.s3.amazonaws.com/vythiri/mist-villa.jpg', 1),
    ('HUT', 'Mist Villa', 'https://resort-table.s3.amazonaws.com/vythiri/g5.jpg', 2),
    ('HUT', 'Mist Villa', 'https://resort-table.s3.amazonaws.com/vythiri/g6.jpg', 3),
    ('HUT', 'Mist Presidential Suite', 'https://resort-table.s3.amazonaws.com/vythiri/presidential-suite.jpg', 1),
    ('HUT', 'Mist Presidential Suite', 'https://resort-table.s3.amazonaws.com/vythiri/g7.jpg', 2),
    ('HUT', 'Mist Presidential Suite', 'https://resort-table.s3.amazonaws.com/vythiri/g8.jpg', 3);

-- ---------------------------------------------------------------------------
-- Seed: amenity images
-- ---------------------------------------------------------------------------
INSERT INTO media_assets (asset_type, related_item, image_url, display_order) VALUES
    ('AMENITY', 'Swimming Pool', 'https://resort-table.s3.amazonaws.com/vythiri/swimming-pool.png', 1),
    ('AMENITY', 'Restaurant', 'https://resort-table.s3.amazonaws.com/vythiri/restaurant.png', 1),
    ('AMENITY', 'Nature Walks', 'https://resort-table.s3.amazonaws.com/vythiri/nature-walks.png', 1),
    ('AMENITY', 'Trekking Experiences', 'https://resort-table.s3.amazonaws.com/vythiri/trekking.png', 1),
    ('AMENITY', 'Family-Friendly Activities', 'https://resort-table.s3.amazonaws.com/vythiri/family.png', 1),
    ('AMENITY', 'Relaxation Amidst Nature', 'https://resort-table.s3.amazonaws.com/vythiri/relax.png', 1);
