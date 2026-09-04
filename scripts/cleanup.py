"""Operator-run teardown utility (blog "Clean up" section).

This is NOT application runtime code. It is run manually, once, by an operator
to remove leftover schedules and avoid ongoing charges. It must never be wired
into an API handler or a schedule target, because it deletes ALL schedules in a
group.

One-time schedules that already fired are removed automatically by
ActionAfterCompletion: DELETE; this utility mops up the remainder (for example,
pending follow-ups for carts that were never converted).

Usage:
    python -m scripts.cleanup cart-abandonment
"""
import sys

from src.config import scheduler


def delete_all_schedules_in_group(group_name: str):
    """Delete all schedules in a group."""
    paginator = scheduler.get_paginator("list_schedules")
    for page in paginator.paginate(GroupName=group_name):
        for schedule in page["Schedules"]:
            scheduler.delete_schedule(
                Name=schedule["Name"],
                GroupName=group_name,
            )


def main(argv=None):
    """Operator entry point. Deletes schedules in each group named on the CLI."""
    groups = list(argv if argv is not None else sys.argv[1:])
    if not groups:
        print(
            "Usage: python -m scripts.cleanup <group_name> [<group_name> ...]",
            file=sys.stderr,
        )
        return 1

    for group_name in groups:
        print(f"Deleting all schedules in group: {group_name}")
        delete_all_schedules_in_group(group_name)
        print(f"Done: {group_name}")

    print(
        "\nNext, delete the empty schedule group and the Lambda/IAM role, e.g.:\n"
        "  aws scheduler delete-schedule-group --name cart-abandonment"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
