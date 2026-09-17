"""Client-side scheduling operations for per-customer cart re-engagement.

This module runs wherever your application detects cart abandonment and
conversion (for example, behind your storefront API). It routes a detected
abandonment to either a single personalized follow-up or a multi-stage sequence
for high-value carts, and cancels all pending follow-ups when a customer
converts. The target Lambda that fires when a schedule is due lives in
src/handler.py. One-time teardown of leftover schedules lives in
scripts/cleanup.py.

Consolidated from blog snippets: schedule follow-up, multi-stage sequences,
cancel on conversion.
"""
import json
from datetime import datetime, timedelta, timezone

from src.config import (
    CART_ABANDONMENT_GROUP,
    FUNCTION_ARN,
    ROLE_ARN,
    scheduler,
)

# Carts at or above this value get a multi-stage follow-up sequence.
HIGH_VALUE_THRESHOLD = 200


def handle_cart_abandonment(customer_id: str, cart_id: str, cart_items: list,
                            session_duration_seconds: int, customer_timezone: str,
                            channel_preference: str, customer_segment: str):
    """Route a detected cart abandonment to the appropriate follow-up strategy.

    High-value carts receive a multi-stage sequence; all others receive a
    single personalized follow-up. A cart is scheduled through exactly one path.
    """
    cart_value = sum(item["price"] * item["quantity"] for item in cart_items)

    if cart_value >= HIGH_VALUE_THRESHOLD:
        on_high_value_cart_abandoned(
            customer_id, cart_id, cart_items,
            session_duration_seconds, channel_preference, customer_segment,
        )
    else:
        on_cart_abandoned(
            customer_id, cart_id, cart_items,
            session_duration_seconds, customer_timezone,
            channel_preference, customer_segment,
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


def on_high_value_cart_abandoned(customer_id: str, cart_id: str,
                                 cart_items: list, session_duration_seconds: int,
                                 channel_preference: str, customer_segment: str):
    """Create a multi-stage follow-up sequence for high-value carts."""
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
                    "session_duration_seconds": session_duration_seconds,
                    "channel_preference": channel_preference,
                    "customer_segment": customer_segment,
                }),
            },
        )


def on_purchase_completed(customer_id: str, cart_id: str):
    """Cancel every pending follow-up for this cart when the customer converts.

    Uses a NamePrefix filter so it cancels both the single-stage schedule
    (cart-abandon-{cart_id}) and any multi-stage schedules
    (cart-abandon-{cart_id}-stage1, -stage2, ...), regardless of which
    scheduling path created them.
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
                pass  # Already fired or deleted concurrently
