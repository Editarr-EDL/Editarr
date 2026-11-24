"""Signed calls to the master-set service endpoints with strict response checks (§7).

Each method raises `CommunityFailure`: remote contract codes keep their code and
defined details, malformed responses are `invalid_response`, and failed master
verification is `snapshot_invalid`. Network failures never become empty results.
"""

import asyncio
import json
import re
import secrets
import threading
import time

from pydantic import ValidationError

from scanner.modeling.community_normalization import (
    SEEDED_ALIASES,
    Alias,
    AliasListError,
    parse_alias_list,
)
from scanner.security.community_download_receipts import (
    ReceiptError,
    ReceiptKey,
    parse_receipt_keys,
)
from scanner.security.community_master_snapshots import (
    MasterUnchanged,
    MasterVerificationError,
    VerifiedMaster,
    verify_master_response,
)

from .master_service_models import (
    AliasesResponse,
    MatchResponse,
    MatchResult,
    OwnVotes,
    ReportResult,
    SubmissionResult,
    SyncResponse,
    VoteResult,
    completed,
)
from .transport import CommunityFailure

MATCH_RESPONSE_BYTES = 262_144
MASTER_RESPONSE_BYTES = 2_800_000
SUBMISSION_RESPONSE_BYTES = 16_384
VOTES_RESPONSE_BYTES = 400_000
SYNC_BATCH = 500
ALIAS_TTL_SECONDS = 3600
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_aliases: dict[str, tuple[float, tuple[Alias, ...]]] = {}
_aliases_lock = threading.Lock()
_OPERATION = re.compile(r"[A-Za-z0-9._~-]{1,128}")


