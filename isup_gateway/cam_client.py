"""Authenticated cam management API; video transport is handled separately."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class CamClient:
    def __init__(self, base, username, password):
        self.base, self.username, self.password = base.rstrip("/"), username, password
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None, method=None, retry=True):
        if path != "/api/auth/login" and not self.token:
            self.token = self.request(
                "/api/auth/login", {"username": self.username, "password": self.password}
            )["token"]
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            self.base + path,
            headers=headers,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
        )
        try:
            with self.opener.open(req, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry and path != "/api/auth/login":
                self.token = None
                return self.request(path, body, method, False)
            raise RuntimeError(f"cam API {path}: HTTP {exc.code}") from None
        if not result.get("ok"):
            raise RuntimeError(f"cam API {path}: {result.get('error', 'rejected')}")
        return result
