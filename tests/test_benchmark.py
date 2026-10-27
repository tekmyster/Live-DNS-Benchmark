import csv
from pathlib import Path
import socket
import struct
import threading
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import dns.message
import dns.rcode
import dns.rrset
import pytest

from dns_benchmark.cli import build_parser, load_inputs, main
from dns_benchmark.core import (Measurement, parse_lines, positive_float, query,
                                summarize, validate_domains, validate_servers)
from dns_benchmark.export import worksheet_names, write_excel


@pytest.fixture
def local_resolver():
    """One local UDP/TCP DNS responder; no public DNS or user settings needed."""
    sockets = []
    threads = []

    def start(tcp=False, rcode=dns.rcode.NOERROR, no_answer=False):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(2)
        if tcp:
            server.listen(1)
        sockets.append(server)

        def receive_exact(connection, count):
            result = b""
            while len(result) < count:
                piece = connection.recv(count - len(result))
                if not piece:
                    raise EOFError("DNS connection closed before the complete message")
                result += piece
            return result

        def response(wire):
            request = dns.message.from_wire(wire)
            reply = dns.message.make_response(request)
            reply.set_rcode(rcode)
            if rcode == dns.rcode.NOERROR and not no_answer:
                question = request.question[0]
                record_type = dns.rdatatype.to_text(question.rdtype)
                value = "2001:db8::1" if record_type == "AAAA" else "192.0.2.1"
                reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", record_type, value))
            return reply.to_wire()

        def serve():
            if tcp:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(2)
                    length = struct.unpack("!H", receive_exact(connection, 2))[0]
                    wire = response(receive_exact(connection, length))
                    connection.sendall(struct.pack("!H", len(wire)) + wire)
            else:
                wire, address = server.recvfrom(65535)
                server.sendto(response(wire), address)

        worker = threading.Thread(target=serve)
        threads.append(worker)
        worker.start()
        return server.getsockname()[1]

    yield start
    for worker in threads:
        worker.join(timeout=3)
    for server in sockets:
        server.close()


@pytest.mark.parametrize("tcp,record_type,expected", [(False, "A", "192.0.2.1"),
                                                    (True, "AAAA", "2001:db8::1")])
def test_real_dns_query(local_resolver, tcp, record_type, expected):
    port = local_resolver(tcp=tcp)
    result = query("127.0.0.1", "example.test", record_type, timeout=1, tcp=tcp, port=port)
    assert result.status == "OK"
    assert result.answer == expected
    assert result.latency_ms >= 0


@pytest.mark.parametrize("rcode,no_answer,status", [(dns.rcode.NXDOMAIN, False, "NXDOMAIN"),
                                                    (dns.rcode.NOERROR, True, "NOANSWER"),
                                                    (dns.rcode.SERVFAIL, False, "ERROR")])
def test_dns_failures_preserve_status(local_resolver, rcode, no_answer, status):
    port = local_resolver(rcode=rcode, no_answer=no_answer)
    result = query("127.0.0.1", "example.test", timeout=0.1, port=port)
    assert result.status == status
    assert result.latency_ms is None
    assert result.answer


def test_timeout():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent:
        silent.bind(("127.0.0.1", 0))
        result = query("127.0.0.1", "example.test", timeout=0.02, port=silent.getsockname()[1])
    assert result.status == "TIMEOUT"
    assert result.latency_ms is None


def test_statistics_exclude_failures_but_report_reliability():
    samples = [Measurement(value, "OK", "") for value in (0, 10, 20)]
    samples.append(Measurement(None, "TIMEOUT", ""))
    stats = summarize(samples)
    assert stats["attempts"] == 4
    assert stats["failure_pct"] == 25
    assert stats["median_ms"] == 10
    assert stats["avg_ms"] == 10
    assert stats["p95_ms"] == pytest.approx(19)
    assert summarize([samples[-1]])["avg_ms"] is None
    assert summarize([])["attempts"] == 0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_intervals(value):
    with pytest.raises(ValueError):
        positive_float(value)


def test_lists_and_domain_validation():
    assert parse_lines("# comment\n1.1.1.1 # primary\n1.1.1.1\n") == ["1.1.1.1"]
    assert validate_domains(["Example.COM", "example.com."]) == ["example.com"]
    assert validate_servers(["2001:0db8::1", "2001:db8::1"]) == ["2001:db8::1"]
    with pytest.raises(ValueError):
        validate_domains(["https://example.com/path"])
    with pytest.raises(ValueError):
        validate_servers(["999.0.0.1"])


def test_explicit_domain_overrides_default_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("servers.ls").write_text("wrong.example\n", encoding="utf-8")
    args = build_parser().parse_args(["-d", "example.com", "-s", "127.0.0.1"])
    assert load_inputs(args) == (["example.com"], ["127.0.0.1"])


def test_csv_retains_failures_and_all_server_samples(tmp_path):
    output = tmp_path / "samples.csv"
    with patch("dns_benchmark.cli.query", side_effect=[Measurement(3, "OK", "192.0.2.1"),
                                                      Measurement(None, "TIMEOUT", "timed out")]):
        assert main(["-s", "127.0.0.1", "-s", "127.0.0.2", "-d", "example.test",
                     "-n", "1", "--csv", str(output)]) == 0
    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert {row["resolver"] for row in rows} == {"127.0.0.1", "127.0.0.2"}
    failed = next(row for row in rows if row["status"] == "TIMEOUT")
    assert failed["latency_ms"] == ""
    assert failed["answer"] == "timed out"


def test_sheet_names_handle_collisions_and_reserved_characters():
    names = worksheet_names(["x" * 40 + ".a", "x" * 40 + ".b", "History", "'a:b'"])
    assert len(set(name.casefold() for name in names.values())) == 4
    assert all(len(name) <= 31 for name in names.values())
    assert names["'a:b'"] == "a_b"
    assert names["History"] != "History"


def test_excel_has_gap_for_failure_and_unique_sheets(tmp_path):
    domains = ["x" * 40 + ".a", "x" * 40 + ".b"]
    path = tmp_path / "output.xlsx"
    write_excel(path, {domain: [[1, "timestamp", 3], [2, "timestamp", None]]
                       for domain in domains}, domains, ["127.0.0.1"])
    with zipfile.ZipFile(path) as workbook:
        root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert root.find(".//s:c[@r='C2']/s:v", ns).text == "3"
        assert root.find(".//s:c[@r='C3']/s:v", ns) is None
        assert "xl/worksheets/sheet2.xml" in workbook.namelist()


def test_export_failure_preserves_previous_workbook(tmp_path):
    path = tmp_path / "existing.xlsx"
    path.write_bytes(b"previous workbook")
    with patch("dns_benchmark.export.os.replace", side_effect=PermissionError("file open in Excel")):
        with pytest.raises(PermissionError):
            write_excel(path, {"example.test": []}, ["example.test"], ["127.0.0.1"])
    assert path.read_bytes() == b"previous workbook"
    assert list(tmp_path.iterdir()) == [path]
