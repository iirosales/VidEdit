"""Windows taskbar identity and relaunch metadata helpers.

When a Python script is pinned to the Windows taskbar, Windows reads the
window's Win32 IPropertyStore (AppUserModel relaunch properties) rather than
the runtime QIcon. Without these properties, pinning falls back to pythonw.exe
and displays the default Python icon.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Optional

APP_USER_MODEL_ID = "iirosales.VidEdit.App.1"


def build_relaunch_command(project_root: Path) -> str:
    """Build the quoted command Windows uses when relaunching a pinned VidEdit icon."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            exe = pythonw

    run_script = project_root / "run.py"
    if run_script.is_file():
        return f'"{exe}" "{run_script}"'
    return f'"{exe}" -m videdit.app'


def set_process_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Assign an explicit AppUserModelID to the current process on Windows."""
    if sys.platform != "win32":
        return False
    try:
        hr = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return hr == 0
    except (AttributeError, OSError):
        return False


def apply_window_taskbar_metadata(
    hwnd: int,
    project_root: Path,
    icon_path: Optional[Path],
    app_id: str = APP_USER_MODEL_ID,
    display_name: str = "VidEdit",
) -> bool:
    """Write AppUserModel ID and relaunch icon/command onto a Win32 HWND's IPropertyStore."""
    if sys.platform != "win32" or not hwnd:
        return False

    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("pwszVal", wintypes.LPWSTR),
            ("_pad", ctypes.c_uint64),
        ]

    def _make_guid(d1: int, d2: int, d3: int, tail: list[int]) -> GUID:
        return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*tail))

    # FMTID for PKEY_AppUserModel_* ({9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3})
    fmtid_app_user_model = _make_guid(
        0x9F4C2855, 0x9F79, 0x4B39, [0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3]
    )
    # IID_IPropertyStore ({886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99})
    iid_ipropertystore = _make_guid(
        0x886D8EEB, 0x8CF2, 0x4446, [0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99]
    )

    vt_lpwstr = 31
    pid_relaunch_command = 2
    pid_relaunch_icon = 3
    pid_relaunch_display_name = 4
    pid_app_id = 5

    shell32 = ctypes.windll.shell32
    shell32.SHGetPropertyStoreForWindow.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long

    store = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreForWindow(
        wintypes.HWND(hwnd),
        ctypes.byref(iid_ipropertystore),
        ctypes.byref(store),
    )
    if hr != 0 or not store.value:
        return False

    vtbl_ptr = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    release_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl_ptr[2])
    set_value_fn = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.POINTER(PROPERTYKEY),
        ctypes.POINTER(PROPVARIANT),
    )(vtbl_ptr[6])
    commit_fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl_ptr[7])

    try:
        entries: list[tuple[int, str]] = [
            (pid_app_id, app_id),
            (pid_relaunch_command, build_relaunch_command(project_root)),
            (pid_relaunch_display_name, display_name),
        ]
        if icon_path is not None and icon_path.is_file():
            entries.append((pid_relaunch_icon, f"{icon_path},0"))

        for pid, text in entries:
            key = PROPERTYKEY(fmtid_app_user_model, pid)
            val = PROPVARIANT(vt_lpwstr, 0, 0, 0, text, 0)
            if set_value_fn(store, ctypes.byref(key), ctypes.byref(val)) != 0:
                return False

        return commit_fn(store) == 0
    except OSError:
        return False
    finally:
        release_fn(store)
