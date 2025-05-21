import os
import json
# Force the script's directory to be the working directory.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import subprocess
import dns.resolver
import concurrent.futures
import ipaddress
from datetime import datetime
import math
import shutil
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
from pysnmp.entity import engine, config
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.entity.rfc3413 import cmdrsp
from pysnmp.smi import builder, view, instrum, rfc1902
import asyncore

##############################################
# Global Data and Variables
##############################################
results_history = {}  # { domain: { dns_ip: [ (dns_latency, result_text, ip_set, ping_rtt), ... ] } }
excel_data = {}       # { domain: [ [iteration, timestamp, <dns_latency>, <IP set>, <RTT>, ...], ... ] }
iteration_count = 0
stop_event = threading.Event()
current_interval = 5.0  # Default interval value

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
# SNMP Configuration Functions
##############################################
def load_snmp_config(config_file=SNMP_CONFIG_FILE):
    """
    Loads SNMP configuration from a JSON file.
    Expected format (example):
    {
       "version": "v3",              // or "v1" or "v2c"
       "community": "public",         // used for v1/v2c
       "v3": {
            "user": "snmpuser",
            "authProtocol": "MD5",    // options: MD5, SHA
            "authKey": "authpass",
            "privProtocol": "DES",    // options: DES, AES
            "privKey": "privpass",
            "securityLevel": "authPriv" // options: noAuthNoPriv, authNoPriv, authPriv
       },
       "port": 1161
    }
    If file is not found, default SNMP v2c settings are used.
    """
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            return json.load(f)
    else:
        # Default SNMP v2c configuration
        default_config = {
            "version": "v2c",
            "community": "public",
            "port": 1161
        }
        with open(config_file, "w") as f:
            json.dump(default_config, f, indent=4)
        return default_config

##############################################
# Utility / DNS Functions
##############################################
def parse_ip_set_from_result(result_text):
    if not result_text or "timed out" in result_text.lower() or "None" in result_text:
        return set()
    ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", result_text)
    return set(ips)

def get_system_dns_servers():
    try:
        output = subprocess.check_output(["ipconfig", "/all"], text=True)
    except subprocess.CalledProcessError:
        return []
    dns_servers = []
    capture = False
    for line in output.splitlines():
        if "DNS Servers" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                candidate = parts[1].strip()
                try:
                    ipaddress.ip_address(candidate)
                    dns_servers.append(candidate)
                except ValueError:
                    pass
            capture = True
        elif capture:
            if line.startswith(" "):
                candidate = line.strip()
                try:
                    ipaddress.ip_address(candidate)
                    dns_servers.append(candidate)
                except ValueError:
                    pass
            else:
                capture = False
    return list(dict.fromkeys(dns_servers))

def get_ping_rtt(server_ip):
    try:
        timeout_value = current_interval * 0.5 if current_interval < 2.0 else 1.0
        rtt = ping(server_ip, timeout=timeout_value, unit="ms")
        return rtt
    except Exception:
        return None

def resolve_domain(server_ip, domain):
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server_ip]
    timeout_value = current_interval * 0.5 if current_interval < 2.0 else 1.0
    resolver.timeout = timeout_value
    resolver.lifetime = timeout_value
    start = time.perf_counter()
    try:
        answer = resolver.resolve(domain, "A")
        dns_latency = (time.perf_counter() - start) * 1000
        result_text = ", ".join(str(r) for r in answer)
    except Exception as e:
        dns_latency = None
        result_text = str(e)
    ip_set = parse_ip_set_from_result(result_text)
    ping_rtt = get_ping_rtt(server_ip)
    return dns_latency, result_text, ip_set, ping_rtt

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
    lat_values = [(0 if lat is None else lat) for (lat, _, _, _) in data]
    if not lat_values:
        min_val, max_val = 0, 1
    else:
        min_val, max_val = min(lat_values), max(lat_values)
        if min_val == max_val:
            min_val, max_val = min_val - 1, max_val + 1
    n = len(data)
    x_spacing = width / (n - 1) if n > 1 else width
    points = []
    for i, v in enumerate(lat_values):
        y = height - int((v - min_val) / (max_val - min_val) * (height - 1)) - 1
        x = int(i * x_spacing)
        points.append((x, y))
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    for i in range(n - 1):
        if data[i][0] is None or data[i+1][0] is None:
            color = "red"
        else:
            color = "blue"
        draw.line([points[i], points[i+1]], fill=color, width=1)
    for pt in points:
        draw.ellipse([pt[0]-1, pt[1]-1, pt[0]+1, pt[1]+1], fill="blue")
    for i in range(1, n):
        if data[i-1][2] != data[i][2]:
            x, y = points[i]
            draw.line([x-2, y-2, x+2, y+2], fill="red", width=1)
            draw.line([x-2, y+2, x+2, y-2], fill="red", width=1)
    return ImageTk.PhotoImage(img)

