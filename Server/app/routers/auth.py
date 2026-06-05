import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from app.database import sessions_collection, users_collection

router = APIRouter()

SESSION_COOKIE_NAME = "manthan_session"
SESSION_DAYS = 7


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return normalized


def password_hash(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = stored_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = password_hash(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate, stored_hash)
    except ValueError:
        return False


def hash_session_token(session_token: str) -> str:
    session_secret = os.getenv("SESSION_SECRET")
    if not session_secret:
        raise RuntimeError("SESSION_SECRET is not configured")
    return hmac.new(
        session_secret.encode("utf-8"),
        session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "email": user["email"],
    }


def set_session_cookie(response: Response, session_token: str, expires_at: datetime) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite=os.getenv("COOKIE_SAMESITE", "lax"),
        max_age=int((expires_at - datetime.now(timezone.utc)).total_seconds()),
        path="/",
    )


async def create_session(response: Response, user_id: ObjectId) -> None:
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)

    await sessions_collection.insert_one({
        "user_id": user_id,
        "session_token_hash": hash_session_token(session_token),
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
    })
    set_session_cookie(response, session_token, expires_at)


async def get_user_from_session(session_token: Optional[str]) -> Optional[dict]:
    if not session_token:
        return None

    session = await sessions_collection.find_one({
        "session_token_hash": hash_session_token(session_token),
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })
    if not session:
        return None

    return await users_collection.find_one({"_id": session["user_id"]})


@router.post("/register", status_code=201)
async def register(payload: RegisterRequest, response: Response):
    email = normalize_email(payload.email)
    existing_user = await users_collection.find_one({"email": email})
    if existing_user:
        raise HTTPException(status_code=409, detail="Email is already registered")

    result = await users_collection.insert_one({
        "name": payload.name.strip(),
        "email": email,
        "password_hash": password_hash(payload.password),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })

    user = await users_collection.find_one({"_id": result.inserted_id})
    await create_session(response, user["_id"])
    return {"user": public_user(user)}


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    user = await users_collection.find_one({"email": normalize_email(payload.email)})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await create_session(response, user["_id"])
    return {"user": public_user(user)}


@router.post("/logout")
async def logout(
    response: Response,
    manthan_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    if manthan_session:
        await sessions_collection.delete_one({
            "session_token_hash": hash_session_token(manthan_session)
        })

    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"message": "Logged out"}


@router.get("/me")
async def me(manthan_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    user = await get_user_from_session(manthan_session)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {"user": public_user(user)}
