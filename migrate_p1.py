"""
migrate_p1.py — Standalone P0/P1 migration.
Does NOT import app.py (avoids the admin-query chicken-and-egg problem).

Run:  python migrate_p1.py
"""
import os
import sqlite3
from pathlib import Path

# ---------- Locate the SQLite DB ----------
# Flask-SQLAlchemy puts it in ./instance/vault.db by default
db_path = Path(__file__).parent / "instance" / "vault.db"

if not db_path.exists():
    print(f"❌ Database not found at {db_path}")
    print("   Make sure you're running this from the project root,")
    print("   and that the app has created the DB at least once.")
    raise SystemExit(1)

print(f"📂 Database: {db_path}")

# ---------- Column definitions to add ----------
# (col_name, col_type_with_default)
USER_COLS = [
    ("school_code",       "VARCHAR(64)"),
    ("is_school_account", "BOOLEAN DEFAULT 0"),
    ("terms_accepted_at", "TIMESTAMP"),
    ("privacy_accepted_at", "TIMESTAMP"),
    ("consent_version",   "VARCHAR(20)"),
]

SCHOOL_CONFIG_SQL = """
CREATE TABLE IF NOT EXISTS school_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_code VARCHAR(64) UNIQUE NOT NULL,
    school_name VARCHAR(200),
    contact_email VARCHAR(120),
    retention_days INTEGER DEFAULT 365,
    visitor_log_days INTEGER DEFAULT 14,
    leaderboard_enabled BOOLEAN DEFAULT 0,
    ai_provider VARCHAR(20) DEFAULT 'groq',
    strict_zip_redaction BOOLEAN DEFAULT 0,
    dpa_signed_at TIMESTAMP,
    dpa_version VARCHAR(20) DEFAULT '1.0',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_school_config_code ON school_config(school_code);
"""

# ---------- Run migration ----------
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 1) Existing columns on user
cur.execute("PRAGMA table_info(user)")
existing = {row[1] for row in cur.fetchall()}

# 2) Add missing columns
for col, coltype in USER_COLS:
    if col in existing:
        print(f"  = user.{col} already exists")
        continue
    print(f"  + adding user.{col} {coltype}")
    cur.execute(f'ALTER TABLE user ADD COLUMN {col} {coltype}')

# 3) Create school_config table
print("  + ensuring school_config table")
cur.executescript(SCHOOL_CONFIG_SQL)

# 4) Add strict_zip_redaction if school_config already existed without it
cur.execute("PRAGMA table_info(school_config)")
sc_cols = {row[1] for row in cur.fetchall()}
if "strict_zip_redaction" not in sc_cols:
    print("  + adding school_config.strict_zip_redaction")
    cur.execute(
        "ALTER TABLE school_config ADD COLUMN strict_zip_redaction BOOLEAN DEFAULT 0"
    )

conn.commit()
conn.close()

print("\n✅ Migration complete.")
print("   You can now run:  python app.py")