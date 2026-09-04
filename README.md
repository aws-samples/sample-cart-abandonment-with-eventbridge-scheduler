# Personalized cart re-engagement with Amazon EventBridge Scheduler

Sample code for the AWS blog post **"Personalized cart re-engagement with Amazon
EventBridge Scheduler."** It shows how to create one schedule per abandoned cart
— with a delay personalized to customer behavior — instead of scanning a
database on a fixed interval, covering scheduling, cancel-on-conversion, and
multi-stage follow-up sequences.

> **This is sample code for demonstration and educational purposes.** It has not
> undergone production hardening and is not intended for production use as-is.
> The customer store, inventory lookup, and message templating are intentionally
> left as stubs for you to implement — see
> [What to do after deploying](#what-to-do-after-deploying).

## The problem

Ecommerce platforms typically send abandoned-cart follow-ups from a batch job: a
Lambda runs on a fixed interval, scans the database for carts abandoned beyond a
threshold, and sends notifications in bulk. Timing is imprecise (every customer
gets the same delay regardless of intent), large batches can trigger rate
limiting from email/push providers, and cost scales with table size because you
scan the whole table on every interval.

## The solution

Create **one one-time schedule per abandoned cart** with Amazon EventBridge
Scheduler, with a delay personalized from customer signals (session duration,
cart value, segment). The schedule fires at the right moment and auto-deletes
afterward (`ActionAfterCompletion: DELETE`). If the customer converts first, you
delete the schedule at no cost. A flexible time window spreads invocations to
avoid bursts against downstream providers.

## Architecture

Your application detects cart abandonment and creates a one-time schedule in
EventBridge Scheduler. When the schedule fires, it invokes a target AWS Lambda
that checks whether the cart is still abandoned and delivers a personalized
notification. If the customer converts first, your checkout flow deletes the
pending schedule.

## Repository layout

| Path | Runs where | Blog snippet |
|------|-----------|--------------|
| `src/config.py` | client + Lambda | shared clients / ARNs / table / topic / group |
| `src/scheduler_client.py` | your app / API backend | Schedule, Cancel, Multi-stage, Dynamic cancel |
| `src/handler.py` | delivery Lambda (per-customer, stubbed) | Delivering the notification |
| `functions/reengagement_target/app.py` | deployed Lambda (SAM) | self-contained target (DynamoDB + SNS) |
| `src/data_layer.py` | your app | DynamoDB/SNS reference impl + stubs |
| `scripts/cleanup.py` | operator, one-time teardown | Clean up |
| `template.yaml` | AWS (CloudFormation/SAM) | infrastructure: Lambda + role + group + DynamoDB + SNS |
| `tests/test_scheduler_client.py` | local | mocked tests (moto) |

The code falls into **3 logical divisions**: (1) scheduling operations
(`scheduler_client.py`), (2) the delivery Lambda (`handler.py` / the deployable
`reengagement_target`), and (3) the operator-run cleanup utility
(`scripts/cleanup.py`).

> The customer/inventory/templating functions in `src/data_layer.py`
> (`get_customer`, `is_in_stock`, `build_message`, `send_push_notification`) are
> **stubs** — replace them with your own systems. Cart storage (`get_cart`,
> `save_cart_to_db`) and `send_email` are implemented against DynamoDB and SNS as
> a deployable reference.

## Prerequisites

- An AWS account
- AWS CLI installed and configured
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Python 3.9+ with boto3

## Deploy the infrastructure with AWS SAM

`template.yaml` provisions everything the scheduling code needs: the target
Lambda, the `cart-abandonment` schedule group, a least-privilege IAM role, a
DynamoDB table (KMS-encrypted, point-in-time recovery), and an SNS topic
(KMS-encrypted).

```bash
sam build
sam deploy --guided        # first-time deploy; prompts for stack name, region, etc.
```

On the first deploy, `--guided` walks you through the settings and offers to save
them to a `samconfig.toml` so later deploys can use plain `sam deploy`. Answer
**Y** to "Allow SAM CLI IAM role creation."

After deploy, note the stack outputs and point the scheduling code at them:

```bash
export SCHEDULER_ROLE_ARN="<SchedulerRoleArn output>"
export REENGAGEMENT_FUNCTION_ARN="<ReengagementFunctionArn output>"
export CARTS_TABLE="<CartsTableName output>"
export REENGAGEMENT_TOPIC_ARN="<ReengagementTopicArn output>"
export AWS_DEFAULT_REGION="us-east-1"    # match your deploy region
```

> **Scope of the deployable stack.** The SAM stack deploys a self-contained
> target (`functions/reengagement_target/app.py`) that reads the cart from
> DynamoDB and publishes to SNS, so you can watch a real notification arrive.
> The blog's per-customer delivery logic (`src/handler.py`) additionally uses
> `get_customer` / `is_in_stock` / `build_message`, which are stubbed — implement
> them for full per-customer personalization.

## How the trigger works: groups vs. schedules

The template creates a schedule **group** but no schedule, and there is no Lambda
"trigger" in `template.yaml` — this is intentional.

- A **schedule group** (`cart-abandonment`) is a container that organizes
  schedules. It does not invoke anything. It is static infrastructure, so it
  lives in the template.
- A **schedule** is the timer that fires and invokes the target. There is **one
  schedule per abandoned cart** — potentially millions — each created at runtime
  by your application (`scheduler_client.on_cart_abandoned`).
- The **trigger** is the `Target` on each schedule, set when the schedule is
  created — not at deploy time.

## Validate the deployment

Confirm the core mechanism — a schedule fires and invokes the Lambda — against
your deployed stack. Substitute values from your stack outputs.

```bash
STACK=<your-stack>; REGION=<your-region>
FUNC=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='ReengagementFunctionArn'].OutputValue" --output text)
FUNCTION_NAME=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='ReengagementFunctionName'].OutputValue" --output text)
ROLE=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='SchedulerRoleArn'].OutputValue" --output text)
TABLE=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='CartsTableName'].OutputValue" --output text)
TOPIC_ARN=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION --query "Stacks[0].Outputs[?OutputKey=='ReengagementTopicArn'].OutputValue" --output text)

# 1. Subscribe an endpoint to receive the notification (confirm the email link).
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
  --notification-endpoint you@example.com --region $REGION

# 2. Seed an abandoned cart in DynamoDB.
aws dynamodb put-item --table-name "$TABLE" --region $REGION \
  --item '{"cart_id":{"S":"cart-validate-1"},"customer_id":{"S":"c1"},"status":{"S":"abandoned"}}'

# 3. Create a follow-up ~2 minutes out (macOS date; Linux: date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%S)
FIRE=$(date -u -v+2M +%Y-%m-%dT%H:%M:%S); echo "FIRE=$FIRE"
aws scheduler create-schedule \
  --name cart-abandon-cart-validate-1 \
  --group-name cart-abandonment \
  --schedule-expression "at($FIRE)" \
  --schedule-expression-timezone "UTC" \
  --flexible-time-window '{"Mode":"FLEXIBLE","MaximumWindowInMinutes":5}' \
  --action-after-completion "DELETE" \
  --target "{\"Arn\":\"$FUNC\",\"RoleArn\":\"$ROLE\",\"Input\":\"{\\\"type\\\":\\\"cart_abandonment\\\",\\\"customer_id\\\":\\\"c1\\\",\\\"cart_id\\\":\\\"cart-validate-1\\\",\\\"cart_items\\\":[]}\"}" \
  --region $REGION

# 4. After the fire time (+ up to ~5 min flexible window), check the logs / your inbox.
aws logs tail "/aws/lambda/$FUNCTION_NAME" --since 15m --region $REGION
# Expect: CART FOLLOW-UP FIRED ... and "Published re-engagement for cart ... to SNS"
```

> The target looks the cart up in DynamoDB by `cart_id` and skips delivery if its
> `status` is not `abandoned` — so if you mark the cart converted before the
> follow-up fires, no notification is sent.

## What to do after deploying

The stack deploys the scheduling *infrastructure* plus a working DynamoDB + SNS
demo path. To build the full personalized experience from the blog:

1. **Wire the code to the stack** — set the environment variables above.
2. **Implement the remaining stubs (new application code)** — `get_customer`,
   `is_in_stock`, `build_message`, and `send_push_notification` in
   `src/data_layer.py`, against your customer store, inventory, templating, and
   push provider.
3. **Deploy your real target** — replace the demo `functions/reengagement_target/`
   with `src/handler.py` (once its stubs are implemented) for full per-customer
   delivery, and redeploy.
4. **Call the scheduling functions** from your application —
   `on_cart_abandoned`, `on_purchase_completed`, `on_high_value_cart_abandoned`,
   and the dynamic/multi-stage cancels.
5. **Test and clean up** — see the sections above.

## Running the tests

The tests mock EventBridge Scheduler with [moto](https://github.com/getmoto/moto),
so they touch **no** real AWS resources or credentials.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

## Clean up

A schedule group cannot be deleted while it still contains schedules, so clear
any remaining schedules first, then tear down the stack:

```bash
# 1. Delete any remaining schedules in the group (operator-run utility)
python -m scripts.cleanup cart-abandonment

# 2. Delete the SAM stack (removes the Lambda, IAM role, group, table, and topic)
sam delete
```

One-time schedules that already fired are removed automatically by
`ActionAfterCompletion: DELETE`.

## Security and compliance

This is illustrative sample code. Before production use:

- **Validate input** on cart/customer fields before building schedule expressions.
- **Enforce ownership** — confirm a `cart_id` belongs to the acting customer
  before scheduling or canceling.
- **Keep IAM least-privilege** — the scheduler role is scoped to invoking only
  the target function; scope any DynamoDB/SNS permissions you add the same way.
- **Encryption** — the DynamoDB table and SNS topic are encrypted with a
  customer-managed KMS key; the Lambda's environment variables are encrypted with
  the same key.
- **Protect PII** — cart contents and customer details flow through the schedule
  payload and logs; review what you store and log.

## Conclusion

Instead of scanning a database on a fixed interval, you create one schedule per
abandoned cart with a delay personalized to customer behavior. This eliminates
idle compute, reduces cost at scale, and delivers notifications at precisely the
right time. The pattern extends to browse abandonment, price-drop alerts,
back-in-stock notifications, and subscription renewals.

- [Amazon EventBridge Scheduler User Guide](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [Setting up the execution role](https://docs.aws.amazon.com/scheduler/latest/UserGuide/setting-up.html)
- [Serverless Land](https://serverlessland.com/)
