"""Check one audit event against the action registry before it is stored or read back."""

from __future__ import annotations

import re

from scanner.security.access.records import AuditEvent, AuditOutcome

from .action_vocabulary import (
    COUNT,
    FLAG,
    ActorKind,
    AuditAction,
    AuditEventRejected,
    DetailRule,
    Scope,
)
from .actions import ACTION_SPECS

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def check_event(event: AuditEvent, actor_kind: ActorKind) -> AuditAction:
    """Return the event's registered action, or raise `AuditEventRejected` naming the rule.

    The message names the rule and the key, never a value.
    """

    try:
        action = AuditAction(event.action)
    except ValueError:
        raise AuditEventRejected("Audit action is not registered") from None
    spec = ACTION_SPECS[action]
    if actor_kind not in spec.actors.get(AuditOutcome(event.outcome), frozenset()):
        raise AuditEventRejected(f"{action} does not allow this actor and outcome")
    if (actor_kind is ActorKind.USER) != (event.actor is not None):
        raise AuditEventRejected("Only a user actor names an account")
    if event.credential is not None and actor_kind is not ActorKind.USER:
        raise AuditEventRejected("Only a user actor has a credential")
    for name, value, scope in (
        ("target_user", event.target_user, spec.target_user),
        ("media_key", event.media_key, spec.media),
        ("resource", event.resource, spec.resource),
    ):
        if (scope is Scope.REQUIRED and value is None) or (
            scope is Scope.ABSENT and value is not None
        ):
            raise AuditEventRejected(f"{action} scope {name} is {scope.value}")
    for name in ("media_key", "resource"):
        value = getattr(event, name)
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise AuditEventRejected(f"Audit {name} is not an identifier")
    for key, value in event.details.items():
        rule = spec.details.get(key)
        if rule is None or not _detail_allowed(rule, value):
            raise AuditEventRejected(f"{action} detail {key!r} is not allowed")
    if not spec.required_details <= event.details.keys():
        raise AuditEventRejected(f"{action} is missing a required detail")
    if spec.consistent is not None and not spec.consistent(event):
        raise AuditEventRejected(f"{action} scope does not match its details")
    return action


def _detail_allowed(rule: DetailRule, value: object) -> bool:
    if rule is FLAG:
        return type(value) is bool
    if rule is COUNT:
        return type(value) is int and value >= 0
    return type(value) is str and value in rule
