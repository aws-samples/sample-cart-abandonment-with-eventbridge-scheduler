"""Target Lambda function invoked by EventBridge Scheduler when a follow-up fires.

This is the blog's per-customer delivery logic. It checks whether the cart is
still abandoned, filters to in-stock items, builds a personalized message, and
delivers it through the customer's preferred channel.

Note: this version calls the application data layer (`get_cart`, `get_customer`,
`is_in_stock`, `build_message`, `send_email`, `send_push_notification`). Some of
those are stubs in this sample (see src/data_layer.py). The self-contained,
deployable target used by the SAM stack lives in
functions/reengagement_target/app.py.
"""
from src.data_layer import (
    build_message,
    get_cart,
    get_customer,
    is_in_stock,
    send_email,
    send_push_notification,
)


def lambda_handler(event, context):
    """Deliver a personalized cart abandonment notification."""
    customer_id = event["customer_id"]
    cart_id = event["cart_id"]

    cart = get_cart(cart_id)
    if cart["status"] != "abandoned":
        return  # Customer already converted

    available_items = [item for item in event["cart_items"]
                       if is_in_stock(item["product_id"])]
    if not available_items:
        return  # Nothing to offer

    customer = get_customer(customer_id)
    message = build_message(
        customer=customer,
        items=available_items,
        session_duration=event["session_duration_seconds"],
        segment=event["customer_segment"],
    )

    if event["channel_preference"] == "email":
        send_email(to=customer["email"], subject=message["subject"],
                   html_body=message["html"])
    elif event["channel_preference"] == "push":
        send_push_notification(device_token=customer["device_token"],
                               title=message["push_title"],
                               body=message["push_body"])
