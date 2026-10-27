"""Desktop integration regressions; skipped when GUI dependencies/display are absent."""

import importlib.util
from pathlib import Path
import threading
import math

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("ping3")
pytest.importorskip("tkinter")


@pytest.fixture
def gui(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("benchmark_gui", Path(__file__).resolve().parents[1] /
                                                "GUI" / "live_dns_benchmark_GUI.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LIVE_EXCEL_FILE", str(tmp_path / "live.xlsx"))
    monkeypatch.setattr(module, "DEFAULT_FQDN_FILE", str(tmp_path / "domains.ls"))
    monkeypatch.setattr(module, "DEFAULT_DNS_FILE", str(tmp_path / "dns.ls"))
    try:
        app = module.DNSBenchmarkGUI()
    except module.tk.TclError as exc:
        pytest.skip(f"No desktop display: {exc}")
    app.withdraw()
    app.fqdn_text.delete("1.0", "end")
    app.fqdn_text.insert("1.0", "example.test")
    app.dns_text.delete("1.0", "end")
    app.dns_text.insert("1.0", "127.0.0.1")
    app.iterations_var.set(1)
    monkeypatch.setattr(module, "get_ping_rtt", lambda server: None)
    yield module, app
    module.stop_event.set()
    if app.benchmark_thread:
        app.benchmark_thread.join(timeout=3)
        assert not app.benchmark_thread.is_alive()
    if app.winfo_exists():
        app.destroy()


def finish(app):
    app.benchmark_thread.join(timeout=3)
    assert not app.benchmark_thread.is_alive()
    app.update_gui()


def test_gui_finite_run_restores_start_and_retains_exports(gui, monkeypatch):
    module, app = gui
    monkeypatch.setattr(module, "query", lambda *args: module.Measurement(5, "OK", "192.0.2.1"))
    app.start_benchmark()
    finish(app)
    assert str(app.start_button["state"]) == "normal"
    assert str(app.stop_button["state"]) == "disabled"
    assert str(app.save_excel_button["state"]) == "normal"
    assert "Complete" in app.status_var.get()
    assert module.excel_data["example.test"][0][2:] == [5, "192.0.2.1", None]
    assert Path(module.LIVE_EXCEL_FILE).is_file()


def test_stop_blocks_restart_until_original_worker_exits(gui, monkeypatch):
    module, app = gui
    entered, release = threading.Event(), threading.Event()

    def slow_query(*args):
        entered.set()
        assert release.wait(timeout=2)
        return module.Measurement(5, "OK", "192.0.2.1")

    monkeypatch.setattr(module, "query", slow_query)
    app.iterations_var.set(0)
    app.interval_var.set(30)
    app.start_benchmark()
    assert entered.wait(timeout=1)
    original = app.benchmark_thread
    app.stop_benchmark()
    assert str(app.start_button["state"]) == "disabled"
    app.start_benchmark()
    assert app.benchmark_thread is original
    release.set()
    finish(app)
    assert "Stopped" in app.status_var.get()
    assert module.iteration_count == 1
    assert str(app.start_button["state"]) == "normal"


def test_failed_answer_does_not_invent_addresses_or_zero_latency(gui, monkeypatch):
    module, app = gui
    monkeypatch.setattr(module, "query", lambda *args:
                        module.Measurement(None, "TIMEOUT", "resolver 127.0.0.1 timed out"))
    app.start_benchmark()
    finish(app)
    sample = module.results_history["example.test"]["127.0.0.1"][0]
    assert sample[0] is None and sample[2] == set()
    assert module.excel_data["example.test"][0][2:] == [None, "", None]
    tree = app.domain_tabs["example.test"]
    values = tree.item("127.0.0.1", "values")
    assert "TIMEOUT" in values[1]
    assert values[-2] == "Failed"
    assert values[-3] == "100.0"


def test_input_error_preserves_previous_run(gui, monkeypatch):
    module, app = gui
    monkeypatch.setattr(module, "query", lambda *args: module.Measurement(5, "OK", "192.0.2.1"))
    errors = []
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args: errors.append(args))
    app.start_benchmark()
    finish(app)
    original = app.benchmark_thread
    app.timeout_var.set("nan")
    app.start_benchmark()
    assert errors
    assert app.benchmark_thread is original
    assert len(module.results_history["example.test"]["127.0.0.1"]) == 1


def test_excel_lock_does_not_abort_measurement(gui, monkeypatch):
    module, app = gui
    monkeypatch.setattr(module, "query", lambda *args: module.Measurement(5, "OK", "192.0.2.1"))

    def locked(*args):
        raise PermissionError("workbook open")

    monkeypatch.setattr(module, "write_live_excel", locked)
    app.iterations_var.set(2)
    app.interval_var.set(0.01)
    app.start_benchmark()
    finish(app)
    assert module.iteration_count == 2
    assert module.benchmark_error is None
    assert "Live Excel unavailable" in app.status_var.get()


def test_gui_query_controls_reach_shared_engine(gui, monkeypatch):
    module, app = gui
    requests = []

    def capture(*args):
        requests.append(args)
        return module.Measurement(3, "OK", "2001:db8::1")

    monkeypatch.setattr(module, "query", capture)
    app.record_var.set("AAAA")
    app.timeout_var.set("0.75")
    app.tcp_var.set(True)
    app.start_benchmark()
    finish(app)
    assert requests == [("127.0.0.1", "example.test", "AAAA", 0.75, True)]
    app.open_live_graph_window()
    line = app.ax.lines[0]
    assert list(line.get_ydata()) == [3]


def test_chart_keeps_failed_samples_as_gaps(gui, monkeypatch):
    module, app = gui
    monkeypatch.setattr(module, "query", lambda *args: module.Measurement(3, "OK", "192.0.2.1"))
    app.start_benchmark()
    finish(app)
    module.results_history["example.test"]["127.0.0.1"].extend([
        (None, "TIMEOUT", set(), None), (7, "192.0.2.1", {"192.0.2.1"}, None)])
    app.open_live_graph_window()
    values = list(app.ax.lines[0].get_ydata())
    assert values[0] == 3 and math.isnan(values[1]) and values[2] == 7


def test_invalid_saved_list_preserves_file(gui, monkeypatch):
    module, app = gui
    path = Path(module.DEFAULT_DNS_FILE)
    path.write_text("127.0.0.1\n", encoding="utf-8")
    app.dns_text.delete("1.0", "end")
    app.dns_text.insert("1.0", "invalid IP")
    errors = []
    monkeypatch.setattr(module.messagebox, "showerror", lambda *args: errors.append(args))
    app.save_dns()
    assert errors and path.read_text(encoding="utf-8") == "127.0.0.1\n"
