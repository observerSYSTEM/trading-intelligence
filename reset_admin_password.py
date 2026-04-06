from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import User

EMAIL = "admin@yourdomain.com"
NEW_HASH = "$2b$12$Pw/fAjsOFLznsVO1jDbbCeRefyyetNXCuPbObfNHs9./ZDFIQ218a"

db = SessionLocal()
try:
    user = db.execute(select(User).where(User.email == EMAIL)).scalar_one_or_none()
    if not user:
        print(f"User not found: {EMAIL}")
    else:
        user.password_hash = NEW_HASH
        db.add(user)
        db.commit()
        print(f"Password reset OK for {EMAIL}")
finally:
    db.close()