"""Post-process _vulturevision.pyd to fix the null DLL name entry.

MinGW cross-compilation produces an import table entry where the
Python DLL name is set to the literal string ``(null)`` instead of
``python312.dll``.  When the Windows PE loader tries to resolve this
entry it looks for a DLL named ``(null)``, fails, and raises
``ERROR_MOD_NOT_FOUND``.

This script patches the import table: it locates the ``(null)`` entry
and redirects the Name RVA to an injected ``python312.dll\0`` string.

``python312.dll`` (the full API) is required because the .pyd imports
private ``_Py*`` symbols that are not part of the stable ABI exported
by ``python3.dll``.

Usage::

    python windows/patch_pyd.py path/to/_vulturevision.pyd
"""

import sys

TARGET = b"(null)\x00"
REPLACEMENT = b"python312.dll\x00"


def patch_pyd(pyd_path: str) -> None:
    import pefile

    with open(pyd_path, "rb") as f:
        data = bytearray(f.read())
    # Parse bytes so pefile does not keep a Windows mapping open while
    # the patched binary is written back to the same path.
    pe = pefile.PE(data=bytes(data))

    null_offset = data.find(TARGET)
    if null_offset < 0:
        print("[PATCH] No (null) DLL name found — nothing to do")
        return

    # Resolve the (null) string's RVA before modifying the file
    null_rva = _offset_to_rva(pe, null_offset)
    if null_rva is None:
        print("[PATCH] Could not resolve (null) RVA")
        return

    # Check if REPLACEMENT already exists somewhere in the file
    repl_offset = data.find(REPLACEMENT)

    if repl_offset < 0:
        # Inject at (null)'s location (must have enough room)
        end = null_offset + len(REPLACEMENT)
        if end > len(data):
            print("[PATCH] Not enough space to inject python312.dll string")
            return
        data[null_offset:end] = REPLACEMENT + b"\x00" * (end - null_offset - len(REPLACEMENT))
        repl_offset = null_offset
        with open(pyd_path, "wb") as f:
            f.write(data)
        print(f"[PATCH] Injected python312.dll at file offset {hex(repl_offset)}")

    # Re-read after file modification
    pe = pefile.PE(data=bytes(data))
    repl_rva = _offset_to_rva(pe, repl_offset)
    if repl_rva is None:
        print("[PATCH] Could not resolve python312.dll RVA after injection")
        return

    # Update the import descriptor — match null, empty, or python3.dll
    for imp in pe.DIRECTORY_ENTRY_IMPORT:
        dll = imp.dll
        if dll is not None:
            dll_stripped = dll.strip()
            if dll_stripped in (b"", b"(null)", b"python3.dll"):
                old_rva = imp.struct.Name
                imp.struct.Name = repl_rva
                imp.dll = REPLACEMENT
                print(f"[PATCH] Fixed [{dll.decode('utf-8', errors='replace')}]: "
                      f"Name RVA {hex(old_rva)} -> {hex(repl_rva)}")

    pe.write(pyd_path)
    print(f"[PATCH] Written to {pyd_path}")


def _offset_to_rva(pe, file_offset: int):
    for section in pe.sections:
        va = section.VirtualAddress
        raw_start = section.PointerToRawData
        raw_end = raw_start + section.SizeOfRawData
        if raw_start <= file_offset < raw_end:
            return va + (file_offset - raw_start)
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-_vulturevision.pyd>")
        sys.exit(1)
    patch_pyd(sys.argv[1])
