"""Read-only hardware probe for `buzzkey --probe-hardware --json`.

Swarmterm's Settings -> Voice shows this as a suggestion before the user
starts buzzkey (Swarmterm SWA-42). The classification lives here, not in
Swarmterm, so there is only one place that decides which model fits a
machine. buzzkey's own first start still makes the final choice.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

# Below this, large-v3-turbo on CUDA does not fit next to a desktop.
MIN_CUDA_VRAM_GB = 6.0
MIN_RAM_GB = 4.0


@dataclass
class Hardware:
    gpu_class: Optional[str]  # 'nvidia', 'amd_rdna1', 'amd_rdna2+' or None
    gpu_name: Optional[str]
    vram_gb: Optional[float]
    has_vulkan: bool
    cpu_count: int
    ram_gb: Optional[float]


def recommend(hw: Hardware) -> dict:
    """Pure: hardware facts in, the JSON object Swarmterm shows out."""
    parts = []
    if hw.gpu_name:
        parts.append(hw.gpu_name + (f", {hw.vram_gb:.0f} GB VRAM" if hw.vram_gb else ""))
    parts.append(f"{hw.cpu_count} CPU threads")
    if hw.ram_gb:
        parts.append(f"{hw.ram_gb:.0f} GB RAM")
    summary = "; ".join(parts)

    if hw.ram_gb is not None and hw.ram_gb < MIN_RAM_GB:
        return {"summary": summary, "model": None, "runtime": None, "voiceAdvised": False}
    if hw.gpu_class == "nvidia" and (hw.vram_gb is None or hw.vram_gb >= MIN_CUDA_VRAM_GB):
        return {"summary": summary, "model": "large-v3-turbo", "runtime": "cuda", "voiceAdvised": True}
    if hw.gpu_class == "nvidia":
        return {"summary": summary, "model": "small", "runtime": "cuda", "voiceAdvised": True}
    if hw.gpu_name and hw.has_vulkan:
        return {"summary": summary, "model": "small", "runtime": "vulkan", "voiceAdvised": True}
    if hw.cpu_count >= 8:
        return {"summary": summary, "model": "small", "runtime": "cpu", "voiceAdvised": True}
    if hw.cpu_count >= 4:
        return {"summary": summary, "model": "base", "runtime": "cpu", "voiceAdvised": True}
    return {"summary": summary, "model": None, "runtime": None, "voiceAdvised": False}


def _nvidia_vram_gb() -> Optional[float]:
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip().splitlines()[0]) / 1024
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _ram_gb() -> Optional[float]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    return None


def detect() -> Hardware:
    from .platform.windows import gpu
    gpu_class, gpu_name = gpu.detect_gpu_class()
    return Hardware(
        gpu_class=gpu_class,
        gpu_name=gpu_name,
        vram_gb=_nvidia_vram_gb() if gpu_class == "nvidia" else None,
        has_vulkan=gpu.has_vulkan_driver(),
        cpu_count=os.cpu_count() or 1,
        ram_gb=_ram_gb(),
    )


def probe_json() -> str:
    return json.dumps(recommend(detect()))
