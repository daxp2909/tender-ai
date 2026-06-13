"""
TenderAI — Auth Module (auth.py)
===================================
Handles user registration, login, and password management.
"""

import os
import secrets
import bcrypt
from supabase import create_client, Client


def get_admin_client() -> Client:
    """Get Supabase client with service role key (bypasses RLS)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")

    if not url:
        raise EnvironmentError("SUPABASE_URL is not set.")
    if not key:
        raise EnvironmentError("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SERVICE_KEY) is not set.")

    return create_client(url, key)


# ── Register ──────────────────────────────────────────────────

def register_user(email: str, password: str) -> dict:
    """Register a new user. Returns {success, user?} or {success, error?}."""
    try:
        admin = get_admin_client()

        existing = admin.table("users") \
                        .select("id") \
                        .eq("email", email) \
                        .execute()

        if existing.data:
            return {"success": False, "error": "Email already registered"}

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        result = admin.table("users").insert({
            "email": email,
            "password_hash": password_hash,
            "role": "user"
        }).execute()

        user = result.data[0]
        return {"success": True, "user": user}

    except Exception as e:
        print(f"[auth] Register error: {e}")
        return {"success": False, "error": str(e)}


# ── Login ─────────────────────────────────────────────────────

def login_user(email: str, password: str) -> dict:
    """Login by checking hashed password."""
    try:
        admin = get_admin_client()

        result = admin.table("users") \
                      .select("id, email, password_hash, role") \
                      .eq("email", email) \
                      .execute()

        if not result.data:
            return {"success": False, "error": "Invalid email or password"}

        user = result.data[0]

        if bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            safe_user = {
                "id": user["id"],
                "email": user["email"],
                "role": user.get("role", "user")
            }
            return {"success": True, "user": safe_user}
        else:
            return {"success": False, "error": "Invalid email or password"}

    except Exception as e:
        print(f"[auth] Login error: {e}")
        return {"success": False, "error": "Login failed. Please try again."}


# ── Password Reset ────────────────────────────────────────────

def create_reset_token(email: str) -> str | None:
    """Create a password reset token. Returns token or None if user not found."""
    try:
        admin = get_admin_client()

        # Check if user exists
        result = admin.table("users") \
                      .select("id") \
                      .eq("email", email) \
                      .execute()

        if not result.data:
            return None  # User not found — don't reveal this

        token = secrets.token_urlsafe(32)
        return token

    except Exception as e:
        print(f"[auth] Create reset token error: {e}")
        return None


def update_password(email: str, new_password: str) -> dict:
    """Update a user's password."""
    try:
        admin = get_admin_client()

        password_hash = bcrypt.hashpw(
            new_password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        admin.table("users") \
             .update({"password_hash": password_hash}) \
             .eq("email", email) \
             .execute()

        return {"success": True}

    except Exception as e:
        print(f"[auth] Update password error: {e}")
        return {"success": False, "error": str(e)}
