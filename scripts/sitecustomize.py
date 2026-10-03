"""Keep the native engine and media DLLs available on Windows."""
import os
import sys
from pathlib import Path

_dll_handles = []
if sys.platform == 'win32':
    for directory in (Path(sys.prefix) / 'Library/bin', Path(sys.prefix) / 'Lib/site-packages/vulturevision'):
        if directory.is_dir():
            _dll_handles.append(os.add_dll_directory(str(directory)))
