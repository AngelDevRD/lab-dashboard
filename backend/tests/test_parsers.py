"""Tests para parsers.py — edge cases y except vacíos."""

from app.collectors.parsers import (
    parse_uptime,
    parse_cpu_temp,
    parse_cpu_power_rapl,
    parse_top_procs,
    parse_battery,
    parse_updates,
    parse_ping,
    parse_docker_disk,
    parse_disk_temp,
    parse_disk,
    parse_mem,
    parse_loadavg,
    parse_docker,
    parse_services,
    parse_cpu_freq,
    parse_disk_io,
    parse_smart_health,
    parse_docker_stats,
    parse_ping_extended,
    parse_tailscale_ip,
    parse_system_info,
    parse_clock,
)


class TestParseUptime:
    def test_empty(self):
        assert parse_uptime("") == {"pretty": "N/A", "seconds": 0.0}

    def test_malformed_seconds(self):
        raw = "up 1 hour\nnotanumber junk"
        result = parse_uptime(raw)
        assert result["pretty"] == "up 1 hour"
        assert result["seconds"] == 0.0


class TestParseCpuTemp:
    def test_empty(self):
        assert parse_cpu_temp("") == {"value": None, "per_core": [], "amd_package_power_w": None}

    def test_malformed_json(self):
        assert parse_cpu_temp("{bad") == {"value": None, "per_core": [], "amd_package_power_w": None}

    def test_plain_numbers(self):
        raw = "55000\n62000\n"
        result = parse_cpu_temp(raw)
        assert result["value"] == 55.0
        assert result["per_core"] == [55.0, 62.0]

    def test_ignores_non_temperature_sensors_before_coretemp(self):
        """Regresion: en angel1/angel2 (auditoria de precision) `sensors -j`
        lista el chip de bateria (voltaje, no temperatura) o el chip WiFi
        *antes* que coretemp en el JSON. La version vieja tomaba "el primer
        *_input que aparezca" y devolvia 40.6C (voltaje de bateria) en vez
        de los ~49C reales de los nucleos."""
        raw = """
        {
          "BAT1-acpi-0": {"Adapter": "ACPI interface", "in0": {"in0_input": 7.895}},
          "coretemp-isa-0000": {
            "Adapter": "ISA adapter",
            "Package id 0": {"temp1_input": 49.0},
            "Core 0": {"temp2_input": 49.0},
            "Core 1": {"temp3_input": 49.0},
            "Core 2": {"temp4_input": 49.0},
            "Core 3": {"temp5_input": 48.0}
          },
          "acpitz-acpi-0": {"Adapter": "ACPI interface", "temp1": {"temp1_input": 49.0}}
        }
        """
        result = parse_cpu_temp(raw)
        assert result["value"] == 49.0  # Package id, no el voltaje de BAT1
        assert result["per_core"] == [49.0, 49.0, 49.0, 48.0]

    def test_prefers_package_over_core_average(self):
        raw = """
        {"coretemp-isa-0000": {
          "Package id 0": {"temp1_input": 42.0},
          "Core 0": {"temp2_input": 40.0},
          "Core 1": {"temp3_input": 44.0}
        }}
        """
        result = parse_cpu_temp(raw)
        assert result["value"] == 42.0
        assert result["per_core"] == [40.0, 44.0]

    def test_no_coretemp_falls_back_to_none(self):
        raw = '{"BAT0-acpi-0": {"Adapter": "ACPI interface", "in0": {"in0_input": 7.7}}}'
        assert parse_cpu_temp(raw) == {"value": None, "per_core": [], "amd_package_power_w": None}

    def test_amd_k10temp_label(self):
        """AMD (driver k10temp) no tiene etiquetas 'Package'/'Core N' como
        Intel -- un solo 'temp1' generico, identificado por el nombre del
        chip. Ver auditoria .6."""
        raw = '{"k10temp-pci-00c3": {"Adapter": "PCI adapter", "temp1": {"temp1_input": 46.6}}}'
        result = parse_cpu_temp(raw)
        assert result["value"] == 46.6
        assert result["per_core"] == []

    def test_amd_package_power_extracted(self):
        raw = """
        {"fam15h_power-pci-00c4": {"Adapter": "PCI adapter", "power1": {"power1_average": 7.27}},
         "k10temp-pci-00c3": {"Adapter": "PCI adapter", "temp1": {"temp1_input": 46.6}}}
        """
        result = parse_cpu_temp(raw)
        assert result["amd_package_power_w"] == 7.27

    def test_amd_package_power_negative_treated_as_invalid(self):
        """El sensor a veces da un valor negativo espurio en reposo -- no
        existe consumo negativo, se descarta en vez de mostrarse."""
        raw = """
        {"fam15h_power-pci-00c4": {"Adapter": "PCI adapter", "power1": {"power1_average": -0.5}}}
        """
        result = parse_cpu_temp(raw)
        assert result["amd_package_power_w"] is None


