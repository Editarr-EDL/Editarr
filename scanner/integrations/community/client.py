"""Durable enrollment and current-profile workflows using only backend signing keys."""

import base64
import copy
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from scanner.persistence.repository.repository_community import (
    identity_connection,
    load_local_identity,
    set_connection,
)
from scanner.security.community_client_state import update_client_state, validate_profile
from scanner.security.private_identity_files import IdentityHome, IdentityUnavailable

from .transport import CommunityFailure, CommunityTransport


def absent_status(service_url: str) -> dict:
    """`CommunityClient.status` for an account that has no identity yet."""
    return {
        "configured": True,
        "serviceUrl": service_url,
        "identity": None,
        "connected": False,
        "profile": None,
        "pending": None,
        "pendingRename": None,
        "pendingRecovery": False,
        "pendingRotation": None,
        "rotationCompleted": None,
    }


class CommunityClient:
    """Community workflows of the one identity stored in `home`.

    Callers resolve whose home it is (`repository_community.user_identity_home`);
    every private file, pending proof, and connection row this client touches
    belongs to that identity.
    """

    def __init__(
        self, db: Session, home: IdentityHome, transport: CommunityTransport, *, clock=time.time
    ):
        self.db = db
        self.home = home
        self.transport = copy.copy(transport)
        self.transport.before_send = self._before_send
        self.clock = clock

    @property
    def data_dir(self) -> Path:
        return Path(self.home.data_dir)

    def connection(self, *, populate_existing: bool = False):
        """This identity's connection row for the configured service, if any."""
        return identity_connection(
            self.db,
            self.home.credential_ref,
            self.transport.endpoint.url,
            populate_existing=populate_existing,
        )

    def disconnected(self) -> bool:
        """A detached connection keeps its key binding but no active contributor."""
        row = self.connection(populate_existing=True)
        return bool(row is not None and row.contributor_id is None and row.key_id is not None)

    def _before_send(self):
        """Recheck detachment outside the workflow transaction before each attempt."""
        with Session(self.db.get_bind()) as db:
            row = identity_connection(db, self.home.credential_ref, self.transport.endpoint.url)
            if row is not None and row.contributor_id is None and row.key_id is not None:
                raise CommunityFailure("not_connected")

    def _identity(self, *, allow_recovery=False, allow_disconnected=False):
        try:
            identity = load_local_identity(self.db, self.home)
        except IdentityUnavailable as error:
            if not allow_recovery or error.status != "incomplete":
                raise
            identity = load_local_identity(self.db, self.home, recovery=True)
            if not identity.recovery or identity.recovery["state"] != "pending":
                raise error
        if identity.recovery and identity.recovery["state"] == "pending" and not allow_recovery:
            raise CommunityFailure("recovery_pending")
        if identity.service_url not in {None, self.transport.endpoint.url}:
            raise CommunityFailure("wrong_service")
        if self.disconnected() and not allow_disconnected:
            raise CommunityFailure("not_connected")
        if identity.client_state and identity.client_state["profile"]:
            self._project(identity)
        return identity

    def _project(self, identity):
        """Repair an interrupted database projection from the atomic private registration."""
        row = identity_connection(self.db, self.home.credential_ref, identity.service_url)
        if row is not None and row.contributor_id is None and row.key_id is not None:
            return
        if row is not None and (row.contributor_id, row.installation_id, row.key_id) == (
            identity.contributor_id,
            identity.installation_id,
            identity.key_id,
        ):
            return
        if row is not None and row.contributor_id is not None:
            if (
                (identity.rotation or identity.recovery)
                and (identity.rotation or identity.recovery)["state"] == "completed"
                and (row.contributor_id, row.installation_id, row.key_id)
                == (
                    identity.contributor_id,
                    identity.installation_id,
                    (identity.rotation or identity.recovery).get(
                        "localOldKeyId", (identity.rotation or identity.recovery)["oldKeyId"]
                    ),
                )
            ):
                row.key_id = identity.key_id
                self.db.flush()
                self.db.commit()
                return
            raise CommunityFailure("identity_conflict")
        set_connection(
            self.db,
            credential_ref=self.home.credential_ref,
            service_url=identity.service_url,
            contributor_id=identity.contributor_id,
            installation_id=identity.installation_id,
            key_id=identity.key_id,
        )
        self.db.commit()

    def _state(self, identity):
        state = copy.deepcopy(
            identity.client_state or {"version": 1, "pending": None, "profile": None}
        )
        state.setdefault("rename", None)
        return state

    def _expire(self, identity):
        state = self._state(identity)
        if state["pending"] and state["pending"]["expires_at"] <= int(self.clock()):
            state["pending"] = None
            return update_client_state(self.home, expected=identity.client_state, state=state)
        return identity

    def status(self) -> dict:
        """Expose safe status only; the journal and its confirmation secret remain private."""
        identity = self._identity(allow_recovery=True, allow_disconnected=True)
        disconnected = self.disconnected()
        if not disconnected and not (identity.recovery and identity.recovery["state"] == "pending"):
            identity = self._expire(identity)
        state = self._state(identity)
        pending = state["pending"]
        return {
            "configured": True,
            "serviceUrl": self.transport.endpoint.url,
            "identity": {
                "installationId": identity.installation_id,
                "keyId": identity.key_id,
                "lastKnownKeyState": identity.last_known_key_state,
            },
            "connected": identity.contributor_id is not None and not disconnected,
            "profile": state["profile"],
            "pending": None
            if pending is None
            else {
                "stateId": pending["state_id"],
                "expiresAt": pending["expires_at"],
                "awaitingConfirmation": True,
            },
            "pendingRename": state["rename"],
            "pendingRecovery": bool(identity.recovery and identity.recovery["state"] == "pending"),
            "pendingRotation": None
            if identity.pending_replacement is None
            else {
                "operationId": identity.pending_replacement.operation_id,
                "newKeyId": identity.pending_replacement.key_id,
                "state": "pending",
            },
            "rotationCompleted": None
            if not identity.rotation or identity.rotation["state"] != "completed"
            else {
                "operationId": identity.rotation["operationId"],
                "keyId": identity.key_id,
                "committedAt": identity.rotation["result"]["committedAt"],
            },
        }

    def start(self) -> dict:
        """Durably bind the secret before returning the cookie-setting hosted browser URL."""
        identity = self._identity(allow_recovery=True, allow_disconnected=True)
        if self.disconnected():
            row = self.connection()
            if identity.contributor_id is not None:
                row.contributor_id = identity.contributor_id
                self.db.commit()
                return self.status()
            self.db.delete(row)
            self.db.commit()
        identity = self._expire(self._identity())
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        if identity.last_known_key_state not in {"unenrolled", "active"}:
            raise CommunityFailure("recovery_required")
        state = self._state(identity)
        if state["pending"] is None:
            state["pending"] = {
                "service_url": self.transport.endpoint.url,
                "audience": self.transport.endpoint.audience,
                "secret": secrets.token_hex(32),
                "state_id": None,
                "expires_at": int(self.clock()) + 1200,
            }
            identity = update_client_state(self.home, expected=identity.client_state, state=state)
        pending = state["pending"]
        if pending["service_url"] != self.transport.endpoint.url:
            raise CommunityFailure("wrong_service")
        if pending["state_id"] is not None:
            return self.status()
        parameters = {
            "installationId": identity.installation_id,
            "keyId": identity.key_id,
            "publicKey": base64.b64encode(identity.public_key).decode("ascii"),
            "confirmationHash": hashlib.sha256(
                ("editarr-enrollment-confirmation-v1:" + pending["secret"]).encode("ascii")
            ).hexdigest(),
        }
        return {
            **self.status(),
            "authorizationUrl": self.transport.endpoint.url
            + "/v1/discord/start?"
            + urlencode(parameters),
        }

    def disconnect(self) -> dict:
        """Detach locally, retaining signing material, consent, and pending operations."""
        identity = self._identity(allow_recovery=True, allow_disconnected=True)
        row = self.connection()
        if row is None:
            set_connection(
                self.db,
                credential_ref=self.home.credential_ref,
                service_url=self.transport.endpoint.url,
                contributor_id=None,
                installation_id=identity.installation_id,
                key_id=identity.key_id,
            )
        else:
            row.contributor_id = None
        self.db.commit()
        return self.status()

    def cancel(self) -> dict:
        identity = self._identity()
        state = self._state(identity)
        state["pending"] = None
        update_client_state(self.home, expected=identity.client_state, state=state)
        return self.status()

    async def complete(self, state_id: str) -> dict:
        """Called only after local administrator approval; retain the intent on uncertainty."""
        if not re.fullmatch(r"[0-9a-f]{64}", state_id):
            raise CommunityFailure("invalid_handoff")
        identity = self._expire(self._identity())
        state = self._state(identity)
        pending = state["pending"]
        if pending is None:
            raise CommunityFailure("enrollment_expired")
        if pending["service_url"] != self.transport.endpoint.url:
            raise CommunityFailure("wrong_service")
        if pending["state_id"] not in {None, state_id}:
            raise CommunityFailure("handoff_conflict")
        if pending["state_id"] is None:
            pending["state_id"] = state_id
            pending["expires_at"] = min(pending["expires_at"], int(self.clock()) + 600)
            identity = update_client_state(self.home, expected=identity.client_state, state=state)
        body = json.dumps(
            {
                "version": 1,
                "stateId": state_id,
                "installationId": identity.installation_id,
                "confirmationSecret": pending["secret"],
            },
            separators=(",", ":"),
        ).encode()
        response = await self.transport.request(
            "POST", "/v1/enrollments/complete", identity=identity, body=body, operation_id=state_id
        )
        return self._accept(identity, response, clear_pending=True)

    def _accept(self, identity, response, *, clear_pending=False, clear_rename=False):
        profile = validate_profile(
            response, identity.installation_id, identity.key_id, identity.contributor_id
        )
        state = self._state(identity)
        previous = state["profile"]
        if previous and profile["profileRevision"] < previous["profileRevision"]:
            raise CommunityFailure("stale_profile")
        state["profile"] = profile
        if clear_pending:
            state["pending"] = None
        if clear_rename:
            state["rename"] = None
        updated = update_client_state(
            self.home,
            expected=identity.client_state,
            state=state,
            contributor_id=profile["contributorId"],
            service_url=self.transport.endpoint.url,
        )
        self._project(updated)
        return self.status()

    async def refresh(self) -> dict:
        if self.disconnected():
            return self.status()
        identity = self._expire(self._identity())
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        if identity.contributor_id is None:
            return self.status()
        response = await self.transport.request("GET", "/v1/identity", identity=identity)
        return self._accept(identity, response)

    async def rename(self, display_name: str, operation_id: str) -> dict:
        identity = self._identity()
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        if identity.contributor_id is None:
            raise CommunityFailure("not_connected")
        if (
            not isinstance(display_name, str)
            or not 3 <= len(display_name) <= 40
            or not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", operation_id)
        ):
            raise CommunityFailure("invalid_profile")
        state = self._state(identity)
        intent = {"operation_id": operation_id, "display_name": display_name}
        if state["rename"] not in (None, intent):
            raise CommunityFailure("operation_conflict")
        if state["rename"] is None:
            state["rename"] = intent
            identity = update_client_state(self.home, expected=identity.client_state, state=state)
        response = await self.transport.request(
            "PATCH",
            "/v1/identity",
            identity=identity,
            operation_id=operation_id,
            body=json.dumps(
                {"displayName": display_name}, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
        )
        return self._accept(identity, response, clear_rename=True)

    def cancel_rename(self) -> dict:
        """Discard a local retry intent, not a rename already committed by the service."""
        identity = self._identity()
        state = self._state(identity)
        state["rename"] = None
        update_client_state(self.home, expected=identity.client_state, state=state)
        return self.status()
