"""可选托盘接入；只管理本程序图标，不读取指标或运行维护。"""

import ctypes as c
from ctypes import wintypes as w
import uuid


class NotifyData(c.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HANDLE), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", c.c_byte * 16), ("hBalloonIcon", w.HANDLE)]


class WindowsTray:
    def __init__(self, callback, *, loader=None):
        self.callback = callback
        self.loader = loader
        self.window = None
        self.added = False

    def start(self):
        if self.window is not None:
            return
        loader = self.loader or (lambda name: c.WinDLL(name, use_last_error=True))
        self.user, self.shell, self.kernel = (loader(name) for name in ("user32.dll", "shell32.dll", "kernel32.dll"))
        from .windows_process import bind, checked
        self.bind, self.checked = bind, checked
        callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        class WindowClass(c.Structure):
            _fields_ = [("style", w.UINT), ("lpfnWndProc", callback_type), ("cbClsExtra", c.c_int),
                        ("cbWndExtra", c.c_int), ("hInstance", w.HANDLE), ("hIcon", w.HANDLE),
                        ("hCursor", w.HANDLE), ("hbrBackground", w.HANDLE), ("lpszMenuName", w.LPCWSTR),
                        ("lpszClassName", w.LPCWSTR)]
        default = bind(self.user, "DefWindowProcW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t)
        def dispatch(hwnd, message, wp, lp):
            if message == 0x8001:
                event = int(lp) & 0xffff
                if event in (0x202, 0x205, 0x400, 0x401):
                    self.callback()
                return 0
            return default(hwnd, message, wp, lp)
        self.proc = callback_type(dispatch)
        self.name = "MemoryCleanupTray_" + uuid.uuid4().hex
        self.instance = bind(self.kernel, "GetModuleHandleW", [w.LPCWSTR], w.HANDLE)(None)
        self.wc = WindowClass(lpfnWndProc=self.proc, hInstance=self.instance, lpszClassName=self.name)
        checked(bind(self.user, "RegisterClassW", [c.POINTER(WindowClass)], w.ATOM)(c.byref(self.wc)))
        try:
            self.window = checked(bind(self.user, "CreateWindowExW", [w.DWORD, w.LPCWSTR, w.LPCWSTR,
                w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HANDLE, w.HANDLE, c.c_void_p], w.HWND)(
                    0, self.name, "", 0, 0, 0, 0, 0, w.HWND(-3), None, self.instance, None))
            icon = bind(self.user, "LoadIconW", [w.HANDLE, c.c_void_p], w.HANDLE)(None, c.c_void_p(32512))
            self.data = NotifyData(cbSize=c.sizeof(NotifyData), hWnd=self.window, uID=1,
                uFlags=1 | 2 | 4, uCallbackMessage=0x8001, hIcon=icon,
                szTip="内存清理：点击恢复窗口，退出请使用窗口中的退出按钮")
            self.notify_call = bind(self.shell, "Shell_NotifyIconW", [w.DWORD, c.POINTER(NotifyData)], w.BOOL)
            checked(self.notify_call(0, c.byref(self.data)))
            self.added = True
            self.data.uVersion = 4
            checked(self.notify_call(4, c.byref(self.data)))
        except Exception:
            self.close()
            raise

    def notify(self, text):
        if self.added:
            self.data.uFlags = 0x10
            self.data.szInfoTitle = "内存整理结果"
            self.data.szInfo = text[:255]
            self.data.dwInfoFlags = 1
            self.checked(self.notify_call(1, c.byref(self.data)))

    def close(self):
        if self.added:
            self.checked(self.notify_call(2, c.byref(self.data)))
            self.added = False
        if self.window is not None:
            self.checked(self.bind(self.user, "DestroyWindow", [w.HWND], w.BOOL)(self.window))
            self.window = None
        if hasattr(self, "name"):
            self.checked(self.bind(self.user, "UnregisterClassW", [w.LPCWSTR, w.HANDLE], w.BOOL)(self.name, self.instance))
            del self.name
