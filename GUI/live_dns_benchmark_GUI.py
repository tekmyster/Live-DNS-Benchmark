import os
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dns_benchmark.core import (query, system_dns_servers, parse_lines, positive_float,
                                validate_domains, validate_servers, Measurement, summarize)
from dns_benchmark.export import write_excel
# Force the script's directory to be the working directory.
if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import concurrent.futures
import ipaddress
from datetime import datetime
import math
import re

# For matplotlib integration.
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# For sparkline image generation.
from PIL import Image, ImageDraw, ImageTk

# Import ping3 for high-resolution ping RTT.
from ping3 import ping

##############################################
# SNMP Agent Imports (pysnmp)
##############################################
try:
    from pysnmp.entity import engine, config
    from pysnmp.carrier.asyncore.dgram import udp
    from pysnmp.entity.rfc3413 import cmdrsp
    from pysnmp.smi import builder, view, instrum, rfc1902
    import asyncore
    SNMP_AVAILABLE = True
except ImportError:
    # Optional legacy integration must not prevent DNS measurements or the UI.
    SNMP_AVAILABLE = False

##############################################
# Global Data and Variables
##############################################
results_history = {}  # { domain: { dns_ip: [ (dns_latency, result_text, ip_set, ping_rtt), ... ] } }
excel_data = {}       # { domain: [ [iteration, timestamp, <dns_latency>, <IP set>, <RTT>, ...], ... ] }
iteration_count = 0
stop_event = threading.Event()
current_interval = 5.0
query_timeout = 2.0
query_record_type = "A"
query_tcp = False
state_lock = threading.RLock()
benchmark_error = None
export_error = None

##############################################
# Constants
##############################################
HISTORY_LEN = 20
DEFAULT_DOMAIN = "google.com"  # fallback for servers.ls if empty
DEFAULT_FQDN_FILE = "servers.ls"
DEFAULT_DNS_FILE = "dns.ls"
LIVE_EXCEL_FILE = "dns_benchmark_live.xlsx"
SNMP_CONFIG_FILE = "snmp_config.json"

##############################################
# Utility / DNS Functions
##############################################
def parse_ip_set_from_result(result_text):
    if not result_text or "timed out" in result_text.lower() or "None" in result_text:
        return set()
    ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", result_text)
    return set(ips)

def get_system_dns_servers():
    return system_dns_servers()


def get_ping_rtt(server_ip):
    try:
        timeout_value = current_interval * 0.5 if current_interval < 2.0 else 1.0
        rtt = ping(server_ip, timeout=timeout_value, unit="ms")
        return None if rtt is False else rtt
    except Exception:
        return None

def resolve_domain(server_ip, domain):
    sample = query(server_ip, domain, query_record_type, query_timeout, query_tcp)
    # Extract addresses only from successful answers, never from exception text.
    addresses = set()
    if sample.status == "OK":
        for value in sample.answer.split(", "):
            try:
                addresses.add(str(ipaddress.ip_address(value)))
            except ValueError:
                pass
    result = sample.answer if sample.status == "OK" else f"{sample.status}: {sample.answer}"
    return sample.latency_ms, result, addresses, get_ping_rtt(server_ip)