def encode(document: dict) -> bytes:
    return json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def require_token(value: object, code: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise CommunityFailure(code)
    return value


def _validated(model, value):
    try:
        return model.model_validate(value, strict=True)
    except (ValidationError, ValueError, TypeError) as error:
        raise CommunityFailure("invalid_response") from error


def _completed(model, value):
    try:
        return completed(model, value)
    except (ValidationError, ValueError, TypeError) as error:
        raise CommunityFailure("invalid_response") from error


class MasterService:
    """The signed master-set endpoints for the identity a `CommunityClient` holds."""

    def __init__(self, client):
        self.client = client

    @property
    def service_url(self) -> str:
        return self.client.transport.endpoint.url

    def identity(self):
        """The connected identity; a pending key rotation blocks every signed request."""
        identity = self.client._identity()
        if not identity.contributor_id:
            raise CommunityFailure("not_connected")
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        return identity

    async def _request(self, method, path, *, body=b"", operation_id=None, **options):
        return await self.client.transport.request(
            method,
            path,
            identity=self.identity(),
            body=body,
            operation_id=operation_id,
            **options,
        )

    async def match(self, query: dict) -> tuple[MatchResult, ...]:
        """`POST /v1/match` (read-only): the server results in contract order."""
        value = await self._request(
            "POST",
            "/v1/match",
            body=encode(query),
            operation_id="match-" + secrets.token_urlsafe(16),
            response_bytes=MATCH_RESPONSE_BYTES,
        )
        response = _validated(MatchResponse, value)
        if len({item.version_id for item in response.results}) != len(response.results) or len(
            response.results
        ) > query.get("limit", 20):
            raise CommunityFailure("invalid_response")
        return tuple(response.results)

    async def aliases(self) -> tuple[Alias, ...]:
        """`GET /v1/aliases`: the approved aliases, Theatrical first."""
        value = await self._request("GET", "/v1/aliases")
        _validated(AliasesResponse, value)
        try:
            return parse_alias_list(value)
        except AliasListError as error:
            raise CommunityFailure("invalid_response") from error

    async def cached_aliases(self, *, clock=time.monotonic) -> tuple[Alias, ...]:
        """The approved aliases, refreshed at most hourly per service.

        When the service cannot be reached, the last list is reused, and before any
        list was fetched the seeded aliases (§3.4) stand in.
        """
        with _aliases_lock:
            cached = _aliases.get(self.service_url)
        if cached is not None and clock() - cached[0] < ALIAS_TTL_SECONDS:
            return cached[1]
        try:
            aliases = await self.aliases()
        except CommunityFailure:
            return cached[1] if cached is not None else SEEDED_ALIASES
        with _aliases_lock:
            _aliases[self.service_url] = (clock(), aliases)
        return aliases

    def approved_aliases(self) -> tuple[Alias, ...]:
        """`cached_aliases` for synchronous callers outside an event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.cached_aliases())
        with _aliases_lock:
            cached = _aliases.get(self.service_url)
        return cached[1] if cached is not None else SEEDED_ALIASES

    async def receipt_keys(self) -> tuple[ReceiptKey, ...]:
        """Fetch and pin the service's receipt-key history before verifying a master."""
        history = await self._request("GET", "/v1/receipt-keys", verify_response_digest=True)
        try:
            if (
                set(history) != {"version", "service", "keys"}
                or type(history["version"]) is not int
            ):
                raise ValueError
            if history["version"] != 1 or history["service"] != self.service_url:
                raise ValueError
            return parse_receipt_keys(history["keys"])
        except (ValueError, ReceiptError) as error:
            raise CommunityFailure("invalid_receipt_keys") from error

    async def master(
        self, version_id: str, *, known_revision: int | None = None
    ) -> VerifiedMaster | MasterUnchanged:
        """`GET /v1/versions/{id}/master`, verified against freshly pinned receipt keys.

        A merged version raises `version_merged` with `details["mergedInto"]`.
        """
        require_token(version_id, "invalid_master_request")
        if known_revision is not None and (type(known_revision) is not int or known_revision < 1):
            raise CommunityFailure("invalid_master_request")
        keys = await self.receipt_keys()
        identity = self.identity()
        path = f"/v1/versions/{version_id}/master"
        if known_revision is not None:
            path += f"?knownRevision={known_revision}"
        value = await self.client.transport.request(
            "GET",
            path,
            identity=identity,
            response_bytes=MASTER_RESPONSE_BYTES,
        )
        try:
            return verify_master_response(
                value,
                service=self.service_url,
                contributor_id=identity.contributor_id,
                version_id=version_id,
                known_revision=known_revision,
                trusted_keys=keys,
            )
        except MasterVerificationError as error:
            raise CommunityFailure("snapshot_invalid") from error

    async def submit(self, body: bytes, submission_id: str) -> SubmissionResult:
        """`POST /v1/submissions` with exact approved bytes and `submit-<submissionId>`."""
        operation_id = "submit-" + require_token(submission_id, "invalid_submission")
        value = await self._request(
            "POST",
            "/v1/submissions",
            body=body,
            operation_id=operation_id,
            response_bytes=SUBMISSION_RESPONSE_BYTES,
        )
        result = _completed(SubmissionResult, value)
        if (result.operation_id, result.submission_id) != (operation_id, submission_id):
            raise CommunityFailure("invalid_response")
        return result

    async def vote(self, filter_id: str, body: bytes, operation_id: str) -> VoteResult:
        """`POST /v1/filters/{id}/votes` with the saved vote bytes."""
        require_token(filter_id, "invalid_vote")
        if not _OPERATION.fullmatch(operation_id):
            raise CommunityFailure("invalid_vote")
        value = await self._request(
            "POST", f"/v1/filters/{filter_id}/votes", body=body, operation_id=operation_id
        )
        result = _completed(VoteResult, value)
        if (result.operation_id, result.filter_id, result.vote) != (
            operation_id,
            filter_id,
            json.loads(body)["vote"],
        ):
            raise CommunityFailure("invalid_response")
        return result

    async def my_votes(self, version_id: str) -> OwnVotes:
        """`GET /v1/versions/{id}/votes`: the caller's own current votes on a version."""
        require_token(version_id, "invalid_master_request")
        value = await self._request(
            "GET", f"/v1/versions/{version_id}/votes", response_bytes=VOTES_RESPONSE_BYTES
        )
        votes = _validated(OwnVotes, value)
        if votes.version_id != version_id:
            raise CommunityFailure("invalid_response")
        return votes

    async def sync_revisions(self, run_id: str, items: list[tuple[str, int]]) -> SyncResponse:
        """`POST /v1/sync/revisions` (read-only) for one batch of at most 500 versions."""
        require_token(run_id, "invalid_sync")
        if not 1 <= len(items) <= SYNC_BATCH or len({item[0] for item in items}) != len(items):
            raise CommunityFailure("invalid_sync")
        body = {
            "version": 1,
            "runId": run_id,
            "items": [{"versionId": version, "revision": revision} for version, revision in items],
        }
        value = await self._request(
            "POST",
            "/v1/sync/revisions",
            body=encode(body),
            operation_id="sync-" + secrets.token_urlsafe(16),
            response_bytes=MATCH_RESPONSE_BYTES,
        )
        response = _validated(SyncResponse, value)
        known = {version for version, _revision in items}
        for change in response.changed:
            if (
                change.version_id not in known
                or (change.status == "changed") != (change.revision is not None)
                or (change.status == "merged") != (change.merged_into is not None)
            ):
                raise CommunityFailure("invalid_response")
        return response

    async def report(self, document: dict, operation_id: str) -> ReportResult:
        """`POST /v1/reports` for a filter or version the caller holds a receipt for."""
        if not _OPERATION.fullmatch(operation_id):
            raise CommunityFailure("invalid_report")
        value = await self._request(
            "POST", "/v1/reports", body=encode(document), operation_id=operation_id
        )
        result = _completed(ReportResult, value)
        if result.operation_id != operation_id:
            raise CommunityFailure("invalid_response")
        return result
