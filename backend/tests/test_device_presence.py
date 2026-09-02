"""Tests para device_presence.py: parseo de tablas ARP/vecinos (Windows y
Linux, con variantes de formato) y la lógica de online/offline."""

import json
from unittest.mock import patch

import pytest

from app import device_presence
from app.device_presence import (
    _WINDOWS_ARP_LINE,
    _LINUX_NEIGH_LINE,
    _LINUX_ARP_LINE,
    parse_arp_table,
    resolve_ip_by_mac,
    is_device_online,
    check_all,
)

MAC = "24:da:9b:14:a4:ca"
IP = "192.168.100.104"


class TestParseWindowsArp:
    def test_standard_output_dash_separator(self):
        output = (
            "\n"
            "Interfaz: 192.168.100.5 --- 0xb\n"
            "  Dirección de Internet   Dirección física      Tipo\n"
            "  192.168.100.104      24-da-9b-14-a4-ca     dinámico\n"
            "  192.168.100.1        aa-bb-cc-dd-ee-ff     dinámico\n"
        )
        table = parse_arp_table(output, [_WINDOWS_ARP_LINE])
        assert table[MAC] == IP
        assert table["aa:bb:cc:dd:ee:ff"] == "192.168.100.1"

    def test_english_locale_static_entry(self):
        # Regional variants change the status word ("static"/"estático") and
        # can add extra columns/spacing, but never the IP/MAC columns.
        output = (
            "Interface: 192.168.100.5 --- 0xb\n"
            "  Internet Address      Physical Address      Type\n"
            "  192.168.100.104       24-da-9b-14-a4-ca     static\n"
        )
        table = parse_arp_table(output, [_WINDOWS_ARP_LINE])
        assert table[MAC] == IP

    def test_extra_blank_lines_and_multiple_interfaces(self):
        output = (
            "\n"
            "Interfaz: 192.168.100.5 --- 0xb\n"
            "\n"
            "  192.168.100.104      24-DA-9B-14-A4-CA     dinámico\n"
            "\n"
            "Interfaz: 100.70.1.38 --- 0x1e\n"
            "  100.70.1.1           11-22-33-44-55-66     estático\n"
        )
        table = parse_arp_table(output, [_WINDOWS_ARP_LINE])
        assert table[MAC] == IP
        assert table["11:22:33:44:55:66"] == "100.70.1.1"

    def test_mac_normalized_case_insensitive(self):
        output = "  192.168.100.104      24-DA-9B-14-A4-CA     dinámico\n"
        table = parse_arp_table(output, [_WINDOWS_ARP_LINE])
        assert MAC in table  # ya viene en minúscula pese al input en mayúscula


class TestParseLinux:
    def test_ip_neigh_reachable(self):
        output = "192.168.100.104 dev eth0 lladdr 24:da:9b:14:a4:ca REACHABLE\n"
        table = parse_arp_table(output, [_LINUX_NEIGH_LINE])
        assert table[MAC] == IP

    def test_ip_neigh_stale_still_resolves_ip(self):
        # STALE = el kernel no confirmo hace poco que siga ahi, pero SI
        # conoce la MAC -> resuelve la IP igual. El ping despues es quien
        # confirma si de verdad sigue respondiendo.
        output = "192.168.100.66 dev eth0 lladdr fa:2c:a4:98:61:64 STALE\n"
        table = parse_arp_table(output, [_LINUX_NEIGH_LINE])
        assert table["fa:2c:a4:98:61:64"] == "192.168.100.66"

    def test_ip_neigh_failed_entry_has_no_lladdr_and_is_ignored(self):
        # FAILED no trae lladdr (el kernel ya no sabe la MAC) -> no matchea,
        # exactamente el comportamiento pedido: "ausencia total" = offline.
        output = (
            "192.168.100.104 dev eth0  FAILED\n"
            "192.168.100.66 dev eth0 lladdr fa:2c:a4:98:61:64 STALE\n"
        )
        table = parse_arp_table(output, [_LINUX_NEIGH_LINE])
        assert MAC not in table
        assert "fa:2c:a4:98:61:64" in table

    def test_ip_neigh_multiple_devices(self):
        output = (
            "192.168.100.104 dev eth0 lladdr 24:da:9b:14:a4:ca REACHABLE\n"
            "192.168.100.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff STALE\n"
        )
        table = parse_arp_table(output, [_LINUX_NEIGH_LINE])
        assert table[MAC] == IP
        assert table["aa:bb:cc:dd:ee:ff"] == "192.168.100.1"

    def test_arp_n_fallback_output(self):
        output = (
            "Address                  HWtype  HWaddress           Flags Mask Iface\n"
            "192.168.100.104          ether   24:da:9b:14:a4:ca   C            eth0\n"
        )
        table = parse_arp_table(output, [_LINUX_ARP_LINE])
        assert table[MAC] == IP

    def test_arp_n_incomplete_entry_ignored(self):
        # `arp -n` marca una entrada sin resolver como "(incomplete)" en vez
        # de una MAC -- no debe matchear el patron de MAC.
        output = (
            "Address                  HWtype  HWaddress           Flags Mask Iface\n"
            "192.168.100.200          (incomplete)                              eth0\n"
            "192.168.100.104          ether   24:da:9b:14:a4:ca   C            eth0\n"
        )
        table = parse_arp_table(output, [_LINUX_ARP_LINE])
        assert table[MAC] == IP
        assert len(table) == 1


