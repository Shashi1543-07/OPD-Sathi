import pytest

from app.config import Settings
from app.notifications.providers import (
    TEMPLATES,
    ConsoleProvider,
    TwilioSMSProvider,
    WhatsAppCloudProvider,
    build_notification_provider,
    render_message,
)


@pytest.mark.parametrize("language", ["en", "hi", "mr"])
@pytest.mark.parametrize("template", list(TEMPLATES))
def test_notification_templates_are_localized_and_queue_only(language: str, template: str) -> None:
    body = render_message(template, language, "GEN-004")
    assert "GEN-004" in body
    assert "diagnos" not in body.lower()
    assert "medicine" not in body.lower()


def test_template_rejects_unknown_language_template_and_untrusted_token() -> None:
    with pytest.raises(ValueError):
        render_message("unknown", "en", "GEN-001")
    with pytest.raises(ValueError):
        render_message("token_issued", "fr", "GEN-001")
    with pytest.raises(ValueError):
        render_message("token_issued", "en", "GEN-001\nmedical-details")


def test_console_is_default_and_live_providers_require_explicit_configuration() -> None:
    assert isinstance(build_notification_provider(Settings(app_env="test")), ConsoleProvider)
    with pytest.raises(ValueError, match="Twilio"):
        Settings(app_env="test", notification_provider="twilio")
    with pytest.raises(ValueError, match="WhatsApp"):
        Settings(app_env="test", notification_provider="whatsapp")


@pytest.mark.asyncio
async def test_console_provider_never_writes_recipient(capsys: pytest.CaptureFixture[str]) -> None:
    await ConsoleProvider().send(phone="9876543210", body="Your token is GEN-001.")
    output = capsys.readouterr().out
    assert "GEN-001" in output
    assert "9876543210" not in output


def test_explicit_provider_selection_builds_the_matching_adapter() -> None:
    twilio = Settings(
        app_env="test",
        notification_provider="twilio",
        twilio_account_sid="AC" + "a" * 32,
        twilio_auth_token="synthetic-secret",
        twilio_from_number="+15005550006",
    )
    whatsapp = Settings(
        app_env="test",
        notification_provider="whatsapp",
        whatsapp_access_token="synthetic-secret",
        whatsapp_phone_number_id="synthetic-number-id",
    )
    assert isinstance(build_notification_provider(twilio), TwilioSMSProvider)
    assert isinstance(build_notification_provider(whatsapp), WhatsAppCloudProvider)
