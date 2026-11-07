"""Persistent notification outbox delivery boundaries."""

from __future__ import annotations

from typing import Protocol

from process_optimizer.workflow.schemas import NotificationPayloadV1


class NotificationDeliveryError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__("Notification delivery failed")
        self.error_code = error_code


class NotificationAdapter(Protocol):
    """Delivery port; only a successful return permits a DELIVERED status."""

    def deliver(self, template: str, payload: NotificationPayloadV1) -> None: ...


class LocalDemoNotificationAdapter:
    """Safe local sink that performs no external I/O."""

    def __init__(self, *, fail: bool = False) -> None:
        self.deliveries: list[tuple[str, NotificationPayloadV1]] = []
        self._fail = fail

    def deliver(self, template: str, payload: NotificationPayloadV1) -> None:
        if self._fail:
            raise NotificationDeliveryError("LOCAL_DEMO_FAILURE")
        self.deliveries.append((template, payload))


class MicrosoftTeamsNotificationAdapter(Protocol):
    """Future Teams delivery port; credentials and implementation are intentionally absent."""

    def deliver(self, template: str, payload: NotificationPayloadV1) -> None: ...
