"""
Memory System API Client - Adapted for LoCoMo-Plus testing.

Async client for the memory system API at http://localhost:8010.
Key endpoint: POST /api/v1/memory/chat

The system auto-triggers background extraction every 5 user messages
in the same session_id (WINDOW_SIZE=10, TRIGGER_EVERY=5).
"""
import asyncio
import logging
import uuid
from typing import Optional, Dict, Any, List

import httpx

logger = logging.getLogger(__name__)


def device_to_owner_uuid(device_id: str) -> str:
    """Convert device_id to a deterministic UUID (used as owner_id).

    Uses uuid5(NAMESPACE_DNS, 'memory_system.device.<device_id>') so the
    same device_id always maps to the same owner_id.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"memory_system.device.{device_id}"))


class MemoryClient:
    """Async API client for the memory system, tailored for LoCoMo-Plus evaluation.

    Usage::

        async with MemoryClient() as client:
            ok = await client.health_check()
            result = await client.ingest_dialogue(device_id, user_name, turns, session_id)
            recall = await client.recall(device_id, user_name, query)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8010",
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None

    # -- async context manager -------------------------------------------

    async def __aenter__(self) -> "MemoryClient":
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
            self.client = None

    # -- health check ----------------------------------------------------

    async def health_check(self) -> bool:
        """GET /health — returns True if the service is reachable and healthy."""
        try:
            response = await self.client.get("/health")
            response.raise_for_status()
            logger.info("Health check passed (status %d)", response.status_code)
            return True
        except Exception as e:
            logger.warning("Health check failed: %s", e)
            return False

    # -- core turn-level API ---------------------------------------------

    async def send_turn(
        self,
        owner_id: str,
        user_name: str,
        user_message: str,
        assistant_message: str,
        session_id: str,
    ) -> dict:
        """POST /api/v1/memory/chat — send a single (user, assistant) turn pair.

        Args:
            owner_id: Valid UUID identifying the owner / device.
            user_name: Display name of the user.
            user_message: The user's message text.
            assistant_message: The assistant's reply text (can be empty).
            session_id: Session identifier; the server accumulates a buffer
                        per session and auto-extracts every 5 user messages.

        Returns:
            Full JSON response dict from the server.
        """
        payload = {
            "owner_id": owner_id,
            "user_name": user_name,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "session_id": session_id,
        }

        try:
            response = await self.client.post("/api/v1/memory/chat", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "send_turn HTTP error %d: %s",
                e.response.status_code,
                e.response.text[:300],
            )
            raise
        except Exception as e:
            logger.error("send_turn failed: %s", e)
            raise

    # -- dialogue ingestion ----------------------------------------------

    async def ingest_dialogue(
        self,
        device_id: str,
        user_name: str,
        turns: List[Dict[str, str]],
        session_id: str,
    ) -> dict:
        """Ingest a full dialogue by sending turn pairs sequentially.

        The server auto-triggers background extraction every 5 user messages
        within the same session_id (WINDOW_SIZE=10, TRIGGER_EVERY=5).

        Args:
            device_id: Device identifier (converted to owner_id internally).
            user_name: Display name of the user.
            turns: List of dicts with ``{role: 'user'|'assistant', content: str}``.
            session_id: Session identifier for the conversation.

        Returns:
            dict with keys:
                total_pairs  — number of (user, assistant) pairs found
                success_count — number of pairs successfully sent
                last_response — server response from the last successful call
        """
        owner_id = device_to_owner_uuid(device_id)

        # Pair up (user, assistant) turns
        pairs: List[tuple] = []
        i = 0
        while i < len(turns):
            turn = turns[i]
            role = turn.get("role", "user")
            content = turn.get("content", "")

            if role == "user":
                assistant_msg = ""
                if i + 1 < len(turns):
                    next_turn = turns[i + 1]
                    if next_turn.get("role") == "assistant":
                        assistant_msg = next_turn.get("content", "")
                        i += 1  # skip the assistant turn
                pairs.append((content, assistant_msg))
            # skip standalone assistant turns (rare edge case)
            i += 1

        total_pairs = len(pairs)
        if total_pairs == 0:
            logger.warning("No user turns found in dialogue — nothing to ingest")
            return {
                "total_pairs": 0,
                "success_count": 0,
                "last_response": None,
            }

        logger.info(
            "Ingesting %d turn pairs for device=%s session=%s",
            total_pairs, device_id, session_id,
        )

        success_count = 0
        last_response = None

        for idx, (user_msg, assistant_msg) in enumerate(pairs):
            try:
                resp = await self.send_turn(
                    owner_id=owner_id,
                    user_name=user_name,
                    user_message=user_msg,
                    assistant_message=assistant_msg,
                    session_id=session_id,
                )
                success_count += 1
                last_response = resp
                logger.debug(
                    "Turn %d/%d sent successfully (person_id=%s)",
                    idx + 1, total_pairs, resp.get("person_id", "?"),
                )
            except Exception as e:
                logger.warning("Turn %d/%d failed: %s", idx + 1, total_pairs, e)

        logger.info(
            "Ingestion complete: %d/%d pairs succeeded", success_count, total_pairs,
        )
        return {
            "total_pairs": total_pairs,
            "success_count": success_count,
            "last_response": last_response,
        }

    # -- recall ----------------------------------------------------------

    async def recall(
        self,
        device_id: str,
        user_name: str,
        query: str,
        session_id: Optional[str] = None,
    ) -> dict:
        """Recall memories relevant to *query* via /api/v1/memory/chat.

        Uses a fresh recall session_id to avoid interference with the
        ingestion session buffer.

        Args:
            device_id: Device identifier.
            user_name: Display name of the user.
            query: The recall query text.
            session_id: Optional explicit session_id; if omitted a fresh
                        ``recall_<random>`` id is generated.

        Returns:
            dict with keys:
                profile_summary — Layer-1 user profile text
                events          — list of recalled event dicts
                recalled_count  — len(events)
                source          — recall source label
                confidence      — confidence level (high/uncertain/low/empty)
                intent          — classified intent
        """
        owner_id = device_to_owner_uuid(device_id)
        recall_session = session_id or f"recall_{uuid.uuid4().hex[:12]}"

        try:
            resp = await self.send_turn(
                owner_id=owner_id,
                user_name=user_name,
                user_message=query,
                assistant_message="",
                session_id=recall_session,
            )

            recall_data = resp.get("recall") or {}
            events = recall_data.get("events") or []
            profile_summary = recall_data.get("profile_summary") or ""
            source = recall_data.get("source") or ""
            confidence = recall_data.get("confidence") or "empty"
            intent = recall_data.get("intent") or "general"

            return {
                "profile_summary": profile_summary,
                "events": events,
                "recalled_count": len(events),
                "source": source,
                "confidence": confidence,
                "intent": intent,
            }

        except Exception as e:
            logger.warning("recall failed: %s", e)
            return {
                "profile_summary": "",
                "events": [],
                "recalled_count": 0,
                "source": "",
                "confidence": "empty",
                "intent": "general",
            }

    # -- context formatting ----------------------------------------------

    @staticmethod
    def build_context_string(recall_data: Dict[str, Any]) -> str:
        """Format recall data into a readable English context string.

        Example output::

            Profile: A 30-year-old software engineer who enjoys hiking.

            Related memories:
            - [2024-03-15] Discussed weekend hiking trip to Mount Tam.
            - [2024-03-10] Mentioned adopting a new puppy named Max.

        Args:
            recall_data: Dict returned by :meth:`recall`.

        Returns:
            Human-readable context string (empty string if nothing to show).
        """
        parts: List[str] = []

        profile = (recall_data.get("profile_summary") or "").strip()
        if profile:
            parts.append(f"Profile: {profile}")

        events = recall_data.get("events") or []
        if events:
            event_lines: List[str] = []
            for ev in events[:10]:  # cap to avoid overly long context
                summary = (ev.get("summary") or "").strip()
                if not summary:
                    continue
                date_str = ""
                t = ev.get("event_time") or ""
                if t and len(str(t)) >= 10:
                    date_str = f"[{str(t)[:10]}] "
                event_lines.append(f"- {date_str}{summary}")
            if event_lines:
                parts.append("Related memories:\n" + "\n".join(event_lines))

        return "\n\n".join(parts) if parts else ""


# -----------------------------------------------------------------------
# Quick smoke-test when run directly
# -----------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )

    async def _main():
        async with MemoryClient() as client:
            healthy = await client.health_check()
            if healthy:
                print("Memory system is healthy and reachable.")
            else:
                print("Memory system is NOT reachable at", client.base_url)

    asyncio.run(_main())
