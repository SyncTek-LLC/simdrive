"""Dogfood gap 2 — `simctl spawn defaults read` answers from the wrong domain.

An agent with no first-class preferences primitive improvised
``xcrun simctl spawn <udid> defaults read <bundle> <key>`` and filed a *major*
"this toggle never persists" defect against a toggle that persisted fine.
Half an agent's budget went into the false lead.

Measured on a booted iOS 26 simulator, the mechanism is that a simulator keeps
TWO files for one domain: the app's sandboxed
``Containers/Data/Application/<uuid>/Library/Preferences/<bundle>.plist`` and a
device-wide ``data/Library/Preferences/<bundle>.plist``. `simctl spawn defaults`
reads and writes the second; the app reads and writes the first; they never
sync. So `app_defaults` reads the container plist off disk and flags keys that
are only in the device-wide domain, and `set_app_defaults` writes the container
plist as a *path domain* and verifies the result against disk.

These tests run without a simulator: the container path and the simctl
invocations are mocked, and the plists are real files written by plistlib.
"""
from __future__ import annotations

import datetime
import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simdrive import prefs
from simdrive.sim import SimError


# ── helpers ─────────────────────────────────────────────────────────────────


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def _container(tmp_path: Path, values: dict | None = None, fmt=plistlib.FMT_BINARY) -> Path:
    """Build a fake app data container with a preferences plist inside."""
    root = tmp_path / "Containers" / "Data" / "Application" / "ABC"
    prefs_dir = root / "Library" / "Preferences"
    prefs_dir.mkdir(parents=True, exist_ok=True)
    if values is not None:
        with (prefs_dir / "com.example.app.plist").open("wb") as fh:
            plistlib.dump(values, fh, fmt=fmt)
    return root


def _with_container(root: Path):
    return patch("simdrive.prefs.sim.get_app_container", return_value=root)


def _make_sim_session(tmp_path: Path, bundle: str | None = "com.example.app"):
    from simdrive.sim import Device
    d = Device(udid="SIM-PREFS", name="iPhone 17 Pro", os_version="26.0", state="Booted")
    return SimpleNamespace(
        session_id="sid-prefs",
        device=d,
        target="simulator",
        app_bundle_id=bundle,
        workdir=tmp_path,
        last_action_at=0.0,
    )


# ── read: the disk is the source of truth ───────────────────────────────────


def test_reads_binary_plist_from_disk(tmp_path):
    root = _container(tmp_path, {"hiddenLibrariesEnabled": True, "launchCount": 7})
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"] == {"hiddenLibrariesEnabled": True, "launchCount": 7}
    assert out["exists"] is True
    assert out["plist_path"].endswith("com.example.app.plist")


def test_reads_xml_plist_too(tmp_path):
    root = _container(tmp_path, {"theme": "sepia"}, fmt=plistlib.FMT_XML)
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"] == {"theme": "sepia"}


def test_keys_filter_reports_found_and_missing(tmp_path):
    root = _container(tmp_path, {"a": 1, "b": 2, "c": 3})
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app", keys=["a", "c", "nope"])
    assert out["values"] == {"a": 1, "c": 3}
    assert out["missing_keys"] == ["nope"]


def test_missing_plist_returns_empty_dict_and_a_note_not_an_exception(tmp_path):
    """The app may simply never have written a default yet. That is data, not an error."""
    root = _container(tmp_path, values=None)
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"] == {}
    assert out["exists"] is False
    assert out["note"], "a missing plist must be explained, not silently empty"


def test_corrupt_plist_is_reported_not_raised(tmp_path):
    root = _container(tmp_path, values=None)
    (root / "Library" / "Preferences" / "com.example.app.plist").write_bytes(b"not a plist at all")
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"] == {}
    assert "unreadable" in out["note"].lower() or "parse" in out["note"].lower()


def test_uninstalled_app_raises_a_structured_error():
    with patch("simdrive.prefs.sim._simctl", return_value=_fail("No such file or directory")):
        with pytest.raises(SimError) as exc:
            prefs.read_defaults("UDID", "com.nope.nope")
    assert "com.nope.nope" in str(exc.value)


# ── JSON coercion — real plists carry Data and Date values ──────────────────


def test_date_and_data_values_survive_as_json(tmp_path):
    """Real app plists carry NSDate and NSData; both must cross the MCP boundary."""
    root = _container(tmp_path, {
        "validThrough": datetime.datetime(2026, 9, 9, 20, 18, 0),
        "readerSettings": b'{"theme":"sepia"}',
    })
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"]["validThrough"] == "2026-09-09T20:18:00"
    blob = out["values"]["readerSettings"]
    assert blob["__type__"] == "data"
    import base64
    assert base64.b64decode(blob["base64"]) == b'{"theme":"sepia"}'

    import json
    json.dumps(out)  # must not raise


