import os
import uuid
from sqlalchemy import create_engine, text
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./local_dev.db")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@yourdomain.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin123!")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
password_hash = pwd_context.hash(ADMIN_PASSWORD)

engine = create_engine(DB_URL, pool_pre_ping=True)

with engine.begin() as conn:
    existing = conn.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": ADMIN_EMAIL},
    ).fetchone()

    if existing:
        conn.execute(
            text("""
                UPDATE users
                SET password_hash = :password_hash,
                    is_active = true
                WHERE email = :email
            """),
            {"email": ADMIN_EMAIL, "password_hash": password_hash},
        )
        print(f"Updated user: {ADMIN_EMAIL}")
    else:
        conn.execute(
            text("""
                INSERT INTO users (
                    id, email, full_name, password_hash, is_active
                )
                VALUES (
                    :id, :email, 'Admin User', :password_hash, 1
                )
            """),
            {
                "id": str(uuid.uuid4()),
                "email": ADMIN_EMAIL,
                "password_hash": password_hash,
            },
        )
        print(f"Created user: {ADMIN_EMAIL}")

print(f"Password: {ADMIN_PASSWORD}")