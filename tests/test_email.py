"""Tests for ResendEmailService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.protocols.email import EmailMessage, IEmailService
from infra.email import ResendEmailService


def _make_service(
    api_key: str = "re_test_key", from_addr: str = "noreply@example.se"
) -> ResendEmailService:
    with patch.dict("os.environ", {"RESEND_API_KEY": api_key, "EMAIL_FROM": from_addr}):
        return ResendEmailService()


def _mock_response(
    status_code: int = 200, json_data: dict | None = None, text: str = ""
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _message() -> EmailMessage:
    return EmailMessage(
        to=["magnus@example.se"],
        subject="Prisvarning",
        html_body="<p>hej</p>",
    )


class TestResendEmailService:
    def test_implements_protocol(self) -> None:
        assert isinstance(_make_service(), IEmailService)

    def test_is_configured_requires_key_and_from(self) -> None:
        assert _make_service().is_configured() is True
        assert _make_service(api_key="").is_configured() is False
        assert _make_service(from_addr="").is_configured() is False

    @pytest.mark.asyncio
    async def test_send_unconfigured_fails_without_http(self) -> None:
        service = _make_service(api_key="")
        with patch("httpx.AsyncClient") as mock_cls:
            result = await service.send(_message())
        assert result.success is False
        assert "not configured" in result.error
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_success_returns_message_id(self) -> None:
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, {"id": "abc-123"}))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await service.send(_message())

        assert result.success is True
        assert result.message_id == "abc-123"
        # Payload carries from/to/subject/html and bearer auth
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["from"] == "noreply@example.se"
        assert kwargs["json"]["to"] == ["magnus@example.se"]
        assert kwargs["json"]["html"] == "<p>hej</p>"
        assert kwargs["headers"]["Authorization"] == "Bearer re_test_key"

    @pytest.mark.asyncio
    async def test_send_api_error_returns_failure(self) -> None:
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_response(422, text='{"message":"domain not verified"}')
        )

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await service.send(_message())

        assert result.success is False
        assert "422" in result.error
        assert "domain not verified" in result.error

    @pytest.mark.asyncio
    async def test_send_network_error_returns_failure(self) -> None:
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("dns fail"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await service.send(_message())

        assert result.success is False
        assert "dns fail" in result.error

    @pytest.mark.asyncio
    async def test_send_batch_sends_each(self) -> None:
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200, {"id": "x"}))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            results = await service.send_batch([_message(), _message()])

        assert len(results) == 2
        assert all(r.success for r in results)
        assert mock_client.post.call_count == 2
