"""Mocked tests proving the sample works as the blog describes.

These tests use moto to mock EventBridge Scheduler, so no real AWS resources or
credentials are touched. They verify each capability in the blog: schedule a
personalized follow-up, the delay heuristic, cancel on conversion, multi-stage
sequences, dynamic prefix cancel, the delivery handler, and cleanup.

Run:  pytest -q
"""
import json

import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"
GROUP = "cart-abandonment"

# A fake device token used only in tests. Not a credential; the value is
# meaningless. nosec silences Bandit's hardcoded-secret heuristic, which flags
# any literal assigned near an identifier containing "token".
FAKE_DEVICE_TOKEN = "example-device-token"  # nosec B105


@pytest.fixture
def aws_env(monkeypatch):
    """Fake AWS credentials/region so boto3 clients initialize under moto."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def modules(aws_env):
    """Import the sample modules under an active moto mock and create the group."""
    with mock_aws():
        boto3.client("scheduler", region_name=REGION).create_schedule_group(
            Name=GROUP
        )

        import importlib

        import src.config as config
        importlib.reload(config)
        import src.scheduler_client as sc
        importlib.reload(sc)
        import src.handler as handler
        importlib.reload(handler)
        import scripts.cleanup as cleanup
        importlib.reload(cleanup)

        yield {"config": config, "sc": sc, "handler": handler, "cleanup": cleanup}


def _get_schedule(name):
    return boto3.client("scheduler", region_name=REGION).get_schedule(
        Name=name, GroupName=GROUP
    )


ITEMS = [{"product_id": "p1", "price": 60.0, "quantity": 2}]  # cart_value = 120


# --------------------------------------------------------------------------- #
# calculate_optimal_delay — pure logic
# --------------------------------------------------------------------------- #
def test_delay_vip(modules):
    assert modules["sc"].calculate_optimal_delay(10, "vip", 5) == 30


def test_delay_high_intent(modules):
    # long session + high cart value
    assert modules["sc"].calculate_optimal_delay(700, "regular", 150) == 30


def test_delay_medium_intent(modules):
    assert modules["sc"].calculate_optimal_delay(200, "regular", 50) == 60


def test_delay_low_intent(modules):
    assert modules["sc"].calculate_optimal_delay(10, "regular", 5) == 120


# --------------------------------------------------------------------------- #
# on_cart_abandoned
# --------------------------------------------------------------------------- #
def test_on_cart_abandoned_creates_schedule(modules):
    sc = modules["sc"]
    sc.on_cart_abandoned(
        customer_id="c1", cart_id="cart1", cart_items=ITEMS,
        session_duration_seconds=700, customer_timezone="UTC",
        channel_preference="email", customer_segment="regular",
    )
    sched = _get_schedule("cart-abandon-cart1")
    assert sched["ScheduleExpression"].startswith("at(")
    assert sched["FlexibleTimeWindow"]["Mode"] == "FLEXIBLE"
    assert sched["FlexibleTimeWindow"]["MaximumWindowInMinutes"] == 5
    assert sched["ActionAfterCompletion"] == "DELETE"
    payload = json.loads(sched["Target"]["Input"])
    assert payload["type"] == "cart_abandonment"
    assert payload["customer_id"] == "c1"
    assert payload["cart_items"] == ITEMS
    assert "abandoned_at" in payload


# --------------------------------------------------------------------------- #
# on_purchase_completed
# --------------------------------------------------------------------------- #
def test_cancel_on_conversion(modules):
    sc = modules["sc"]
    sc.on_cart_abandoned("c1", "cart2", ITEMS, 700, "UTC", "email", "regular")
    sc.on_purchase_completed("c1", "cart2")
    client = boto3.client("scheduler", region_name=REGION)
    with pytest.raises(client.exceptions.ResourceNotFoundException):
        client.get_schedule(Name="cart-abandon-cart2", GroupName=GROUP)


def test_cancel_when_absent_is_silent(modules):
    modules["sc"].on_purchase_completed("c1", "never-existed")  # must not raise


# --------------------------------------------------------------------------- #
# Multi-stage sequences
# --------------------------------------------------------------------------- #
def test_high_value_creates_three_stages(modules):
    sc = modules["sc"]
    items = [{"product_id": "p", "price": 300.0, "quantity": 1}]  # value 300 >= 200
    sc.on_high_value_cart_abandoned("c1", "cartHV", items)
    for suffix in ("stage1", "stage2", "stage3"):
        sched = _get_schedule(f"cart-abandon-cartHV-{suffix}")
        assert sched["FlexibleTimeWindow"]["Mode"] == "OFF"


def test_low_value_creates_no_stages(modules):
    sc = modules["sc"]
    items = [{"product_id": "p", "price": 10.0, "quantity": 1}]  # value 10 < 200
    sc.on_high_value_cart_abandoned("c1", "cartLV", items)
    client = boto3.client("scheduler", region_name=REGION)
    with pytest.raises(client.exceptions.ResourceNotFoundException):
        client.get_schedule(Name="cart-abandon-cartLV-stage1", GroupName=GROUP)


def test_multi_stage_cancel_removes_all(modules):
    sc = modules["sc"]
    items = [{"product_id": "p", "price": 300.0, "quantity": 1}]
    sc.on_high_value_cart_abandoned("c1", "cartHV2", items)
    sc.on_purchase_completed_multi_stage("c1", "cartHV2")
    remaining = boto3.client("scheduler", region_name=REGION).list_schedules(
        GroupName=GROUP
    )["Schedules"]
    assert [s for s in remaining if s["Name"].startswith("cart-abandon-cartHV2")] == []


def test_dynamic_cancel_by_prefix(modules):
    sc = modules["sc"]
    items = [{"product_id": "p", "price": 300.0, "quantity": 1}]
    sc.on_high_value_cart_abandoned("c1", "cartDyn", items)
    sc.on_purchase_completed_dynamic("c1", "cartDyn")
    remaining = boto3.client("scheduler", region_name=REGION).list_schedules(
        GroupName=GROUP
    )["Schedules"]
    assert [s for s in remaining if s["Name"].startswith("cart-abandon-cartDyn")] == []


# --------------------------------------------------------------------------- #
# Delivery handler (src/handler.py, with stubbed data layer patched)
# --------------------------------------------------------------------------- #
def _event(channel="email"):
    return {
        "customer_id": "c1", "cart_id": "cart1",
        "cart_items": [{"product_id": "p1", "price": 60.0, "quantity": 2}],
        "session_duration_seconds": 700, "customer_segment": "regular",
        "channel_preference": channel,
    }


def test_handler_sends_email(modules):
    from unittest.mock import patch
    handler = modules["handler"]
    with patch.object(handler, "get_cart", return_value={"status": "abandoned"}), \
            patch.object(handler, "is_in_stock", return_value=True), \
            patch.object(handler, "get_customer", return_value={"email": "c1@example.com"}), \
            patch.object(handler, "build_message", return_value={"subject": "S", "html": "H"}), \
            patch.object(handler, "send_email") as send:
        handler.lambda_handler(_event("email"), None)
    send.assert_called_once()
    assert send.call_args.kwargs["to"] == "c1@example.com"


def test_handler_sends_push(modules):
    from unittest.mock import patch
    handler = modules["handler"]
    with patch.object(handler, "get_cart", return_value={"status": "abandoned"}), \
            patch.object(handler, "is_in_stock", return_value=True), \
            patch.object(handler, "get_customer", return_value={"device_token": FAKE_DEVICE_TOKEN}), \
            patch.object(handler, "build_message", return_value={"push_title": "T", "push_body": "B"}), \
            patch.object(handler, "send_push_notification") as push:
        handler.lambda_handler(_event("push"), None)
    push.assert_called_once_with(device_token=FAKE_DEVICE_TOKEN, title="T", body="B")


def test_handler_skips_converted_cart(modules):
    from unittest.mock import patch
    handler = modules["handler"]
    with patch.object(handler, "get_cart", return_value={"status": "converted"}), \
            patch.object(handler, "send_email") as send, \
            patch.object(handler, "send_push_notification") as push:
        handler.lambda_handler(_event("email"), None)
    send.assert_not_called()
    push.assert_not_called()


def test_handler_skips_when_no_items_in_stock(modules):
    from unittest.mock import patch
    handler = modules["handler"]
    with patch.object(handler, "get_cart", return_value={"status": "abandoned"}), \
            patch.object(handler, "is_in_stock", return_value=False), \
            patch.object(handler, "send_email") as send:
        handler.lambda_handler(_event("email"), None)
    send.assert_not_called()


# --------------------------------------------------------------------------- #
# Cleanup utility
# --------------------------------------------------------------------------- #
def test_cleanup_main_clears_group(modules):
    sc = modules["sc"]
    cleanup = modules["cleanup"]
    sc.on_cart_abandoned("c1", "cx", ITEMS, 700, "UTC", "email", "regular")
    rc = cleanup.main([GROUP])
    assert rc == 0
    assert boto3.client("scheduler", region_name=REGION).list_schedules(
        GroupName=GROUP
    )["Schedules"] == []


def test_cleanup_main_without_args_errors(modules):
    assert modules["cleanup"].main([]) == 1
