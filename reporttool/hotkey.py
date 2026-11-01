"""Windows 全域快捷鍵（RegisterHotKey），在背景執行緒跑訊息迴圈。"""
import sys
import threading

MODIFIERS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, ";": 0xBA, "/": 0xBF, "`": 0xC0, ".": 0xBE, ",": 0xBC,
}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


def parse_hotkey(text: str) -> tuple[int, int]:
    """把 "Ctrl+Alt+R" 轉成 (modifiers, virtual key code)。"""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷鍵是空的")
    *mod_parts, key = parts
    mods = 0
    for m in mod_parts:
        if m not in MODIFIERS:
            raise ValueError(f"不認得的修飾鍵：{m}")
        mods |= MODIFIERS[m]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        raise ValueError(f"不認得的按鍵：{key}")
    if mods == 0:
        raise ValueError("快捷鍵至少要有一個修飾鍵（Ctrl / Alt / Shift / Win）")
    return mods, vk


class GlobalHotkey:
    """callback 會在背景執行緒被呼叫，GUI 端請透過 Qt signal 轉回主執行緒。"""

    def __init__(self, hotkey: str, callback):
        self.hotkey = hotkey
        self.callback = callback
        self.error = ""
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._ok = False

    def start(self) -> bool:
        if sys.platform != "win32":
            self.error = "全域快捷鍵目前只支援 Windows"
            return False
        try:
            self._mods, self._vk = parse_hotkey(self.hotkey)
        except ValueError as e:
            self.error = str(e)
            return False
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self._ok

    def _run(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        self._ok = bool(user32.RegisterHotKey(None, 1, self._mods | MOD_NOREPEAT, self._vk))
        if not self._ok:
            self.error = f"無法註冊快捷鍵 {self.hotkey}（可能已被其他程式使用）"
        self._ready.set()
        if not self._ok:
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.callback()
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self._thread and self._thread.is_alive() and self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1)
