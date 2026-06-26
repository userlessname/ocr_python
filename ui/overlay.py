import tkinter as tk
import datetime
import ctypes
from PIL import Image, ImageGrab, ImageTk
from screeninfo import get_monitors
from utils.helpers import get_monitor_at_cursor

class SnippingTool:
    """Overlay window for selecting screen region"""
    
    def __init__(self, callback):
        self.callback = callback
        self.start_x = None
        self.start_y = None
        self.current_x = None
        self.current_y = None
        self.root = None
        self.canvas = None
        self.rect = None
        self.screenshot = None
        self.photo_image = None
        self.monitor_info = None
    
    def start(self):
        """Start the snipping overlay"""
        self.monitor_info = get_monitor_at_cursor()
        mon_x = self.monitor_info['x']
        mon_y = self.monitor_info['y']
        mon_width = self.monitor_info['width']
        mon_height = self.monitor_info['height']
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{current_time}] Opening snipping tool on monitor: {self.monitor_info['name']} ({mon_width}x{mon_height} at {mon_x},{mon_y})")
        
        full_screenshot = ImageGrab.grab(all_screens=True)
        monitors = get_monitors()
        virtual_left = min(m.x for m in monitors)
        virtual_top = min(m.y for m in monitors)
        
        crop_x1 = mon_x - virtual_left
        crop_y1 = mon_y - virtual_top
        crop_x2 = crop_x1 + mon_width
        crop_y2 = crop_y1 + mon_height
        
        self.screenshot = full_screenshot.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        
        self.root = tk.Tk()
        self.root.withdraw()
        
        try:
            self.root.tk.call('tk', 'scaling', 1.0)
        except:
            pass
        
        self.root.overrideredirect(True)
        self.root.geometry(f"{mon_width}x{mon_height}+{mon_x}+{mon_y}")
        self.root.attributes('-topmost', True)
        self.root.config(cursor="cross", bg='black')
        
        # Set canvas background to black to prevent white flash during map
        self.canvas = tk.Canvas(self.root, width=mon_width, height=mon_height, highlightthickness=0, bg='black')
        self.canvas.pack()
        
        darkened = self.screenshot.copy()
        darkened = Image.blend(darkened, Image.new('RGB', darkened.size, (0, 0, 0)), 0.4)
        
        if darkened.size != (mon_width, mon_height):
            darkened = darkened.resize((mon_width, mon_height), Image.Resampling.LANCZOS)
        
        self.photo_image = ImageTk.PhotoImage(darkened)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image)
        
        self.canvas.bind('<Button-1>', self.on_mouse_down)
        self.canvas.bind('<B1-Motion>', self.on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_mouse_up)
        self.root.bind('<Escape>', self.on_escape)
        
        self.canvas.create_text(
            mon_width // 2, 30,
            text="Click and drag to select area. Press ESC to cancel.",
            fill='white',
            font=('Arial', 16, 'bold')
        )
        
        # Ensure all geometry and background changes are processed before showing
        self.root.update_idletasks()
        
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.root.focus_force()
        
        try:
            hwnd = self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TOPMOST = 0x00000008
            ex_style = ctypes.windll.user32.GetWindowLongA(hwnd, GWL_EXSTYLE)
            new_style = ex_style | WS_EX_LAYERED | WS_EX_TOPMOST
            ctypes.windll.user32.SetWindowLongA(hwnd, GWL_EXSTYLE, new_style)
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x00000002)
        except Exception as e:
            print(f"Could not set WS_EX_LAYERED: {e}")
        
        try:
            self.root.grab_set_global()
        except Exception:
            self.root.grab_set()
        
        self.force_topmost()
        self.root.mainloop()

    def force_topmost(self):
        """Aggressively keep window on top (called once)"""
        try:
            target_hwnd = self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_TOPMOST = 0x00000008
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            
            current_fg = ctypes.windll.user32.GetForegroundWindow()
            ex_style = ctypes.windll.user32.GetWindowLongA(target_hwnd, GWL_EXSTYLE)
            if not (ex_style & WS_EX_TOPMOST):
                ctypes.windll.user32.SetWindowLongA(target_hwnd, GWL_EXSTYLE, ex_style | WS_EX_TOPMOST)
            
            if current_fg != target_hwnd:
                try:
                    ctypes.windll.user32.SetWindowPos(current_fg, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
                except:
                    pass
            
            ctypes.windll.user32.BringWindowToTop(target_hwnd)
            ctypes.windll.user32.SetWindowPos(target_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.focus_force()
            
        except Exception:
            pass
    
    def on_mouse_down(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='#00ff00', width=2)
    
    def on_mouse_drag(self, event):
        self.current_x = event.x
        self.current_y = event.y
        if self.rect:
            self.canvas.coords(self.rect, self.start_x, self.start_y, self.current_x, self.current_y)
    
    def on_mouse_up(self, event):
        self.current_x = event.x
        self.current_y = event.y
        x1, y1 = min(self.start_x, self.current_x), min(self.start_y, self.current_y)
        x2, y2 = max(self.start_x, self.current_x), max(self.start_y, self.current_y)
        
        self.root.destroy()
        
        if x2 - x1 > 10 and y2 - y1 > 10:
            screenshot_width, screenshot_height = self.screenshot.size
            mon_width, mon_height = self.monitor_info['width'], self.monitor_info['height']
            scale_x, scale_y = screenshot_width / mon_width, screenshot_height / mon_height
            
            actual_x1, actual_y1 = int(x1 * scale_x), int(y1 * scale_y)
            actual_x2, actual_y2 = int(x2 * scale_x), int(y2 * scale_y)
            
            cropped = self.screenshot.crop((actual_x1, actual_y1, actual_x2, actual_y2))
            self.callback(cropped)
        else:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Selection too small, cancelled.")
    
    def on_escape(self, event):
        self.root.destroy()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Snipping cancelled.")
