"""Inbound inbox API: submit video links for the bridge to archive.

``POST /api/integrations/v1/inbox/items`` is the HTTP drop-in replacement for the
bridge's local ``inbox.json``. Submitted links are normalized, deduplicated and
enqueued into the shared ``dispatch`` queue (``app.services.dispatch_store``) so
the offline bridge processes them through the exact same fast/slow double track
as Feishu rows (TC-P4-01 / TC-P4-12).

Security / compliance:
  * Bearer integration token (same auth as the v1 external batch API).
  * ``rights_attested`` MUST be True (compliance red line, TC-P4-06).
  * ``Idempotency-Key`` required; identical replays return the first result.
  * Single batch capped at a runtime-configurable limit (default 100 items),
    rejected atomically (TC-P4-03).
  * Illegal links are flagged and rejected per-item without failing the whole
    batch; valid links are still ingested (TC-P4-07).
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from sqlalchemy.ext.asyncio import AsyncSession

from app.routers_v2.integrations import integration_session
from app.schemas import (
    ImportErrorDetail,
    InboxIngestCreate,
    InboxIngestResult,
    InboxItemResult,
)
from app.services.dispatch_store import (
    DispatchStore,
    normalize_inbox_link,
)
from app.services.runtime_settings import get_runtime_settings


router = APIRouter(
    prefix="/api/integrations/v1/inbox",
    tags=["inbound-inbox-v1"],
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_digest(rights_attested: bool, items: list) -> str:
    canonical = {
        "rights_attested": bool(rights_attested),
        "items": [
            {"link": i.link, "tags": i.tags or "", "source": i.source}
            for i in items
        ],
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(blob)


def _reject(status_code: int, code: str, message: str, field: str | None = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "retryable": False,
            "field": field,
        },
    )


@router.post(
    "/items",
    response_model=InboxIngestResult,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_inbox_items(
    payload: InboxIngestCreate,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    session: AsyncSession = Depends(integration_session),
):
    # Idempotency-Key is mandatory (mirrors the v1 external batch API).
    if not idempotency_key:
        _reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "idempotency_key_required",
            "入站提交必须提供 Idempotency-Key",
            field="Idempotency-Key",
        )

    # Compliance red line: rights must be attested (TC-P4-06).
    if not payload.rights_attested:
        _reject(
            status.HTTP_400_BAD_REQUEST,
            "rights_attestation_required",
            "必须确认有权处理这些视频/链接",
            field="rights_attested",
        )

    # Atomic batch cap (TC-P4-03): reject entirely, never partially insert.
    # Limit is runtime-configurable (default 100) so it can be tuned from the
    # settings UI without a code change.
    max_batch = (await get_runtime_settings(session))["inbox_batch_limit"]
    if len(payload.items) > max_batch:
        _reject(
            status.HTTP_400_BAD_REQUEST,
            "invalid_item_count",
            f"单次入站最多 {max_batch} 条，超出部分请拆分批次",
            field="items",
        )

    key_hash = _sha256(idempotency_key)
    digest = _request_digest(payload.rights_attested, payload.items)
    store = DispatchStore()

    # Idempotency replay / conflict detection (TC-P4-05).
    existing = store.get_idempotency(key_hash)
    if existing:
        stored_digest, stored_response = existing
        if stored_digest == digest:
            replayed = json.loads(stored_response)
            replayed["replayed"] = True
            if int(replayed.get("rejected") or 0):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=replayed
                )
            return replayed
        _reject(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "相同 Idempotency-Key 已用于不同清单",
            field="Idempotency-Key",
        )

    results: list[InboxItemResult] = []
    for item in payload.items:
        ok, normalized, error = normalize_inbox_link(item.link, item.source)
        if not ok:
            results.append(
                InboxItemResult(
                    record_id="",
                    link=item.link,
                    normalized_link="",
                    source=item.source,
                    status="rejected",
                    error=ImportErrorDetail(
                        code="invalid_link",
                        message=error or "链接不合法",
                        retryable=False,
                        field="items.link",
                    ),
                )
            )
            continue

        # Dedupe by normalized key: never enqueue the same link twice
        # (TC-P4-04 / TC-P4-11). The record_id is derived from the key so the
        # SQLite primary key also guards against concurrent double inserts.
        record_id = "inbox-" + _sha256(normalized)
        created = store.enqueue(
            record_id,
            item.link,
            item.tags or "",
            "",
            "",
            source=item.source,
            normalized_key=normalized,
        )
        # Already present (earlier submission or concurrent insert): keep a
        # single queue row, report as accepted/duplicate.
        status_value = "pending" if created else "duplicate"
        results.append(
            InboxItemResult(
                record_id=record_id,
                link=item.link,
                normalized_link=normalized,
                source=item.source,
                status=status_value,
                error=None,
            )
        )

    accepted = sum(1 for r in results if r.status == "pending")
    duplicates = sum(1 for r in results if r.status == "duplicate")
    rejected = sum(1 for r in results if r.status == "rejected")
    result = InboxIngestResult(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        items=results,
        replayed=False,
    )

    # Persist idempotency BEFORE returning so an identical replay is stable,
    # then surface partial rejection with a 400 (TC-P4-07) while valid links
    # are already queued.
    store.put_idempotency(key_hash, digest, result.model_dump_json())
    if rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=result.model_dump()
        )
    return result