def test_nested_containers_are_coerced_recursively(tmp_path):
    root = _container(tmp_path, {
        "accounts": [{"id": "a", "seen": datetime.datetime(2026, 1, 2, 3, 4, 5)}],
    })
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app")
    assert out["values"]["accounts"][0]["seen"] == "2026-01-02T03:04:05"


# ── read: the device-domain trap gets named ─────────────────────────────────


def test_key_only_in_the_device_wide_domain_is_flagged(tmp_path):
    """The exact shape of the false finding: someone set the toggle with
    `simctl spawn defaults write`, so it lives in a file the app never reads."""
    root = _container(tmp_path, {"unrelated": 1})
    device_prefs = tmp_path / "Library" / "Preferences"
    device_prefs.mkdir(parents=True)
    with (device_prefs / "com.example.app.plist").open("wb") as fh:
        plistlib.dump({"hiddenLibrariesEnabled": True}, fh)

    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app", keys=["hiddenLibrariesEnabled"])

    assert out["values"] == {}, "the app's own domain does not have it, and that is the truth"
    assert out["missing_keys"] == ["hiddenLibrariesEnabled"]
    assert out["device_domain_only"] == {"hiddenLibrariesEnabled": True}
    assert "WARNING" in out["note"]


def test_no_device_domain_noise_when_the_key_is_simply_absent(tmp_path):
    root = _container(tmp_path, {"unrelated": 1})
    with _with_container(root):
        out = prefs.read_defaults("UDID", "com.example.app", keys=["nope"])
    assert out["device_domain_only"] == {}
    assert "WARNING" not in out["note"]


# ── write: into the app's own domain, verified against disk ─────────────────


def test_write_targets_the_container_plist_as_a_path_domain(tmp_path):
    """A bare `defaults write <bundle>` lands in the device-wide domain, which the
    app never reads — measured on a booted iOS 26 sim. The domain must be the
    container plist path."""
    root = _container(tmp_path, {})
    calls: list[list[str]] = []

    def _fake_simctl(*args, **kwargs):
        calls.append(list(args))
        return _ok()

    good = {"values": {"flag": True}, "exists": True, "plist_path": "p",
            "missing_keys": [], "device_domain_only": {}, "note": ""}
    with _with_container(root), patch("simdrive.prefs.sim._simctl", side_effect=_fake_simctl):
        with patch("simdrive.prefs.read_defaults", return_value=good):
            prefs.write_defaults("UDID", "com.example.app", {"flag": True})

    argv = calls[0]
    domain = argv[argv.index("write") + 1]
    assert domain.endswith("Library/Preferences/com.example.app"), domain
    assert "Containers/Data/Application" in domain, domain
    assert not domain.endswith(".plist"), "defaults path domains omit the extension"
    assert domain != "com.example.app", (
        "a bare bundle domain writes the device-wide plist, not the app's"
    )


def test_write_uses_typed_defaults_flags(tmp_path):
    """`defaults write x 1` stores the *string* "1"; the type flag is not optional."""
    root = _container(tmp_path, {})
    calls: list[list[str]] = []

    def _fake_simctl(*args, **kwargs):
        calls.append(list(args))
        return _ok()

    with _with_container(root), patch("simdrive.prefs.sim._simctl", side_effect=_fake_simctl):
        with patch("simdrive.prefs.read_defaults", return_value={
            "values": {"flag": True, "count": 3, "ratio": 0.5, "name": "x"},
            "exists": True, "plist_path": "p", "missing_keys": [],
            "device_domain_only": {}, "note": "",
        }):
            prefs.write_defaults("UDID", "com.example.app", {
                "flag": True, "count": 3, "ratio": 0.5, "name": "x",
            })

    argv = [" ".join(c) for c in calls]
    assert any("-bool true" in a and "flag" in a for a in argv), argv
    assert any("-int 3" in a and "count" in a for a in argv), argv
    assert any("-float 0.5" in a and "ratio" in a for a in argv), argv
    assert any("-string x" in a and "name" in a for a in argv), argv


def test_write_verifies_by_reading_the_plist_back(tmp_path):
    """The verification is a real round-trip, not an exit-code check: this test
    only passes if the value the write claimed is the value found on disk."""
    root = _container(tmp_path, {})
    reads = [
        # First verification read: not yet visible.
        {"values": {}, "exists": True, "plist_path": "p", "missing_keys": [],
         "device_domain_only": {}, "note": ""},
        {"values": {"flag": True}, "exists": True, "plist_path": "p", "missing_keys": [],
         "device_domain_only": {}, "note": ""},
    ]
    with _with_container(root), patch("simdrive.prefs.sim._simctl", return_value=_ok()):
        with patch("simdrive.prefs.read_defaults", side_effect=reads):
            out = prefs.write_defaults("UDID", "com.example.app", {"flag": True})
    assert out["verified"] == {"flag": True}
    assert out["all_verified"] is True


