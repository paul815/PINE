"""Windows file handles that do not fight with an atomic replace.

Replacing a file under Windows normally locks out everybody involved. Two
separate defaults cause it, and fixing either one alone changes nothing:

* CPython's ``open()`` does not pass FILE_SHARE_DELETE, so a reader holding
  the file denies the writer the DELETE access a replace needs.
* ``os.replace`` is MoveFileExW, which renames through FileRenameInformation.
  That refuses an open destination even when every holder granted
  FILE_SHARE_DELETE — measured directly, not assumed.

Both together give the POSIX behaviour the rest of the code already expects:
the rename succeeds while readers hold the file, and those readers go on
reading the old contents until they close. Readers therefore never block a
write, and never see half a document.

FileRenameInfoEx wants Windows 10 1607 or newer and a filesystem that
implements it. A project folder on an exFAT stick does not, so
``rename_posix`` reports that through RENAME_UNSUPPORTED and the caller falls
back to ``os.replace``.

On POSIX ``AVAILABLE`` is False and nothing else in this module is defined.
"""

import os

AVAILABLE = os.name == 'nt'

# Codes that mean "this Windows or this filesystem cannot do it", as opposed
# to "not right now": ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED and
# ERROR_INVALID_PARAMETER, which is what an older kernel returns for an info
# class it does not know.
RENAME_UNSUPPORTED = frozenset({1, 50, 87})

if AVAILABLE:  # pragma: no cover - exercised only on Windows
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _SHARE_READ_WRITE_DELETE = 0x00000001 | 0x00000002 | 0x00000004
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _FILE_RENAME_INFO_EX = 22
    _RENAME_REPLACE_IF_EXISTS = 0x1
    _RENAME_POSIX_SEMANTICS = 0x2

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _create_file(path, access):
        """CreateFileW sharing everything, raising what open() would raise."""
        handle = _kernel32.CreateFileW(
            os.fspath(path), access, _SHARE_READ_WRITE_DELETE, None,
            _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            # WinError maps 2 to FileNotFoundError and 5 to PermissionError,
            # so callers catching those keep working unchanged.
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def open_shared(path, encoding='utf-8'):
        """open() for reading, but sharing delete so a replace can proceed.

        O_BINARY on purpose: newline translation is the TextIOWrapper's job,
        and letting the C runtime do it as well would translate twice.
        """
        handle = _create_file(path, _GENERIC_READ)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _kernel32.CloseHandle(handle)
            raise
        # The descriptor owns the handle from here; closing it closes both.
        try:
            return open(fd, encoding=encoding, closefd=True)
        except BaseException:
            os.close(fd)
            raise

    def rename_posix(src, dst):
        """Rename *src* over *dst* the way POSIX would.

        Raises OSError with a .winerror in RENAME_UNSUPPORTED when the volume
        or the Windows build cannot; the caller falls back from there.
        """
        handle = _create_file(src, _DELETE | _SYNCHRONIZE)
        try:
            name = os.path.abspath(os.fspath(dst))

            # FILE_RENAME_INFO ends in a variable-length name, so the struct
            # has to be built to fit it. Letting ctypes lay the fields out
            # keeps the padding before RootDirectory correct on both 32- and
            # 64-bit.
            class _FileRenameInfo(ctypes.Structure):
                _fields_ = [
                    ('Flags', wintypes.DWORD),
                    ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD),
                    ('FileName', wintypes.WCHAR * (len(name) + 1)),
                ]

            info = _FileRenameInfo()
            info.Flags = _RENAME_REPLACE_IF_EXISTS | _RENAME_POSIX_SEMANTICS
            info.RootDirectory = None
            info.FileNameLength = len(name) * 2  # bytes, excluding the NUL
            info.FileName = name

            ok = _kernel32.SetFileInformationByHandle(
                handle, _FILE_RENAME_INFO_EX, ctypes.byref(info), ctypes.sizeof(info),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(handle)
