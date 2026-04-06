"""BrowserStack App Automate backend for SpecterQA iOS.

Provides the same interface as XCTestBackend but routes actions to
BrowserStack's real device cloud via their Appium REST API.

Requires BROWSERSTACK_USERNAME and BROWSERSTACK_ACCESS_KEY env vars.
"""

import base64
import json
import logging
import os
import subprocess
import urllib.request
import urllib.error
from typing import Any, Optional

logger = logging.getLogger("specterqa.ios.browserstack")

_BS_HUB = "https://hub-cloud.browserstack.com/wd/hub"


class BrowserStackError(Exception):
    pass


class BrowserStackBackend:
    """Appium-compatible backend for BrowserStack real devices."""

    def __init__(
        self,
        username: str = "",
        access_key: str = "",
        app_url: str = "",  # BrowserStack app URL after upload
        device: str = "iPhone 15 Pro",
        os_version: str = "17",
    ):
        self.username = username or os.environ.get("BROWSERSTACK_USERNAME", "")
        self.access_key = access_key or os.environ.get("BROWSERSTACK_ACCESS_KEY", "")
        self.app_url = app_url
        self.device = device
        self.os_version = os_version
        self._session_id: Optional[str] = None
        self._hub_url = _BS_HUB

        # Compatibility with XCTestBackend interface
        self._device_width = 393.0
        self._device_height = 852.0
        self._display_width = 393
        self._display_height = 852

    @staticmethod
    def is_available() -> bool:
        """Check if BrowserStack credentials are configured."""
        return bool(
            os.environ.get("BROWSERSTACK_USERNAME")
            and os.environ.get("BROWSERSTACK_ACCESS_KEY")
        )

    def upload_app(self, app_path: str) -> str:
        """Upload an .ipa/.app to BrowserStack. Returns the app URL."""
        url = "https://api-cloud.browserstack.com/app-automate/upload"
        result = subprocess.run(
            [
                "curl", "-s",
                "-u", f"{self.username}:{self.access_key}",
                "-X", "POST", url,
                "-F", f"file=@{app_path}",
            ],
            capture_output=True, text=True, timeout=300,
        )
        data = json.loads(result.stdout)
        self.app_url = data.get("app_url", "")
        if not self.app_url:
            raise BrowserStackError(f"Upload failed: {data}")
        logger.info("Uploaded app: %s", self.app_url)
        return self.app_url

    def start_session(self, bundle_id: str = "") -> str:
        """Create an Appium session on a BrowserStack device."""
        capabilities = {
            "desiredCapabilities": {
                "platformName": "ios",
                "deviceName": self.device,
                "os_version": self.os_version,
                "app": self.app_url,
                "autoAcceptAlerts": True,
                "browserstack.local": False,
                "browserstack.debug": True,
            }
        }

        data = json.dumps(capabilities).encode()
        auth = base64.b64encode(f"{self.username}:{self.access_key}".encode()).decode()

        req = urllib.request.Request(
            f"{self._hub_url}/session",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())

        self._session_id = result.get("sessionId") or result.get("value", {}).get("sessionId")
        if not self._session_id:
            raise BrowserStackError(f"No session ID: {result}")

        logger.info("BrowserStack session: %s", self._session_id)
        return self._session_id

    def _request(self, method: str, path: str, body: Any = None) -> dict:
        """Send a request to the Appium session."""
        url = f"{self._hub_url}/session/{self._session_id}{path}"
        data = json.dumps(body).encode() if body else None
        auth = base64.b64encode(f"{self.username}:{self.access_key}".encode()).decode()

        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    # --- Same interface as XCTestBackend ---

    def health(self) -> dict:
        return {"status": "ok", "provider": "browserstack", "device": self.device}

    def tap(self, x: float, y: float, duration: float = 0.0) -> dict:
        action = {
            "actions": [{
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": int(duration * 1000) if duration > 0 else 50},
                    {"type": "pointerUp", "button": 0},
                ],
            }]
        }
        self._request("POST", "/actions", action)
        return {"status": "ok"}

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.3) -> dict:
        action = {
            "actions": [{
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerMove", "duration": int(duration * 1000), "x": int(x2), "y": int(y2)},
                    {"type": "pointerUp", "button": 0},
                ],
            }]
        }
        self._request("POST", "/actions", action)
        return {"status": "ok"}

    def swipe_back(self) -> dict:
        return self.swipe(5, 422, 200, 422)

    def type_text(self, text: str) -> dict:
        # Find the active element and send keys
        active = self._request("POST", "/element/active")
        element_id = active.get("value", {}).get("ELEMENT", "")
        if element_id:
            self._request("POST", f"/element/{element_id}/value", {"text": text})
        return {"status": "ok"}

    def press_key(self, key: str) -> dict:
        key_map = {"return": "\n", "enter": "\n", "delete": "\b", "tab": "\t", "space": " "}
        char = key_map.get(key.lower(), "")
        if char:
            return self.type_text(char)
        return {"status": "ok", "warning": f"key '{key}' not mapped"}

    def screenshot(self) -> dict:
        result = self._request("GET", "/screenshot")
        b64 = result.get("value", "")
        return {"base64": b64, "width": self._display_width, "height": self._display_height}

    def source(self) -> dict:
        result = self._request("GET", "/source")
        return result

    def stop(self) -> None:
        """End the BrowserStack session."""
        if self._session_id:
            try:
                self._request("DELETE", "")
            except Exception:
                pass
            self._session_id = None
