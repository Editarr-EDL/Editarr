"""Serve the MU-08 synthetic Jellyfin boundary for the account-link journey.

`python account_link_provider.py [STATE]` serves the fixture state STATE (default
`baseline`; `replaced` is the replacement server with a new server ID) and prints one
JSON line with `url`, `apiKey`, `serverId`, and `users`: each served user's `userId`
and a random `password`. It serves until stdin closes or the process is signalled. It
adds the authenticated `/Library/VirtualFolders` read that Editarr's settings
connection test needs and answers it with no libraries. It is a labelled substitute
for a live Jellyfin server and listens on loopback only.
"""

from __future__ import annotations

import json
import secrets
import sys

from tests.jellyfin_user_boundary import FIXTURE, JellyfinBoundary, _json


class LinkBoundary(JellyfinBoundary):
    def _dispatch(self, request, method):
        if method == "GET" and request.path.split("?")[0] == "/Library/VirtualFolders":
            if self.api_key in request.headers.get("Authorization", ""):
                return _json(request, 200, [])
        return super()._dispatch(request, method)


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    served = FIXTURE["states"][state]["users"]
    passwords = {user["Id"]: secrets.token_urlsafe(18) for user in served}
    with LinkBoundary(secrets.token_urlsafe(24), passwords) as boundary:
        boundary.state = state
        users = {
            user["Name"]: {"userId": user["Id"], "password": passwords[user["Id"]]}
            for user in served
        }
        print(
            json.dumps(
                {
                    "url": boundary.url,
                    "apiKey": boundary.api_key,
                    "serverId": boundary.server_info()["Id"],
                    "users": users,
                }
            ),
            flush=True,
        )
        sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
