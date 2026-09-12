"""MongoDB Atlas persistence — optional by design.

Rooms, transcripts and workplans are written here. Every call degrades to a no-op if
Mongo is unreachable: a database outage during the demo must cost us persistence, never
the demo itself. The in-memory room store remains the source of truth for a live room.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class Store:
    """Thin async wrapper over pymongo's AsyncMongoClient. Never raises to callers."""

    def __init__(self, uri: str, db_name: str) -> None:
        self.uri = uri
        self.db_name = db_name
        self._client: Any = None
        self._db: Any = None

    @property
    def enabled(self) -> bool:
        return self._db is not None

    async def connect(self) -> bool:
        if not self.uri:
            log.info("MONGODB_URI unset — running without persistence")
            return False
        try:
            from pymongo import AsyncMongoClient

            self._client = AsyncMongoClient(self.uri, serverSelectionTimeoutMS=4000)
            await self._client.admin.command("ping")
            self._db = self._client[self.db_name]
            log.info("connected to MongoDB (%s)", self.db_name)
            return True
        except Exception as exc:  # noqa: BLE001 — any failure means "no persistence"
            log.warning("MongoDB unavailable, continuing without persistence: %s", exc)
            self._client = None
            self._db = None
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass

    async def upsert_room(self, room_id: str, doc: dict) -> None:
        if not self.enabled:
            return
        try:
            await self._db.rooms.update_one({"_id": room_id}, {"$set": doc}, upsert=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("upsert_room failed: %s", exc)

    async def append_event(self, room_id: str, event: dict) -> None:
        if not self.enabled:
            return
        try:
            await self._db.events.insert_one({"room_id": room_id, **event})
        except Exception as exc:  # noqa: BLE001
            log.warning("append_event failed: %s", exc)

    async def save_plan(self, room_id: str, plan: dict) -> None:
        if not self.enabled:
            return
        try:
            await self._db.workplans.update_one(
                {"_id": f"{room_id}:{plan.get('plan_id')}"},
                {"$set": {"room_id": room_id, **plan}},
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("save_plan failed: %s", exc)

    async def load_events(self, room_id: str) -> list[dict]:
        if not self.enabled:
            return []
        try:
            cursor = self._db.events.find({"room_id": room_id}, {"_id": 0}).sort("ts", 1)
            return [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            log.warning("load_events failed: %s", exc)
            return []
