import ctypes
from ctypes import wintypes
from PIL import Image as PILImage, ImageDraw
sound_enabled = True

def set_sound_enabled(enabled: bool):
    global sound_enabled
    sound_enabled = enabled

def _set_dpi_awareness():
    """Set process DPI awareness (Per-Monitor v2). Must be called before any GUI init."""
    try:
        # Modern: Per-Monitor DPI Aware v2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # Fallback: System DPI Aware
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_screen_size():
    """Get actual screen size accounting for DPI scaling"""
    _set_dpi_awareness()
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    return width, height

def get_mouse_position():
    """Get the current mouse cursor position"""
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y

def get_monitor_at_cursor():
    """
    Returns the monitor info dict for the screen where the mouse cursor is located.
    """
    mouse_x, mouse_y = get_mouse_position()
    from screeninfo import get_monitors
    monitors = get_monitors()
    
    for monitor in monitors:
        if (monitor.x <= mouse_x < monitor.x + monitor.width and
            monitor.y <= mouse_y < monitor.y + monitor.height):
            return {
                'x': monitor.x,
                'y': monitor.y,
                'width': monitor.width,
                'height': monitor.height,
                'name': monitor.name
            }
    
    for monitor in monitors:
        if monitor.is_primary:
            return {
                'x': monitor.x, 'y': monitor.y, 'width': monitor.width, 'height': monitor.height, 'name': monitor.name
            }
            
    if monitors:
        m = monitors[0]
        return {'x': m.x, 'y': m.y, 'width': m.width, 'height': m.height, 'name': m.name}
        
    width, height = get_screen_size()
    return {'x': 0, 'y': 0, 'width': width, 'height': height, 'name': 'default'}

def create_tray_icon_idle():
    """Create the idle-state tray icon (blue circle with white T)"""
    icon_size = 64
    icon_image = PILImage.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_image)
    draw.ellipse([4, 4, icon_size - 4, icon_size - 4], fill=(30, 144, 255, 255))  # DodgerBlue
    draw.text((icon_size // 2 - 8, icon_size // 2 - 14), "T", fill=(255, 255, 255, 255))
    return icon_image


def create_tray_icon_busy():
    """Create the busy-state tray icon (orange circle with white T)"""
    icon_size = 64
    icon_image = PILImage.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_image)
    draw.ellipse([4, 4, icon_size - 4, icon_size - 4], fill=(255, 140, 0, 255))  # DarkOrange
    draw.text((icon_size // 2 - 8, icon_size // 2 - 14), "T", fill=(255, 255, 255, 255))
    return icon_image


# Backward-compatible alias
create_tray_icon = create_tray_icon_idle


def play_sound_start():
    """Short tick sound when OCR starts. Uses Windows API, no files needed."""
    if sound_enabled:
        ctypes.windll.user32.MessageBeep(0x00000000)  # MB_OK = default beep


def play_sound_done():
    """Short double-beep when OCR completes."""
    if sound_enabled:
        try:
            # PC speaker beep: 800 Hz, 120 ms
            ctypes.windll.kernel32.Beep(800, 120)
        except Exception:
            # Fallback if PC speaker unavailable
            ctypes.windll.user32.MessageBeep(0x00000040)  # MB_ICONASTERISK