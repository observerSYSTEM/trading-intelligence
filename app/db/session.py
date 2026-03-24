from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db_url import normalize_database_url
from app.core.config import settings

engine = create_engine(
    normalize_database_url(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
