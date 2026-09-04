"""Client-side scheduling operations for per-customer cart re-engagement.

This module runs wherever your application detects cart abandonment and
conversion (for example, behind your storefront API). It creates one-time
EventBridge Scheduler schedules with a personalized delay, cancels them when a
customer converts, and supports multi-stage follow-up sequences for high-value
carts. The target Lambda that fires when a schedule is due lives in
src/handler.py. One-time teardown of leftover schedules lives in
scripts/cleanup.py.

Consolidated from blog snippets: schedule follow-up, cancel on conversion,
multi-stage sequences, dynamic cancel.
"""
import json
from datetime import datetime, timedelta, timezone

from src.config import (
    CART_ABANDONMENT_GROUP,
    FUNCTION_ARN,
    ROLE_ARN,
    scheduler,
)


def on_cart_abandoned(customer_id: str, cart_id: str, cart_items: list,
                      session_duration_seconds: int, customer_timezone: str,
                      channel_preference: str, customer_segment: str):
    """Schedule a personalized follow-up when a cart is abandoned."""
    delay_minutes = calculate_optimal_delay(
        session_duration=session_duration_seconds,
        segment=customer_segment,
        cart_value=sum(item["price"] * item["quantity"] for item in cart_items),
    )

    fire_time = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)

    scheduler.create_schedule(
        Name=f"cart-abandon-{cart_id}",
        GroupName=CART_ABANDONMENT_GROUP,
        ScheduleExpression=f"at({fire_time.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "FLEXIBLE", "MaximumWindowInMinutes": 5},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": FUNCTION_ARN,
            "RoleArn": ROLE_ARN,
            "Input": json.dumps({
                "type": "cart_abandonment",
                "customer_id": customer_id,
                "cart_id": cart_id,
                "cart_items": cart_items,
                "session_duration_seconds": session_duration_seconds,
                "channel_preference": channel_preference,
                "customer_segment": customer_segment,
                "abandoned_at": datetime.now(timezone.utc).isoformat(),
            }),
        },
    )


def calculate_optimal_delay(session_duration: int, segment: str,
                            cart_value: float) -> int:
    """Determine follow-up delay based on customer signals."""
    if segment == "vip":
        return 30

    if session_duration > 600 and cart_value > 100:
        return 30  # High intent: long session, high cart value

    if session_duration > 180:
        return 60  # Medium intent

    return 120  # Low intent / casual browse


def on_purchase_completed(customer_id: str, cart_id: str):
    """Cancel the abandonment follow-up when the customer converts."""
    try:
        scheduler.delete_schedule(
            Name=f"cart-abandon-{cart_id}",
            GroupName=CART_ABANDONMENT_GROUP,
        )
    except scheduler.exceptions.ResourceNotFoundException:
        pass  # Follow-up already fired or was never scheduled


def on_high_value_cart_abandoned(customer_id: str, cart_id: str,
                                 cart_items: list):
    """Create a multi-stage follow-up sequence for high-value carts."""
    cart_value = sum(item["price"] * item["quantity"] for item in cart_items)
    if cart_value < 200:
        return

    stages = [
        {"delay_minutes": 30, "stage": "gentle_reminder", "suffix": "stage1"},
        {"delay_minutes": 240, "stage": "social_proof", "suffix": "stage2"},
        {"delay_minutes": 1440, "stage": "incentive_offer", "suffix": "stage3"},
    ]

    for stage in stages:
        fire_time = datetime.now(timezone.utc) + timedelta(minutes=stage["delay_minutes"])
        scheduler.create_schedule(
            Name=f"cart-abandon-{cart_id}-{stage['suffix']}",
            GroupName=CART_ABANDONMENT_GROUP,
            ScheduleExpression=f"at({fire_time.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": FUNCTION_ARN,
                "RoleArn": ROLE_ARN,
                "Input": json.dumps({
                    "type": "cart_abandonment",
                    "stage": stage["stage"],
                    "customer_id": customer_id,
                    "cart_id": cart_id,
                    "cart_items": cart_items,
                }),
            },
        )


def on_purchase_completed_multi_stage(customer_id: str, cart_id: str):
    """Cancel all pending follow-up stages on conversion."""
    for suffix in ["stage1", "stage2", "stage3"]:
        try:
            scheduler.delete_schedule(
                Name=f"cart-abandon-{cart_id}-{suffix}",
                GroupName=CART_ABANDONMENT_GROUP,
            )
        except scheduler.exceptions.ResourceNotFoundException:
            pass


def on_purchase_completed_dynamic(customer_id: str, cart_id: str):
    """Cancel all pending stages using a name prefix filter.

    For dynamic sequences where the number of stages varies per customer
    segment, discover and delete all related schedules by name prefix.
    """
    prefix = f"cart-abandon-{cart_id}"
    paginator = scheduler.get_paginator("list_schedules")
    for page in paginator.paginate(GroupName=CART_ABANDONMENT_GROUP, NamePrefix=prefix):
        for schedule in page["Schedules"]:
            try:
                scheduler.delete_schedule(
                    Name=schedule["Name"],
                    GroupName=CART_ABANDONMENT_GROUP,
                )
            except scheduler.exceptions.ResourceNotFoundException:
                pass
