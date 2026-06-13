"""
TenderAI — Auth Module (auth.py)
===================================
Handles ONLY user registration and login.
All other database operations are in db.py.

Why split? Clean separation of concerns:
  auth.py  → who are you? (register, login)
  db.py    → what do you have? (profile, analyses, leads, feedback)
"""

import os
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

        # Check if email already exists
        existing = admin.table("users") \
                        .select("id") \
                        .eq("email", email) \
                        .execute()

        if existing.data:
            return {"success": False, "error": "Email already registered"}

        # Hash password with bcrypt
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        # Insert user
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
    """Login by checking hashed password. Returns {success, user?} or {success, error?}."""
    try:
        admin = get_admin_client()

        # Find user by email
        result = admin.table("users") \
                      .select("id, email, password_hash, role") \
                      .eq("email", email) \
                      .execute()

        if not result.data:
            return {"success": False, "error": "Invalid email or password"}

        user = result.data[0]

        # Check password
        if bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            # Don't return password_hash to session
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
