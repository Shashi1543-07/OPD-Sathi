"""Provider adapters for queue-only messages; callers enforce consent before send."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from app.config import Settings

TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "token_issued": {
        "en": "Your queue token is {token}.",
        "hi": "आपका कतार टोकन {token} है।",
        "mr": "तुमचा रांगेचा टोकन {token} आहे.",
    },
    "five_ahead": {
        "en": "There are about 5 people ahead of token {token}.",
        "hi": "टोकन {token} से पहले लगभग 5 लोग हैं।",
        "mr": "टोकन {token} च्या आधी साधारण 5 रुग्ण आहेत.",
    },
    "two_ahead": {
        "en": "There are about 2 people ahead of token {token}.",
        "hi": "टोकन {token} से पहले लगभग 2 लोग हैं।",
        "mr": "टोकन {token} च्या आधी साधारण 2 रुग्ण आहेत.",
    },
    "your_turn": {
        "en": "It is nearly token {token}'s turn. Please return to the queue desk.",
        "hi": "टोकन {token} की बारी जल्द आएगी। कृपया कतार डेस्क पर आएँ।",
        "mr": "टोकन {token} ची पाळी लवकरच येईल. कृपया रांग डेस्कजवळ या.",
    },
}


def render_message(template: str, language: str, token: str) -> str:
    """Render only a fixed, allow-listed template and the nonclinical queue token."""
    if language not in {"en", "hi", "mr"} or template not in TEMPLATES:
        raise ValueError("unsupported notification template or language")
    if (
        not token
        or len(token) > 24
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for char in token)
    ):
        raise ValueError("invalid queue token")
    return TEMPLATES[template][language].format(token=token)


class NotificationProvider(Protocol):
    channel: str

    async def send(self, *, phone: str, body: str) -> str | None: ...


class ConsoleProvider:
    """Safe local provider; logs message text but never a recipient identifier."""

    channel = "console"

    async def send(self, *, phone: str, body: str) -> str:
        del phone
        print(f"[notification] {body}")
        return "console"


class TwilioSMSProvider:
    """Twilio Messages REST adapter. Enable explicitly through Settings."""

    channel = "sms"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def send(self, *, phone: str, body: str) -> str | None:
        url = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.settings.twilio_account_sid}/Messages.json"
        )
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(
                url,
                auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
                data={"To": f"+91{phone}", "From": self.settings.twilio_from_number, "Body": body},
            )
            response.raise_for_status()
            payload: Any = response.json()
            sid = payload.get("sid") if isinstance(payload, dict) else None
            return str(sid) if sid else None
        finally:
            if own_client:
                await client.aclose()


class WhatsAppCloudProvider:
    """Meta WhatsApp Cloud API adapter. Enable explicitly through Settings."""

    channel = "whatsapp"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        api_version: str = "v21.0",
    ) -> None:
        self.settings = settings
        self.client = client
        self.api_version = api_version

    async def send(self, *, phone: str, body: str) -> str | None:
        url = (
            f"https://graph.facebook.com/{self.api_version}/"
            f"{self.settings.whatsapp_phone_number_id}/messages"
        )
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": f"91{phone}",
                    "type": "text",
                    "text": {"body": body},
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
            messages = payload.get("messages") if isinstance(payload, dict) else None
            message_id = messages[0].get("id") if isinstance(messages, list) and messages else None
            return str(message_id) if message_id else None
        finally:
            if own_client:
                await client.aclose()


def build_notification_provider(settings: Settings) -> NotificationProvider:
    """Construct the explicitly selected adapter; console remains the safe default."""
    if settings.notification_provider == "twilio":
        return TwilioSMSProvider(settings)
    if settings.notification_provider == "whatsapp":
        return WhatsAppCloudProvider(settings)
    return ConsoleProvider()
