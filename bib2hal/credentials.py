"""HAL credentials: kept in memory for the session, optionally remembered in the OS keychain.

Never written to a text file, never read from one. Backends, in order of preference:
  1. the `keyring` package, if installed (pip install keyring) — all platforms
  2. Windows Credential Manager (via ctypes, no dependency)
  3. macOS Keychain (`security` command)
  4. Linux Secret Service (`secret-tool` command, e.g. GNOME Keyring / KWallet)
If none is available, credentials live only as long as the program runs.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

SERVICE = "bib2hal"
ACCOUNT = "hal"                      # one HAL account per user (the public hal.science)
_session: dict[str, tuple[str, str]] = {}


def _name(target: str) -> str:
    return f"{SERVICE}:{target}"


# --------------------------------------------------------------------------- backends

class _Keyring:
    label = "system keychain (keyring)"

    def __init__(self):
        import keyring  # noqa: F401  (ImportError -> backend unavailable)
        self.k = keyring

    def get(self, target):
        v = self.k.get_password(SERVICE, _name(target))
        return v

    def set(self, target, value):
        self.k.set_password(SERVICE, _name(target), value)

    def delete(self, target):
        try:
            self.k.delete_password(SERVICE, _name(target))
        except Exception:  # noqa: BLE001
            pass


class _Windows:
    label = "Windows Credential Manager"

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("not windows")
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]

        self.ct, self.CRED = ctypes, CREDENTIAL
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        self.api.CredReadW.restype = wintypes.BOOL
        self.api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        self.api.CredWriteW.restype = wintypes.BOOL
        self.api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.api.CredDeleteW.restype = wintypes.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]

    def get(self, target):
        ct = self.ct
        p = ct.POINTER(self.CRED)()
        if not self.api.CredReadW(_name(target), 1, 0, ct.byref(p)):   # 1 = CRED_TYPE_GENERIC
            return None
        try:
            c = p.contents
            blob = ct.string_at(c.CredentialBlob, c.CredentialBlobSize)
            return blob.decode("utf-16-le")
        finally:
            self.api.CredFree(p)

    def set(self, target, value):
        ct = self.ct
        blob = value.encode("utf-16-le")
        buf = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        c = self.CRED()
        c.Type, c.Persist = 1, 2                                         # GENERIC, LOCAL_MACHINE
        c.TargetName, c.UserName, c.Comment = _name(target), SERVICE, "bib2hal HAL account"
        c.CredentialBlobSize, c.CredentialBlob = len(blob), ct.cast(buf, ct.POINTER(ct.c_ubyte))
        if not self.api.CredWriteW(ct.byref(c), 0):
            raise OSError(f"CredWrite failed ({ct.get_last_error()})")

    def delete(self, target):
        self.api.CredDeleteW(_name(target), 1, 0)


class _MacOS:
    label = "macOS Keychain"

    def __init__(self):
        if sys.platform != "darwin" or not shutil.which("security"):
            raise OSError("no macOS keychain")

    def get(self, target):
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", _name(target), "-w"],
                           capture_output=True, text=True)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None

    def set(self, target, value):
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", _name(target), "-w", value],
                       check=True, capture_output=True)

    def delete(self, target):
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", _name(target)],
                       capture_output=True)


class _SecretTool:
    label = "Secret Service keyring"

    def __init__(self):
        if not sys.platform.startswith("linux") or not shutil.which("secret-tool"):
            raise OSError("no secret-tool")

    def get(self, target):
        r = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "account", _name(target)],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 and r.stdout else None

    def set(self, target, value):
        subprocess.run(["secret-tool", "store", "--label", "bib2hal HAL account", "service", SERVICE,
                        "account", _name(target)], input=value, text=True, check=True, capture_output=True)

    def delete(self, target):
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", _name(target)], capture_output=True)


_backend = None
_backend_checked = False


def backend():
    """The keychain backend in use, or None (session-only)."""
    global _backend, _backend_checked
    if not _backend_checked:
        _backend_checked = True
        for cls in (_Keyring, _Windows, _MacOS, _SecretTool):
            try:
                _backend = cls()
                break
            except Exception:  # noqa: BLE001 — try the next one
                continue
    return _backend


def keychain_label() -> str:
    b = backend()
    return b.label if b else ""


# --------------------------------------------------------------------------- public API

def remember(login: str, password: str) -> bool:
    """Save in the OS keychain. Returns False if no keychain is available or it failed."""
    b = backend()
    if not b:
        return False
    try:
        b.set(ACCOUNT, json.dumps({"login": login, "password": password}))
        return True
    except Exception:  # noqa: BLE001
        return False


def _from_keychain() -> tuple[str, str] | None:
    b = backend()
    if not b:
        return None
    try:
        raw = b.get(ACCOUNT)
        if raw:
            d = json.loads(raw)
            return d["login"], d["password"]
    except Exception:  # noqa: BLE001
        return None
    return None


def set_session(login: str, password: str) -> None:
    _session[ACCOUNT] = (login, password)


def get() -> tuple[str, str] | None:
    """The signed-in HAL account (login, password): this session first, then the OS keychain."""
    if ACCOUNT in _session:
        return _session[ACCOUNT]
    kc = _from_keychain()
    if kc:
        _session[ACCOUNT] = kc
    return kc


def source() -> str:
    """'keychain' (remembered on this computer), 'session' (until bib2hal stops) or '' (signed out)."""
    if _from_keychain():
        return "keychain"
    return "session" if ACCOUNT in _session else ""


def forget() -> None:
    _session.pop(ACCOUNT, None)
    b = backend()
    if b:
        try:
            b.delete(ACCOUNT)
        except Exception:  # noqa: BLE001
            pass