class TestParseCpuPowerRapl:
    def test_empty_no_rapl(self):
        assert parse_cpu_power_rapl("") is None

    def test_computes_watts_from_two_samples(self):
        # 1_000_000 uJ de delta en ~1s == 1W
        assert parse_cpu_power_rapl("1000000 2000000") == 1.0

    def test_counter_wraparound_returns_none(self):
        assert parse_cpu_power_rapl("2000000 1000000") is None

    def test_malformed_returns_none(self):
        assert parse_cpu_power_rapl("not a number") is None


class TestParseTopProcs:
    def test_empty(self):
        assert parse_top_procs("") == []

    def test_malformed_line(self):
        raw = "abc name 1.5\nonlytwo\n123 name notanumber\n456 name 2.5"
        result = parse_top_procs(raw)
        assert len(result) == 1
        assert result[0]["pid"] == 456

    def test_valid_line(self):
        result = parse_top_procs("1234 bash 0.5\n5678 python 12.3")
        assert len(result) == 2
        assert result[0] == {"pid": 1234, "name": "bash", "value": 0.5}


class TestParseBattery:
    def test_empty(self):
        assert parse_battery("") == {"available": False}

    def test_missing_parts(self):
        assert parse_battery("onlyone") == {"available": False}

    def test_non_digit_voltage(self):
        result = parse_battery("80|Charging|notanumber")
        assert result["available"] is True
        assert result["voltage"] is None
        assert result["percent"] == 80

    def test_energy_fallback_uses_filtered_voltage_not_instant_spike(self):
        """charge_now*V es el unico camino a energy_now cuando el firmware no
        expone energy_now (caso angel1). Una caida transitoria de voltaje
        (pico de IR bajo carga de CPU) no debe pegarle de lleno a energy_now
        -- ver comentario en parsers.parse_battery."""
        host = "test-host-energy-fallback"
        # parts: capacity|status|voltage|energy_now|energy_full|power_now|
        #        time_to_empty|time_to_full|charge_now|charge_full|current_now
        stable_line = "80|Charging|8500000||||||2500000|2900000|"
        for _ in range(20):
            parse_battery(stable_line, host)
        # Voltaje cae de golpe una sola muestra (pico de IR transitorio).
        spike_line = "80|Charging|8000000||||||2500000|2900000|"
        result = parse_battery(spike_line, host)
        # energy_now = charge_now * voltaje_filtrado -- el voltaje filtrado
        # todavia no llego a 8.0V tras un solo pico (EMA alpha=0.1), asi que
        # energy_now no debe caer tanto como si hubiera usado el voltaje crudo.
        raw_voltage_energy = 2500000 * 8_000_000 / 1_000_000 / 1_000_000
        assert result["energy_now_wh"] > raw_voltage_energy

    def test_power_now_stays_instantaneous(self):
        """power_now (a diferencia de energy_now) debe seguir usando el
        voltaje crudo de la muestra -- tiene que reaccionar al instante."""
        host = "test-host-power-instant"
        for _ in range(20):
            parse_battery("80|Charging|8500000||||||||1000000", host)
        result = parse_battery("80|Charging|8000000||||||||1000000", host)
        assert result["power_now_w"] == round(1_000_000 * 8_000_000 / 1_000_000 / 1_000_000, 2)


