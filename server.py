#!/usr/bin/env python3

import http.server
import sqlite3
import json
import uuid
import hashlib
import base64
import io
import datetime
import os
import urllib.parse
from pathlib import Path

import tempfile

# Database stored alongside server.py; falls back to temp dir if disk I/O fails
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sealtrust.db"))
PORT = int(os.environ.get("PORT", 3000))

# Restaurant seed data
RESTAURANT_NAMES = [
    ("Sakura Sushi Bar", "123 Main St"),
    ("Bella Napoli Pizza", "456 Oak Ave"),
    ("Green Leaf Kitchen", "789 Elm Rd"),
    ("Smoke & Grill BBQ", "321 Pine Ln"),
    ("Taco Loco Express", "654 Maple Dr"),
]

DRIVER_NAMES = [
    "Alex Chen", "Maria Garcia", "James Wilson", "Priya Patel",
    "Marcus Johnson", "Sofia Rodriguez", "David Kim", "Emma Thompson"
]

CUSTOMER_NAMES = [
    "Sarah Mitchell", "John Anderson", "Lisa Wong", "Robert Taylor",
    "Jennifer Brown", "Michael Davis", "Amanda Lee", "Christopher White",
    "Nicole Green", "Daniel Martinez"
]

class SealTrustDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Check if data exists
            cursor.execute("SELECT COUNT(*) as count FROM sqlite_master WHERE type='table'")
            has_tables = cursor.fetchone()['count'] > 0

            if not has_tables:
                self.create_tables(cursor)
                self.seed_data(cursor)

            conn.commit()

    def create_tables(self, cursor):
        cursor.execute("""
            CREATE TABLE restaurants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT,
                city TEXT,
                plan TEXT DEFAULT 'pro',
                monthly_fee REAL DEFAULT 500.00,
                seal_inventory INTEGER DEFAULT 0,
                total_deliveries INTEGER DEFAULT 0,
                total_verifications INTEGER DEFAULT 0,
                tamper_incidents INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE seals (
                id TEXT PRIMARY KEY,
                serial_number TEXT UNIQUE NOT NULL,
                restaurant_id TEXT REFERENCES restaurants(id),
                status TEXT DEFAULT 'unused',
                driver_id TEXT,
                customer_id TEXT,
                delivery_id TEXT,
                sealed_at TEXT,
                verified_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE drivers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                reward_balance REAL DEFAULT 0.00,
                total_verifications INTEGER DEFAULT 0,
                total_earnings REAL DEFAULT 0.00,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                reward_balance REAL DEFAULT 0.00,
                total_verifications INTEGER DEFAULT 0,
                total_earnings REAL DEFAULT 0.00,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE deliveries (
                id TEXT PRIMARY KEY,
                seal_id TEXT REFERENCES seals(id),
                restaurant_id TEXT REFERENCES restaurants(id),
                driver_id TEXT REFERENCES drivers(id),
                customer_id TEXT,
                status TEXT DEFAULT 'pending',
                sealed_at TEXT,
                delivered_at TEXT,
                verified_at TEXT,
                rating INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE rewards (
                id TEXT PRIMARY KEY,
                user_type TEXT NOT NULL,
                user_id TEXT NOT NULL,
                delivery_id TEXT REFERENCES deliveries(id),
                amount REAL NOT NULL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE analytics_events (
                id TEXT PRIMARY KEY,
                restaurant_id TEXT,
                event_type TEXT,
                data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def seed_data(self, cursor):
        now = datetime.datetime.now()
        restaurants = []

        # Create restaurants
        for i, (name, address) in enumerate(RESTAURANT_NAMES):
            rest_id = str(uuid.uuid4())
            restaurants.append(rest_id)
            cursor.execute("""
                INSERT INTO restaurants (id, name, address, city, plan, monthly_fee,
                                       seal_inventory, total_deliveries, total_verifications,
                                       tamper_incidents, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rest_id, name, address, "Austin, TX", "pro", 500.00, 200, 0, 0, 0, now.isoformat()))

        # Create drivers
        drivers = []
        for name in DRIVER_NAMES:
            driver_id = str(uuid.uuid4())
            drivers.append(driver_id)
            email = name.lower().replace(" ", ".") + "@driver.local"
            cursor.execute("""
                INSERT INTO drivers (id, name, email, phone, reward_balance,
                                    total_verifications, total_earnings, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (driver_id, name, email, "555-0000", 0.0, 0, 0.0, now.isoformat()))

        # Create customers
        customers = []
        for name in CUSTOMER_NAMES:
            cust_id = str(uuid.uuid4())
            customers.append(cust_id)
            email = name.lower().replace(" ", ".") + "@customer.local"
            cursor.execute("""
                INSERT INTO customers (id, name, email, reward_balance,
                                      total_verifications, total_earnings, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (cust_id, name, email, 0.0, 0, 0.0, now.isoformat()))

        # Create seals (200 per restaurant)
        all_seals = []
        for rest_idx, rest_id in enumerate(restaurants):
            rest_name = RESTAURANT_NAMES[rest_idx][0]
            prefix = rest_name[:3].upper()
            for i in range(200):
                seal_id = str(uuid.uuid4())
                hex_suffix = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:6].upper()
                serial = f"ST-{prefix}-{hex_suffix}"
                all_seals.append((seal_id, serial, rest_id))
                cursor.execute("""
                    INSERT INTO seals (id, serial_number, restaurant_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (seal_id, serial, rest_id, "unused", now.isoformat()))

        # Create 150 completed deliveries with realistic spread
        verified_count = 0
        tampered_count = 0
        delivery_data = []

        import random
        random.seed(42)  # Reproducible seed data

        # Build seal index per restaurant for even distribution
        seals_by_restaurant = {}
        for seal_id, serial, rest_id in all_seals:
            if rest_id not in seals_by_restaurant:
                seals_by_restaurant[rest_id] = []
            seals_by_restaurant[rest_id].append((seal_id, serial, rest_id))

        seal_counters = {r: 0 for r in restaurants}

        for i in range(150):
            delivery_id = str(uuid.uuid4())
            # Distribute across restaurants evenly
            rest_id = restaurants[i % len(restaurants)]
            idx = seal_counters[rest_id]
            seal_entry = seals_by_restaurant[rest_id][idx]
            seal_counters[rest_id] = idx + 1
            seal_id = seal_entry[0]
            driver_id = drivers[i % len(drivers)]
            customer_id = customers[i % len(customers)]

            # Spread timestamps over last 30 days with some randomness
            days_ago = i % 30
            hours = random.randint(8, 22)
            delivery_time = now - datetime.timedelta(days=days_ago, hours=hours)

            is_tampered = i < 5  # First 5 are tampered
            status = "tampered" if is_tampered else "verified"
            rating = random.choice([3, 4, 4, 5, 5]) if not is_tampered else 1

            cursor.execute("""
                INSERT INTO deliveries (id, seal_id, restaurant_id, driver_id, customer_id,
                                       status, sealed_at, delivered_at, verified_at, rating, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (delivery_id, seal_id, rest_id, driver_id, customer_id, status,
                  delivery_time.isoformat(), delivery_time.isoformat(),
                  delivery_time.isoformat(), rating,
                  delivery_time.isoformat()))

            # Update seal status
            cursor.execute("""
                UPDATE seals SET status = ?, driver_id = ?, customer_id = ?,
                               delivery_id = ?, sealed_at = ?, verified_at = ?
                WHERE id = ?
            """, (status, driver_id, customer_id, delivery_id,
                  delivery_time.isoformat(),
                  delivery_time.isoformat() if not is_tampered else None,
                  seal_id))

            if not is_tampered:
                # Create rewards for verified deliveries
                driver_reward_id = str(uuid.uuid4())
                customer_reward_id = str(uuid.uuid4())

                cursor.execute("""
                    INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (driver_reward_id, "driver", driver_id, delivery_id, 0.10,
                      "Verified delivery", delivery_time.isoformat()))

                cursor.execute("""
                    INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (customer_reward_id, "customer", customer_id, delivery_id, 0.05,
                      "Verified delivery", delivery_time.isoformat()))

                verified_count += 1
            else:
                tampered_count += 1

            delivery_data.append({
                'id': delivery_id,
                'restaurant_id': rest_id,
                'driver_id': driver_id,
                'is_tampered': is_tampered
            })

        # Update restaurant stats
        rest_delivery_counts = {}
        rest_tamper_counts = {}
        rest_seal_counts = {}

        for rest_id in restaurants:
            rest_delivery_counts[rest_id] = 0
            rest_tamper_counts[rest_id] = 0
            rest_seal_counts[rest_id] = 200

        for delivery in delivery_data:
            rest_id = delivery['restaurant_id']
            rest_delivery_counts[rest_id] += 1
            rest_seal_counts[rest_id] -= 1  # Each delivery uses a seal
            if delivery['is_tampered']:
                rest_tamper_counts[rest_id] += 1

        for rest_id in restaurants:
            cursor.execute("""
                UPDATE restaurants
                SET total_deliveries = ?, total_verifications = ?,
                    tamper_incidents = ?, seal_inventory = ?
                WHERE id = ?
            """, (rest_delivery_counts[rest_id],
                  rest_delivery_counts[rest_id] - rest_tamper_counts[rest_id],
                  rest_tamper_counts[rest_id],
                  rest_seal_counts[rest_id],
                  rest_id))

        # Update driver stats
        for driver_id in drivers:
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(amount) as total FROM rewards
                WHERE user_type = 'driver' AND user_id = ?
            """, (driver_id,))
            row = cursor.fetchone()
            verifications = row['count'] or 0
            earnings = row['total'] or 0.0
            cursor.execute("""
                UPDATE drivers SET reward_balance = ?, total_verifications = ?, total_earnings = ?
                WHERE id = ?
            """, (earnings, verifications, earnings, driver_id))

        # Update customer stats
        for customer_id in customers:
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(amount) as total FROM rewards
                WHERE user_type = 'customer' AND user_id = ?
            """, (customer_id,))
            row = cursor.fetchone()
            verifications = row['count'] or 0
            earnings = row['total'] or 0.0
            cursor.execute("""
                UPDATE customers SET reward_balance = ?, total_verifications = ?, total_earnings = ?
                WHERE id = ?
            """, (earnings, verifications, earnings, customer_id))


