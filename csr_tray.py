"""CSR's icon in the Windows notification area (the system tray).

Plain ctypes over the Win32 shell API, no extra packages. The icon lives on its own
thread with its own hidden window and message loop. Click it to open the dashboard,
right-click for the menu. Crash and disconnect alerts show as Windows notifications.

Start with Windows is a value under HKCU\\...\\Run, no admin rights needed.
"""
import ctypes
import os
import sys
import threading
from ctypes import wintypes as wt

if os.name != "nt":                                   # nothing to do off Windows
    raise ImportError("the tray icon is Windows only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_NULL = 0x0002, 0x0010, 0x0111, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # the icon's callback
WM_RUN = WM_APP + 2           # run a queued call on the tray thread
NIN_BALLOONUSERCLICK = 0x0400 + 5
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x1, 0x2, 0x3
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
SM_CXSMICON, SM_CYSMICON = 49, 50
IDI_APPLICATION = 32512


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                ("time", wt.DWORD), ("pt", POINT)]


def _sig(fn, res, *args):
    fn.restype, fn.argtypes = res, list(args)


_sig(user32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.RegisterClassW, wt.ATOM, ctypes.POINTER(WNDCLASSW))
_sig(user32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
_sig(user32.DestroyWindow, wt.BOOL, wt.HWND)
_sig(user32.GetMessageW, wt.BOOL, ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
_sig(user32.TranslateMessage, wt.BOOL, ctypes.POINTER(MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(MSG))
_sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.PostQuitMessage, None, ctypes.c_int)
_sig(user32.RegisterWindowMessageW, wt.UINT, wt.LPCWSTR)
_sig(user32.CreatePopupMenu, wt.HMENU)
_sig(user32.AppendMenuW, wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
_sig(user32.SetMenuDefaultItem, wt.BOOL, wt.HMENU, wt.UINT, wt.UINT)
_sig(user32.TrackPopupMenu, wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HWND, wt.LPVOID)
_sig(user32.DestroyMenu, wt.BOOL, wt.HMENU)
_sig(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(POINT))
_sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
_sig(user32.LoadImageW, wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
_sig(user32.LoadIconW, wt.HICON, wt.HINSTANCE, wt.LPVOID)
_sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(shell32.Shell_NotifyIconW, wt.BOOL, wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
_sig(shell32.ExtractIconExW, wt.UINT, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.HICON),
     ctypes.POINTER(wt.HICON), wt.UINT)
_sig(kernel32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)


# ---- Start with Windows ------------------------------------------------------ #
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "CSR"


def autostart_command():
    """What Windows runs at sign-in: CSR in the tray, no browser tab."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --no-browser'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.abspath(sys.argv[0])
    return f'"{w if os.path.isfile(w) else exe}" "{script}" --serve --no-browser'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _t = winreg.QueryValueEx(k, RUN_NAME)
        return v == autostart_command()
    except OSError:
        return False


def set_autostart(on):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except OSError:
                    pass
        return True
    except OSError:
        return False


# ---- the icon ------------------------------------------------------------------ #
def _load_icon(ico_path=None):
    cx, cy = user32.GetSystemMetrics(SM_CXSMICON), user32.GetSystemMetrics(SM_CYSMICON)
    if getattr(sys, "frozen", False):                 # the exe's own icon
        small = wt.HICON()
        if shell32.ExtractIconExW(sys.executable, 0, None, ctypes.byref(small), 1) and small.value:
            return small.value
    if ico_path and os.path.isfile(ico_path):
        h = user32.LoadImageW(None, ico_path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if h:
            return h
    return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))


class Tray:
    """items: list of (label, callback) or None for a separator. A label starting with
    '!' is the bold default (what a left-click does). A callback may be a
    (callback, is_checked) pair for a checkbox item."""

    def __init__(self, tip, items, ico_path=None):
        self.tip, self.items, self.ico_path = tip, items, ico_path
        self.hwnd = None
        self._ready = threading.Event()
        self._queue = []
        self._lock = threading.Lock()
        self._wndproc = WNDPROC(self._proc)           # keep a reference: ctypes would free it
        self._taskbar_created = 0

    # -- public, safe from any thread --
    def start(self):
        threading.Thread(target=self._run, name="csr-tray", daemon=True).start()
        self._ready.wait(5)
        return self.hwnd is not None

    def notify(self, title, text, level="info"):
        self._call(lambda: self._balloon(title, text, level))

    def set_tip(self, tip):
        self.tip = tip
        self._call(lambda: self._shell(NIM_MODIFY, NIF_TIP))

    def stop(self):
        """Take the icon away now. Call before the process exits, or Windows leaves a
        dead icon behind until the mouse passes over it."""
        if self.hwnd:
            try:
                self._shell(NIM_DELETE, 0)
            except Exception:
                pass

    # -- tray thread --
    def _call(self, fn):
        if not self.hwnd:
            return
        with self._lock:
            self._queue.append(fn)
        user32.PostMessageW(self.hwnd, WM_RUN, 0, 0)

    def _nid(self, flags):
        n = NOTIFYICONDATAW()
        n.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        n.hWnd, n.uID, n.uFlags = self.hwnd, 1, flags
        n.uCallbackMessage = WM_TRAY
        n.hIcon = self.hicon
        n.szTip = self.tip[:127]
        return n

    def _shell(self, msg, flags):
        n = self._nid(flags)
        return shell32.Shell_NotifyIconW(msg, ctypes.byref(n))

    def _balloon(self, title, text, level):
        n = self._nid(NIF_INFO)
        n.szInfoTitle = (title or "CSR")[:63]
        n.szInfo = (text or " ")[:255]
        n.dwInfoFlags = {"error": NIIF_ERROR, "warn": NIIF_WARNING}.get(level, NIIF_INFO)
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(n))

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "CSRTrayWindow"
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(0, "CSRTrayWindow", "CSR", 0, 0, 0, 0, 0,
                                               None, None, hinst, None)
            self.hicon = _load_icon(self.ico_path)
            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            self._shell(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
        except Exception:
            self.hwnd = None
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _menu(self):
        m = user32.CreatePopupMenu()
        cmds = {}
        for i, it in enumerate(self.items, start=1):
            if it is None:
                user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
                continue
            label, cb = it
            flags = MF_STRING
            if isinstance(cb, tuple):
                cb, checked = cb
                if checked():
                    flags |= MF_CHECKED
            user32.AppendMenuW(m, flags, i, label.lstrip("!"))
            if label.startswith("!"):
                user32.SetMenuDefaultItem(m, i, 0)
            cmds[i] = cb
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)         # or the menu won't close on a click away
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd in cmds:
            threading.Thread(target=cmds[cmd], daemon=True).start()

    def _default(self):
        for it in self.items:
            if it and it[0].startswith("!"):
                cb = it[1][0] if isinstance(it[1], tuple) else it[1]
                threading.Thread(target=cb, daemon=True).start()
                return

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, NIN_BALLOONUSERCLICK):
                    self._default()
                elif ev == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_RUN:
                with self._lock:
                    q, self._queue = self._queue, []
                for fn in q:
                    try:
                        fn()
                    except Exception:
                        pass
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self._shell(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)   # Explorer restarted
                return 0
            if msg == WM_DESTROY:
                self._shell(NIM_DELETE, 0)
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