class TestResolveIpByMac:
    def test_found(self):
        table = {MAC: IP}
        assert resolve_ip_by_mac(MAC, table) == IP

    def test_found_different_case_and_separator(self):
        table = {MAC: IP}
        assert resolve_ip_by_mac("24-DA-9B-14-A4-CA", table) == IP

    def test_not_found(self):
        assert resolve_ip_by_mac("ff:ff:ff:ff:ff:ff", {MAC: IP}) is None


class TestIsDeviceOnline:
    def test_no_arp_entry_is_offline_without_pinging(self):
        with patch("app.device_presence._ping") as mock_ping:
            online, ip = is_device_online("ff:ff:ff:ff:ff:ff", arp_table={MAC: IP})
        assert online is False
        assert ip is None
        mock_ping.assert_not_called()

    def test_arp_entry_but_ping_fails_is_offline(self):
        # Entrada ARP vieja/cacheada: el dispositivo ya no está aunque el SO
        # todavía lo recuerde.
        with patch("app.device_presence._ping", return_value=False) as mock_ping:
            online, ip = is_device_online(MAC, arp_table={MAC: IP})
        assert online is False
        assert ip == IP
        mock_ping.assert_called_once()

    def test_arp_entry_and_ping_succeeds_is_online(self):
        with patch("app.device_presence._ping", return_value=True):
            online, ip = is_device_online(MAC, arp_table={MAC: IP})
        assert online is True
        assert ip == IP


class TestIsDeviceOnlineFallbackIp:
    """Un dispositivo puede estar prendido y conectado sin que ESTE host en
    particular tenga trafico ARP reciente con el (confirmado en produccion:
    MACs ausentes de `ip neigh show` para dispositivos que si respondian).
    El fallback a la ultima IP conocida cubre ese caso."""

    FALLBACK_IP = "192.168.100.66"

    def test_no_arp_entry_but_fallback_ip_responds_is_online(self):
        with patch("app.device_presence._ping", return_value=True) as mock_ping:
            online, ip = is_device_online(MAC, arp_table={}, fallback_ip=self.FALLBACK_IP)
        assert online is True
        assert ip == self.FALLBACK_IP
        mock_ping.assert_called_once_with(self.FALLBACK_IP, 1.0)

    def test_no_arp_entry_and_fallback_ip_does_not_respond_is_offline(self):
        # Probablemente cambio de IP (DHCP) o esta realmente apagado -- sin
        # mas pistas, offline es la respuesta correcta.
        with patch("app.device_presence._ping", return_value=False):
            online, ip = is_device_online(MAC, arp_table={}, fallback_ip=self.FALLBACK_IP)
        assert online is False
        assert ip == self.FALLBACK_IP

    def test_no_arp_entry_and_no_fallback_ip_is_offline_without_pinging(self):
        with patch("app.device_presence._ping") as mock_ping:
            online, ip = is_device_online(MAC, arp_table={}, fallback_ip=None)
        assert online is False
        assert ip is None
        mock_ping.assert_not_called()

    def test_arp_entry_takes_priority_over_fallback_ip(self):
        # Si el kernel SI tiene una entrada ARP fresca, es mas confiable que
        # la ultima IP recordada (que podria estar desactualizada).
        with patch("app.device_presence._ping", return_value=True) as mock_ping:
            online, ip = is_device_online(MAC, arp_table={MAC: IP}, fallback_ip="192.168.100.200")
        assert online is True
        assert ip == IP
        mock_ping.assert_called_once_with(IP, 1.0)


