"""Explicit signed catalog queries; failed requests never masquerade as empty results."""

import asyncio
import json
import secrets
from dataclasses import asdict

from starlette.concurrency import run_in_threadpool

from scanner.modeling.community_matching import BANDS, POLICY_VERSION, classify
from scanner.modeling.community_ranking import rank_revision
from scanner.modeling.model_community_lookup import LookupAvailability, LookupPage, LookupRequest

from .client import CommunityClient
from .lookup_targets import capture_target
from .transport import CommunityFailure

RESPONSE_BYTES = 3_000_000


def _validated(model, value):
    try:
        if type(value.get("version")) is not int or any(
            item.get("compatibility", {}).get("selected") is not False
            for item in value.get("candidates", [])
        ):
            raise ValueError
        result = model.model_validate_json(json.dumps(value), strict=True, extra="forbid")
        if result.policyVersion != POLICY_VERSION:
            raise CommunityFailure("unsupported_policy")
        return result
    except (ValueError, TypeError, AttributeError) as error:
        raise CommunityFailure("invalid_response") from error


class CommunityLookup:
    def __init__(self, client: CommunityClient):
        self.client = client

    async def run(self, request: LookupRequest, *, should_cancel=None) -> dict:
        """Resolve exactly one target and retain independent availability/page outcomes."""
        identity = self.client._identity()
        if identity.contributor_id is None:
            raise CommunityFailure("not_connected")
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        capture_arguments = {"should_cancel": should_cancel}
        if getattr(self.client, "authorize_target", None) is not None:
            capture_arguments["authorize"] = self.client.authorize_target
        work = asyncio.create_task(
            run_in_threadpool(capture_target, self.client.db, request, **capture_arguments)
        )
        try:
            captured = await asyncio.shield(work)
        except asyncio.CancelledError:
            try:
                await work
            except Exception:
                pass
            raise
        except (OSError, ValueError) as error:
            raise CommunityFailure("target_unavailable") from error
        captured.check(should_cancel)
        evidence = asdict(captured.evidence)
        result = {
            "version": 1,
            "policyVersion": POLICY_VERSION,
            "targetKey": captured.key,
            "targetEvidence": evidence,
            "warnings": list(captured.warnings),
            "availability": None,
            "availabilityError": None,
            "page": None,
            "pageError": None,
        }

        async def query(path, payload, model):
            captured.check(should_cancel)
            value = await self.client.transport.request(
                "POST",
                path,
                identity=identity,
                body=json.dumps(payload, separators=(",", ":"), allow_nan=False).encode(),
                operation_id=secrets.token_urlsafe(16),
                response_bytes=RESPONSE_BYTES,
            )
            captured.check(should_cancel)
            return _validated(model, value)

        try:
            availability = await query(
                "/v1/availability/batch",
                {
                    "version": 1,
                    "items": [{"id": "target", "evidence": evidence}],
                    "refresh": request.refresh,
                },
                LookupAvailability,
            )
            item = availability.items[0]
            if (
                item.status == "missing_identity"
                and (item.candidateCount is not None or item.channels is not None or item.truncated)
                or item.status == "ok"
                and (item.candidateCount is None or item.channels is None)
            ):
                raise CommunityFailure("invalid_response")
            if item.channels is not None and (
                set(item.channels) != {"audio", "video"}
                or any(
                    set(counts) != set(BANDS)
                    or any(
                        type(count) is not int or not 0 <= count <= 96 for count in counts.values()
                    )
                    for counts in item.channels.values()
                )
            ):
                raise CommunityFailure("invalid_response")
            result["availability"] = availability.model_dump(mode="json")
            if item.status == "missing_identity":
                result["pageError"] = {"code": "missing_identity", "retryable": False}
                return result
        except CommunityFailure as error:
            if error.code in {"file_changed", "lookup_cancelled", "unsupported_policy"}:
                raise
            result["availabilityError"] = {"code": error.code, "retryable": error.retryable}
            return result
        try:
            page = await query(
                "/v1/matches",
                {
                    "version": 1,
                    "evidence": evidence,
                    "pageSize": request.page_size,
                    "cursor": request.cursor,
                    "refresh": request.refresh,
                },
                LookupPage,
            )
            if len(page.candidates) > request.page_size or len(
                {item.revisionId for item in page.candidates}
            ) != len(page.candidates):
                raise CommunityFailure("invalid_response")
            for candidate in page.candidates:
                expected = json.loads(
                    json.dumps(asdict(classify(captured.evidence, candidate.evidence)))
                )
                if candidate.compatibility.model_dump() != expected:
                    raise CommunityFailure("invalid_response")
                if candidate.summary.submission_id != candidate.revisionId:
                    raise CommunityFailure("invalid_response")
                if candidate.ranking is not None:
                    try:
                        expected_ranking = rank_revision(
                            candidate.statistics.likes, candidate.statistics.dislikes
                        )
                    except ValueError as error:
                        raise CommunityFailure("invalid_response") from error
                    if candidate.ranking.model_dump() != expected_ranking:
                        raise CommunityFailure("invalid_response")
            result["page"] = page.model_dump(mode="json", exclude_unset=True)
        except CommunityFailure as error:
            if error.code in {"file_changed", "lookup_cancelled", "unsupported_policy"}:
                raise
            result["pageError"] = {"code": error.code, "retryable": error.retryable}
        captured.check(should_cancel)
        return result
