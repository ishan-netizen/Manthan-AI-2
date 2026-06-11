import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "manthan_ai")

_client = None
_db = None
users_collection = None
sessions_collection = None
password_resets_collection = None
analyses_collection = None

if MONGODB_URI:
    _client = AsyncIOMotorClient(
        MONGODB_URI,
        tls=True,
        tlsAllowInvalidCertificates=True,
        tlsAllowInvalidHostnames=True,
        serverSelectionTimeoutMS=30000,
        connectTimeoutMS=30000,
    )
    _db = _client[MONGODB_DB]
    users_collection = _db["users"]
    sessions_collection = _db["sessions"]
    password_resets_collection = _db["password_resets"]
    analyses_collection = _db["analyses"]


async def ensure_indexes() -> None:
    if analyses_collection is None:
        return
    await users_collection.create_index("email", unique=True)
    await sessions_collection.create_index("session_token_hash", unique=True)
    await sessions_collection.create_index("expires_at", expireAfterSeconds=0)
    await password_resets_collection.create_index("token_hash", unique=True)
    await password_resets_collection.create_index("expires_at", expireAfterSeconds=0)
    await analyses_collection.create_index([("user_id", 1), ("created_at", -1)])
