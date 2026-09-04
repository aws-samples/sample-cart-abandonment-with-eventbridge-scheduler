"""Application data-access and notification layer.

The blog presents these as helper calls ("your application's data access
layer"). This sample provides a **deployable reference implementation** backed
by Amazon DynamoDB (cart storage) and Amazon SNS (notification delivery) so you
can deploy the stack and watch a re-engagement notification actually arrive.

This is one concrete implementation, not the only one — swap DynamoDB for your
own store and SNS for your own notification channel (push provider, Amazon SES,
etc.). The per-customer helpers (`get_customer`, `send_push_notification`) and
`is_in_stock` are intentionally left as stubs, because they depend on your user
model and inventory system.
"""
from src.config import CARTS_TABLE, REENGAGEMENT_TOPIC_ARN, dynamodb, sns


# --------------------------------------------------------------------------- #
# Cart storage — Amazon DynamoDB (deployable reference implementation)
# --------------------------------------------------------------------------- #
def save_cart_to_db(customer_id: str, cart_id: str, cart_items: list,
                    status: str = "abandoned") -> None:
    """Persist a cart to DynamoDB."""
    dynamodb.Table(CARTS_TABLE).put_item(
        Item={
            "cart_id": cart_id,
            "customer_id": customer_id,
            "cart_items": cart_items,
            "status": status,
        }
    )


def mark_cart_converted(cart_id: str) -> None:
    """Mark a cart converted (purchased) in DynamoDB."""
    dynamodb.Table(CARTS_TABLE).update_item(
        Key={"cart_id": cart_id},
        UpdateExpression="SET #s = :converted",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":converted": "converted"},
    )


def get_cart(cart_id: str) -> dict:
    """Look up a cart record in DynamoDB. Returns {} if not found."""
    return dynamodb.Table(CARTS_TABLE).get_item(Key={"cart_id": cart_id}).get("Item", {})


# --------------------------------------------------------------------------- #
# Notification delivery — Amazon SNS (deployable reference implementation)
# --------------------------------------------------------------------------- #
def send_email(to: str, subject: str, html_body: str) -> None:
    """Deliver a re-engagement email by publishing to an SNS topic.

    Named `send_email` to match the blog. In this reference implementation it
    publishes to SNS; subscribe an email or SMS endpoint to the topic to receive
    it. Replace with Amazon SES or your own email provider for direct HTML email.
    """
    sns.publish(TopicArn=REENGAGEMENT_TOPIC_ARN, Subject=subject, Message=html_body)


# --------------------------------------------------------------------------- #
# Per-customer / inventory helpers — stubs (implement against your own systems)
# --------------------------------------------------------------------------- #
def get_customer(customer_id: str) -> dict:
    """Look up a customer record (channel preference, email, device token).

    Left as a stub: per-customer routing depends on your user model. Return a
    dict like {"channel_preference": "email", "email": "..."}.
    """
    raise NotImplementedError("Replace with your application's customer store")


def is_in_stock(product_id: str) -> bool:
    """Return whether a product is in stock. Replace with your inventory system."""
    raise NotImplementedError("Replace with your inventory system")


def send_push_notification(device_token: str, title: str, body: str) -> None:
    """Deliver a push notification. Replace with your push provider (e.g. a
    mobile push service). Left as a stub in this sample."""
    raise NotImplementedError("Replace with your push notification provider")


def build_message(customer: dict, items: list, session_duration: int,
                  segment: str) -> dict:
    """Build the notification content. Replace with your templating.

    Left as a stub: message content/templating is application-specific. Return a
    dict with subject/html/push_title/push_body keys.
    """
    raise NotImplementedError("Replace with your message templating")
