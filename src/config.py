"""Shared configuration and AWS clients.

The blog snippets hardcode ROLE_ARN and FUNCTION_ARN for readability. In the
runnable sample we read them (and the DynamoDB table / SNS topic added for the
deployable end-to-end demo) from environment variables so no AWS account IDs are
committed and the same code runs unchanged in dev, test, and production.
"""
import os

import boto3

# One shared EventBridge Scheduler client, reused across all operations.
scheduler = boto3.client("scheduler")

# DynamoDB resource and SNS client for the deployable end-to-end demo: carts are
# stored in a DynamoDB table and re-engagement notifications are published to an
# SNS topic.
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

# IAM role that grants EventBridge Scheduler permission to invoke the target
# Lambda function. See "Setting up EventBridge Scheduler" in the blog post.
ROLE_ARN = os.environ.get(
    "SCHEDULER_ROLE_ARN",
    "arn:aws:iam::111122223333:role/SchedulerToLambdaRole",
)

# ARN of the Lambda function that delivers the notification (see src/handler.py).
FUNCTION_ARN = os.environ.get(
    "REENGAGEMENT_FUNCTION_ARN",
    "arn:aws:lambda:us-east-1:111122223333:function:cart-reengagement",
)

# DynamoDB table that stores carts, and the SNS topic notifications publish to.
# Both are created by template.yaml; their names/ARNs come from the stack
# outputs (set as environment variables).
CARTS_TABLE = os.environ.get("CARTS_TABLE", "cart-abandonment-carts")
REENGAGEMENT_TOPIC_ARN = os.environ.get("REENGAGEMENT_TOPIC_ARN", "")

# Schedule group used to organize cart-abandonment schedules.
CART_ABANDONMENT_GROUP = "cart-abandonment"