def test_write_rejects_a_disk_value_that_disagrees_with_what_was_asked(tmp_path):
    """`defaults write` exiting 0 while the plist holds a different value must not
    be reported as verified."""
    root = _container(tmp_path, {})
    wrong = {"values": {"flag": False}, "exists": True, "plist_path": "p",
             "missing_keys": [], "device_domain_only": {}, "note": ""}
    with _with_container(root), patch("simdrive.prefs.sim._simctl", return_value=_ok()):
        with patch("simdrive.prefs.read_defaults", return_value=wrong):
            out = prefs.write_defaults("UDID", "com.example.app", {"flag": True})
    assert out["all_verified"] is False
    assert out["unverified"] == ["flag"]


def test_write_reports_unverified_rather_than_claiming_success(tmp_path):
    """If the value never reaches disk we must say so — a false 'written' is exactly
    the failure mode this whole tool exists to remove."""
    root = _container(tmp_path, {})
    empty = {"values": {}, "exists": True, "plist_path": "p", "missing_keys": [],
             "device_domain_only": {}, "note": ""}
    with _with_container(root), patch("simdrive.prefs.sim._simctl", return_value=_ok()):
        with patch("simdrive.prefs.read_defaults", return_value=empty):
            out = prefs.write_defaults("UDID", "com.example.app", {"flag": True})
    assert out["all_verified"] is False
    assert "flag" in out["unverified"]
    assert "NOT written" in out["note"]


def test_write_never_kills_cfprefsd(tmp_path):
    """`killall` does not exist in the simulator runtime, and running it on the host
    would kill the developer's own preferences daemon. Measured, not assumed."""
    root = _container(tmp_path, {})
    empty = {"values": {}, "exists": True, "plist_path": "p", "missing_keys": [],
             "device_domain_only": {}, "note": ""}
    calls: list[list[str]] = []

    def _fake_simctl(*args, **kwargs):
        calls.append(list(args))
        return _ok()

    with _with_container(root), patch("simdrive.prefs.sim._simctl", side_effect=_fake_simctl):
        with patch("simdrive.prefs.read_defaults", return_value=empty):
            prefs.write_defaults("UDID", "com.example.app", {"flag": True})
    assert not any("killall" in " ".join(c) for c in calls), calls


def test_write_failure_from_simctl_surfaces(tmp_path):
    root = _container(tmp_path, {})
    with _with_container(root), patch("simdrive.prefs.sim._simctl", return_value=_fail("nope")):
        with pytest.raises(SimError):
            prefs.write_defaults("UDID", "com.example.app", {"flag": True})


def test_write_rejects_unsupported_value_types(tmp_path):
    root = _container(tmp_path, {})
    with _with_container(root):
        with pytest.raises(SimError) as exc:
            prefs.write_defaults("UDID", "com.example.app", {"blob": object()})
    assert "blob" in str(exc.value)


def test_write_encodes_collections_as_plist_literals(tmp_path):
    """defaults(1) parses a plist literal for structured values."""
    root = _container(tmp_path, {})
    calls: list[list[str]] = []

    def _fake_simctl(*args, **kwargs):
        calls.append(list(args))
        return _ok()

    good = {"values": {"libs": ["a", "b"]}, "exists": True, "plist_path": "p",
            "missing_keys": [], "device_domain_only": {}, "note": ""}
    with _with_container(root), patch("simdrive.prefs.sim._simctl", side_effect=_fake_simctl):
        with patch("simdrive.prefs.read_defaults", return_value=good):
            prefs.write_defaults("UDID", "com.example.app", {"libs": ["a", "b"]})
    written = calls[0]
    assert written[-1].startswith("(") or written[-1].startswith("<"), written


# ── MCP tool wiring ─────────────────────────────────────────────────────────