class TestParseUpdates:
    def test_empty(self):
        assert parse_updates("") == 0

    def test_invalid(self):
        assert parse_updates("notanumber") == 0

    def test_valid(self):
        assert parse_updates("5") == 5


class TestParsePing:
    def test_no_match(self):
        assert parse_ping("destination unreachable") is None

    def test_valid(self):
        assert parse_ping("time=12.3 ms") == 12.3


class TestParseDockerDisk:
    def test_empty(self):
        assert parse_docker_disk("") is None

    def test_no_docker(self):
        assert parse_docker_disk("__NO_DOCKER__") is None

    def test_malformed_line(self):
        result = parse_docker_disk("image|")
        assert result == {"total_bytes": 0}

    def test_with_data(self):
        raw = "image1|500 MB\nimage2|1.5 GB"
        result = parse_docker_disk(raw)
        assert result is not None
        assert result["total_bytes"] == 500 * 1000**2 + 1.5 * 1000**3


class TestParseDiskTemp:
    def test_no_match(self):
        assert parse_disk_temp("no numbers here") is None

    def test_valid(self):
        assert parse_disk_temp("30") == 30.0


class TestParseDisk:
    def test_empty(self):
        assert parse_disk("") == {"total": 0, "used": 0, "free": 0, "percent": 0.0}

    def test_short_line(self):
        assert parse_disk("100") == {"total": 0, "used": 0, "free": 0, "percent": 0.0}


class TestParseMem:
    def test_empty(self):
        result = parse_mem("")
        assert result["total"] == 0

    def test_valid(self):
        raw = "MemTotal: 8000000\nMemFree: 2000000\nMemAvailable: 4000000\nSwapTotal: 1000000\nSwapFree: 500000"
        result = parse_mem(raw)
        assert result["total"] == 8000000 * 1024
        assert result["percent"] > 0


class TestParseLoadavg:
    def test_empty(self):
        assert parse_loadavg("") == {"load1": 0.0, "load5": 0.0, "load15": 0.0}

    def test_valid(self):
        assert parse_loadavg("1.5 2.3 3.1") == {"load1": 1.5, "load5": 2.3, "load15": 3.1}


class TestParseDocker:
    def test_empty(self):
        assert parse_docker("")["available"] is False

    def test_no_docker(self):
        assert parse_docker("__NO_DOCKER__")["available"] is False

    def test_malformed_line(self):
        result = parse_docker("name|state")
        assert result["available"] is True
        assert len(result["containers"]) == 0

    def test_zero_containers_still_available(self):
        # docker instalado y corriendo pero sin contenedores desplegados:
        # el marcador __DOCKER_OK__ es lo unico que distingue esto de "sin docker".
        result = parse_docker("__DOCKER_OK__")
        assert result["available"] is True
        assert result["running"] == 0
        assert result["containers"] == []

    def test_valid_containers(self):
        raw = "web|running|Up 2h\ndb|exited|Exited 0\n__DOCKER_OK__"
        result = parse_docker(raw)
        assert result["available"] is True
        assert result["running"] == 1
        assert result["stopped"] == 1


class TestParseServices:
    def test_missing_lines(self):
        result = parse_services("active", ["nginx", "ssh"])
        assert result == {"nginx": "active", "ssh": "unknown"}

    def test_valid(self):
        assert parse_services("active\ninactive\n", ["nginx", "ssh"]) == {
            "nginx": "active",
            "ssh": "inactive",
        }


class TestParseCpuFreq:
    def test_empty(self):
        assert parse_cpu_freq("") == {"current_mhz": None, "max_mhz": None}

    def test_valid(self):
        assert parse_cpu_freq("1800\n3600000") == {"current_mhz": 1800, "max_mhz": 3600}


