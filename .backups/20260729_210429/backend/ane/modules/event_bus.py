"""In-memory publish/subscribe event bus.

All state changes flow through here. Modules subscribe to event types they care about.
The bus itself has zero knowledge of modules or database — it's pure routing.

State-change handlers are registered by game_engine at startup to apply
validated state changes to the database via the appropriate managers.
"""

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]  # (session_id, data) -> None

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler):
        """Register a handler for a specific event type."""
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed: {event_type} → {handler.__name__}")

    def subscribe_all(self, event_types: list[str], handler: Handler):
        """Register the same handler for multiple event types."""
        for et in event_types:
            self.subscribe(et, handler)

    async def publish(self, event_type: str, session_id: str, data: dict[str, Any] | None = None):
        """Publish an event. All registered handlers for this type are called.

        Handlers run sequentially for a given event type, so ordering is preserved.
        """
        payload = data or {}
        logger.info(f"Event: {event_type}  session={session_id}  data={payload}")
        for handler in self._subscribers.get(event_type, []):
            try:
                await handler(session_id, payload)
            except Exception:
                logger.exception(f"Handler {handler.__name__} failed for event {event_type}")

    async def publish_state_changes(
        self,
        session_id: str,
        changes: list[dict],
    ):
        """Publish a batch of state change events in order.

        This is the primary entry point for applying state changes from LLM output.
        Each change dict must have at least 'type' and is routed to the matching handler.
        """
        for change in changes:
            event_type = change.get("type", "unknown")
            if event_type == "unknown":
                logger.warning(f"Dropped state change with no type: {change}")
                continue
            await self.publish(event_type, session_id, change)


# Global singleton for the app lifetime.
bus = EventBus()
