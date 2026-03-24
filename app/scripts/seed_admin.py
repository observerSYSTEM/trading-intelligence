from dotenv import load_dotenv
load_dotenv()

import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db_url import normalize_database_url
from app.db.models import User
from app.core.security import hash_password  # implement bcrypt

DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        raise RuntimeError("ADMIN_EMAIL / ADMIN_PASSWORD not set")

    engine = create_engine(normalize_database_url(DATABASE_URL))

    with Session(engine) as session:
        existing = session.execute(select(User).where(User.email == ADMIN_EMAIL)).scalar_one_or_none()
        if existing:
            print("Admin already exists.")
            return

        admin = User(
            full_name="Admin User",
            email=ADMIN_EMAIL,
            password_hash=hash_password(ADMIN_PASSWORD),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.commit()
        print("Admin created:", ADMIN_EMAIL)

if __name__ == "__main__":
    main()
