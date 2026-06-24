"""iOS 17+ tunnel fix: choose_reachable_host must reach WDA via the CoreDevice
tunnel IP when the host WDA announces (Wi-Fi IP or `localhost`) is not routable
from this Mac.

Regression context: on iOS 17+ a USB-attached device is reachable from the host
only via the RemoteServiceTunnel (connectionProperties.tunnelIPAddress, an IPv6
ULA). WDA announces the first on-device IP it finds — often `localhost` or a
Wi-Fi IP on a network the Mac can't reach — so smoke_test against that host fails
"Connection refused". choose_reachable_host probes candidates and returns one
that actually answers /status.
"""
from __future__ import annotations

from unittest.mock import patch

from simdrive.wda import bootstrap


def test_fmt_host_brackets_ipv6_only():
    assert bootstrap._fmt_host("fd1a:431c:b5a0::1") == "[fd1a:431c:b5a0::1]"
    assert bootstrap._fmt_host("[fd1a:431c:b5a0::1]") == "[fd1a:431c:b5a0::1]"
    assert bootstrap._fmt_host("192.168.1.10") == "192.168.1.10"
    assert bootstrap._fmt_host("localhost") == "localhost"


def test_falls_back_to_tunnel_when_announced_unreachable():
    # WDA announced a Wi-Fi IP the Mac can't route to; only the tunnel answers.
    with patch.object(bootstrap, "_resolve_tunnel_ip", return_value="fd1a:431c:b5a0::1"), \
         patch.object(bootstrap, "_probe_status", side_effect=lambda h, p, **k: h == "[fd1a:431c:b5a0::1]"):
        host = bootstrap.choose_reachable_host("192.168.1.10", 8100, "CORE-UUID")
    assert host == "[fd1a:431c:b5a0::1]"


def test_localhost_announcement_uses_tunnel():
    # The original failure mode: WDA announced localhost (no routable IP).
    with patch.object(bootstrap, "_resolve_tunnel_ip", return_value="fd1a:431c:b5a0::1"), \
         patch.object(bootstrap, "_probe_status", side_effect=lambda h, p, **k: h == "[fd1a:431c:b5a0::1]"):
        host = bootstrap.choose_reachable_host("localhost", 8100, "CORE-UUID")
    assert host == "[fd1a:431c:b5a0::1]"


def test_falls_back_to_announced_when_no_tunnel():
    # No tunnel resolvable, but the announced Wi-Fi IP is reachable.
    with patch.object(bootstrap, "_resolve_tunnel_ip", return_value=None), \
         patch.object(bootstrap, "_probe_status", side_effect=lambda h, p, **k: h == "192.168.1.10"):
        host = bootstrap.choose_reachable_host("192.168.1.10", 8100, "CORE-UUID")
    assert host == "192.168.1.10"


def test_returns_best_candidate_when_nothing_reachable():
    # Nothing answers — return the tunnel candidate so smoke_test's error names
    # the address actually tried (an actionable failure, not a misleading one).
    with patch.object(bootstrap, "_resolve_tunnel_ip", return_value="fd1a:431c:b5a0::1"), \
         patch.object(bootstrap, "_probe_status", return_value=False):
        host = bootstrap.choose_reachable_host("localhost", 8100, "CORE-UUID")
    assert host == "[fd1a:431c:b5a0::1]"
