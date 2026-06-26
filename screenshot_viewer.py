import sys
import subprocess
import os

def open_screenshot(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return
    # Use default system viewer
    if sys.platform.startswith('darwin'):
        subprocess.call(('open', filepath))
    elif os.name == 'nt':
        os.startfile(filepath)
    elif os.name == 'posix':
        subprocess.call(('xdg-open', filepath))
    else:
        print("Unsupported OS")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python screenshot_viewer.py <filepath>")
    else:
        open_screenshot(sys.argv[1])