##############################################
# Live Excel Writing Functionality
##############################################
def write_live_excel(live_file, excel_data, domains, servers_order):
    try:
        import xlsxwriter
    except ImportError:
        print("xlsxwriter not installed.")
        return
    if os.path.exists(live_file):
        try:
            os.remove(live_file)
            print("Removed existing live Excel file:", os.path.abspath(live_file))
        except PermissionError:
            print("PermissionError: Unable to remove live Excel file. Ensure it's closed.")
            return
    workbook = xlsxwriter.Workbook(live_file)
    for domain in domains:
        ws_name = domain if len(domain) <= 31 else domain[:31]
        worksheet = workbook.add_worksheet(ws_name)
        headers = ["Iteration", "Timestamp"]
        for server in servers_order:
            headers.extend([f"{server} Latency", f"{server} IPs", f"{server} RTT"])
        for col, header in enumerate(headers):
            worksheet.write(0, col, header)
        rows = excel_data[domain]
        for row_num, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                worksheet.write(row_num, col, value)
        chart = workbook.add_chart({'type': 'line'})
        num_rows = len(rows)
        for i, server in enumerate(servers_order):
            col = 2 + i*3
            chart.add_series({
                'name':       [ws_name, 0, col],
                'categories': [ws_name, 1, 0, num_rows, 0],
                'values':     [ws_name, 1, col, num_rows, col],
            })
        chart.set_title({'name': f"Latency for {domain}"})
        chart.set_x_axis({'name': 'Iteration'})
        chart.set_y_axis({'name': 'Latency (ms)'})
        worksheet.insert_chart('H2', chart)
    workbook.close()
    print("Live Excel file written to", os.path.abspath(live_file))

##############################################
# SNMP Agent and MIB Update
##############################################
def load_snmp_config(config_file="snmp_config.json"):
    """
    Loads SNMP configuration from a JSON file.
    If the file does not exist, it creates one with default settings.
    Supports SNMP v1/v2c and v3.
    """
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
    """
    Runs an SNMP agent on localhost using the settings from snmp_config.json.
    Supports SNMP v1, v2c, and v3.
    """
    snmp_config = load_snmp_config()
    port = snmp_config.get("port", 1161)
    # Setup transport on the specified UDP port.
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
        # Map protocol strings to pysnmp constants:
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
    # Create MIB instrumentation for SNMP GET/GETNEXT
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
    snmp_thread = threading.Thread(target=run_snmp_agent, daemon=True)
    snmp_thread.start()
    mib_update_thread = threading.Thread(target=update_snmp_mib, daemon=True)
    mib_update_thread.start()

##############################################
# SNMP Engine Initialization
##############################################
snmpEngine = engine.SnmpEngine()

##############################################
# Benchmarking Loop (Background Thread)
##############################################
def benchmarking_loop(domains, interval, iterations, dns_servers):
    global iteration_count, results_history, excel_data, current_interval
    for domain in domains:
        results_history[domain] = {ip: [] for ip in dns_servers}
        excel_data[domain] = []
    iteration_count = 0
    while (iterations == 0 or iteration_count < iterations) and not stop_event.is_set():
        iteration_count += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {}
            for domain in domains:
                for ip in dns_servers:
                    futures[executor.submit(resolve_domain, ip, domain)] = (domain, ip)
            for future in concurrent.futures.as_completed(futures):
                domain, ip = futures[future]
                try:
                    dns_latency, result_text, ip_set, ping_rtt = future.result()
                except Exception as e:
                    dns_latency, result_text, ip_set, ping_rtt = None, str(e), set(), None
                results_history[domain][ip].append((dns_latency, result_text, ip_set, ping_rtt))
        print(f"Iteration {iteration_count} complete.")
        for domain in domains:
            row = [iteration_count, now_str]
            for ip in dns_servers:
                if results_history[domain][ip]:
                    last_dns_latency, last_result, last_ips, last_rtt = results_history[domain][ip][-1]
                    row.append(last_dns_latency if last_dns_latency is not None else 0)
                    row.append(", ".join(sorted(last_ips)) if last_ips else "")
                    row.append(last_rtt if last_rtt is not None else 0)
                else:
                    row.extend([0, "", 0])
            excel_data[domain].append(row)
        write_live_excel(LIVE_EXCEL_FILE, excel_data, domains, dns_servers)
        time.sleep(current_interval)

