from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes


USER_COUNT = 10_000
PIXEL_COUNT = 1_024
PACKED_BYTES_PER_ART = PIXEL_COUNT * 3


def rss_bytes() -> int:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_memory_info.restype = wintypes.BOOL
        if get_memory_info(get_process(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return 0
    with open("/proc/self/statm", encoding="ascii") as statm:
        return int(statm.read().split()[1]) * int(os.sysconf("SC_PAGE_SIZE"))


def compact_user(index: int) -> dict:
    return {
        "id": f"user_{index:08x}",
        "username": f"fixture{index:05d}",
        "friend_code": f"fixture{index:05d}",
        "display_name": f"Fixture {index:05d}",
        "status_message": "",
        "profile_pixels_blank": True,
        "profile_art_version": 0,
        "profile_image_url": "",
        "profile_thumbnail_url": "",
    }


def main() -> None:
    before = rss_bytes()
    users = [compact_user(index) for index in range(USER_COUNT)]
    after = rss_bytes()
    compact_storage = sum(
        len(json.dumps(user, separators=(",", ":")).encode("utf-8"))
        for user in users
    )
    legacy_pixels_json = len(json.dumps(["#123456"] * PIXEL_COUNT, separators=(",", ":")).encode("utf-8"))
    report = {
        "users": USER_COUNT,
        "compact_runtime_rss_delta_bytes": max(0, after - before),
        "compact_user_json_bytes": compact_storage,
        "legacy_pixel_json_bytes_estimate": legacy_pixels_json * USER_COUNT,
        "packed_art_bytes_if_all_nonblank": PACKED_BYTES_PER_ART * USER_COUNT,
        "blank_art_bytes": 0,
        "initial_list_pixel_arrays": 0,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
