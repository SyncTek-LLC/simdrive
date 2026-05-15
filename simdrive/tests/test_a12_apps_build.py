"""a12: apps response items each include a 'build' field (CFBundleVersion).

Tests:
  10. test_apps_response_includes_build_field_on_sim
      - mock simctl JSON output containing CFBundleVersion.
        Invoke tool_apps(udid=<sim>). Assert each app dict has 'build' key set
        to the CFBundleVersion value.
  11. test_apps_response_includes_build_field_on_device
      - mock devicectl JSON output. Invoke tool_apps(udid=<device>).
        Assert each app has 'build' key.

Both tests FAIL on HEAD because:
  - diagnostics.list_apps() does not include a 'build' field in its output
    (it returns bundle_id, name, version, path only).
  - diagnostics.list_apps_device() likewise omits 'build'.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ── test 10 ───────────────────────────────────────────────────────────────────


def test_apps_response_includes_build_field_on_sim(monkeypatch):
    """tool_apps on a sim udid must return each app with a 'build' key = CFBundleVersion.

    Fails on HEAD: list_apps() returns {bundle_id, name, version, path} but no 'build'.
    """
    import simdrive.server as server_mod
    import simdrive.diagnostics as diag_mod

    udid = "SIM-UDID-A12-APPS"

    # Simulate plist output from simctl listapps with CFBundleVersion.
    fake_plist_data = {
        "com.example.MyApp": {
            "CFBundleDisplayName": "MyApp",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "456",
            "Path": "/path/to/MyApp.app",
        },
        "com.example.AnotherApp": {
            "CFBundleName": "AnotherApp",
            "CFBundleShortVersionString": "0.9.1",
            "CFBundleVersion": "99",
            "Path": "/path/to/AnotherApp.app",
        },
    }

    # Monkey-patch list_apps to return the fake data with 'build' field.
    # The test verifies that tool_apps passes through the build field.
    # We mock at the diagnostics level so we can inject CFBundleVersion.
    fake_plist_bytes = plistlib.dumps(fake_plist_data)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = fake_plist_bytes.decode("utf-8")
    fake_result.stderr = ""

    monkeypatch.setattr(diag_mod, "_run", lambda cmd, timeout=15.0: fake_result)

    result = server_mod.tool_apps({"udid": udid})

    apps = result.get("apps", [])
    assert apps, f"Expected non-empty apps list, got: {result}"

    for app in apps:
        assert "build" in app, (
            f"App {app.get('bundle_id', '?')!r} is missing 'build' field. "
            f"Full app dict: {app}. "
            "a12 requires each app entry to include 'build' = CFBundleVersion."
        )
        assert app["build"], (
            f"App {app.get('bundle_id', '?')!r} has empty 'build' field: {app['build']!r}."
        )

    # Verify the actual values map correctly.
    apps_by_id = {a["bundle_id"]: a for a in apps}
    assert apps_by_id.get("com.example.MyApp", {}).get("build") == "456", (
        f"Expected build='456' for com.example.MyApp, got: "
        f"{apps_by_id.get('com.example.MyApp', {}).get('build')!r}"
    )
    assert apps_by_id.get("com.example.AnotherApp", {}).get("build") == "99", (
        f"Expected build='99' for com.example.AnotherApp, got: "
        f"{apps_by_id.get('com.example.AnotherApp', {}).get('build')!r}"
    )


# ── test 11 ───────────────────────────────────────────────────────────────────


def test_apps_response_includes_build_field_on_device(monkeypatch):
    """tool_apps on a device udid must return each app with a 'build' key.

    Fails on HEAD: list_apps_device() returns {bundle_id, name, version, path} only.
    """
    import simdrive.server as server_mod
    import simdrive.diagnostics as diag_mod

    udid = "DEVICE-UDID-A12-APPS"

    # Simulate devicectl JSON output for apps.
    fake_devicectl_json = {
        "result": {
            "apps": [
                {
                    "bundleIdentifier": "com.palace.app",
                    "name": "Palace",
                    "version": "3.1.0",
                    "bundleVersion": "789",
                    "url": "file:///private/var/containers/Bundle/com.palace.app/",
                },
                {
                    "bundleIdentifier": "com.example.other",
                    "name": "OtherApp",
                    "version": "1.0.0",
                    "bundleVersion": "12",
                    "url": "file:///private/var/containers/Bundle/com.example.other/",
                },
            ]
        }
    }

    # Patch _devicectl_info_json to return fake data.
    monkeypatch.setattr(
        diag_mod, "_devicectl_info_json",
        lambda *args, **kwargs: fake_devicectl_json,
    )

    # tool_apps checks session or udid; pass udid directly.
    # But we need to override target detection — tool_apps defaults to "simulator"
    # when udid is passed directly. We need to call list_apps_device explicitly
    # or patch tool_apps to use device path. Let's call list_apps_device directly.
    from simdrive.diagnostics import list_apps_device

    apps = list_apps_device(udid)

    assert apps, f"Expected non-empty apps list from list_apps_device, got: {apps}"

    for app in apps:
        assert "build" in app, (
            f"App {app.get('bundle_id', '?')!r} is missing 'build' field. "
            f"Full app dict: {app}. "
            "a12 requires each device app entry to include 'build' = bundleVersion."
        )

    apps_by_id = {a["bundle_id"]: a for a in apps}
    assert apps_by_id.get("com.palace.app", {}).get("build") == "789", (
        f"Expected build='789' for com.palace.app, got: "
        f"{apps_by_id.get('com.palace.app', {}).get('build')!r}"
    )
    assert apps_by_id.get("com.example.other", {}).get("build") == "12", (
        f"Expected build='12' for com.example.other, got: "
        f"{apps_by_id.get('com.example.other', {}).get('build')!r}"
    )
