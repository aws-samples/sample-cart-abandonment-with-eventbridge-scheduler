"""Deployable target Lambda for the SAM stack (end-to-end demo).

EventBridge Scheduler invokes this function when a cart follow-up fires. It reads
the cart from DynamoDB, skips it if the customer already converted, and publishes
a personalized re-engagement message to an SNS topic — so after deploying and
subscribing to the topic, you receive a real notification.

This is a self-contained handler (its own boto3 clients, no repo imports) so it
packages cleanly as a Lambda. It mirrors the logic in src/handler.py, which is
the blog's per-customer version. Swap SNS for your own notification channel and
add your own inventory / message templating as needed.

Environment variables (set by template.yaml):
  CARTS_TABLE             - DynamoDB table holding carts
  REENGAGEMENT_TOPIC_ARN  - SNS topic to publish notifications to
"""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CARTS_TABLE = os.environ["CARTS_TABLE"]
REENGAGEMENT_TOPIC_ARN = os.environ["REENGAGEMENT_TOPIC_ARN"]

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")


def lambda_handler(event, context):
    """Deliver a re-engagement notification: read the cart, skip if converted,
    publish to SNS."""
    logger.info("CART FOLLOW-UP FIRED: %s", json.dumps(event))

    cart_id = event["cart_id"]
    cart = dynamodb.Table(CARTS_TABLE).get_item(Key={"cart_id": cart_id}).get("Item")

    # Skip if the customer already converted (or the cart no longer exists).
    if cart and cart.get("status") != "abandoned":
        logger.info("Cart %s no longer abandoned (status=%s); skipping",
                    cart_id, cart.get("status"))
        return {"delivered": False, "reason": "not_abandoned"}

    stage = event.get("stage", "reminder")
    item_count = len(event.get("cart_items", []))
    message = (
        f"You left {item_count} item(s) in your cart. "
        f"Complete your purchase to secure them. (stage: {stage})"
    )
    sns.publish(
        TopicArn=REENGAGEMENT_TOPIC_ARN,
        Subject="You left something in your cart",
        Message=message,
    )
    logger.info("Published re-engagement for cart %s (stage %s) to SNS", cart_id, stage)
    return {"delivered": True, "cart_id": cart_id, "stage": stage}