##############################################
# GUI Application
##############################################
class DNSBenchmarkGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DNS Benchmark GUI")
        self.geometry("1100x700")
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
            current_interval = self.interval_var.get()
            print("Interval updated to:", current_interval)
        except Exception as e:
            print("Error updating interval:", e)

    def create_widgets(self):
        config_frame = ttk.LabelFrame(self, text="Configuration")
        config_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(config_frame, text="Interval (sec):").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.interval_var = tk.DoubleVar(value=5.0)
        self.interval_var.trace_add("write", self.update_interval)
        ttk.Entry(config_frame, textvariable=self.interval_var, width=8).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(config_frame, text="Iterations (0=infinite):").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        self.iterations_var = tk.IntVar(value=0)
        ttk.Entry(config_frame, textvariable=self.iterations_var, width=8).grid(row=0, column=3, padx=5, pady=5)

        # FQDNs (servers.ls) Text Widget.
        fqdns_frame = ttk.LabelFrame(config_frame, text="FQDNs (servers.ls)")
        fqdns_frame.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.fqdn_text = tk.Text(fqdns_frame, height=5, width=40)
        self.fqdn_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fqdn_scroll = ttk.Scrollbar(fqdns_frame, orient=tk.VERTICAL, command=self.fqdn_text.yview)
        self.fqdn_text.configure(yscrollcommand=fqdn_scroll.set)
        fqdn_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if os.path.exists(DEFAULT_FQDN_FILE):
            with open(DEFAULT_FQDN_FILE, "r") as f:
                self.fqdn_text.insert(tk.END, f.read())
        else:
            self.fqdn_text.insert(tk.END, DEFAULT_DOMAIN)
        self.save_fqdn_button = ttk.Button(fqdns_frame, text="Save FQDNs", command=self.save_fqdns)
        self.save_fqdn_button.pack(pady=2)

        # DNS Servers (dns.ls) Text Widget.
        dns_frame = ttk.LabelFrame(config_frame, text="DNS Servers (dns.ls)")
        dns_frame.grid(row=1, column=2, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.dns_text = tk.Text(dns_frame, height=5, width=40)
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
        self.save_dns_button = ttk.Button(dns_frame, text="Save DNS Servers", command=self.save_dns)
        self.save_dns_button.pack(pady=2)

        button_frame = ttk.Frame(config_frame)
        button_frame.grid(row=2, column=0, columnspan=4, pady=5)
        self.start_button = ttk.Button(button_frame, text="Start Benchmark", command=self.start_benchmark)
        self.start_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop Benchmark", command=self.stop_benchmark, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=5)
        self.graph_button = ttk.Button(button_frame, text="Open Live Graph", command=self.open_live_graph_window, state=tk.DISABLED)
        self.graph_button.grid(row=0, column=2, padx=5)
        self.save_excel_button = ttk.Button(button_frame, text="Save Excel As...", command=self.save_excel, state=tk.DISABLED)
        self.save_excel_button.grid(row=0, column=3, padx=5)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.domain_tabs = {}

    def save_fqdns(self):
        content = self.fqdn_text.get("1.0", tk.END).strip()
        with open(DEFAULT_FQDN_FILE, "w") as f:
            f.write(content)
        messagebox.showinfo("Saved", f"FQDN list saved to {os.path.abspath(DEFAULT_FQDN_FILE)}")

    def save_dns(self):
        content = self.dns_text.get("1.0", tk.END).strip()
        with open(DEFAULT_DNS_FILE, "w") as f:
            f.write(content)
        messagebox.showinfo("Saved", f"DNS servers list saved to {os.path.abspath(DEFAULT_DNS_FILE)}")

    def start_benchmark(self):
        try:
            interval = self.interval_var.get()
            iterations = self.iterations_var.get()
            fqdns_content = self.fqdn_text.get("1.0", tk.END).strip()
            domains = [line.strip() for line in fqdns_content.splitlines() if line.strip()]
            if not domains:
                messagebox.showerror("Input Error", "No FQDNs provided.")
                return
            dns_content = self.dns_text.get("1.0", tk.END).strip()
            dns_servers = [line.strip() for line in dns_content.splitlines() if line.strip()]
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
            self.notebook.forget(tab)
        self.domain_tabs.clear()

        global results_history, excel_data, iteration_count
        results_history = {}
        excel_data = {}
        iteration_count = 0
        stop_event.clear()
        self.sparkline_images = {}

        for domain in domains:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=domain)
            hscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL)
            vscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL)
            columns = ("DNS Server", "Result", "MIN (ms)", "MAX (ms)", "AVG (ms)", "Latest (ms)", "RTT (ms)")
            tree = ttk.Treeview(frame, columns=columns, show="tree headings",
                                xscrollcommand=hscroll.set, yscrollcommand=vscroll.set)
            tree.heading("#0", text="Graph")
            tree.column("#0", width=70, anchor="center")
            for col in columns:
                tree.heading(col, text=col)
                tree.column(col, width=90, anchor="center")
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
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.graph_button.config(state=tk.DISABLED)
        self.save_excel_button.config(state=tk.DISABLED)

    def update_gui(self):
        for domain, tree in self.domain_tabs.items():
            if domain in results_history:
                for row in tree.get_children():
                    tree.delete(row)
                for ip, hist in results_history[domain].items():
                    latencies = [lat for (lat, _, _, _) in hist if lat is not None]
                    if latencies:
                        min_val = min(latencies)
                        max_val = max(latencies)
                        avg_val = sum(latencies)/len(latencies)
                        latest_latency, latest_result, _, latest_rtt = hist[-1]
                        min_str = f"{min_val:.2f}"
                        max_str = f"{max_val:.2f}"
                        avg_str = f"{avg_val:.2f}"
                        latest_str = f"{latest_latency:.2f}" if latest_latency is not None else "Failed"
                        rtt_str = f"{latest_rtt:.3f}" if latest_rtt is not None else "N/A"
                    else:
                        min_str = max_str = avg_str = latest_str = "N/A"
                        rtt_str = "N/A"
                        latest_result = "N/A"
                    display_result = (latest_result[:37]+"...") if len(latest_result) > 40 else latest_result
                    spark_img = generate_sparkline(hist)
                    self.sparkline_images[(domain, ip)] = spark_img
                    tree.insert("", tk.END, text="", image=spark_img,
                                values=(ip, display_result, min_str, max_str, avg_str, latest_str, rtt_str))
        self.after(1000, self.update_gui)

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
        domain_data = results_history[current_domain]
        max_iters = 0
        for ip, hist in domain_data.items():
            if not hist:
                continue
            max_iters = max(max_iters, len(hist))
            iterations = list(range(1, len(hist)+1))
            lat_list = [(0 if lat is None else lat) for (lat,_,_,_) in hist]
            self.ax.plot(iterations, lat_list, marker="o", label=ip)
            for i in range(1, len(hist)):
                prev_ips = hist[i-1][2]
                curr_ips = hist[i][2]
                if prev_ips != curr_ips and (prev_ips or curr_ips):
                    self.ax.annotate("★", (i+1, lat_list[i]),
                                     textcoords="offset points", xytext=(0,5),
                                     color="red", fontsize=12, ha="center")
        if max_iters > 0:
            self.ax.set_xlim(1, max_iters+1)
        self.ax.legend()
        self.canvas.draw()
        self.after(1000, self.update_live_graph)

    def save_excel(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                                 filetypes=[("Excel files","*.xlsx")])
        if file_path:
            try:
                shutil.copy(LIVE_EXCEL_FILE, file_path)
                messagebox.showinfo("Excel Saved", f"Excel data saved to {file_path}")
            except Exception as e:
                messagebox.showerror("Excel Error", str(e))

    def on_close(self):
        stop_event.set()
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
    snmp_thread = threading.Thread(target=run_snmp_agent, daemon=True)
    snmp_thread.start()
    mib_update_thread = threading.Thread(target=update_snmp_mib, daemon=True)
    mib_update_thread.start()

##############################################
# SNMP Engine Initialization
##############################################
snmpEngine = engine.SnmpEngine()

##############################################
# Main: Start SNMP Agent and GUI Application
##############################################
if __name__ == "__main__":
    start_snmp_agent()
    app = DNSBenchmarkGUI()
    app.mainloop()
