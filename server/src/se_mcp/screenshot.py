"""Capture the Space Engineers window from outside the game (Windows only).

Uses PrintWindow(PW_RENDERFULLCONTENT), which works for DirectX windows even when they're covered,
e.g. while the player is looking at the Claude window. Fails if the game is minimized.
"""

from __future__ import annotations

import ctypes
import io
import sys
from ctypes import wintypes

from PIL import Image

WINDOW_TITLE = "Space Engineers"
PW_RENDERFULLCONTENT = 0x2


class ScreenshotError(Exception):
    pass


def _find_window() -> int:
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == WINDOW_TITLE:
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        raise ScreenshotError("Space Engineers window not found. Is the game running?")
    return found[0]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def capture_window() -> Image.Image:
    if sys.platform != "win32":
        raise ScreenshotError("Screenshots are only supported on Windows.")

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel sizes on scaled displays
    except (AttributeError, OSError):
        pass

    hwnd = _find_window()
    if user32.IsIconic(hwnd):
        raise ScreenshotError("Space Engineers is minimized; restore the window to take a screenshot.")

    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise ScreenshotError("The game window has no size.")

    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        gdi32.SelectObject(memory_dc, bitmap)
        # Flag 0x1 (client area only) | PW_RENDERFULLCONTENT.
        if not user32.PrintWindow(hwnd, memory_dc, 0x1 | PW_RENDERFULLCONTENT):
            raise ScreenshotError("PrintWindow failed.")

        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth, header.biHeight = width, -height  # negative = top-down rows
        header.biPlanes, header.biBitCount = 1, 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0):
            raise ScreenshotError("GetDIBits failed.")
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)

    if image.getbbox() is None:
        raise ScreenshotError("Captured an all-black frame (exclusive fullscreen?). Use windowed or borderless mode.")
    return image


def screenshot_jpeg(max_width: int = 1280, quality: int = 80) -> tuple[bytes, tuple[int, int]]:
    image = capture_window()
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue(), image.size
