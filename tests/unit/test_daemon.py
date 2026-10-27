"""Tests for the privileged daemon, its protocol, and the RPC client."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from wifi_hotspot import daemon
from wifi_hotspot import engine as engine_module
from wifi_hotspot.config import Paths, load_profile, save_profile
from wifi_hotspot.daemon_client import DaemonClient, DaemonError, DaemonUnavailable
from wifi_hotspot.models import HotspotConfig
from wifi_hotspot.state import RuntimeState


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(run_dir=tmp_path / "run", etc_dir=tmp_path / "etc")


def make_service(paths: Paths) -> daemon.DaemonService:
    return daemon.DaemonService(paths)


def test_peer_uid_zero_is_authorized_for_privileged_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[int] = []
    monkeypatch.setattr(daemon, "pkcheck_authorize", lambda pid: (called.append(pid), True)[1])
    assert daemon.authorize(123, 0, "start") is True
    assert called == []


def test_unprivileged_methods_do_not_require_polkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daemon, "pkcheck_authorize", lambda _pid: pytest.fail("called"))
    assert daemon.authorize(123, 1000, "status") is True
    assert daemon.authorize(123, 1000, "clients") is True


def test_non_root_privileged_method_uses_polkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    monkeypatch.setattr(daemon, "pkcheck_authorize", lambda pid: seen.append(pid) or True)
    assert daemon.authorize(4321, 1000, "start") is True
    assert seen == [4321]

    monkeypatch.setattr(daemon, "pkcheck_authorize", lambda _pid: False)
    assert daemon.authorize(4321, 1000, "stop") is False


def test_dispatch_rejects_unknown_method(paths: Paths) -> None:
    with pytest.raises(daemon.RpcError, match="desconocido"):
        make_service(paths).dispatch("nope", {}, pid=1, uid=0)


def test_dispatch_denies_privileged_action(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon, "pkcheck_authorize", lambda _pid: False)
    with pytest.raises(daemon.PermissionDenied):
        make_service(paths).dispatch("start", {}, pid=123, uid=1000)


def test_ping_and_status(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_module.HotspotEngine,
        "status",
        lambda _self: {"status": "stopped", "state_file": str(paths.state_file)},
    )
    service = make_service(paths)
    assert service.dispatch("ping", {}, pid=1, uid=0)["pong"] is True
    assert service.dispatch("status", {}, pid=1, uid=0)["status"] == "stopped"


def test_start_returns_state_and_config(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_start(self: engine_module.HotspotEngine) -> RuntimeState:
        return RuntimeState(
            status="running",
            ssid=self.config.ssid,
            ap_interface=self.config.ap_interface,
            uplink="eth0",
            ap_ip=self.config.ap_ip,
            channel=6,
            band="2.4",
        )

    monkeypatch.setattr(engine_module.HotspotEngine, "start", fake_start)
    service = make_service(paths)
    result = service.dispatch(
        "start",
        {"profile": "default", "overrides": {"ssid": "DaemonNet", "password": "Password123"}},
        pid=1,
        uid=0,
    )
    assert result["state"]["ssid"] == "DaemonNet"
    assert result["config"]["ssid"] == "DaemonNet"
    assert result["config"]["password"] == "Password123"


def test_start_can_generate_password(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_module.HotspotEngine,
        "start",
        lambda _self: RuntimeState(status="running", ssid="DaemonNet"),
    )
    result = make_service(paths).dispatch(
        "start",
        {"overrides": {"ssid": "DaemonNet"}, "generate_password": True},
        pid=1,
        uid=0,
    )
    assert len(result["config"]["password"]) >= 16


def test_stop_and_restart(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module.HotspotEngine, "stop", lambda _self: True)
    monkeypatch.setattr(
        engine_module.HotspotEngine,
        "restart",
        lambda _self: RuntimeState(status="running", ssid="Restarted"),
    )
    service = make_service(paths)
    assert service.dispatch("stop", {}, pid=1, uid=0) == {"stopped": True}
    result = service.dispatch("restart", {"overrides": {"ssid": "Restarted"}}, pid=1, uid=0)
    assert result["state"]["ssid"] == "Restarted"


def test_disconnect_requires_mac(paths: Paths) -> None:
    with pytest.raises(daemon.RpcError, match="MAC"):
        make_service(paths).dispatch("disconnect", {}, pid=1, uid=0)


def test_disconnect_calls_engine(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        engine_module.HotspotEngine,
        "disconnect_client",
        lambda _self, mac: seen.append(mac),
    )
    result = make_service(paths).dispatch("disconnect", {"mac": "AA:BB:CC:DD:EE:FF"}, pid=1, uid=0)
    assert result == {"disconnected": "aa:bb:cc:dd:ee:ff"}
    assert seen == ["AA:BB:CC:DD:EE:FF"]


def test_profiles_crud(paths: Paths) -> None:
    service = make_service(paths)
    config = HotspotConfig(profile_name="cafe", ssid="CafeNet", password="Password123").to_dict()
    saved = service.dispatch("profiles.save", {"config": config}, pid=1, uid=0)
    assert saved["saved"] == "cafe"
    assert load_profile(paths, "cafe") is not None

    assert service.dispatch("profiles.list", {}, pid=1, uid=0) == ["cafe"]
    fetched = service.dispatch("profiles.get", {"profile": "cafe"}, pid=1, uid=0)
    assert fetched["ssid"] == "CafeNet"
    assert fetched["password"] == "Password123"

    assert service.dispatch("profiles.delete", {"profile": "cafe"}, pid=1, uid=0) == {
        "deleted": True
    }
    assert service.dispatch("profiles.list", {}, pid=1, uid=0) == []


def test_profiles_get_missing(paths: Paths) -> None:
    with pytest.raises(daemon.RpcError, match="No existe"):
        make_service(paths).dispatch("profiles.get", {"profile": "missing"}, pid=1, uid=0)


def test_profiles_save_with_overrides_preserves_other_fields(paths: Paths) -> None:
    service = make_service(paths)
    original = HotspotConfig(
        profile_name="default",
        ssid="OriginalNet",
        password="Password123",
        channel=6,
        country="ES",
    )
    save_profile(paths, original)

    service.dispatch(
        "profiles.save",
        {"profile": "default", "overrides": {"ssid": "RenamedNet"}},
        pid=1,
        uid=0,
    )
    stored = load_profile(paths, "default")
    assert stored is not None
    assert stored.ssid == "RenamedNet"
    assert stored.password == "Password123"
    assert stored.channel == 6
    assert stored.country == "ES"


def test_profiles_summary_does_not_expose_password(paths: Paths) -> None:
    service = make_service(paths)
    config = HotspotConfig(profile_name="cafe", ssid="CafeNet", password="Password123")
    save_profile(paths, config)

    summary = service.dispatch("profiles.summary", {"profile": "cafe"}, pid=1, uid=0)
    assert summary["profile_name"] == "cafe"
    assert summary["ssid"] == "CafeNet"
    assert summary["stored"] is True
    assert "password" not in summary

    missing = service.dispatch("profiles.summary", {"profile": "default"}, pid=1, uid=0)
    assert missing["stored"] is False


def test_profiles_save_rejects_invalid_overrides(paths: Paths) -> None:
    with pytest.raises(daemon.RpcError, match="objeto"):
        make_service(paths).dispatch(
            "profiles.save",
            {"profile": "default", "overrides": ["not", "a", "dict"]},
            pid=1,
            uid=0,
        )


def test_shutdown_returns_flag(paths: Paths) -> None:
    assert make_service(paths).dispatch("shutdown", {}, pid=1, uid=0) == {"shutdown": True}


def test_socket_protocol_roundtrip(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.ensure_runtime()
    saved = HotspotConfig(profile_name="default", ssid="ProtocolNet", password="Password123")
    save_profile(paths, saved)
    stop_event = threading.Event()
    server = daemon.UnixSocketServer(paths.daemon_socket, make_service(paths), stop_event)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DaemonClient(paths, timeout=5)
        assert client.available() is True
        assert client.ping()["pong"] is True
        names = client.list_profiles()
        assert names == ["default"]
        # profiles.get is privileged; allow it explicitly for this protocol test.
        monkeypatch.setattr(daemon, "authorize", lambda _pid, _uid, _method: True)
        profile = client.get_profile("default")
        assert profile["ssid"] == "ProtocolNet"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        stop_event.set()


def test_socket_protocol_reports_errors(paths: Paths) -> None:
    paths.ensure_runtime()
    server = daemon.UnixSocketServer(paths.daemon_socket, make_service(paths), threading.Event())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(paths.daemon_socket))
        sock.sendall(b'{"id": 7, "method": "missing"}\n')
        response = json.loads(sock.recv(65536).split(b"\n", 1)[0])
        sock.close()
        assert response["ok"] is False
        assert response["id"] == 7
        assert response["code"] == "unknown_method"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_unavailable(tmp_path: Path) -> None:
    client = DaemonClient(Paths(run_dir=tmp_path / "run", etc_dir=tmp_path / "etc"))
    with pytest.raises(DaemonUnavailable):
        client.ping()


def test_client_raises_daemon_error(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.ensure_runtime()
    server = daemon.UnixSocketServer(paths.daemon_socket, make_service(paths), threading.Event())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DaemonClient(paths, timeout=5)
        with pytest.raises(DaemonError) as excinfo:
            client.request("does.not.exist")
        assert excinfo.value.code == "unknown_method"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
