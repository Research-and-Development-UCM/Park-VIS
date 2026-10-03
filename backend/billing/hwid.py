"""HWID generation and caching.

Vendored from the billing client package. Generates a unique, durable
Hardware ID across Linux, Windows, and macOS:
- Linux: /sys/class/dmi/id/product_uuid + primary MAC address
- Windows: MachineGuid registry value (or Win32 ComputerSystemProduct UUID) + primary MAC address
- macOS / Fallback: IOPlatformUUID / uuid.getnode() MAC + persistent random token if needed

Stores a SHA-256 digest on disk ($LV_HOME/hwid) so the same host always
presents the same durable identity to billing.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from .paths import hwid_path


class HWIDGenerator:
    def __init__(self, file_path: Optional[Path] = None):
        self._hwid: Optional[str] = None
        self._file = Path(file_path) if file_path else hwid_path()

    def generate(self) -> str:
        system_uuid = self._get_system_uuid() or "dev-no-uuid"
        mac_address = self._get_primary_mac() or "dev-no-mac"
        raw = f"{system_uuid}:{mac_address}"
        self._hwid = hashlib.sha256(raw.encode()).hexdigest()
        return self._hwid

    def get(self, force_regenerate: bool = False) -> str:
        if force_regenerate or self._hwid is None:
            if not force_regenerate:
                cached = self._load_from_file()
                if cached:
                    self._hwid = cached
                    return self._hwid
            self._hwid = self.generate()
            self._save_to_file()
        return self._hwid

    def _get_system_uuid(self) -> Optional[str]:
        if sys.platform == "win32":
            # 1. Try Windows Registry MachineGuid
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                    val, _ = winreg.QueryValueEx(key, "MachineGuid")
                    if val and isinstance(val, str) and val.strip():
                        return val.strip()
            except Exception:
                pass

            # 2. Try PowerShell CIM ComputerSystemProduct UUID
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                )
                cleaned = out.strip()
                if cleaned and len(cleaned) > 8 and "error" not in cleaned.lower():
                    return cleaned
            except Exception:
                pass
            return None

        # Linux / Unix
        dmi_uuid = self._read_sys_file("/sys/class/dmi/id/product_uuid")
        if dmi_uuid:
            return dmi_uuid

        # macOS
        if sys.platform == "darwin":
            try:
                out = subprocess.check_output(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                )
                for line in out.splitlines():
                    if "IOPlatformUUID" in line:
                        parts = line.split("=")
                        if len(parts) > 1:
                            return parts[1].replace('"', '').strip()
            except Exception:
                pass

        return None

    def _read_sys_file(self, path: str) -> Optional[str]:
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except (IOError, PermissionError, FileNotFoundError):
            return None

    def _get_primary_mac(self) -> Optional[str]:
        # 1. Try /sys/class/net on Linux
        try:
            net_dir = Path("/sys/class/net")
            if net_dir.exists():
                for iface in net_dir.iterdir():
                    if iface.name == "lo":
                        continue
                    addr_file = iface / "address"
                    if addr_file.exists():
                        with open(addr_file) as f:
                            mac = f.read().strip()
                            if mac and mac != "00:00:00:00:00:00":
                                return mac
        except (IOError, PermissionError, FileNotFoundError):
            pass

        # 2. Cross-platform fallback via uuid.getnode()
        try:
            node = uuid.getnode()
            # If multicast bit is NOT set, this is a real hardware MAC
            if (node >> 40) & 1 == 0:
                mac_bytes = [(node >> (8 * i)) & 0xFF for i in reversed(range(6))]
                return ":".join(f"{b:02x}" for b in mac_bytes)
        except Exception:
            pass

        return None

    def _load_from_file(self) -> Optional[str]:
        try:
            if self._file.exists():
                cached = self._file.read_text().strip()
                # Validate the cached value is a 64-char hex digest
                if len(cached) == 64 and all(c in "0123456789abcdef" for c in cached.lower()):
                    return cached.lower()
        except OSError:
            pass
        return None

    def _save_to_file(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(self._hwid or "")
            try:
                os.chmod(self._file, 0o600)
            except (OSError, NotImplementedError):
                pass
        except OSError:
            pass


_default: Optional[HWIDGenerator] = None


def default_generator() -> HWIDGenerator:
    global _default
    if _default is None:
        _default = HWIDGenerator()
    return _default


def get_hwid() -> str:
    return default_generator().get()