class SealTrustHandler(http.server.BaseHTTPRequestHandler):
    db = None

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_file(self, filepath, mime_type="text/html"):
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_json({"error": "Not found"}, 404)

    def read_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode())
        except:
            return {}

    def get_query_params(self):
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parsed.query)

    def do_GET(self):
        path = self.path.split('?')[0]

        # API Routes
        if path == "/api/restaurants":
            self.handle_get_restaurants()
        elif path.startswith("/api/restaurants/") and path.endswith("/dashboard"):
            rest_id = path.split('/')[3]
            self.handle_get_restaurant_dashboard(rest_id)
        elif path.startswith("/api/restaurants/"):
            rest_id = path.split('/')[3]
            self.handle_get_restaurant(rest_id)
        elif path == "/api/drivers":
            self.handle_get_drivers()
        elif path.startswith("/api/drivers/") and path.endswith("/rewards"):
            driver_id = path.split('/')[3]
            self.handle_get_driver_rewards(driver_id)
        elif path.startswith("/api/drivers/") and path.endswith("/deliveries"):
            driver_id = path.split('/')[3]
            self.handle_get_driver_deliveries(driver_id)
        elif path.startswith("/api/drivers/"):
            driver_id = path.split('/')[3]
            self.handle_get_driver(driver_id)
        elif path == "/api/customers":
            self.handle_get_customers()
        elif path.startswith("/api/customers/") and path.endswith("/rewards"):
            cust_id = path.split('/')[3]
            self.handle_get_customer_rewards(cust_id)
        elif path.startswith("/api/customers/"):
            cust_id = path.split('/')[3]
            self.handle_get_customer(cust_id)
        elif path.startswith("/api/analytics/enhanced/"):
            rest_id = path.split('/')[4]
            self.handle_get_enhanced_analytics(rest_id)
        elif path.startswith("/api/analytics/") and path != "/api/analytics/overview":
            rest_id = path.split('/')[3]
            self.handle_get_restaurant_analytics(rest_id)
        elif path == "/api/analytics/overview":
            self.handle_get_analytics_overview()
        elif path == "/api/stats":
            self.handle_get_stats()
        elif path.startswith("/api/seals/"):
            serial = path.split('/')[3]
            self.handle_get_seal(serial)
        elif path == "/api/demo/scenario":
            self.handle_get_demo_scenario()
        elif path == "/api/admin/dashboard":
            self.handle_get_admin_dashboard()
        elif path == "/api/alerts":
            self.handle_get_alerts()
        elif path.startswith("/scan/"):
            # Smart link: QR code on physical seals points here
            serial = path.split('/')[2] if len(path.split('/')) > 2 else ''
            self.handle_smart_link(serial)
        elif path == "/download":
            self.send_file("./public/download.html", "text/html")
        else:
            # Static files
            if path == "/":
                self.send_file("./public/index.html", "text/html")
            else:
                filepath = f"./public{path}"
                mime_map = {
                    ".html": "text/html",
                    ".css": "text/css",
                    ".js": "text/javascript",
                    ".png": "image/png",
                    ".svg": "image/svg+xml",
                    ".json": "application/json"
                }
                ext = Path(filepath).suffix
                mime = mime_map.get(ext, "text/plain")
                self.send_file(filepath, mime)

    def do_POST(self):
        path = self.path.split('?')[0]

        if path == "/api/seals/activate":
            self.handle_activate_seal()
        elif path == "/api/seals/verify":
            self.handle_verify_seal()
        elif path.startswith("/api/restaurants/") and path.endswith("/order-seals"):
            rest_id = path.split('/')[3]
            self.handle_order_seals(rest_id)
        elif path == "/api/demo/run-step":
            self.handle_run_demo_step()
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # Restaurant endpoints
    def handle_get_restaurants(self):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM restaurants ORDER BY name")
                rows = cursor.fetchall()
                restaurants = [dict(row) for row in rows]
            self.send_json({"data": restaurants})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_restaurant(self, rest_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM restaurants WHERE id = ?", (rest_id,))
                row = cursor.fetchone()
                if not row:
                    self.send_json({"error": "Restaurant not found"}, 404)
                    return
                self.send_json(dict(row))
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_restaurant_dashboard(self, rest_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Get restaurant info
                cursor.execute("SELECT * FROM restaurants WHERE id = ?", (rest_id,))
                rest = cursor.fetchone()
                if not rest:
                    self.send_json({"error": "Restaurant not found"}, 404)
                    return

                # Get recent deliveries
                cursor.execute("""
                    SELECT d.*, s.serial_number FROM deliveries d
                    LEFT JOIN seals s ON d.seal_id = s.id
                    WHERE d.restaurant_id = ? ORDER BY d.created_at DESC LIMIT 10
                """, (rest_id,))
                deliveries = [dict(row) for row in cursor.fetchall()]

                # Get daily verification counts for last 14 days (as array, newest last)
                now = datetime.datetime.now()
                daily_list = []
                for i in range(13, -1, -1):
                    date = (now - datetime.timedelta(days=i)).date()
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM deliveries
                        WHERE restaurant_id = ? AND status = 'verified'
                        AND DATE(created_at) = ?
                    """, (rest_id, str(date)))
                    count = cursor.fetchone()['count']
                    daily_list.append({"date": str(date), "count": count})

                # Get recent seals
                cursor.execute("""
                    SELECT * FROM seals WHERE restaurant_id = ?
                    ORDER BY created_at DESC LIMIT 20
                """, (rest_id,))
                recent_seals = [dict(row) for row in cursor.fetchall()]

                # Get average rating
                cursor.execute("""
                    SELECT AVG(rating) as avg_rating FROM deliveries
                    WHERE restaurant_id = ? AND rating IS NOT NULL
                """, (rest_id,))
                avg_rating_row = cursor.fetchone()
                avg_rating = avg_rating_row['avg_rating'] if avg_rating_row else None

                # Get recent deliveries with driver names and seal serials
                cursor.execute("""
                    SELECT d.*, s.serial_number as seal_serial, dr.name as driver_name
                    FROM deliveries d
                    LEFT JOIN seals s ON d.seal_id = s.id
                    LEFT JOIN drivers dr ON d.driver_id = dr.id
                    WHERE d.restaurant_id = ? ORDER BY d.created_at DESC LIMIT 20
                """, (rest_id,))
                deliveries = [dict(row) for row in cursor.fetchall()]

                dashboard = {
                    "restaurant": dict(rest),
                    "recent_deliveries": deliveries,
                    "recent_seals": recent_seals,
                    "daily_verifications": daily_list,
                    "avg_rating": avg_rating,
                    "seal_inventory": rest['seal_inventory'],
                    "total_deliveries": rest['total_deliveries'],
                    "total_verifications": rest['total_verifications'],
                    "tamper_incidents": rest['tamper_incidents']
                }

                self.send_json(dashboard)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_order_seals(self, rest_id):
        try:
            body = self.read_body()
            quantity = body.get('quantity', 0)

            if quantity <= 0:
                self.send_json({"error": "Invalid quantity"}, 400)
                return

            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Verify restaurant exists
                cursor.execute("SELECT name FROM restaurants WHERE id = ?", (rest_id,))
                rest = cursor.fetchone()
                if not rest:
                    self.send_json({"error": "Restaurant not found"}, 404)
                    return

                rest_name = rest['name']
                prefix = rest_name[:3].upper()
                new_seals = []

                for _ in range(quantity):
                    seal_id = str(uuid.uuid4())
                    hex_suffix = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:6].upper()
                    serial = f"ST-{prefix}-{hex_suffix}"
                    now = datetime.datetime.now().isoformat()

                    cursor.execute("""
                        INSERT INTO seals (id, serial_number, restaurant_id, status, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (seal_id, serial, rest_id, "unused", now))

                    new_seals.append({"id": seal_id, "serial_number": serial})

                # Update restaurant seal inventory
                cursor.execute("""
                    UPDATE restaurants SET seal_inventory = seal_inventory + ?
                    WHERE id = ?
                """, (quantity, rest_id))

                conn.commit()

                # Get updated inventory
                cursor.execute("SELECT seal_inventory FROM restaurants WHERE id = ?", (rest_id,))
                new_inv = cursor.fetchone()['seal_inventory']

                self.send_json({
                    "success": True,
                    "status": "success",
                    "quantity_ordered": quantity,
                    "new_inventory": new_inv,
                    "seals_created": new_seals[:5]  # Return first 5 as sample
                }, 201)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # Seal endpoints
    def handle_get_seal(self, serial):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM seals WHERE serial_number = ?", (serial,))
                row = cursor.fetchone()
                if not row:
                    self.send_json({"error": "Seal not found"}, 404)
                    return
                self.send_json(dict(row))
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_activate_seal(self):
        try:
            body = self.read_body()
            serial_number = body.get('serial_number')
            driver_id = body.get('driver_id')
            restaurant_id = body.get('restaurant_id')

            if not all([serial_number, driver_id, restaurant_id]):
                self.send_json({"error": "Missing required fields"}, 400)
                return

            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Get seal
                cursor.execute("SELECT * FROM seals WHERE serial_number = ?", (serial_number,))
                seal = cursor.fetchone()
                if not seal:
                    self.send_json({"error": "Seal not found"}, 404)
                    return

                if seal['status'] != "unused":
                    self.send_json({"error": "Seal already in use"}, 400)
                    return

                # Create delivery
                delivery_id = str(uuid.uuid4())
                now = datetime.datetime.now().isoformat()

                cursor.execute("""
                    INSERT INTO deliveries (id, seal_id, restaurant_id, driver_id, status, sealed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (delivery_id, seal['id'], restaurant_id, driver_id, "sealed", now, now))

                # Update seal
                cursor.execute("""
                    UPDATE seals SET status = ?, driver_id = ?, delivery_id = ?, sealed_at = ?
                    WHERE id = ?
                """, ("sealed", driver_id, delivery_id, now, seal['id']))

                # Decrease restaurant seal inventory
                cursor.execute("""
                    UPDATE restaurants SET seal_inventory = seal_inventory - 1
                    WHERE id = ?
                """, (restaurant_id,))

                conn.commit()

                self.send_json({
                    "status": "success",
                    "delivery_id": delivery_id,
                    "seal_id": seal['id'],
                    "sealed_at": now
                }, 201)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_verify_seal(self):
        try:
            body = self.read_body()
            serial_number = body.get('serial_number')
            customer_id = body.get('customer_id')

            if not serial_number or not customer_id:
                self.send_json({"error": "Missing required fields"}, 400)
                return

            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Get seal
                cursor.execute("SELECT * FROM seals WHERE serial_number = ?", (serial_number,))
                seal = cursor.fetchone()
                if not seal:
                    self.send_json({"error": "Seal not found"}, 404)
                    return

                if seal['status'] == "tampered":
                    self.send_json({"error": "Seal has been tampered with"}, 400)
                    return

                if seal['status'] == "verified":
                    self.send_json({"error": "Seal already verified"}, 400)
                    return

                # Get delivery
                cursor.execute("SELECT * FROM deliveries WHERE seal_id = ?", (seal['id'],))
                delivery = cursor.fetchone()
                if not delivery:
                    self.send_json({"error": "Delivery not found"}, 404)
                    return

                now = datetime.datetime.now().isoformat()

                # Update seal
                cursor.execute("""
                    UPDATE seals SET status = ?, customer_id = ?, verified_at = ?
                    WHERE id = ?
                """, ("verified", customer_id, now, seal['id']))

                # Update delivery
                cursor.execute("""
                    UPDATE deliveries SET status = ?, customer_id = ?, verified_at = ?
                    WHERE id = ?
                """, ("verified", customer_id, now, delivery['id']))

                # Issue rewards
                driver_reward_id = str(uuid.uuid4())
                customer_reward_id = str(uuid.uuid4())

                cursor.execute("""
                    INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (driver_reward_id, "driver", delivery['driver_id'], delivery['id'], 0.10,
                      "Verified delivery", now))

                cursor.execute("""
                    INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (customer_reward_id, "customer", customer_id, delivery['id'], 0.05,
                      "Verified delivery", now))

                # Update driver reward balance
                cursor.execute("""
                    UPDATE drivers SET reward_balance = reward_balance + 0.10,
                                     total_verifications = total_verifications + 1,
                                     total_earnings = total_earnings + 0.10
                    WHERE id = ?
                """, (delivery['driver_id'],))

                # Update customer reward balance
                cursor.execute("""
                    UPDATE customers SET reward_balance = reward_balance + 0.05,
                                       total_verifications = total_verifications + 1,
                                       total_earnings = total_earnings + 0.05
                    WHERE id = ?
                """, (customer_id,))

                # Update restaurant stats
                cursor.execute("""
                    UPDATE restaurants
                    SET total_verifications = total_verifications + 1
                    WHERE id = ?
                """, (delivery['restaurant_id'],))

                conn.commit()

                self.send_json({
                    "status": "verified",
                    "seal_id": seal['id'],
                    "delivery_id": delivery['id'],
                    "driver_reward": 0.10,
                    "customer_reward": 0.05,
                    "verified_at": now
                }, 200)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # Driver endpoints
    def handle_get_drivers(self):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM drivers ORDER BY name")
                rows = cursor.fetchall()
                drivers = [dict(row) for row in rows]
            self.send_json({"data": drivers})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_driver(self, driver_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,))
                row = cursor.fetchone()
                if not row:
                    self.send_json({"error": "Driver not found"}, 404)
                    return
                self.send_json(dict(row))
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_driver_rewards(self, driver_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM rewards WHERE user_type = 'driver' AND user_id = ?
                    ORDER BY created_at DESC
                """, (driver_id,))
                rows = cursor.fetchall()
                rewards = [dict(row) for row in rows]
            self.send_json({"data": rewards})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_driver_deliveries(self, driver_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT d.*, s.serial_number, r.name as restaurant_name
                    FROM deliveries d
                    LEFT JOIN seals s ON d.seal_id = s.id
                    LEFT JOIN restaurants r ON d.restaurant_id = r.id
                    WHERE d.driver_id = ?
                    ORDER BY d.created_at DESC
                """, (driver_id,))
                rows = cursor.fetchall()
                deliveries = [dict(row) for row in rows]
            self.send_json({"data": deliveries})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # Customer endpoints
    def handle_get_customers(self):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM customers ORDER BY name")
                rows = cursor.fetchall()
                customers = [dict(row) for row in rows]
            self.send_json({"data": customers})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_customer(self, cust_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM customers WHERE id = ?", (cust_id,))
                row = cursor.fetchone()
                if not row:
                    self.send_json({"error": "Customer not found"}, 404)
                    return
                self.send_json(dict(row))
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_customer_rewards(self, cust_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM rewards WHERE user_type = 'customer' AND user_id = ?
                    ORDER BY created_at DESC
                """, (cust_id,))
                rows = cursor.fetchall()
                rewards = [dict(row) for row in rows]
            self.send_json({"data": rewards})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # Analytics endpoints
    def handle_get_analytics_overview(self):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT SUM(total_deliveries) as total FROM restaurants")
                total_deliveries = cursor.fetchone()['total'] or 0

                cursor.execute("SELECT SUM(total_verifications) as total FROM restaurants")
                total_verifications = cursor.fetchone()['total'] or 0

                cursor.execute("SELECT SUM(tamper_incidents) as total FROM restaurants")
                tamper_incidents = cursor.fetchone()['total'] or 0

                cursor.execute("SELECT SUM(amount) as total FROM rewards")
                total_rewards = cursor.fetchone()['total'] or 0.0

                cursor.execute("SELECT SUM(monthly_fee) as total FROM restaurants")
                total_revenue = cursor.fetchone()['total'] or 0.0

                tamper_rate = (tamper_incidents / total_deliveries * 100) if total_deliveries > 0 else 0

                self.send_json({
                    "total_deliveries": total_deliveries,
                    "total_verifications": total_verifications,
                    "tamper_incidents": tamper_incidents,
                    "tamper_rate_percent": round(tamper_rate, 2),
                    "total_rewards_paid": round(total_rewards, 2),
                    "monthly_revenue": round(total_revenue, 2)
                })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_restaurant_analytics(self, rest_id):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT * FROM restaurants WHERE id = ?", (rest_id,))
                rest = cursor.fetchone()
                if not rest:
                    self.send_json({"error": "Restaurant not found"}, 404)
                    return

                cursor.execute("""
                    SELECT SUM(amount) as total FROM rewards r
                    JOIN deliveries d ON r.delivery_id = d.id
                    WHERE d.restaurant_id = ?
                """, (rest_id,))
                rewards_paid = cursor.fetchone()['total'] or 0.0

                tamper_rate = (rest['tamper_incidents'] / rest['total_deliveries'] * 100) if rest['total_deliveries'] > 0 else 0

                self.send_json({
                    "restaurant_id": rest['id'],
                    "restaurant_name": rest['name'],
                    "plan": rest['plan'],
                    "monthly_fee": rest['monthly_fee'],
                    "total_deliveries": rest['total_deliveries'],
                    "total_verifications": rest['total_verifications'],
                    "tamper_incidents": rest['tamper_incidents'],
                    "tamper_rate_percent": round(tamper_rate, 2),
                    "seal_inventory": rest['seal_inventory'],
                    "rewards_paid_to_users": round(rewards_paid, 2)
                })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_stats(self):
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) as count FROM restaurants")
                total_restaurants = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM drivers")
                total_drivers = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM customers")
                total_customers = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM seals WHERE status = 'unused'")
                unused_seals = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'verified'")
                verified_deliveries = cursor.fetchone()['count']

                cursor.execute("SELECT SUM(amount) as total FROM rewards")
                total_rewards = cursor.fetchone()['total'] or 0.0

                self.send_json({
                    "total_restaurants": total_restaurants,
                    "total_drivers": total_drivers,
                    "total_customers": total_customers,
                    "unused_seals": unused_seals,
                    "verified_deliveries": verified_deliveries,
                    "total_verifications": verified_deliveries,
                    "total_rewards_paid": round(total_rewards, 2)
                })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


    def handle_get_demo_scenario(self):
        """Returns a scripted demo scenario with step-by-step data for a live walkthrough."""
        try:
            demo_steps = [
                {
                    "step": 1,
                    "title": "Restaurant Seals Order",
                    "description": "Sakura Sushi orders 50 tamper-evident seals",
                    "actor": "restaurant",
                    "action": "order_seals",
                    "data": {
                        "restaurant_name": "Sakura Sushi Bar",
                        "quantity": 50
                    }
                },
                {
                    "step": 2,
                    "title": "Seal Applied to Order",
                    "description": "Driver Alex Chen picks up order #1247 and applies seal ST-SAK-A3F2B1",
                    "actor": "driver",
                    "action": "activate_seal",
                    "data": {
                        "driver_name": "Alex Chen",
                        "serial": "DEMO-SEAL-001",
                        "order_number": "#1247"
                    }
                },
                {
                    "step": 3,
                    "title": "Order In Transit",
                    "description": "Delivery tracked in real-time via GPS",
                    "actor": "system",
                    "action": "in_transit",
                    "data": {
                        "eta_minutes": 28,
                        "distance_km": 4.2
                    }
                },
                {
                    "step": 4,
                    "title": "Customer Verification",
                    "description": "Sarah Mitchell scans QR code on delivery - seal intact!",
                    "actor": "customer",
                    "action": "verify_seal",
                    "data": {
                        "customer_name": "Sarah Mitchell",
                        "result": "verified",
                        "reward": "$0.05"
                    }
                },
                {
                    "step": 5,
                    "title": "Rewards Distributed",
                    "description": "Driver earns $0.10, customer earns $0.05 in rewards",
                    "actor": "system",
                    "action": "rewards",
                    "data": {
                        "driver_reward": "$0.10",
                        "customer_reward": "$0.05",
                        "driver_total": "$14.50",
                        "customer_total": "$7.25"
                    }
                },
                {
                    "step": 6,
                    "title": "Tamper Alert!",
                    "description": "ALERT: Seal ST-BEL-7C2E91 broken on delivery #1248 - notification sent to Bella Napoli Pizza",
                    "actor": "system",
                    "action": "tamper_alert",
                    "data": {
                        "restaurant": "Bella Napoli Pizza",
                        "driver": "Marcus Johnson",
                        "serial": "ST-BEL-7C2E91",
                        "timestamp": "2:47 PM"
                    }
                }
            ]
            self.send_json({"steps": demo_steps})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_admin_dashboard(self):
        """Returns system-wide metrics for the admin/ops dashboard."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Total counts
                cursor.execute("SELECT COUNT(*) as count FROM restaurants")
                total_restaurants = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM drivers")
                total_drivers = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM customers")
                total_customers = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM seals")
                total_seals = cursor.fetchone()['count']

                cursor.execute("SELECT COUNT(*) as count FROM deliveries")
                total_deliveries = cursor.fetchone()['count']

                # Revenue calculation
                cursor.execute("SELECT SUM(monthly_fee) as total FROM restaurants")
                revenue = cursor.fetchone()['total'] or 0.0

                # Total rewards paid
                cursor.execute("SELECT SUM(amount) as total FROM rewards")
                total_rewards_paid = cursor.fetchone()['total'] or 0.0

                # Tamper rate
                cursor.execute("SELECT SUM(tamper_incidents) as total FROM restaurants")
                total_tamper_incidents = cursor.fetchone()['total'] or 0
                tamper_rate = (total_tamper_incidents / total_deliveries * 100) if total_deliveries > 0 else 0

                # Per-restaurant breakdown
                cursor.execute("""
                    SELECT r.id, r.name, r.total_deliveries, r.total_verifications,
                           r.tamper_incidents, r.monthly_fee
                    FROM restaurants r
                    ORDER BY r.name
                """)
                restaurants = [dict(row) for row in cursor.fetchall()]

                # Top 5 drivers by total_verifications
                cursor.execute("""
                    SELECT id, name, total_verifications, total_earnings, reward_balance
                    FROM drivers
                    ORDER BY total_verifications DESC
                    LIMIT 5
                """)
                top_drivers = [dict(row) for row in cursor.fetchall()]

                # Recent 10 deliveries with driver name, restaurant name, seal serial, status
                cursor.execute("""
                    SELECT d.id, d.status, d.created_at,
                           dr.name as driver_name,
                           r.name as restaurant_name,
                           s.serial_number as seal_serial
                    FROM deliveries d
                    LEFT JOIN drivers dr ON d.driver_id = dr.id
                    LEFT JOIN restaurants r ON d.restaurant_id = r.id
                    LEFT JOIN seals s ON d.seal_id = s.id
                    ORDER BY d.created_at DESC
                    LIMIT 10
                """)
                recent_deliveries = [dict(row) for row in cursor.fetchall()]

                # Daily delivery volume for last 30 days
                now = datetime.datetime.now()
                daily_volume = []
                for i in range(29, -1, -1):
                    date = (now - datetime.timedelta(days=i)).date()
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM deliveries
                        WHERE DATE(created_at) = ?
                    """, (str(date),))
                    count = cursor.fetchone()['count']
                    daily_volume.append({"date": str(date), "count": count})

                dashboard = {
                    "summary": {
                        "total_restaurants": total_restaurants,
                        "total_drivers": total_drivers,
                        "total_customers": total_customers,
                        "total_seals": total_seals,
                        "total_deliveries": total_deliveries,
                        "revenue": round(revenue, 2),
                        "total_rewards_paid": round(total_rewards_paid, 2),
                        "tamper_rate_percent": round(tamper_rate, 2)
                    },
                    "restaurants": restaurants,
                    "top_drivers": top_drivers,
                    "recent_deliveries": recent_deliveries,
                    "daily_volume": daily_volume
                }
                self.send_json(dashboard)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_alerts(self):
        """Returns all tampered deliveries with context."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT d.id as alert_id, d.created_at as timestamp,
                           r.name as restaurant_name, dr.name as driver_name,
                           s.serial_number as seal_serial, d.id as delivery_id
                    FROM deliveries d
                    JOIN seals s ON d.seal_id = s.id
                    JOIN drivers dr ON d.driver_id = dr.id
                    JOIN restaurants r ON d.restaurant_id = r.id
                    WHERE d.status = 'tampered'
                    ORDER BY d.created_at DESC
                """)
                alerts = []
                for row in cursor.fetchall():
                    alert = dict(row)
                    alert['severity'] = 'critical'
                    alerts.append(alert)

                self.send_json({"alerts": alerts})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_get_enhanced_analytics(self, rest_id):
        """Returns deep analytics for a restaurant."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                # Verify restaurant exists
                cursor.execute("SELECT * FROM restaurants WHERE id = ?", (rest_id,))
                rest = cursor.fetchone()
                if not rest:
                    self.send_json({"error": "Restaurant not found"}, 404)
                    return

                # Hourly heatmap: count of deliveries for each hour 0-23
                hourly_heatmap = {}
                for hour in range(24):
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM deliveries
                        WHERE restaurant_id = ? AND CAST(strftime('%H', created_at) AS INTEGER) = ?
                    """, (rest_id, hour))
                    count = cursor.fetchone()['count']
                    hourly_heatmap[str(hour)] = count

                # Driver rankings: for each driver who delivered for this restaurant
                cursor.execute("""
                    SELECT dr.id, dr.name,
                           COUNT(d.id) as deliveries,
                           SUM(CASE WHEN d.status = 'tampered' THEN 1 ELSE 0 END) as tampered,
                           AVG(CASE WHEN d.rating IS NOT NULL THEN d.rating ELSE NULL END) as avg_rating
                    FROM drivers dr
                    LEFT JOIN deliveries d ON dr.id = d.driver_id AND d.restaurant_id = ?
                    WHERE d.id IS NOT NULL
                    GROUP BY dr.id, dr.name
                    ORDER BY deliveries DESC
                """, (rest_id,))
                driver_rankings = []
                for row in cursor.fetchall():
                    driver_rankings.append({
                        "driver_id": row['id'],
                        "driver_name": row['name'],
                        "deliveries": row['deliveries'] or 0,
                        "tampered": row['tampered'] or 0,
                        "avg_rating": round(row['avg_rating'], 2) if row['avg_rating'] else None
                    })

                # Peak hours: top 3 hours by delivery count
                peak_hours = sorted(hourly_heatmap.items(), key=lambda x: x[1], reverse=True)[:3]
                peak_hours = [{"hour": int(h), "count": c} for h, c in peak_hours]

                # Food safety score: 100 - (tamper_incidents / total_deliveries * 100), clamped 0-100
                total_deliveries = rest['total_deliveries']
                tamper_incidents = rest['tamper_incidents']
                if total_deliveries > 0:
                    food_safety_score = 100 - (tamper_incidents / total_deliveries * 100)
                    food_safety_score = max(0, min(100, food_safety_score))
                else:
                    food_safety_score = 100

                # Daily volume: deliveries per day for last 30 days
                now = datetime.datetime.now()
                daily_volume = []
                for i in range(29, -1, -1):
                    date = (now - datetime.timedelta(days=i)).date()
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM deliveries
                        WHERE restaurant_id = ? AND DATE(created_at) = ?
                    """, (rest_id, str(date)))
                    count = cursor.fetchone()['count']
                    daily_volume.append({"date": str(date), "count": count})

                # Verification rate: total_verifications / total_deliveries * 100
                verification_rate = (rest['total_verifications'] / total_deliveries * 100) if total_deliveries > 0 else 0

                analytics = {
                    "restaurant_id": rest_id,
                    "restaurant_name": rest['name'],
                    "hourly_heatmap": hourly_heatmap,
                    "driver_rankings": driver_rankings,
                    "peak_hours": peak_hours,
                    "food_safety_score": round(food_safety_score, 2),
                    "daily_volume": daily_volume,
                    "verification_rate": round(verification_rate, 2)
                }
                self.send_json(analytics)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_run_demo_step(self):
        """Executes a demo step against the DB."""
        try:
            body = self.read_body()
            step = body.get('step', 0)

            if step < 1 or step > 6:
                self.send_json({"error": "Invalid step number"}, 400)
                return

            with self.db.get_connection() as conn:
                cursor = conn.cursor()

                if step == 1:
                    # Order seals for Sakura Sushi
                    cursor.execute("SELECT id FROM restaurants WHERE name = 'Sakura Sushi Bar'")
                    rest_row = cursor.fetchone()
                    if rest_row:
                        rest_id = rest_row['id']
                        # Create 50 seals
                        for i in range(50):
                            seal_id = str(uuid.uuid4())
                            hex_suffix = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:6].upper()
                            serial = f"ST-SAK-{hex_suffix}"
                            now = datetime.datetime.now().isoformat()
                            cursor.execute("""
                                INSERT INTO seals (id, serial_number, restaurant_id, status, created_at)
                                VALUES (?, ?, ?, ?, ?)
                            """, (seal_id, serial, rest_id, "unused", now))
                        cursor.execute("""
                            UPDATE restaurants SET seal_inventory = seal_inventory + 50
                            WHERE id = ?
                        """, (rest_id,))
                        conn.commit()

                elif step == 2:
                    # Activate a seal (Alex Chen driver)
                    cursor.execute("SELECT id FROM drivers WHERE name = 'Alex Chen'")
                    driver_row = cursor.fetchone()
                    cursor.execute("SELECT id FROM restaurants WHERE name = 'Sakura Sushi Bar'")
                    rest_row = cursor.fetchone()
                    if driver_row and rest_row:
                        driver_id = driver_row['id']
                        rest_id = rest_row['id']
                        # Get an unused seal
                        cursor.execute("SELECT id FROM seals WHERE restaurant_id = ? AND status = 'unused' LIMIT 1", (rest_id,))
                        seal_row = cursor.fetchone()
                        if seal_row:
                            seal_id = seal_row['id']
                            delivery_id = str(uuid.uuid4())
                            now = datetime.datetime.now().isoformat()
                            cursor.execute("""
                                INSERT INTO deliveries (id, seal_id, restaurant_id, driver_id, status, sealed_at, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (delivery_id, seal_id, rest_id, driver_id, "sealed", now, now))
                            cursor.execute("""
                                UPDATE seals SET status = ?, driver_id = ?, delivery_id = ?, sealed_at = ?
                                WHERE id = ?
                            """, ("sealed", driver_id, delivery_id, now, seal_id))
                            cursor.execute("""
                                UPDATE restaurants SET seal_inventory = seal_inventory - 1
                                WHERE id = ?
                            """, (rest_id,))
                            conn.commit()

                elif step == 3:
                    # Mark most recent sealed delivery as in_transit
                    cursor.execute("""
                        SELECT d.id FROM deliveries d
                        WHERE d.status = 'sealed'
                        ORDER BY d.created_at DESC LIMIT 1
                    """)
                    delivery_row = cursor.fetchone()
                    if delivery_row:
                        # For demo purposes, just keep in sealed state
                        # (in_transit is more of a UI state in the demo)
                        pass
                    conn.commit()

                elif step == 4:
                    # Verify the seal (Sarah Mitchell customer)
                    cursor.execute("SELECT id FROM customers WHERE name = 'Sarah Mitchell'")
                    customer_row = cursor.fetchone()
                    if customer_row:
                        customer_id = customer_row['id']
                        # Get the most recent sealed delivery
                        cursor.execute("""
                            SELECT d.id, d.seal_id, d.driver_id, d.restaurant_id FROM deliveries d
                            WHERE d.status = 'sealed'
                            ORDER BY d.created_at DESC LIMIT 1
                        """)
                        delivery_row = cursor.fetchone()
                        if delivery_row:
                            delivery_id = delivery_row['id']
                            seal_id = delivery_row['seal_id']
                            driver_id = delivery_row['driver_id']
                            rest_id = delivery_row['restaurant_id']
                            now = datetime.datetime.now().isoformat()

                            cursor.execute("""
                                UPDATE seals SET status = ?, customer_id = ?, verified_at = ?
                                WHERE id = ?
                            """, ("verified", customer_id, now, seal_id))

                            cursor.execute("""
                                UPDATE deliveries SET status = ?, customer_id = ?, verified_at = ?
                                WHERE id = ?
                            """, ("verified", customer_id, now, delivery_id))

                            # Issue rewards
                            driver_reward_id = str(uuid.uuid4())
                            customer_reward_id = str(uuid.uuid4())

                            cursor.execute("""
                                INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (driver_reward_id, "driver", driver_id, delivery_id, 0.10,
                                  "Verified delivery", now))

                            cursor.execute("""
                                INSERT INTO rewards (id, user_type, user_id, delivery_id, amount, description, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (customer_reward_id, "customer", customer_id, delivery_id, 0.05,
                                  "Verified delivery", now))

                            # Update balances
                            cursor.execute("""
                                UPDATE drivers SET reward_balance = reward_balance + 0.10,
                                                 total_verifications = total_verifications + 1,
                                                 total_earnings = total_earnings + 0.10
                                WHERE id = ?
                            """, (driver_id,))

                            cursor.execute("""
                                UPDATE customers SET reward_balance = reward_balance + 0.05,
                                                   total_verifications = total_verifications + 1,
                                                   total_earnings = total_earnings + 0.05
                                WHERE id = ?
                            """, (customer_id,))

                            cursor.execute("""
                                UPDATE restaurants SET total_verifications = total_verifications + 1
                                WHERE id = ?
                            """, (rest_id,))
                            conn.commit()

                elif step == 5:
                    # Rewards already distributed in step 4
                    pass

                elif step == 6:
                    # Create a tampered delivery alert (Marcus Johnson driver, Bella Napoli)
                    cursor.execute("SELECT id FROM drivers WHERE name = 'Marcus Johnson'")
                    driver_row = cursor.fetchone()
                    cursor.execute("SELECT id FROM restaurants WHERE name = 'Bella Napoli Pizza'")
                    rest_row = cursor.fetchone()
                    if driver_row and rest_row:
                        driver_id = driver_row['id']
                        rest_id = rest_row['id']
                        # Get an unused seal
                        cursor.execute("SELECT id FROM seals WHERE restaurant_id = ? AND status = 'unused' LIMIT 1", (rest_id,))
                        seal_row = cursor.fetchone()
                        if seal_row:
                            seal_id = seal_row['id']
                            delivery_id = str(uuid.uuid4())
                            now = datetime.datetime.now().isoformat()
                            cursor.execute("""
                                INSERT INTO deliveries (id, seal_id, restaurant_id, driver_id, status, sealed_at, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (delivery_id, seal_id, rest_id, driver_id, "tampered", now, now))
                            cursor.execute("""
                                UPDATE seals SET status = ?, driver_id = ?, delivery_id = ?, sealed_at = ?
                                WHERE id = ?
                            """, ("tampered", driver_id, delivery_id, now, seal_id))
                            cursor.execute("""
                                UPDATE restaurants SET seal_inventory = seal_inventory - 1,
                                                     tamper_incidents = tamper_incidents + 1
                                WHERE id = ?
                            """, (rest_id,))
                            conn.commit()

            self.send_json({"success": True, "message": f"Step {step} executed"})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_smart_link(self, serial):
        """Smart link handler for QR codes on physical seals.
        In production: detects if app is installed via deep link.
        If app installed -> deep link to activation/verification screen.
        If not -> redirect to download page with serial pre-filled.
        For this demo: always serve the download page with seal context."""
        # Look up the seal to provide context
        seal_info = None
        restaurant_name = "a SealTrust partner restaurant"
        if serial:
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT s.serial_number, s.status, r.name as restaurant_name
                        FROM seals s
                        JOIN restaurants r ON s.restaurant_id = r.id
                        WHERE s.serial_number = ?
                    """, (serial,))
                    row = cursor.fetchone()
                    if row:
                        seal_info = dict(row)
                        restaurant_name = row['restaurant_name']
            except:
                pass

        # Redirect to download page with context
        redirect_url = f"/download?serial={serial}&restaurant={urllib.parse.quote(restaurant_name)}"
        if seal_info and seal_info['status'] == 'sealed':
            redirect_url += "&action=verify"
        elif seal_info and seal_info['status'] == 'unused':
            redirect_url += "&action=activate"

        self.send_response(302)
        self.send_header("Location", redirect_url)
        self.end_headers()


def run_server():
    global DATABASE
    # Try primary path; fall back to temp dir if mounted filesystem blocks SQLite
    try:
        db = SealTrustDatabase(DATABASE)
    except Exception:
        DATABASE = os.path.join(tempfile.gettempdir(), "sealtrust.db")
        print(f"  (Using fallback DB path: {DATABASE})")
        db = SealTrustDatabase(DATABASE)

    SealTrustHandler.db = db

    # Serve static files relative to script location
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = http.server.HTTPServer(("0.0.0.0", PORT), SealTrustHandler)
    print(f"\n🔐 SealTrust Platform running at http://localhost:{PORT}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    run_server()