class TestCheckAllPersistsLastIp:
    """check_all persiste la ultima IP confirmada en disco (ver
    config.DEVICE_LAST_IP_FILE) para que sobreviva un reinicio del backend
    -- sin esto, cada redeploy del timer de auto-actualizacion perderia el
    fallback de is_device_online."""

    @pytest.fixture(autouse=True)
    def _isolate_state(self, tmp_path, monkeypatch):
        # Cada test arranca con el estado en memoria vacio y su propio
        # archivo temporal, para no pisarse entre tests ni con el archivo
        # real del proyecto.
        monkeypatch.setattr(device_presence.config, "DEVICE_LAST_IP_FILE", tmp_path / "last_ip.json")
        monkeypatch.setattr(device_presence, "_last_ip_state", {})
        monkeypatch.setattr(device_presence, "_last_ip_loaded", True)

    @pytest.mark.asyncio
    async def test_online_via_fallback_ip_when_already_known(self, monkeypatch):
        # La IP ya estaba persistida de un ciclo anterior: no hay ARP fresca,
        # pero el ping directo a la ultima IP conocida la confirma igual.
        devices = [{"id": "g3", "name": "G3", "mac": MAC}]
        monkeypatch.setattr(device_presence, "get_arp_table", lambda: {})
        monkeypatch.setattr(device_presence, "_last_ip_state", {"g3": IP})
        monkeypatch.setattr(device_presence, "_ping", lambda ip, timeout: True)

        results = await check_all(devices)

        assert results == [{"id": "g3", "name": "G3", "status": "online", "ip": IP, "mac": MAC}]
        # El valor no cambio respecto al ya conocido -> no hace falta
        # reescribir el archivo en cada ciclo (ver test siguiente para el
        # caso en que si cambia).
        assert not device_presence.config.DEVICE_LAST_IP_FILE.exists()

    @pytest.mark.asyncio
    async def test_first_time_seen_via_arp_gets_persisted(self, monkeypatch):
        # Primera vez que se ve a este dispositivo (nunca hubo IP previa):
        # la ARP ya lo resuelve directamente, y esa IP queda persistida.
        devices = [{"id": "g3", "name": "G3", "mac": MAC}]
        monkeypatch.setattr(device_presence, "get_arp_table", lambda: {MAC: IP})
        monkeypatch.setattr(device_presence, "_last_ip_state", {})
        monkeypatch.setattr(device_presence, "_ping", lambda ip, timeout: True)

        results = await check_all(devices)

        assert results == [{"id": "g3", "name": "G3", "status": "online", "ip": IP, "mac": MAC}]
        saved = json.loads(device_presence.config.DEVICE_LAST_IP_FILE.read_text())
        assert saved == {"g3": IP}

    @pytest.mark.asyncio
    async def test_new_ip_overwrites_stale_saved_one(self, monkeypatch):
        new_ip = "192.168.100.200"
        devices = [{"id": "g3", "name": "G3", "mac": MAC}]
        monkeypatch.setattr(device_presence, "get_arp_table", lambda: {MAC: new_ip})
        monkeypatch.setattr(device_presence, "_last_ip_state", {"g3": IP})
        monkeypatch.setattr(device_presence, "_ping", lambda ip, timeout: True)

        results = await check_all(devices)

        assert results[0]["ip"] == new_ip
        saved = json.loads(device_presence.config.DEVICE_LAST_IP_FILE.read_text())
        assert saved == {"g3": new_ip}

    @pytest.mark.asyncio
    async def test_offline_device_does_not_write_to_disk(self, monkeypatch, tmp_path):
        devices = [{"id": "g3", "name": "G3", "mac": MAC}]
        monkeypatch.setattr(device_presence, "get_arp_table", lambda: {})
        monkeypatch.setattr(device_presence, "_last_ip_state", {})
        monkeypatch.setattr(device_presence, "_ping", lambda ip, timeout: True)

        await check_all(devices)

        assert not (tmp_path / "last_ip.json").exists()
