"""The labelled community service double used by the community journey.

`ProbeService` extends the signature-verifying `ActivityService` test double. Every
request is first judged by `community-contract.mjs`, which runs the community
service's own route methods and body validators, so the double refuses what the
real service refuses. It answers lookups with `missing_identity`, and applies
signed renames under the real uniqueness and operation-replay rules. Nothing here
contacts a real service.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx

from scanner.modeling.community_matching import POLICY_VERSION
from tests.test_community_activity_http import ActivityService

CONTRACT = Path(__file__).with_name("community-contract.mjs")
WORDS = (
    "Amber Birch Cedar Coral Dawn Elm Fern Frost Grove Harbor Iris Jade Lake Maple Moss Opal "
    "Pine Quartz Rain Reed River Silver Snow Spring Stone Summer Sun Tide Vale Willow Wind Winter"
).split()
INITIAL_NAMES = [" ".join(WORDS[index : index + 3]) for index in range(0, len(WORDS) - 2, 3)]


def _node() -> str | None:
    return os.environ.get("EDITARR_VERIFY_NODE") or shutil.which("node")


def contract_unavailable() -> str | None:
    """Why the service validators cannot be bundled here (no Node, esbuild, or zod), or None."""
    node = _node()
    if node is None:
        return "node is required to run the community service validators"
    result = subprocess.run(
        [node, str(CONTRACT), "--check"], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode == 0:
        return None
    return (
        result.stderr.strip().splitlines()
        or [f"{CONTRACT.name} --check exited {result.returncode}"]
    )[0]


class ContractJudge:
    """A persistent `community-contract.mjs` process that judges one request per line."""

    def __init__(self):
        node = _node()
        if node is None:
            raise RuntimeError("The community double needs Node to run the service's validators")
        self.process = subprocess.Popen(
            [node, str(CONTRACT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        ready = self.process.stdout.readline()
        if not ready:
            raise RuntimeError(
                f"{CONTRACT.name} exited {self.process.wait()} before judging; see its stderr"
            )
        assert json.loads(ready) == {"ready": True}

    def __call__(self, request: httpx.Request) -> dict:
        line = {
            "method": request.method,
            "path": request.url.path,
            "contentType": request.headers.get("content-type"),
            "body": request.content.decode(),
        }
        self.process.stdin.write(json.dumps(line) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


def _failure(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code}})


class ProbeService(ActivityService):
    """The labelled double with contract-checked requests, lookups, and renames.

    Contributors start with distinct generated names. A rename keeps the real
    service's rules: a name another contributor holds, in any case, is taken; the
    same operation ID replays only its own body.
    """

    def __init__(self):
        super().__init__()
        self.judge = ContractJudge()
        self.names = {}
        self.renames = {}

    def close(self):
        self.judge.close()

    def _name(self, contributor):
        if contributor not in self.names:
            held = {key for _, key, _ in self.names.values()}
            name = next(name for name in INITIAL_NAMES if name.lower() not in held)
            self.names[contributor] = (name, name.lower(), 1)
        return self.names[contributor]

    def profile(self, installation):
        profile = super().profile(installation)
        profile["displayName"], _, profile["profileRevision"] = self._name(profile["contributorId"])
        return profile

    async def __call__(self, request):
        verdict = self.judge(request)
        if verdict["status"] != 200:
            return _failure(verdict["status"], verdict["code"])
        if request.url.path == "/v1/availability/batch":
            self._signer(request)
            items = [
                {
                    "id": item["id"],
                    "status": "missing_identity",
                    "candidateCount": None,
                    "truncated": False,
                    "channels": None,
                }
                for item in json.loads(request.content)["items"]
            ]
            return httpx.Response(
                200, json={"version": 1, "policyVersion": POLICY_VERSION, "items": items}
            )
        if request.url.path == "/v1/identity" and request.method == "PATCH":
            return self._rename(request, verdict["facts"])
        return await super().__call__(request)

    def _rename(self, request, facts):
        installation = self._signer(request)
        self.requests.append((request.url.path, installation, request.content.decode()))
        contributor = self.profile(installation)["contributorId"]
        operation = request.headers.get("idempotency-key")
        if not operation:
            return _failure(400, "invalid_rename")
        digest = hashlib.sha256(request.content).hexdigest()
        previous = self.renames.get((contributor, operation))
        if previous is not None and previous != digest:
            return _failure(409, "operation_conflict")
        if previous is None:
            if any(
                key == facts["nameKey"]
                for owner, (_, key, _) in self.names.items()
                if owner != contributor
            ):
                return _failure(409, "display_name_taken")
            name, _, revision = self._name(contributor)
            if name != facts["displayName"]:
                self.names[contributor] = (facts["displayName"], facts["nameKey"], revision + 1)
            self.renames[(contributor, operation)] = digest
        return httpx.Response(200, json=self.profile(installation))
