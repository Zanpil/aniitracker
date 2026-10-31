"""Only the game's sound, the way Game Bar records it: Windows process loopback (Windows 10 2004+).

Windows' audio engine hands over what one program plays, the same feature as OBS's "application
audio capture". The game's process is never opened or read: its id, taken from the process list,
only tells the audio engine whose sound to give. Discord, a Twitch stream or music are left out.
"""
import ctypes, threading, time, uuid
from ctypes import POINTER, byref, c_int32, c_uint16, c_uint32, c_void_p, wintypes
import numpy as np

GAME_EXE = "aniimo.exe"
RATE, CHANNELS = 44100, 2

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
mmdevapi = ctypes.WinDLL("Mmdevapi")
mmdevapi.ActivateAudioInterfaceAsync.restype = ctypes.HRESULT


class GUID(ctypes.Structure):
    _fields_ = [("d1", c_uint32), ("d2", c_uint16), ("d3", c_uint16), ("d4", ctypes.c_ubyte * 8)]


def guid(s):
    return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


IID_IUnknown = guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_Completion = guid("41D949AB-9862-444A-80F6-C261334DA5EB")  # IActivateAudioInterfaceCompletionHandler
IID_IAudioClient = guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = (wintypes.HANDLE, POINTER(PROCESSENTRY32W))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = (c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)


def game_pid(exe=GAME_EXE):
    """Id of the running game, from the process list (the process itself is not opened), or None."""
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(snap, byref(e))
        while ok:
            if e.szExeFile.lower() == exe:
                return e.th32ProcessID
            ok = kernel32.Process32NextW(snap, byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return None


def method(obj, index, *argtypes, restype=ctypes.HRESULT):
    """Method number `index` of the COM object `obj` (HRESULT failures raise OSError)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return lambda *args: ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])(obj, *args)


def release(obj):
    if obj:
        method(obj, 2, restype=c_uint32)()


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", c_uint16), ("r1", c_uint16), ("r2", c_uint16), ("r3", c_uint16), ("blob", BLOB)]


class ACTIVATION_PARAMS(ctypes.Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS, process loopback variant
    _fields_ = [("type", c_int32), ("pid", wintypes.DWORD), ("mode", c_int32)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
                ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
                ("cbSize", c_uint16)]


QI = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
REF = ctypes.WINFUNCTYPE(c_uint32, c_void_p)
DONE = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p)


class VTBL(ctypes.Structure):
    _fields_ = [("qi", QI), ("addref", REF), ("release", REF), ("done", DONE)]


class Handler(ctypes.Structure):
    """IActivateAudioInterfaceCompletionHandler: only signals that the activation finished."""
    _fields_ = [("vtbl", POINTER(VTBL))]

    def __init__(self):
        self.finished = threading.Event()
        known = [bytes(i) for i in (IID_IUnknown, IID_Completion, IID_IAgileObject)]

        def qi(this, riid, out):
            if bytes(riid.contents) in known:
                out[0] = this
                return 0
            out[0] = None
            return -2147467262  # E_NOINTERFACE

        self._table = VTBL(QI(qi), REF(lambda this: 1), REF(lambda this: 1),
                           DONE(lambda this, op: self.finished.set() or 0))
        super().__init__(ctypes.pointer(self._table))


class GameAudio:
    """with GameAudio(pid) as g: g.read() -> new mono float32 samples at RATE (maybe empty)."""

    def __init__(self, pid):
        self.pid = pid

    def __enter__(self):
        ole32.CoInitializeEx(None, 0)  # multithreaded: the activation completes on another thread
        params = ACTIVATION_PARAMS(1, self.pid, 0)  # PROCESS_LOOPBACK, include the game's child processes
        pv = PROPVARIANT(vt=65)  # VT_BLOB
        pv.blob.cbSize, pv.blob.pBlobData = ctypes.sizeof(params), ctypes.addressof(params)
        handler, op = Handler(), c_void_p()
        mmdevapi.ActivateAudioInterfaceAsync("VAD\\Process_Loopback", byref(IID_IAudioClient), byref(pv),
                                             byref(handler), byref(op))
        try:
            if not handler.finished.wait(10):
                raise OSError("Windows ne répond pas")
            hr, client = ctypes.c_long(), c_void_p()
            method(op, 3, POINTER(ctypes.c_long), POINTER(c_void_p))(byref(hr), byref(client))
            if hr.value < 0:
                raise OSError(f"activation refusée (0x{hr.value & 0xFFFFFFFF:08X})")
        finally:
            release(op)
        self.client = client
        fmt = WAVEFORMATEX(1, CHANNELS, RATE, RATE * 2 * CHANNELS, 2 * CHANNELS, 16, 0)  # PCM 16 bits
        flags = 0x00020000 | 0x00040000 | 0x80000000  # LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM
        method(client, 3, c_int32, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
               POINTER(WAVEFORMATEX), c_void_p)(0, flags, 2_000_000, 0, byref(fmt), None)  # 200 ms buffer
        self.event = kernel32.CreateEventW(None, False, False, None)
        method(client, 13, wintypes.HANDLE)(self.event)
        self.capture = c_void_p()
        method(client, 14, POINTER(GUID), POINTER(c_void_p))(byref(IID_IAudioCaptureClient), byref(self.capture))
        self.next_size = method(self.capture, 5, POINTER(c_uint32))
        self.get = method(self.capture, 3, POINTER(c_void_p), POINTER(c_uint32), POINTER(wintypes.DWORD),
                          c_void_p, c_void_p)
        self.free = method(self.capture, 4, c_uint32)
        method(client, 10)()  # Start
        return self

    def read(self, wait_ms=100):
        kernel32.WaitForSingleObject(self.event, wait_ms)
        parts, n = [], c_uint32()
        self.next_size(byref(n))
        while n.value:
            data, frames, flags = c_void_p(), c_uint32(), wintypes.DWORD()
            self.get(byref(data), byref(frames), byref(flags), None, None)
            if flags.value & 2:  # AUDCLNT_BUFFERFLAGS_SILENT
                parts.append(np.zeros(frames.value, np.float32))
            else:
                x = np.frombuffer(ctypes.string_at(data, frames.value * 2 * CHANNELS), np.int16)
                parts.append(x.reshape(-1, CHANNELS).mean(axis=1, dtype=np.float32) / 32768)
            self.free(frames.value)
            self.next_size(byref(n))
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def __exit__(self, *exc):
        method(self.client, 11)()  # Stop
        release(self.capture)
        release(self.client)
        kernel32.CloseHandle(self.event)
