#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
python << 'PY'
import os, time
import psycopg2
url = os.environ.get("DATABASE_URL", "")
# postgresql+psycopg2://user:pass@host:port/db
raw = url.replace("postgresql+psycopg2://", "")
creds, hostpart = raw.split("@", 1)
user, password = creds.split(":", 1)
hostport, db = hostpart.split("/", 1)
if ":" in hostport:
    host, port = hostport.split(":", 1)
else:
    host, port = hostport, "5432"
for i in range(60):
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)
        conn.close()
        print("PostgreSQL is ready.")
        break
    except Exception as e:
        print(f"Waiting... ({i+1}/60) {e}")
        time.sleep(2)
else:
    raise SystemExit("PostgreSQL not available")
PY

python << 'PY'
from sqlalchemy import inspect, text

from app import create_app, seed_demo_data
from app.extensions import db

app = create_app()
with app.app_context():
    # 新表（如 peak_audits）直接建出。
    db.create_all()

    # 旧库幂等补列：db.create_all() 不会改动已存在的表结构。
    inspector = inspect(db.engine)
    batch_cols = {c["name"] for c in inspector.get_columns("slake_batches")}
    if "peak_version" not in batch_cols:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE slake_batches ADD COLUMN peak_version INTEGER NOT NULL DEFAULT 0")
            )
        print("added slake_batches.peak_version")

    seed_demo_data()
    print("migrate/seed done")
PY

exec gunicorn wsgi:app --bind 0.0.0.0:8000 --workers 2 --timeout 120
