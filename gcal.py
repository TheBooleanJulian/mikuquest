"""
gcal.py — Google Calendar integration for MiguQuest.

Setup flow (one-time):
1. Create a Google Cloud project → Enable Calendar API
2. Create OAuth2 credentials (Desktop app type) → Download credentials.json
3. Set env var GOOGLE_CREDENTIALS_JSON = contents of credentials.json
4. Send /gcalauth to your bot → visit the URL → authorise
5. Send /gcalcode <code> to complete auth
6. Token saved to /data/gcal_token.json → auto-refreshed forever
"""
import os
import json
import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

TOKEN_PATH = os.environ.get("GCAL_TOKEN_PATH", "data/gcal_token.json")
SCOPES     = ["https://www.googleapis.com/auth/calendar.readonly"]


def _get_credentials_dict() -> Optional[dict]:
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def get_auth_url() -> Optional[str]:
    """Return OAuth authorisation URL, or None if credentials not configured."""
    creds_dict = _get_credentials_dict()
    if not creds_dict:
        return None
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(
            creds_dict,
            scopes=SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        # Store flow state for later code exchange
        os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
        with open(TOKEN_PATH + ".flow", "w") as f:
            json.dump(creds_dict, f)
        return auth_url
    except Exception as e:
        logger.error(f"[GCal] get_auth_url error: {e}")
        return None


def exchange_code(code: str) -> bool:
    """Exchange auth code for tokens and save to TOKEN_PATH."""
    flow_path = TOKEN_PATH + ".flow"
    if not os.path.exists(flow_path):
        return False
    try:
        from google_auth_oauthlib.flow import Flow
        with open(flow_path) as f:
            creds_dict = json.load(f)
        flow = Flow.from_client_config(
            creds_dict,
            scopes=SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        flow.fetch_token(code=code.strip())
        creds = flow.credentials
        token_data = {
            "token":         creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     creds.client_id,
            "client_secret": creds.client_secret,
            "scopes":        list(creds.scopes),
        }
        os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_data, f)
        os.remove(flow_path)
        logger.info("[GCal] Token saved successfully.")
        return True
    except Exception as e:
        logger.error(f"[GCal] exchange_code error: {e}")
        return False


def is_authenticated() -> bool:
    return os.path.exists(TOKEN_PATH)


def _get_service():
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        with open(TOKEN_PATH) as f:
            token_data = json.load(f)

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", SCOPES),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            token_data["token"] = creds.token
            with open(TOKEN_PATH, "w") as f:
                json.dump(token_data, f)

        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"[GCal] _get_service error: {e}")
        return None


def fetch_todays_events(days_ahead: int = 1) -> List[Dict]:
    """Return calendar events for today (and optionally tomorrow)."""
    service = _get_service()
    if not service:
        return []
    try:
        cal_id    = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
        now       = datetime.utcnow()
        time_min  = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
        time_max  = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                     + timedelta(days=days_ahead)).isoformat() + "Z"

        result = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = []
        for item in result.get("items", []):
            start = item.get("start", {})
            due   = start.get("dateTime", start.get("date", ""))[:10]
            events.append({
                "id":      item["id"],
                "summary": item.get("summary", "GCal event"),
                "due":     due,
                "all_day": "date" in start,
            })
        return events
    except Exception as e:
        logger.error(f"[GCal] fetch_todays_events error: {e}")
        return []


def infer_tag_from_event(summary: str) -> str:
    low = summary.lower()
    tag_map = {
        "#tutoring":  ["tutor", "angela", "denzel", "pakorn", "jessica", "theethus",
                       "rin", "poon", "lesson", "class", "math"],
        "#accurova":  ["shoot", "photobooth", "client", "accurova", "photo", "session"],
        "#dev":       ["deploy", "code", "sprint", "standup", "review", "pr", "bug"],
        "#busking":   ["busking", "fattkew", "nac", "busk"],
        "#personal":  ["doctor", "dentist", "gym", "cosplay", "miku"],
    }
    for tag, keywords in tag_map.items():
        if any(k in low for k in keywords):
            return tag
    return "#general"
