import base64

import pytest

import dex_execution


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_order_returns_unsigned_transaction_and_route(monkeypatch):
    transaction = base64.b64encode(b"\\x00" * 120).decode()
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update({"url": url, "params": params, "headers": headers})
        return FakeResponse({
            "transaction": transaction,
            "requestId": "req-123",
            "inAmount": "1000",
            "outAmount": "2500",
            "otherAmountThreshold": "2400",
            "router": "metis",
            "transactionVersion": 0,
            "routePlan": [{"swapInfo": {"label": "Raydium"}}],
        })

    monkeypatch.setattr(dex_execution.requests, "get", fake_get)
    result = dex_execution.create_order(
        "11111111111111111111111111111111",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        1000,
        "11111111111111111111111111111111",
    )

    assert result["request_id"] == "req-123"
    assert result["output_amount"] == 2500
    assert result["minimum_output_amount"] == 2400
    assert result["signing"] == "USER_WALLET_REQUIRED"
    assert result["execution"] == "LOCKED"
    assert captured["params"]["taker"] == "11111111111111111111111111111111"


def test_order_rejects_missing_transaction(monkeypatch):
    monkeypatch.setattr(
        dex_execution.requests, "get",
        lambda *args, **kwargs: FakeResponse({"transaction": None, "errorMessage": "No route"}),
    )
    with pytest.raises(ValueError, match="did not build"):
        dex_execution.create_order(
            "11111111111111111111111111111111",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            1000,
            "11111111111111111111111111111111",
        )


def test_order_rejects_mismatched_input_amount(monkeypatch):
    transaction = base64.b64encode(b"\\x00" * 120).decode()
    monkeypatch.setattr(
        dex_execution.requests, "get",
        lambda *args, **kwargs: FakeResponse({
            "transaction": transaction, "requestId": "req",
            "inAmount": "999", "outAmount": "2000",
        }),
    )
    with pytest.raises(ValueError, match="does not match"):
        dex_execution.create_order(
            "11111111111111111111111111111111",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            1000,
            "11111111111111111111111111111111",
        )


def test_execute_is_locked_by_default(monkeypatch):
    monkeypatch.setattr(dex_execution, "EXECUTE_ENABLED", False)
    with pytest.raises(dex_execution.ExecutionLocked, match="disabled"):
        dex_execution.execute_signed_order("AAAA", "req", confirmed=True)


def test_execute_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(dex_execution, "EXECUTE_ENABLED", True)
    signed = base64.b64encode(b"\\x00" * 120).decode()
    with pytest.raises(dex_execution.ExecutionLocked, match="confirmation"):
        dex_execution.execute_signed_order(signed, "req", confirmed=False)


def test_execute_posts_only_signed_transaction_and_request_id(monkeypatch):
    monkeypatch.setattr(dex_execution, "EXECUTE_ENABLED", True)
    signed = base64.b64encode(b"\\x00" * 120).decode()
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update({"url": url, "json": json})
        return FakeResponse({
            "status": "Success", "signature": "signature123",
            "code": 0, "outputAmountResult": "2500",
        })

    monkeypatch.setattr(dex_execution.requests, "post", fake_post)
    result = dex_execution.execute_signed_order(signed, "req-123", confirmed=True)

    assert captured["json"] == {
        "signedTransaction": signed, "requestId": "req-123"
    }
    assert result["status"] == "Success"
    assert result["explorer_url"].endswith("signature123")


def test_execution_status_reports_locked_and_non_custodial(monkeypatch):
    monkeypatch.setattr(dex_execution, "EXECUTE_ENABLED", False)
    status = dex_execution.get_execution_status()
    assert status["execution_locked"] is True
    assert status["wallet_secret_required_on_server"] is False
