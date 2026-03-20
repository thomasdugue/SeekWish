"""Shared Supabase helper — auth verification and client initialization."""

import os


def get_service_client():
    """Return a Supabase client with service-role privileges (bypasses RLS)."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return create_client(url, key)


def get_user_id(headers) -> str | None:
    """Extract and verify user from Authorization header. Returns user_id or None."""
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        client = get_service_client()
        user_resp = client.auth.get_user(token)
        if user_resp and user_resp.user:
            return str(user_resp.user.id)
    except Exception:
        pass
    return None