def read_fqdn_list(file_path, default_domain):
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        print(f"File {abs_path} not found. Using default: {default_domain}")
        return [default_domain]
    with open(file_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    print("Loaded FQDNs from", abs_path, ":", lines)
    return lines if lines else [default_domain]

def read_dns_list(file_path):
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        print(f"File {abs_path} not found. Using system DNS servers.")
        dns_servers = get_system_dns_servers()
        if not dns_servers:
            dns_servers = ["8.8.8.8", "8.8.4.4"]
        with open(file_path, "w") as f:
            for server in dns_servers:
                f.write(server + "\n")
        return dns_servers
    with open(file_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        print(f"File {abs_path} is empty. Using system DNS servers.")
        dns_servers = get_system_dns_servers()
        if not dns_servers:
            dns_servers = ["8.8.8.8", "8.8.4.4"]
        with open(file_path, "w") as f:
            for server in dns_servers:
                f.write(server + "\n")
        return dns_servers
    print("Loaded DNS servers from", abs_path, ":", lines)
    return lines

##############################################
# Sparkline Generation
##############################################
def generate_sparkline(history, width=100, height=30):
    data = history[-HISTORY_LEN:]
    values = [sample[0] for sample in data if sample[0] is not None]
    low, high = (min(values), max(values)) if values else (0, 1)
    if high == low:
        low, high = low - 1, high + 1
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    previous = None
    for index, sample in enumerate(data):
        x = 3 + int(index * (width - 7) / max(1, len(data) - 1))
        if sample[0] is None:
            # A cross is a failure marker, not a zero-latency point.
            y = height // 2
            draw.line((x-2, y-2, x+2, y+2), fill="#a83b4b", width=2)
            draw.line((x-2, y+2, x+2, y-2), fill="#a83b4b", width=2)
            previous = None
            continue
        y = height - 4 - int((sample[0] - low) / (high - low) * (height - 8))
        point = (x, y)
        if previous is not None:
            draw.line((previous, point), fill="#176a9b", width=2)
        draw.ellipse((x-1, y-1, x+1, y+1), fill="#176a9b")
        if index and data[index-1][2] and sample[2] and data[index-1][2] != sample[2]:
            draw.rectangle((x-2, y-2, x+2, y+2), outline="#af601a")
        previous = point
    return ImageTk.PhotoImage(image)

##############################################
# Live Excel Writing Functionality
##############################################
def write_live_excel(live_file, excel_data, domains, servers_order):
    write_excel(live_file, excel_data, domains, servers_order, include_rtt=True)

##############################################
# Benchmarking Loop (Background Thread)
##############################################
def benchmarking_loop(domains, interval, iterations, dns_servers):
    global iteration_count, benchmark_error, export_error
    try:
        while (iterations == 0 or iteration_count < iterations) and not stop_event.is_set():
            now_str = datetime.now().astimezone().isoformat(timespec="seconds")
            round_results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = {executor.submit(resolve_domain, ip, domain): (domain, ip)
                           for domain in domains for ip in dns_servers}
                for future in concurrent.futures.as_completed(futures):
                    domain, ip = futures[future]
                    try:
                        round_results[(domain, ip)] = future.result()
                    except Exception as exc:
                        round_results[(domain, ip)] = (None, f"Query failed: {exc}", set(), None)
            with state_lock:
                iteration_count += 1
                for domain in domains:
                    row = [iteration_count, now_str]
                    for ip in dns_servers:
                        sample = round_results[(domain, ip)]
                        results_history[domain][ip].append(sample)
                        latency, _, addresses, rtt = sample
                        row.extend([latency, ", ".join(sorted(addresses)), rtt])
                    excel_data[domain].append(row)
                snapshot = {domain: [list(row) for row in rows] for domain, rows in excel_data.items()}
            try:
                write_live_excel(LIVE_EXCEL_FILE, snapshot, domains, dns_servers)
                export_error = None
            except Exception as exc:
                # Keep collecting samples if the destination is locked or unavailable.
                export_error = str(exc)
            if iterations and iteration_count >= iterations:
                break
            stop_event.wait(current_interval)
    except Exception as exc:
        benchmark_error = str(exc)

##############################################
# GUI Application
##############################################
class DNSBenchmarkGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Live DNS Benchmark")
        self.geometry("1200x760")
        self.minsize(780, 600)
        self.configure(background="#f4f6f8")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f4f6f8", foreground="#172b3a")
        style.configure("TButton", padding=(12, 7))
        style.configure("TEntry", fieldbackground="white", padding=5)
        style.configure("TLabelframe", padding=10)
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=32)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=(8, 9))
        style.map("Treeview", background=[("selected", "#174f78")], foreground=[("selected", "white")])
        style.configure("TNotebook.Tab", padding=(16, 8))
        self.status_var = tk.StringVar(value="Ready — add domains and resolvers, then start.")
        self.is_stopping = False
        self.is_closing = False
        self.run_domains = []
        self.run_servers = []
        header = ttk.Frame(self, padding=(18, 14))
        header.pack(fill=tk.X)
        ttk.Label(header, text="DNS Benchmark", font=("Segoe UI", 20, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="About measurements", command=self.show_measurement_help).pack(side=tk.RIGHT)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.sparkline_images = {}
        self.create_widgets()
        self.benchmark_thread = None
        self.graph_window = None
        print("Current working directory:", os.getcwd())
        self.after(1000, self.update_gui)

    def update_interval(self, *args):
        global current_interval
        try:
            current_interval = positive_float(self.interval_var.get())
        except Exception as e:
            print("Error updating interval:", e)

    def create_widgets(self):
        config_frame = ttk.LabelFrame(self, text="Benchmark settings")
        config_frame.pack(fill=tk.X, padx=18, pady=(0, 10))
        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(3, weight=1)

        ttk.Label(config_frame, text="Interval (s)").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.interval_var = tk.DoubleVar(value=5.0)
        self.interval_var.trace_add("write", self.update_interval)
        ttk.Entry(config_frame, textvariable=self.interval_var, width=8).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(config_frame, text="Rounds (0 = continuous)").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        self.iterations_var = tk.IntVar(value=0)
        ttk.Entry(config_frame, textvariable=self.iterations_var, width=8).grid(row=0, column=3, padx=5, pady=5)

        # FQDNs (servers.ls) Text Widget.
        fqdns_frame = ttk.LabelFrame(config_frame, text="Domains")
        fqdns_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.fqdn_text = tk.Text(fqdns_frame, height=4, width=32, font=("Segoe UI", 10), relief="solid", borderwidth=1, padx=8, pady=6, wrap="none")
        self.fqdn_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fqdn_scroll = ttk.Scrollbar(fqdns_frame, orient=tk.VERTICAL, command=self.fqdn_text.yview)
        self.fqdn_text.configure(yscrollcommand=fqdn_scroll.set)
        fqdn_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if os.path.exists(DEFAULT_FQDN_FILE):
            with open(DEFAULT_FQDN_FILE, "r") as f:
                self.fqdn_text.insert(tk.END, f.read())
        else:
            self.fqdn_text.insert(tk.END, DEFAULT_DOMAIN)
        self.save_fqdn_button = ttk.Button(fqdns_frame, text="Save domains", command=self.save_fqdns)
        self.save_fqdn_button.pack(pady=2)

        # DNS Servers (dns.ls) Text Widget.
        dns_frame = ttk.LabelFrame(config_frame, text="Resolvers")
        dns_frame.grid(row=1, column=2, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.dns_text = tk.Text(dns_frame, height=4, width=32, font=("Segoe UI", 10), relief="solid", borderwidth=1, padx=8, pady=6, wrap="none")
        self.dns_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dns_scroll = ttk.Scrollbar(dns_frame, orient=tk.VERTICAL, command=self.dns_text.yview)
        self.dns_text.configure(yscrollcommand=dns_scroll.set)
        dns_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if os.path.exists(DEFAULT_DNS_FILE):
            with open(DEFAULT_DNS_FILE, "r") as f:
                self.dns_text.insert(tk.END, f.read())
        else:
            dns_servers = get_system_dns_servers()
            if not dns_servers:
                dns_servers = ["8.8.8.8", "8.8.4.4"]
            self.dns_text.insert(tk.END, "\n".join(dns_servers))
        self.save_dns_button = ttk.Button(dns_frame, text="Save resolvers", command=self.save_dns)
        self.save_dns_button.pack(pady=2)

        query_frame = ttk.Frame(config_frame)
        query_frame.grid(row=2, column=0, columnspan=4, sticky="w", pady=6)
        ttk.Label(query_frame, text="Record").pack(side=tk.LEFT, padx=(0, 6))
        self.record_var = tk.StringVar(value="A")
        ttk.Combobox(query_frame, textvariable=self.record_var, state="readonly", width=7,
                     values=("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA")).pack(side=tk.LEFT)
        ttk.Label(query_frame, text="Timeout (s)").pack(side=tk.LEFT, padx=(18, 6))
        self.timeout_var = tk.StringVar(value="2")
        ttk.Entry(query_frame, textvariable=self.timeout_var, width=7).pack(side=tk.LEFT)
        self.tcp_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(query_frame, text="Use TCP", variable=self.tcp_var).pack(side=tk.LEFT, padx=18)
        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=3, column=0, columnspan=4, pady=5)
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_benchmark)
        self.start_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop", command=self.stop_benchmark, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=5)
        self.graph_button = ttk.Button(button_frame, text="Live chart", command=self.open_live_graph_window, state=tk.DISABLED)
        self.graph_button.grid(row=0, column=2, padx=5)
        self.save_excel_button = ttk.Button(button_frame, text="Export Excel…", command=self.save_excel, state=tk.DISABLED)
        self.save_excel_button.grid(row=0, column=3, padx=5)

        self.notebook = ttk.Notebook(self)
        self.status_label = ttk.Label(self, textvariable=self.status_var, padding=(18, 10), wraplength=1100)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        self.bind("<Configure>", self.resize_status, add="+")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))
        empty = ttk.Frame(self.notebook, padding=32)
        self.notebook.add(empty, text="Results")
        ttk.Label(empty, text="No measurements yet", font=("Segoe UI", 15, "bold")).pack(pady=(40, 8))
        ttk.Label(empty, text="Start a benchmark to compare resolver latency and reliability.").pack()
        self.domain_tabs = {}

    def resize_status(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(200, event.width - 40))

    def save_fqdns(self):
        try:
            domains = validate_domains(parse_lines(self.fqdn_text.get("1.0", tk.END)))
            if not domains:
                raise ValueError("Add at least one domain before saving.")
            Path(DEFAULT_FQDN_FILE).write_text("\n".join(domains) + "\n", encoding="utf-8")
            messagebox.showinfo("Saved", "Domain list saved.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Could not save domains", str(exc))

    def save_dns(self):
        try:
            servers = validate_servers(parse_lines(self.dns_text.get("1.0", tk.END)))
            if not servers:
                raise ValueError("Add at least one resolver before saving.")
            Path(DEFAULT_DNS_FILE).write_text("\n".join(servers) + "\n", encoding="utf-8")
            messagebox.showinfo("Saved", "Resolver list saved.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Could not save resolvers", str(exc))

    def start_benchmark(self):
        if self.benchmark_thread and self.benchmark_thread.is_alive():
            return
        try:
            interval = positive_float(self.interval_var.get())
            iterations = self.iterations_var.get()
            if iterations < 0:
                raise ValueError("Rounds must be zero or greater.")
            timeout = positive_float(self.timeout_var.get())
            fqdns_content = self.fqdn_text.get("1.0", tk.END).strip()
            domains = validate_domains(parse_lines(fqdns_content))
            if not domains:
                messagebox.showerror("Input Error", "Add at least one domain, such as example.com.")
                return
            dns_content = self.dns_text.get("1.0", tk.END).strip()
            dns_servers = validate_servers(parse_lines(dns_content))
            if not dns_servers:
                messagebox.showerror("Input Error", "No DNS servers provided.")
                return
        except Exception as e:
            messagebox.showerror("Input Error", str(e))
            return

        print("Starting benchmark in directory:", os.getcwd())
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.graph_button.config(state=tk.NORMAL)
        self.save_excel_button.config(state=tk.NORMAL)

        for tab in self.notebook.tabs():
            self.nametowidget(tab).destroy()
        self.domain_tabs.clear()

        global results_history, excel_data, iteration_count, current_interval
        global query_timeout, query_record_type, query_tcp, benchmark_error, export_error
        results_history = {domain: {ip: [] for ip in dns_servers} for domain in domains}
        excel_data = {domain: [] for domain in domains}
        iteration_count = 0
        current_interval = interval
        query_timeout = timeout
        query_record_type = self.record_var.get()
        query_tcp = self.tcp_var.get()
        benchmark_error = export_error = None
        self.run_domains, self.run_servers = domains, dns_servers
        self.is_stopping = False
        self.status_var.set("Running — waiting for the first round.")
        stop_event.clear()
        self.sparkline_images = {}

        for domain in domains:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=domain)
            hscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL)
            vscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL)
            columns = ("Resolver", "Result", "Min ms", "Max ms", "Avg ms", "P95 ms", "Fail %", "Latest ms", "Ping ms")
            tree = ttk.Treeview(frame, columns=columns, show="tree headings",
                                xscrollcommand=hscroll.set, yscrollcommand=vscroll.set)
            tree.heading("#0", text="Last 20")
            tree.column("#0", width=112, minwidth=112, stretch=False, anchor="center")
            for col in columns:
                tree.heading(col, text=col)
                tree.column(col, width=80, minwidth=70, anchor="e", stretch=False)
            tree.column("Resolver", width=150, anchor="w", stretch=True)
            tree.column("Result", width=200, anchor="w", stretch=True)
            tree.tag_configure("even", background="#f1f5f8")
            tree.bind("<Double-1>", lambda event, name=domain: self.show_result_details(name))
            tree.bind("<Return>", lambda event, name=domain: self.show_result_details(name))
            hscroll.config(command=tree.xview)
            vscroll.config(command=tree.yview)
            hscroll.pack(side=tk.BOTTOM, fill=tk.X)
            vscroll.pack(side=tk.RIGHT, fill=tk.Y)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.domain_tabs[domain] = tree

        self.benchmark_thread = threading.Thread(
            target=benchmarking_loop,
            args=(domains, interval, iterations, dns_servers),
            daemon=True
        )
        self.benchmark_thread.start()

    def stop_benchmark(self):
        stop_event.set()
        self.is_stopping = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Stopping after the current round…")

    def show_measurement_help(self):
        snmp_note = "Legacy SNMP is available but has not been validated." if SNMP_AVAILABLE else "SNMP monitoring is unavailable in this environment."
        messagebox.showinfo("About measurements",
            "Latency measures DNS resolution. Ping is a separate ICMP measurement; some resolvers block it.\n\n"
            "Statistics cover the full run. Failed queries count toward failure rate and appear as gaps in the chart and Excel. "
            "The interval is the wait between completed rounds. Resolver caches may affect results.\n\n"
            "Double-click a result or press Enter for the full response. Query settings apply to the next run. "
            "Interval changes take effect between rounds. Red crosses mark failed queries; amber squares mark changed addresses.\n\n" + snmp_note +
            ("\n\nLast run issue: " + benchmark_error if benchmark_error else "") +
            ("\n\nLast export issue: " + export_error if export_error else ""))

    def show_result_details(self, domain):
        tree = self.domain_tabs[domain]
        selected = tree.selection()
        if not selected:
            return
        ip = selected[0]
        with state_lock:
            hist = list(results_history.get(domain, {}).get(ip, []))
        if hist:
            messagebox.showinfo("DNS response", f"{domain} via {ip}\n\n{hist[-1][1]}")

    def update_gui(self):
        with state_lock:
            snapshot = {domain: {ip: list(hist) for ip, hist in servers.items()}
                        for domain, servers in results_history.items()}
        for domain, tree in self.domain_tabs.items():
            for index, (ip, hist) in enumerate(snapshot.get(domain, {}).items()):
                stats = summarize([Measurement(sample[0], "", sample[1]) for sample in hist])
                def number(value):
                    return "—" if value is None else f"{value:.2f}"
                if hist:
                    latest_latency, latest_result, _, latest_rtt = hist[-1]
                    latest_str = number(latest_latency) if latest_latency is not None else "Failed"
                else:
                    latest_latency, latest_result, latest_rtt, latest_str = None, "Waiting…", None, "—"
                display_result = (latest_result[:57] + "…") if len(latest_result) > 58 else latest_result
                spark_img = generate_sparkline(hist)
                self.sparkline_images[(domain, ip)] = spark_img
                values = (ip, display_result, number(stats["min_ms"]), number(stats["max_ms"]),
                          number(stats["avg_ms"]), number(stats["p95_ms"]),
                          f"{stats['failure_pct']:.1f}" if hist else "—", latest_str, number(latest_rtt))
                if tree.exists(ip):
                    tree.item(ip, image=spark_img, values=values)
                else:
                    tree.insert("", tk.END, iid=ip, image=spark_img, values=values,
                                tags=("even",) if index % 2 == 0 else ())
        alive = self.benchmark_thread is not None and self.benchmark_thread.is_alive()
        if self.is_closing:
            if not alive:
                self.destroy()
                return
        elif alive:
            if not self.is_stopping:
                self.status_var.set(f"Running · {iteration_count} rounds completed")
        elif self.benchmark_thread is not None:
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            if benchmark_error:
                self.status_var.set("Benchmark stopped unexpectedly. Adjust the inputs and try again.")
            else:
                state = "Stopped" if self.is_stopping else "Complete"
                self.status_var.set(f"{state} · {iteration_count} rounds completed")
        if export_error and not self.is_closing:
            self.status_var.set(self.status_var.get() + " · Live Excel unavailable; close the workbook or use Export Excel.")
        self.after(250, self.update_gui)

    def open_live_graph_window(self):
        if self.graph_window and tk.Toplevel.winfo_exists(self.graph_window):
            return
        self.graph_window = tk.Toplevel(self)
        self.graph_window.title("Live Latency Graph")
        self.graph_window.geometry("800x600")
        self.fig = Figure(figsize=(8,6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("DNS Latency Over Iterations")
        self.ax.set_xlabel("Iteration")
        self.ax.set_ylabel("Latency (ms)")
        self.fig.set_facecolor("#f4f6f8")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_window)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.update_live_graph()

    def update_live_graph(self):
        if not self.graph_window or not tk.Toplevel.winfo_exists(self.graph_window):
            return
        try:
            current_tab = self.notebook.select()
            current_domain = self.notebook.tab(current_tab, "text")
        except Exception:
            current_domain = None
        if current_domain not in results_history:
            self.after(1000, self.update_live_graph)
            return

        self.ax.clear()
        self.ax.set_title(f"DNS Latency for {current_domain}")
        self.ax.set_xlabel("Iteration")
        self.ax.set_ylabel("Latency (ms)")
        with state_lock:
            domain_data = {ip: list(hist) for ip, hist in results_history[current_domain].items()}
        self.ax.set_facecolor("white")
        self.ax.grid(axis="y", color="#dce3e9", linewidth=0.7)
        self.ax.spines[["top", "right"]].set_visible(False)
        self.ax.set_prop_cycle(color=["#176a9b", "#af601a", "#24806d", "#744c9e", "#a83b4b"])
        self.ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        max_iters = 0
        for ip, hist in domain_data.items():
            if not hist:
                continue
            max_iters = max(max_iters, len(hist))
            iterations = list(range(1, len(hist)+1))
            lat_list = [(math.nan if lat is None else lat) for (lat,_,_,_) in hist]
            self.ax.plot(iterations, lat_list, marker="o", label=ip)
            for i in range(1, len(hist)):
                prev_ips = hist[i-1][2]
                curr_ips = hist[i][2]
                if prev_ips != curr_ips and prev_ips and curr_ips and hist[i][0] is not None:
                    self.ax.annotate("★", (i+1, lat_list[i]),
                                     textcoords="offset points", xytext=(0,5),
                                     color="red", fontsize=12, ha="center")
        if max_iters > 0:
            self.ax.set_xlim(1, max_iters+1)
        if self.ax.lines:
            self.ax.legend(frameon=False, fontsize=9)
        self.ax.set_ylim(bottom=0)
        self.fig.tight_layout(pad=2)
        self.canvas.draw()
        self.after(1000, self.update_live_graph)

    def save_excel(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                                 filetypes=[("Excel files","*.xlsx")])
        if file_path:
            try:
                with state_lock:
                    snapshot = {domain: [list(row) for row in rows] for domain, rows in excel_data.items()}
                write_live_excel(file_path, snapshot, self.run_domains, self.run_servers)
                messagebox.showinfo("Excel Saved", f"Excel data saved to {file_path}")
            except Exception as e:
                messagebox.showerror("Excel Error", str(e))

    def on_close(self):
        if self.benchmark_thread and self.benchmark_thread.is_alive():
            self.is_closing = True
            self.stop_benchmark()
            self.status_var.set("Finishing the current round before closing…")
        else:
            self.destroy()

##############################################
# SNMP Agent and MIB Update
##############################################
def load_snmp_config(config_file="snmp_config.json"):
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            return json.load(f)
    else:
        default_config = {
            "version": "v2c",
            "community": "public",
            "v3": {
                "user": "snmpuser",
                "authProtocol": "MD5",
                "authKey": "authpass",
                "privProtocol": "DES",
                "privKey": "privpass",
                "securityLevel": "authPriv"
            },
            "port": 1161
        }
        with open(config_file, "w") as f:
            json.dump(default_config, f, indent=4)
        return default_config

def run_snmp_agent():
    snmp_config = load_snmp_config()
    port = snmp_config.get("port", 1161)
    config.addTransport(
        snmpEngine,
        udp.domainName,
        udp.UdpTransport().openServerMode(('localhost', port))
    )
    version = snmp_config.get("version", "v2c").lower()
    if version in ["v1", "v2c"]:
        community = snmp_config.get("community", "public")
        config.addV1System(snmpEngine, 'my-area', community)
    elif version == "v3":
        v3_cfg = snmp_config.get("v3", {})
        user = v3_cfg.get("user", "snmpuser")
        authProtocolStr = v3_cfg.get("authProtocol", "MD5").upper()
        authKey = v3_cfg.get("authKey", "authpass")
        privProtocolStr = v3_cfg.get("privProtocol", "DES").upper()
        privKey = v3_cfg.get("privKey", "privpass")
        securityLevel = v3_cfg.get("securityLevel", "authPriv")
        if authProtocolStr == "MD5":
            authProtocol = config.usmHMACMD5AuthProtocol
        elif authProtocolStr == "SHA":
            authProtocol = config.usmHMACSHAAuthProtocol
        else:
            authProtocol = config.usmNoAuthProtocol
        if privProtocolStr == "DES":
            privProtocol = config.usmDESPrivProtocol
        elif privProtocolStr == "AES":
            privProtocol = config.usmAesCfb128Protocol
        else:
            privProtocol = config.usmNoPrivProtocol
        config.addV3User(
            snmpEngine,
            user,
            authProtocol, authKey,
            privProtocol, privKey,
            securityLevel=securityLevel
        )
    mibInstrum = instrum.MibInstrumController(snmpEngine.getMibBuilder())
    cmdrsp.GetCommandResponder(snmpEngine, mibInstrum)
    cmdrsp.GetNextCommandResponder(snmpEngine, mibInstrum)
    snmpEngine.transportDispatcher.jobStarted(1)
    try:
        snmpEngine.transportDispatcher.runDispatcher()
    except Exception:
        snmpEngine.transportDispatcher.closeDispatcher()
        raise

def aggregate_global_metrics():
    all_latencies = []
    for domain in results_history:
        for ip in results_history[domain]:
            for (lat, _, _, _) in results_history[domain][ip]:
                if lat is not None:
                    all_latencies.append(lat)
    if not all_latencies:
        return 0, 0, 0
    return min(all_latencies), max(all_latencies), sum(all_latencies)/len(all_latencies)

def update_snmp_mib():
    mibBuilder = snmpEngine.getMibBuilder()
    mibInstrum = instrum.MibInstrumController(mibBuilder)
    globalMinOID = (1,3,6,1,4,1,53864,1,1,1)
    globalMaxOID = (1,3,6,1,4,1,53864,1,1,2)
    globalAvgOID = (1,3,6,1,4,1,53864,1,1,3)
    while True:
        min_val, max_val, avg_val = aggregate_global_metrics()
        mibInstrum.writeVars(
            [(rfc1902.ObjectIdentity(*globalMinOID).resolveWithMib(mibBuilder), rfc1902.Integer32(int(min_val))),
             (rfc1902.ObjectIdentity(*globalMaxOID).resolveWithMib(mibBuilder), rfc1902.Integer32(int(max_val))),
             (rfc1902.ObjectIdentity(*globalAvgOID).resolveWithMib(mibBuilder), rfc1902.Integer32(int(avg_val)))]
        )
        time.sleep(5)

def start_snmp_agent():
    if not SNMP_AVAILABLE:
        return
    snmp_thread = threading.Thread(target=run_snmp_agent, daemon=True)
    snmp_thread.start()
    mib_update_thread = threading.Thread(target=update_snmp_mib, daemon=True)
    mib_update_thread.start()

##############################################
# SNMP Engine Initialization
##############################################
snmpEngine = engine.SnmpEngine() if SNMP_AVAILABLE else None

##############################################
# Main: Start SNMP Agent and GUI Application
##############################################
if __name__ == "__main__":
    start_snmp_agent()
    app = DNSBenchmarkGUI()
    app.mainloop()
