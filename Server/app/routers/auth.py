import hashlib
import hmac
import os
import secrets
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.database import sessions_collection, users_collection, password_resets_collection
from app.utils.email import send_reset_email

logger = logging.getLogger(__name__)

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


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


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
        secure=True,
        samesite="none",
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


async def get_current_user(
    manthan_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Dependency: require a valid session, return the user document."""
    user = await get_user_from_session(manthan_session)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


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


RESET_TOKEN_EXPIRY_MINUTES = 60


def hash_reset_token(token: str) -> str:
    session_secret = os.getenv("SESSION_SECRET")
    if not session_secret:
        raise RuntimeError("SESSION_SECRET is not configured")
    return hmac.new(
        session_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    email = normalize_email(payload.email)
    user = await users_collection.find_one({"email": email})

    if not user:
        return {"message": "If the email is registered, a reset code has been sent"}

    reset_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRY_MINUTES)

    await password_resets_collection.delete_many({"email": email})

    await password_resets_collection.insert_one({
        "email": email,
        "token_hash": hash_reset_token(reset_token),
        "created_at": datetime.now(timezone.utc),
        "expires_at": expires_at,
    })

    email_sent = await asyncio.to_thread(send_reset_email, email, reset_token, RESET_TOKEN_EXPIRY_MINUTES)

    if not email_sent:
        is_debug = os.getenv("DEBUG", "false").lower() == "true"
        logger.warning(f"Failed to send reset email to {email}")
        if is_debug:
            return {
                "message": "Reset token generated (email failed)",
                "token": reset_token,
                "expires_in_minutes": RESET_TOKEN_EXPIRY_MINUTES,
            }
        return {"message": "Email delivery failed, please try again later"}

    return {"message": "If the email is registered, a reset code has been sent"}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    token_hash = hash_reset_token(payload.token)

    doc = await password_resets_collection.find_one({
        "token_hash": token_hash,
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    })

    if not doc:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    email = doc["email"]
    await password_resets_collection.delete_one({"_id": doc["_id"]})

    await users_collection.update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": password_hash(payload.new_password),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    return {"message": "Password has been reset successfully"}
