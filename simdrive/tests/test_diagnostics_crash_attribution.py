"""INIT-2026-641 item 4.7's diagnostics.py sub-step — exact bundle-id crash
attribution, not substring containment.

diagnostics.py::list_crashes (~line 408) attributed a .ips report to a
bundle by substring containment:
    if bundle_id and bundle_id not in crash_bundle: continue
so a lookup for "com.acme.reader" matched a crash whose bundle is
"com.acme.reader.share" (a share extension, a distinct app). This is not
scoped to the new replay crash cross-check alone: server.py::tool_crashes
(~line 1866-1876) calls the identical function, so both consumers inherit
the fix.

Checked directly before writing this test (per the test plan's own
methodology) whether anything depends on the loose match:
`grep -rn "extension" tests/*.py src/simdrive/diagnostics.py docs/*.md`
and a separate grep of CHANGELOG.md/docs for documented intent behind the
bundle filtering. Nothing describes prefix/substring matching as a
deliberate choice, and no existing test constructs a substring-colliding
pair and asserts the extension's crash should be included — every existing
test's bundle_id and the crash's crash_bundle are the same exact string.
A hard exact-match fix is therefore correct, not a silent behavior change
nobody could have relied on.
"""
from __future__ import annotations

import json
from pathlib import Path

from simdrive import diagnostics, server


def _write_ips(reports_dir: Path, name: str, bundle_id: str, mtime: float) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    p = reports_dir / name
    header = json.dumps({"bundleID": bundle_id, "timestamp": "2026-09-20 00:00:00", "bug_type": "309"})
    body = json.dumps({"crashing_thread": 0, "threads": []})
    p.write_text(header + "\n" + body)
    import os
    os.utime(p, (mtime, mtime))
    return p


class TestListCrashesExactBundleMatch:
    def test_replay_crash_check_uses_bundle_id_not_substring(self, tmp_path):
        """Two .ips reports, one for the host app and one for a
        substring-colliding extension. A lookup for the host bundle must
        return only the exact match; the reverse lookup must return only
        the extension's crash.
        """
        reports_dir = tmp_path / "DiagnosticReports"
        _write_ips(reports_dir, "host.ips", "com.acme.reader", mtime=1000.0)
        _write_ips(reports_dir, "ext.ips", "com.acme.reader.share", mtime=1000.0)

        host_results = diagnostics.list_crashes(
            since_ts=0.0, bundle_id="com.acme.reader", reports_dir=reports_dir,
        )
        assert [r["bundle_id"] for r in host_results] == ["com.acme.reader"], (
            f"exact-match lookup for the host bundle must not also return the "
            f"extension's crash; got {[r['bundle_id'] for r in host_results]}"
        )

        ext_results = diagnostics.list_crashes(
            since_ts=0.0, bundle_id="com.acme.reader.share", reports_dir=reports_dir,
        )
        assert [r["bundle_id"] for r in ext_results] == ["com.acme.reader.share"]

    def test_tool_crashes_uses_exact_bundle_match_not_substring(self, tmp_path, monkeypatch):
        """The same fix must apply through the existing tool_crashes MCP
        handler, not only the new replay call path — both call the
        identical list_crashes function.
        """
        reports_dir = tmp_path / "DiagnosticReports"
        _write_ips(reports_dir, "host.ips", "com.acme.reader", mtime=2000.0)
        _write_ips(reports_dir, "ext.ips", "com.acme.reader.share", mtime=2000.0)
        monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_REPORTS_DIR", reports_dir)

        from simdrive import session as session_mod
        from simdrive.sim import Device

        sid = "diag-crash-1"
        session_mod._SESSIONS.pop(sid, None)
        s = session_mod.Session(
            session_id=sid,
            device=Device(udid="UDID-DIAG", name="iPhone Test", os_version="26.3", state="Booted"),
            workdir=tmp_path / "wd",
            target="simulator",
        )
        s.workdir.mkdir(parents=True, exist_ok=True)
        s.started_at = 0.0
        session_mod._SESSIONS[sid] = s

        result = server.tool_crashes({
            "session_id": sid, "app_bundle_id": "com.acme.reader", "since_session_start": True,
        })
        bundles = [c["bundle_id"] for c in result["crashes"]]
        assert bundles == ["com.acme.reader"], (
            f"tool_crashes must use exact bundle matching too; got {bundles}"
        )
