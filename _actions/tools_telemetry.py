"""Телеметрия и сенсорное ядро (SensorCore).

Читает глобальные пути через _tools_core (get_my_dir и т.п.).
"""
import time
import subprocess
import json
from pathlib import Path

import psutil
import wmi
import numpy as np


def get_cpu_load():
    try:
        return psutil.cpu_percent(interval=0.1)
    except Exception:
        return None


def get_gpu_load():
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            universal_newlines=True, timeout=5,
        )
        lines = output.strip().split("\n")
        if lines and lines[0].isdigit():
            return int(lines[0])
        return None
    except Exception:
        return None


def get_cpu_freq():
    # 1. через WMI Performance
    try:
        c = wmi.WMI(namespace=r"root\CIMv2")
        perf_data = c.Win32_PerfFormattedData_Counters_ProcessorInformation(Name="_Total")
        if perf_data and len(perf_data) > 0:
            percent = perf_data[0].PercentProcessorPerformance
            if percent is not None:
                try:
                    percent = float(percent)
                except (ValueError, TypeError):
                    percent = None
                if percent is not None:
                    processors = c.Win32_Processor()
                    if processors and len(processors) > 0:
                        max_freq_mhz = processors[0].MaxClockSpeed
                        if max_freq_mhz:
                            current_mhz = max_freq_mhz * percent / 100.0
                            return round(current_mhz / 1000.0, 2)
    except Exception:
        pass
    # 2. через CurrentClockSpeed
    try:
        c = wmi.WMI(namespace=r"root\CIMv2")
        for processor in c.Win32_Processor():
            if processor.CurrentClockSpeed:
                return round(processor.CurrentClockSpeed / 1000.0, 2)
    except Exception:
        pass
    # 3. через psutil
    try:
        freq = psutil.cpu_freq()
        if freq and freq.current:
            return round(freq.current / 1000, 2)
    except Exception:
        pass
    return None


def get_ram_available():
    try:
        mem = psutil.virtual_memory()
        return round(mem.available / (1024 * 1024))
    except Exception:
        return None


def get_disks_load():
    try:
        c = wmi.WMI(namespace=r"root\CIMv2")
        disks = c.Win32_PerfFormattedData_PerfDisk_PhysicalDisk()
        result = {}
        for d in disks:
            if d.Name != "_Total" and d.Name.strip():
                parts = d.Name.split()
                disk_name = parts[1] if len(parts) >= 2 else d.Name
                try:
                    load = int(d.PercentDiskTime)
                except (ValueError, TypeError):
                    load = 0
                result[disk_name] = load
        return result
    except Exception:
        return None


_last_net_io = None
_last_net_time = None


def get_network_speed():
    global _last_net_io, _last_net_time
    try:
        now = time.time()
        net_io = psutil.net_io_counters()
        if _last_net_io is None or _last_net_time is None:
            _last_net_io = net_io
            _last_net_time = now
            return 0, 0
        interval = now - _last_net_time
        if interval <= 0:
            return 0, 0
        recv_speed = (net_io.bytes_recv - _last_net_io.bytes_recv) / interval / 1024
        sent_speed = (net_io.bytes_sent - _last_net_io.bytes_sent) / interval / 1024
        _last_net_io = net_io
        _last_net_time = now
        return round(recv_speed, 1), round(sent_speed, 1)
    except Exception:
        return None, None


def get_gpu_temp():
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            universal_newlines=True, timeout=5,
        )
        lines = output.strip().split("\n")
        if lines and lines[0].isdigit():
            return int(lines[0])
        return None
    except Exception:
        return None


def get_current_sensor_vector():
    """Возвращает numpy array для сенсорного ядра."""
    gpu_temp = get_gpu_temp() or 0
    gpu_load = get_gpu_load() or 0
    cpu_load = get_cpu_load() or 0
    cpu_freq = get_cpu_freq() or 0
    ram_avail = get_ram_available() or 0
    recv, sent = get_network_speed()
    net_in = recv or 0
    net_out = sent or 0
    disks = get_disks_load()
    disk_load = max(disks.values()) if disks else 0
    return np.array([gpu_temp, gpu_load, cpu_load, cpu_freq,
                     ram_avail, net_in, net_out, disk_load])


class SensorCore:
    def __init__(self, data_dir, get_vector_func, n_clusters=5, history_len=100):
        self.data_dir = Path(data_dir)
        self.get_vector = get_vector_func
        self.n_clusters = n_clusters
        self.history_len = history_len
        self.history = []
        self.kmeans = None
        self.cluster_labels = {}
        self._load()
        self._last_vector = None

    def _load(self):
        state_path = self.data_dir / "sensor_state.json"
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.history = [np.array(v) for v in data.get("history", [])]
                self.cluster_labels = {int(k): v
                                       for k, v in data.get("labels", {}).items()}
            except Exception:
                pass
        self._update_kmeans()

    def _save(self):
        state = {
            "history": [v.tolist() for v in self.history[-self.history_len:]],
            "labels": self.cluster_labels,
        }
        with open(self.data_dir / "sensor_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _update_kmeans(self):
        if len(self.history) < self.n_clusters:
            self.kmeans = None
            return
        from sklearn.cluster import KMeans
        X = np.array(self.history[-self.history_len:])
        self.kmeans = KMeans(n_clusters=self.n_clusters,
                             random_state=42, n_init=10)
        self.kmeans.fit(X)

    def update(self):
        vec = self.get_vector()
        if vec is None:
            return
        self._last_vector = vec
        self.history.append(vec)
        if len(self.history) > self.history_len:
            self.history = self.history[-self.history_len:]
        self._update_kmeans()
        self._save()

    def get_current_cluster(self):
        if (self.kmeans is None
                or not hasattr(self.kmeans, "cluster_centers_")
                or len(self.history) == 0):
            return {"id": -1, "name": None}
        last_vec = self._last_vector if self._last_vector is not None else self.history[-1]
        cluster_id = self.kmeans.predict([last_vec])[0]
        name = self.cluster_labels.get(cluster_id)
        return {"id": int(cluster_id), "name": name}

    def set_cluster_name(self, cluster_id, name):
        self.cluster_labels[cluster_id] = name
        self._save()

    def get_cluster_history(self, n=10):
        if self.kmeans is None or len(self.history) == 0:
            return []
        X = np.array(self.history[-min(n, len(self.history)):])
        labels = self.kmeans.predict(X)
        return [{"cluster": int(l), "vector": v.tolist()}
                for l, v in zip(labels, X)]
