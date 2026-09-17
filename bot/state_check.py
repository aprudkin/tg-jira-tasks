"""Validate a deployment copy of notification state without printing its contents."""

import logging


def main() -> int:
    # State loading can normally log channel identifiers during migration. Deployment
    # diagnostics are deliberately aggregate-only.
    logging.disable(logging.CRITICAL)
    from bot.services.notifications import notification_service

    if notification_service.state_load_error is not None:
        print("Notification state is incompatible with this image")
        return 1
    print("Notification state is compatible with this image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