def test_tool_app_defaults_defaults_to_the_session_bundle(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    seen: dict = {}

    def _fake_read(udid, bundle_id, keys=None):
        seen.update({"udid": udid, "bundle_id": bundle_id, "keys": keys})
        return {"values": {"k": 1}, "exists": True, "plist_path": "/p",
                "missing_keys": [], "device_domain_only": {}, "note": ""}

    monkeypatch.setattr("simdrive.prefs.read_defaults", _fake_read)
    out = server_mod.tool_app_defaults({"session_id": s.session_id})

    assert out["ok"] is True
    assert seen["bundle_id"] == "com.example.app"
    assert seen["udid"] == "SIM-PREFS"
    assert out["values"] == {"k": 1}


def test_tool_app_defaults_bundle_override(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    seen: dict = {}
    monkeypatch.setattr(
        "simdrive.prefs.read_defaults",
        lambda udid, bundle_id, keys=None: seen.update(bundle_id=bundle_id) or {
            "values": {}, "exists": False, "plist_path": "/p", "missing_keys": [],
            "device_domain_only": {}, "note": "n"},
    )
    server_mod.tool_app_defaults({"session_id": s.session_id, "bundle_id": "com.other"})
    assert seen["bundle_id"] == "com.other"


def test_tool_app_defaults_without_any_bundle_is_a_structured_error(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path, bundle=None)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_app_defaults({"session_id": s.session_id})
    assert "bundle" in str(exc.value).lower()


def test_tool_set_app_defaults_requires_a_values_object(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_set_app_defaults({"session_id": s.session_id, "values": ["nope"]})
    assert "values" in str(exc.value)


def test_tool_set_app_defaults_returns_verification(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    monkeypatch.setattr(
        "simdrive.prefs.write_defaults",
        lambda udid, bundle_id, values: {
            "written": ["flag"], "verified": {"flag": True}, "unverified": [],
            "all_verified": True, "plist_path": "/p", "note": "",
        },
    )
    out = server_mod.tool_set_app_defaults(
        {"session_id": s.session_id, "values": {"flag": True}})
    assert out["ok"] is True
    assert out["all_verified"] is True


def test_defaults_tools_are_registered_and_warn_about_the_improvised_command():
    """The description is the only place an agent learns not to reach for
    `simctl spawn defaults`, so the warning is part of the deliverable."""
    import simdrive.server as server_mod
    names = {t["name"] for t in server_mod._TOOLS}
    assert {"app_defaults", "set_app_defaults"} <= names
    for name in ("app_defaults", "set_app_defaults"):
        spec = next(t for t in server_mod._TOOLS if t["name"] == name)
        desc = spec["description"].lower()
        assert "device-wide" in desc, f"{name} must name the wrong-domain trap"
        assert "simctl spawn" in desc and "defaults " in desc, (
            f"{name} must tell the agent why the improvised simctl command is not the answer"
        )


# ── simctl container resolution ─────────────────────────────────────────────


def test_get_app_container_returns_the_parsed_path():
    from simdrive import sim as sim_mod
    out = "/Users/x/Devices/UD/data/Containers/Data/Application/ABC\n"
    with patch("simdrive.sim._simctl", return_value=_ok(out)):
        assert str(sim_mod.get_app_container("UD", "com.example.app")) == out.strip()


def test_get_app_container_rejects_an_empty_path():
    """simctl exiting 0 with no path would otherwise produce a Path('') that
    silently resolves to the working directory."""
    from simdrive import sim as sim_mod
    with patch("simdrive.sim._simctl", return_value=_ok("   \n")):
        with pytest.raises(SimError) as exc:
            sim_mod.get_app_container("UD", "com.example.app")
    assert "empty" in str(exc.value)


# ── tool-surface edge cases ─────────────────────────────────────────────────


def test_prefs_tools_refuse_a_device_session(tmp_path, monkeypatch):
    """There is no `get_app_container` for real hardware; say so rather than
    failing deep inside a simctl call."""
    import simdrive.server as server_mod
    import simdrive.session as session_mod
    from simdrive.sim import Device

    d = Device(udid="DEV", name="iPhone", os_version="26.0", state="available")
    s = SimpleNamespace(session_id="sid-dev-prefs", device=d, target="device",
                        app_bundle_id="com.example.app", workdir=tmp_path, last_action_at=0.0)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    for tool in (server_mod.tool_app_defaults, server_mod.tool_set_app_defaults):
        with pytest.raises(Exception) as exc:
            tool({"session_id": s.session_id, "values": {"a": 1}})
        assert "simulator-only" in str(exc.value)


def test_tool_app_defaults_rejects_non_list_keys(tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)
    with pytest.raises(Exception) as exc:
        server_mod.tool_app_defaults({"session_id": s.session_id, "keys": "hiddenLibraries"})
    assert "keys" in str(exc.value)


def test_tool_set_app_defaults_turns_an_unsupported_type_into_a_structured_error(
        tmp_path, monkeypatch):
    import simdrive.server as server_mod
    import simdrive.session as session_mod

    s = _make_sim_session(tmp_path)
    monkeypatch.setitem(session_mod._SESSIONS, s.session_id, s)

    def _boom(udid, bundle_id, values):
        raise SimError("cannot write preference 'blob': unsupported value type object")

    monkeypatch.setattr("simdrive.prefs.write_defaults", _boom)
    with pytest.raises(Exception) as exc:
        server_mod.tool_set_app_defaults({"session_id": s.session_id, "values": {"blob": 1}})
    assert "unsupported value type" in str(exc.value)
