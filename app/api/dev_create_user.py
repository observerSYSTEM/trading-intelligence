from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from app.db.session import get_db
from app.db.models import User

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/dev/create-user")
def create_user(db: Session = Depends(get_db)):
    email = "admin@yourdomain.com"
    password = "admin123"

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return {"message": "User already exists"}

    user = User(
        email=email,
        full_name="Admin",
        password_hash=pwd_context.hash(password),
        role="admin",
        is_active=True,
    )

    db.add(user)
    db.commit()

    return {"message": "User created", "email": email, "password": password}