class TestParseDiskIo:
    def test_first_sample_has_no_rate(self):
        raw = "8 0 sda 1 2 1000 3 4 5 2000 6 0 0 0"
        assert parse_disk_io(raw, "host-disk-io-1", 1000.0) is None

    def test_second_sample_computes_rate(self):
        host = "host-disk-io-2"
        raw1 = "8 0 sda 1 2 1000 3 4 5 2000 6 0 0 0"
        raw2 = "8 0 sda 1 2 2000 3 4 5 4000 6 0 0 0"
        parse_disk_io(raw1, host, 1000.0)
        result = parse_disk_io(raw2, host, 1001.0)
        assert result == {"read_bps": 512000, "write_bps": 1024000}

    def test_ignores_partitions(self):
        raw = "8 0 sda 1 2 1000 3 4 5 2000 6 0 0 0\n8 1 sda1 1 2 999999 3 4 5 999999 6 0 0 0"
        assert parse_disk_io(raw, "host-disk-io-3", 1000.0) is None


class TestParseSmartHealth:
    def test_passed(self):
        raw = "SMART overall-health self-assessment test result: PASSED"
        assert parse_smart_health(raw) == "ok"

    def test_failed(self):
        raw = "SMART overall-health self-assessment test result: FAILED"
        assert parse_smart_health(raw) == "error"

    def test_permission_denied_is_not_a_failure(self):
        # Regression: this message contains the word "failed" but has nothing
        # to do with disk health — must not be reported as a SMART error.
        raw = "Smartctl open device: /dev/sda failed: Permission denied"
        assert parse_smart_health(raw) is None

    def test_empty(self):
        assert parse_smart_health("") is None


class TestParseDockerStats:
    def test_no_docker(self):
        assert parse_docker_stats("__NO_DOCKER__") is None
        assert parse_docker_stats("") is None

    def test_valid(self):
        raw = "1.50%|100MiB / 2GiB\n2.50%|50MiB / 2GiB"
        result = parse_docker_stats(raw)
        assert result["cpu_percent"] == 4.0
        assert result["mem_used_bytes"] == round(150 * 1024**2)


class TestParsePingExtended:
    def test_empty(self):
        assert parse_ping_extended("") == {"latency_ms": None, "loss_pct": None}

    def test_valid(self):
        raw = "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\nrtt min/avg/max/mdev = 10.1/12.3/14.5/1.2 ms"
        assert parse_ping_extended(raw) == {"latency_ms": 12.3, "loss_pct": 0.0}


class TestParseTailscaleIp:
    def test_empty(self):
        assert parse_tailscale_ip("") is None

    def test_valid(self):
        assert parse_tailscale_ip("100.70.1.38\n") == "100.70.1.38"


class TestParseSystemInfo:
    def test_empty(self):
        assert parse_system_info("") == {
            "os_pretty": None, "kernel": None, "arch": None, "boot_at": None,
        }

    def test_valid(self):
        raw = 'Ubuntu 24.04.4 LTS\n6.8.0-136-generic\nx86_64\n2026-07-24 10:30:00'
        result = parse_system_info(raw)
        assert result["os_pretty"] == "Ubuntu 24.04.4 LTS"
        assert result["kernel"] == "6.8.0-136-generic"
        assert result["arch"] == "x86_64"
        assert result["boot_at"] is not None


class TestParseClock:
    def test_synced(self):
        now = 1_800_000_000.0
        assert parse_clock(f"{now:.3f}", now) == 0.0

    def test_ahead(self):
        now = 1_800_000_000.0
        assert parse_clock(f"{now + 120:.3f}", now) == 120.0

    def test_malformed(self):
        assert parse_clock("not-a-number", 1_800_000_000.0) is None

    def test_empty(self):
        assert parse_clock("", 1_800_000_000.0) is None
