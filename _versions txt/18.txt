import datetime
import os
import time
import threading
import numpy as np
import pyperclip
import re
import cv2
from PIL import Image, ImageEnhance, ImageGrab, ImageOps, ImageDraw, ImageTk
from pytesseract import pytesseract
import keyboard
import pystray
from pystray import MenuItem as item
from PIL import Image as PILImage
import sys
import tkinter as tk
import ctypes

# Make the app DPI aware for Windows scaling
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

# Global state
timestamp = None
pics_dir_path = None
dir_path = os.path.dirname(os.path.realpath(__file__))
pics_dir_path = os.path.join(dir_path, "pics")
running = True
tray_icon = None
snip_window = None

# Add pics folder if not exist
if not os.path.exists(pics_dir_path):
    os.makedirs(pics_dir_path)


def get_screen_size():
    """Get actual screen size accounting for DPI scaling"""
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    return width, height


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
    
    def start(self):
        """Start the snipping overlay"""
        # Take screenshot of entire screen first (at actual resolution)
        self.screenshot = ImageGrab.grab(all_screens=True)
        
        # Get actual screen dimensions
        screen_width, screen_height = get_screen_size()
        
        # Create fullscreen overlay
        self.root = tk.Tk()
        self.root.withdraw()  # Hide initially
        
        # Make DPI aware
        try:
            self.root.tk.call('tk', 'scaling', 1.0)
        except:
            pass
        
        # Remove window decorations and make fullscreen
        self.root.overrideredirect(True)
        self.root.geometry(f"{screen_width}x{screen_height}+0+0")
        self.root.attributes('-topmost', True)
        self.root.config(cursor="cross")
        
        # Create canvas with screenshot as background
        self.canvas = tk.Canvas(
            self.root,
            width=screen_width,
            height=screen_height,
            highlightthickness=0
        )
        self.canvas.pack()
        
        # Create darkened version of screenshot for overlay effect
        darkened = self.screenshot.copy()
        darkened = Image.blend(darkened, Image.new('RGB', darkened.size, (0, 0, 0)), 0.4)
        
        # Resize to match screen if different (handles DPI scaling)
        if darkened.size != (screen_width, screen_height):
            darkened = darkened.resize((screen_width, screen_height), Image.Resampling.LANCZOS)
        
        self.photo_image = ImageTk.PhotoImage(darkened)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo_image)
        
        # Bind events
        self.canvas.bind('<Button-1>', self.on_mouse_down)
        self.canvas.bind('<B1-Motion>', self.on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_mouse_up)
        self.root.bind('<Escape>', self.on_escape)
        
        # Instructions text
        self.canvas.create_text(
            screen_width // 2, 30,
            text="Click and drag to select area. Press ESC to cancel.",
            fill='white',
            font=('Arial', 16, 'bold')
        )
        
        # Show window
        self.root.deiconify()
        self.root.focus_force()
        self.root.mainloop()
    
    def on_mouse_down(self, event):
        """Mouse button pressed"""
        self.start_x = event.x
        self.start_y = event.y
        
        # Create rectangle
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline='#00ff00', width=2
        )
    
    def on_mouse_drag(self, event):
        """Mouse is being dragged"""
        self.current_x = event.x
        self.current_y = event.y
        
        # Update rectangle
        if self.rect:
            self.canvas.coords(
                self.rect,
                self.start_x, self.start_y,
                self.current_x, self.current_y
            )
    
    def on_mouse_up(self, event):
        """Mouse button released - capture the region"""
        self.current_x = event.x
        self.current_y = event.y
        
        # Calculate the bounding box (handle any direction of drag)
        x1 = min(self.start_x, self.current_x)
        y1 = min(self.start_y, self.current_y)
        x2 = max(self.start_x, self.current_x)
        y2 = max(self.start_y, self.current_y)
        
        # Close the overlay
        self.root.destroy()
        
        # Check if selection is valid (minimum size)
        if x2 - x1 > 10 and y2 - y1 > 10:
            # Get the actual screenshot dimensions
            screenshot_width, screenshot_height = self.screenshot.size
            screen_width, screen_height = get_screen_size()
            
            # Calculate scaling factors if screenshot size differs from screen size
            scale_x = screenshot_width / screen_width
            scale_y = screenshot_height / screen_height
            
            # Apply scaling to coordinates
            actual_x1 = int(x1 * scale_x)
            actual_y1 = int(y1 * scale_y)
            actual_x2 = int(x2 * scale_x)
            actual_y2 = int(y2 * scale_y)
            
            # Crop the screenshot to the selected region
            cropped = self.screenshot.crop((actual_x1, actual_y1, actual_x2, actual_y2))
            self.callback(cropped)
        else:
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            print("[{}] Selection too small, cancelled.".format(current_time))
    
    def on_escape(self, event):
        """ESC pressed - cancel"""
        self.root.destroy()
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        print("[{}] Snipping cancelled.".format(current_time))


def enhance_image(image):
    # Upscale for better detail
    image = upscale_and_set_dpi(image, scale_factor=2.0, dpi=300)

    # Convert PIL Image to OpenCV format
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    # Convert to grayscale - Tesseract works well with grayscale
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # Detect if dark background (code editors often have dark themes)
    # Use median of edge pixels to detect background color
    edge_pixels = np.concatenate([
        gray[0, :], gray[-1, :],  # top and bottom rows
        gray[:, 0], gray[:, -1]   # left and right columns
    ])
    bg_color = int(np.median(edge_pixels))
    
    # If dark background (< 128), invert to white background for better OCR
    if bg_color < 128:
        gray = cv2.bitwise_not(gray)
        bg_color = 255  # After inversion, background is white

    # Add padding matching the background color
    padding = 30
    gray = cv2.copyMakeBorder(gray, padding, padding, padding, padding, 
                               cv2.BORDER_CONSTANT, value=bg_color)

    # Convert back to PIL format
    enhanced_image = Image.fromarray(gray)
    
    return enhanced_image


def upscale_and_set_dpi(image, scale_factor=2.0, method='bicubic', dpi=300):
    """Upscales the image using the specified method and sets the DPI"""
    
    # Convert PIL Image to OpenCV format
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    
    new_width = int(img_cv.shape[1] * scale_factor)
    new_height = int(img_cv.shape[0] * scale_factor)
    dim = (new_width, new_height)

    if method == 'bicubic':
        upscaled_img_cv = cv2.resize(img_cv, dim, interpolation=cv2.INTER_CUBIC)
    elif method == 'lanczos':
        upscaled_img_cv = cv2.resize(img_cv, dim, interpolation=cv2.INTER_LANCZOS4)
    else:
        raise ValueError("Invalid upscaling method. Choose 'bicubic' or 'lanczos'.")

    # Convert back to PIL format
    upscaled_image = Image.fromarray(cv2.cvtColor(upscaled_img_cv, cv2.COLOR_BGR2RGB))
    
    # Set DPI
    upscaled_image.info['dpi'] = (dpi, dpi)

    return upscaled_image


def save_image(image):
    """Saves the image to the 'pics' directory and returns the path"""
    global timestamp
    timestamp = str(time.time()).replace(".", "")
    image_path = os.path.join(pics_dir_path, f"{timestamp}_clipboard_image.png")
    image.save(image_path, "PNG")
    return image_path


def save_text(text):
    """Saves the OCR output to a file and copies it to the clipboard"""
    pyperclip.copy(text)
    with open(os.path.join(pics_dir_path, f"{timestamp}_clipboard_text.txt"), 'w') as f:
        f.write(text)
    current_time = datetime.datetime.now().strftime("%H:%M:%S")
    print("[{}] OCR output has been copied to the clipboard.".format(current_time))


def ocr_me(image, image_path):
    # Use image_to_data to get bounding box info for indentation detection
    config = "--psm 6 --oem 3"
    
    # Get detailed OCR data with bounding boxes
    data = pytesseract.image_to_data(image, lang='eng', config=config, output_type=pytesseract.Output.DICT)
    
    # Group words by line number and reconstruct with indentation
    lines = {}
    for i, text in enumerate(data['text']):
        if text.strip():  # Skip empty entries
            line_num = data['line_num'][i]
            block_num = data['block_num'][i]
            key = (block_num, line_num)
            
            if key not in lines:
                lines[key] = {'x': data['left'][i], 'words': []}
            lines[key]['words'].append(text)
    
    # Sort lines by block and line number
    sorted_keys = sorted(lines.keys())
    
    # Find minimum x position (leftmost text) to calculate relative indentation
    if lines:
        min_x = min(line['x'] for line in lines.values())
    else:
        min_x = 0
    
    # Estimate character width based on image scale (2x upscale * typical char width)
    # Average monospace font char is ~7-10 pixels, after 2x upscale = ~14-20
    char_width = 16
    
    # Reconstruct text with indentation
    result_lines = []
    for key in sorted_keys:
        line = lines[key]
        # Calculate indentation based on x offset from minimum
        indent_pixels = line['x'] - min_x
        raw_indent = indent_pixels // char_width
        
        # Round to nearest 4 spaces (Python PEP 8 standard indentation)
        indent_spaces = round(raw_indent / 4) * 4
        
        # Join words with single space and add indentation
        line_text = ' '.join(line['words'])
        result_lines.append(' ' * indent_spaces + line_text)
    
    ocr_text = '\n'.join(result_lines)
    
    # Post-processing to fix common Python OCR errors
    ocr_text = fix_python_ocr_errors(ocr_text)
    
    # Clean up non-ASCII characters but preserve whitespace
    ocr_text = re.sub(r'[^\x00-\x7F]+', '', ocr_text)
    return ocr_text


def fix_python_ocr_errors(text):
    """Fix common OCR errors specific to Python code"""
    
    # GLOBAL FIX: Normalize corrupted quote characters
    # 'ô' is often misread as '"' or vice versa in some fonts/encodings
    text = text.replace('ô', '"')
    text = text.replace('”', '"') # Smart quotes
    text = text.replace('“', '"')
    text = text.replace('‘', "'")
    text = text.replace('’', "'")
    
    # Fix broken function names explicitly
    text = text.replace('def on_mouse down', 'def on_mouse_down')
    
    # GLOBAL FIX: 1 <-> l confusion
    
    # GLOBAL FIX: 1 <-> l confusion
    # When '1' appears between lowercase letters, it's almost always 'l'
    # e.g., "wind1ll" -> "windll", "se1f" -> "self"
    text = re.sub(r'([a-z])1([a-z])', r'\1l\2', text)
    
    # GLOBAL FIX: 0 <-> o confusion  
    # When '0' appears between lowercase letters, it's often 'o'
    # e.g., "c0de" -> "code"
    text = re.sub(r'([a-z])0([a-z])', r'\1o\2', text)
    
    # Cleanup: fix triple letters that result from over-correction
    # e.g., "windlll" -> "windll" (triple l is almost never valid)
    text = re.sub(r'lll+', 'll', text)
    text = re.sub(r'ooo+', 'oo', text)
    
    # Fix @ being misread as 0 in code contexts
    text = re.sub(r'\[@\]', '[0]', text)
    text = re.sub(r'\[@,', '[0,', text) # Slice [0, :]
    text = re.sub(r', @\]', ', 0]', text) # Slice [:, 0]
    text = re.sub(r'\(@\)', '(0)', text)
    text = re.sub(r'\(@,', '(0,', text)
    text = re.sub(r', @\)', ', 0)', text)
    text = re.sub(r', @,', ', 0,', text)
    text = re.sub(r'= @$', '= 0', text, flags=re.MULTILINE)
    text = re.sub(r'= @([,\)\s])', r'= 0\1', text)
    text = re.sub(r'(\d)@([,\)\s\]])', r'\1\2', text)
    text = re.sub(r'(\d)@$', r'\1', text, flags=re.MULTILINE)

    # Fix common misreads for list/array closing brackets
    # e.g. "1D)" -> "])" or "1D" -> "]" at end of block
    text = re.sub(r'1Dl', '])', text)
    text = re.sub(r'1D\)', '])', text)
    
    # Fix capitalization errors
    text = re.sub(r'\bOpencV\b', 'OpenCV', text)
    text = re.sub(r'\bCv2\b', 'cv2', text)
    
    # Fix "Word. Word" -> "Word.Word" (space after dot)
    # e.g. "Image. fromarray" -> "Image.fromarray"
    text = re.sub(r'(\w+)\. (\w+)', r'\1.\2', text)
    
    # Fix mixed quotes like method="bicubic' -> method='bicubic'
    # Heuristic: if starts with " and ends with ', change ' to "
    text = re.sub(r'="([^"]+)\'', r'="\1"', text)
    
    # ===== STEP 1: Fix underscore-space patterns first =====
    
    # Fix "_ word" pattern (underscore space word) -> "_word"
    text = re.sub(r'_ (\w)', r'_\1', text)
    
    # Fix "word _" pattern (word space underscore) -> "word_"  
    text = re.sub(r'(\w) _', r'\1_', text)
    
    # Fix double underscore patterns like "_ _init_ _" -> "__init__"
    text = re.sub(r'_ _', '__', text)
     
    # ===== STEP 2: Fix snake_case patterns (excluding keywords) =====
    
    # Define keywords to exclude from merging
    keywords = r'(?<!def )(?<!if )(?<!elif )(?<!else )(?<!class )(?<!raise )(?<!return )(?<!import )(?<!from )(?<!while )(?<!for )(?<!with )(?<!try )(?<!except )'
    
    # Fix "word word_word" pattern but NOT "def _init__"
    # Use negative lookbehind for keywords
    text = re.sub(r'(?<!\bdef )(?<!\bif )(?<!\bclass )(\w+) (_\w+)', r'\1\2', text)
    
    # Fix "scale factor" -> "scale_factor" (specific heuristic for common kwarg)
    text = re.sub(r'scale factor', 'scale_factor', text)
     
    # Fix common variable patterns: "word word =" -> "word_word ="
    text = re.sub(r'(\w+) (\w+) =', r'\1_\2 =', text)
    
    # Fix "return word word" pattern
    text = re.sub(r'\breturn (\w+) (\w+)$', r'return \1_\2', text, flags=re.MULTILINE)
    
    # Fix "word word," pattern for multiple assignments
    text = re.sub(r'(\w+) (\w+),', r'\1_\2,', text)
    
    # Fix "word word)" pattern in function arguments
    text = re.sub(r'(\w+) (\w+)\)', r'\1_\2)', text)
    
    # Fix function call patterns: "word word(" -> "word_word("
    text = re.sub(r'(\w+) (\w+)\(', r'\1_\2(', text)
    
    # Fix "def word word(" pattern for method definitions  
    def fix_def_pattern(match):
        words = match.group(1).split()
        return 'def ' + '_'.join(words) + '('
    text = re.sub(r'def ([\w ]+)\(', fix_def_pattern, text)
    
    # Fix ".word word" pattern for attribute access
    # EXCLUDE: not, and, or, in, is (Python operators)
    text = re.sub(r'\.(\w+) (\w+)(?! )', r'.\1_\2', text)
    
    # Fix spaces in subscript access - but exclude 'not', 'and', 'or'
    text = re.sub(r'(?<!\bnot )(?<!\band )(?<!\bor )(\w+) (\w+)\.', r'\1_\2.', text)
    
    # Fix "} word" pattern in f-strings
    text = re.sub(r'\} (\w+)', r'}_\1', text)
    
    # ===== STEP 3: Fix keyword patterns LAST =====
    
    # First fix merged keywords (def_ -> def )
    text = re.sub(r'\bdef_', 'def ', text)
    text = re.sub(r'\bif_', 'if ', text)
    text = re.sub(r'\belif_', 'elif ', text)
    text = re.sub(r'\belse_', 'else:', text)
    text = re.sub(r'\bclass_', 'class ', text)
    text = re.sub(r'\braise_', 'raise ', text)
    text = re.sub(r'\breturn_', 'return ', text)
    text = re.sub(r'\bimport_', 'import ', text)
    text = re.sub(r'\bfrom_', 'from ', text)
    text = re.sub(r'\bwhile_', 'while ', text)
    text = re.sub(r'\bfor_', 'for ', text)
    text = re.sub(r'\bwith_', 'with ', text)
    text = re.sub(r'\btry_', 'try:', text)
    text = re.sub(r'\bexcept_', 'except ', text)
    text = re.sub(r'\bnot_', 'not ', text)
    text = re.sub(r'\band_', 'and ', text)
    text = re.sub(r'\bor_', 'or ', text)
    text = re.sub(r'\bin_', 'in ', text)
    text = re.sub(r'\bis_', 'is ', text)
    text = re.sub(r'\bof_', 'of ', text)
    
    # THEN fix __init__ pattern (AFTER def_ split, so "def init__" becomes "def __init__")
    text = re.sub(r'\bdef init__', 'def __init__', text)
    text = re.sub(r'\bdef _init__', 'def __init__', text)
    text = re.sub(r'\bdef _init_', 'def __init__', text)
    
    # Fix coordinate variables ending in l instead of 1
    # e.g., "xl", "yl", "x2", "y2" usually mean x1, y1
    # Check for x or y followed by l, at word boundary
    text = re.sub(r'\b([xy])l\b', r'\1\1', text) # xl -> x1 (wait, no. xl -> x1)
    # Actually, let's just fix xl -> x1 and yl -> y1 specifically
    text = re.sub(r'\bxl\b', 'x1', text)
    text = re.sub(r'\byl\b', 'y1', text)
    
    # Fix squashed lines (class/def/docstring on same line)
    # e.g. "class Foo: ""doc"" def __init__" -> split newlines
    # Add newline before 'def ' if it follows quote or colon
    text = re.sub(r'(:|""|"\)|'') def ', r'\1\n    def ', text)
    
    # NEW: Fix squashed class definitions
    # e.g., 'class SnippingTool: ""Overlay...' -> 'class SnippingTool:\n    """Overlay...'
    text = re.sub(r'(class [\w]+): ("+)', r'\1:\n    \2', text)
    
    # NEW: Fix docstring markers - DO NOT use ^ anchor to handle squashed lines
    # Fix ""Start -> """Start (common OCR error to miss one quote)
    text = re.sub(r'(?<!")""(?=[A-Z])', r'"""', text)
    
    # Fix "mStart -> """Start (specific OCR error where "" looks like m)
    text = re.sub(r'"m(?=[A-Z])', r'"""', text)
    
    # Fix """End" -> """End""" (fix missing end quotes)
    text = re.sub(r'(""".+)"$', r'\1"""', text, flags=re.MULTILINE)
    text = re.sub(r'(""".+)""$', r'\1"""', text, flags=re.MULTILINE) # Handle 4 or more quotes wrapping down to 3
    
    # Fix 4 or 5 quotes at end -> 3 quotes
    text = re.sub(r'"{4,}$', r'"""', text, flags=re.MULTILINE)
    
    # Fix "ctypes ." -> "ctypes."
    text = re.sub(r'\bctypes \.', 'ctypes.', text)
    
    # Fix "windll1" -> "windll"
    text = re.sub(r'\bwindll1\b', 'windll', text)
    
    # Fix squashed lines with variables
    # e.g. "var = None var2 = None" -> split newlines (heuristics)
    # detecting " = " followed by value, then space, then word, then " ="
    text = re.sub(r'( = [A-Za-z0-9_"\']+) ([A-Za-z_][\w_]* = )', r'\1\n\2', text)
    
    # Fix dict keys with spaces/missing quotes: data[ text ] -> data['text']
    text = re.sub(r"data\[\s*([a-zA-Z_]+)\s*\]", r"data['\1']", text)
    text = re.sub(r"data\[\s*line num\s*\]", "data['line_num']", text)
    text = re.sub(r"data\[\s*['\"]?([a-zA-Z_]+)['\"]?\s*\]", r"data['\1']", text)
    text = re.sub(r"data\[\s*['\"]([a-zA-Z_]+)['\"]\s*\]", r"data['\1']", text)
    
    # Fix string literal quotes
    text = re.sub(r'lang=["\']([^"\']+)["\']', r"lang='\1'", text)
    text = re.sub(r"method\s*==\s*['\"]?bicubic['\"]?", "method == 'bicubic'", text)
    text = re.sub(r"method\s*==\s*['\"]?lanczos['\"]?", "method == 'lanczos'", text)

    # Fix file mode: , w') -> , 'w')
    text = re.sub(r",\s*w'\)", ", 'w')", text)
    
    # Fix variable split: upscaled img_cv -> upscaled_img_cv
    text = re.sub(r"upscaled img_cv", "upscaled_img_cv", text)
    
    # Fix string closure: .png) -> .png")
    text = re.sub(r"\.png\)", '.png")', text)

    # --- Iteration 2 Fixes ---
    
    # Fix missing opening quote in dict keys: [ key'] -> ['key']
    text = re.sub(r"\[\s*([a-zA-Z_]+)['\"]?\s*\]", r"['\1']", text)
    
    # Fix yy -> y1 (variable confusion)
    text = re.sub(r"\byy\b", "y1", text)
    
    # Fix squashed lines with _self: .x_self. -> .x\n        self.
    text = re.sub(r"event\.x_self", "event.x\n        self", text)
    
    # Fix malformed hex color #00effe0 -> #00ff00
    text = re.sub(r"#00effe0", "#00ff00", text)

    # --- Iteration 3 Fixes ---
    
    # Fix squashed "def ...: """...""" code" -> "def ...:\n    """..."""\n    code"
    # Matches: def, args, colon, space?, docstring, space, code
    text = re.sub(r'(def [^:]+:)\s*(?<!")("""[^"]+""")\s*(.+)', r'\1\n    \2\n    \3', text)
    
    # Fix generic "word_self." pattern -> "word\n        self."
    text = re.sub(r'(\w+)_self\.', r'\1\n        self.', text)
    
    # --- E2E Verification Fixes ---
    
    # Fix "2.@" -> "2.0"
    text = re.sub(r'(\d)\.@', r'\1.0', text)
    
    # Fix "3@@" -> "300" (or any digit followed by @@)
    text = re.sub(r'(\d)@@', r'\100', text)
    
    # Fix "[@|" -> "[0]"
    text = re.sub(r'\[@\|', '[0]', text)
    
    # Fix "Mouse 1s" -> "Mouse is" (in docstrings mostly, but safe enough if '1s')
    text = re.sub(r'\bMouse 1s\b', 'Mouse is', text)
    
    # Fix "*"" at end of string -> """
    text = re.sub(r'"\*"$', '"""', text, flags=re.MULTILINE)
    
    # Fix missing docstring start quotes: def ...): Docstring..."""
    # Heuristic: ): space CapitalLetter..."""
    text = re.sub(r'\):\s*([A-Z].*?)(?=""")', r'): """\1', text)
    
    # Fix missing generic string start quote: (Invalid...) -> ("Invalid...)
    # Heuristic: (Invalid
    text = re.sub(r'\(Invalid', '("Invalid', text)
    
    # Fix [ word] -> ['word'] (generic dict key with validation)
    # The previous regex might have missed cases with strict spacing or existing quotes
    # New attempt: [ space word ]
    text = re.sub(r'\[ (\w+) \]', r"['\1']", text)
    text = re.sub(r'\[ (\w+)\]', r"['\1']", text)
    # Specific fix for info[ dpi] which is stubborn
    text = re.sub(r"\[\s*dpi\s*\]", "['dpi']", text)
    
    # --- Iteration 4 Fixes ---
    
    # Fix "method == bicubic" missing quotes (ensure it captures if no quotes present)
    text = re.sub(r"method\s*==\s*(?!['\"])(\w+)", r"method == '\1'", text)
    
    # Fix "dpi=@" or "dpi=@@" -> "dpi=300" (heuristic for this var)
    text = re.sub(r"dpi=@+", "dpi=300", text)
    
    # Fix squashed "def ...): Docstring...""" missing start quote
    # Matches: ): space CapitalLetter..."""
    # Fix: insert """ before the CapitalLetter
    text = re.sub(r'(\):\s*)([A-Z].*?""")', r'\1"""\2', text)
    
    # Fix generic missing string start quote: (Invalid...) -> ("Invalid...)
    text = re.sub(r'\(Invalid', '("Invalid', text)
    
    # Fix "Mouse button pressed" start quote (specific case if generic failed)
    text = re.sub(r'(\):\s*)Mouse button', r'\1"""Mouse button', text)

    # --- Iteration 5: String Replacements for Stubborn Bugs ---
    # Regexes failed for these, likely due to context or invisible chars.
    # Updated to regex to handle flexible spacing.
    
    text = text.replace('info[ dpi]', "info['dpi']") # specific enough
    text = re.sub(r"method\s*=\s*['\"]bicubic,\s*dpi", "method='bicubic', dpi", text)
    text = re.sub(r"ValueError\s*\(\s*Invalid", 'ValueError("Invalid', text)
    
    # Fix on_mouse_down squashed line (handles variable spaces)
    # def ...): Mouse -> def ...): """Mouse
    text = re.sub(r'(def on_mouse_(down|drag)\(self, event\):\s*)Mouse', r'\1"""Mouse', text)
    
    # dpi=@ replacement
    text = re.sub(r'dpi\s*=\s*@\)', 'dpi=300)', text)

    # --- Round 2 Fixes ---
    
    # Fix dunder methods/vars: _file_ or file_ -> __file__
    text = re.sub(r'\b_file_\b', '__file__', text)
    text = re.sub(r'\bfile_\b', '__file__', text)
    
    # Fix init_ -> __init__
    text = re.sub(r'\binit_\b', '__init__', text)
    
    # Fix squashed assignments (Value_var): None_var -> None\nvar, True_var -> True\nvar
    text = re.sub(r'\bNone_([a-zA-Z])', r'None\n\1', text)
    text = re.sub(r'\bTrue_([a-zA-Z])', r'True\n\1', text)
    
    # Fix specific variable split: pics dir_path -> pics_dir_path
    text = re.sub(r'\bpics dir_path\b', 'pics_dir_path', text)
    
    # Fix squashed class docstring: class X: "Doc -> class X:\n    """Doc
    text = re.sub(r'(class \w+:)\s*"([^"]+)', r'\1\n    """\2', text)
    
    # Fix missing docstring start quote: "Get actual -> """Get actual
    # (Matches "Capital...)
    text = re.sub(r'(^\s*)"([A-Z])', r'\1"""\2', text, flags=re.MULTILINE)
    
    # Fix literal: pics) -> "pics")
    text = re.sub(r'pics\)', '"pics")', text)

    # --- Iteration 6 (Round 2 Refinements) ---
    
    # Fix squashed globals with space: None var = ... -> None\nvar = ...
    # Matches None/True/False space var space =
    text = re.sub(r'\b(None|True|False)\s+([a-zA-Z_][a-zA-Z0-9_]*\s*=)', r'\1\n\2', text)
    
    # Fix squashed class docstring with triple quotes: class X: """Doc -> class X:\n    """Doc
    text = re.sub(r'(class [^:]+:)\s*("""|")', r'\1\n    """', text)
    
    # Fix "pics)" stubbornness (force quotes)
    text = text.replace(', pics)', ', "pics")')
    
    # Remove space in __init__ (
    text = text.replace('__init__ (', '__init__(')
    
    # Fix "Get actual..."" missing end quote -> "Get actual..."""
    # If ends with "" but not """
    text = re.sub(r'""$', '"""', text, flags=re.MULTILINE)
    
    # --- Iteration 7: Stubborn Round 2 Fixes ---
    
    # Fix "pics") -> "pics") (missing opening quote)
    text = text.replace('pics")', '"pics")')
    text = text.replace(', pics)', ', "pics")') # Cover both cases
    
    # Fix squashed "running = True" after "))"
    # ...realpath(_file_)) running = True
    text = re.sub(r'\)\) running', '))\nrunning', text)
    
    # Fix stubborn docstring quotes (Direct replacement for safety)
    text = text.replace('"Get actual', '"""Get actual')
    text = text.replace('"Overlay window', '"""Overlay window')
    
    # Generic class docstring fix attempt 2 (force newline)
    # Match: class Name: "Doc -> class Name:\n    """Doc
    text = re.sub(r'(class \w+:)\s*"([A-Z])', r'\1\n    """\2', text)

    # --- Iteration 8: Final Specific Fixes ---
    
    # Fix "pics" stubbornness (force quotes around arg)
    text = text.replace('os.path.join(dir_path, pics)', 'os.path.join(dir_path, "pics")')
    
    # Fix SnippingTool docstring
    text = text.replace('class SnippingTool: "Overlay', 'class SnippingTool:\n    """Overlay')
    
    # Fix "Get actual" end quotes (handle trailing space)
    text = re.sub(r'""\s*$', '"""', text, flags=re.MULTILINE)

    # --- Iteration 9: Robust Fixes ---
    
    # Fix pics argument generally: join(..., pics") or join(..., pics) -> join(..., "pics")
    # Matches: , space? pics quote? paren
    text = re.sub(r',\s*pics["\']?\)', ', "pics")', text)
    
    # Fix squashed class docstring robustly
    # match class ...: space quotes ...
    text = re.sub(r'(class [^:]+:)\s*["\']{1,3}(?=[A-Z])', r'\1\n    """', text)
    
    # Fix broken docstring end quotes (if they ended up as "")
    text = re.sub(r'(["\']{3}[^"\']*["\']{2})\s*$', r'\1"', text, flags=re.MULTILINE)

    # --- Iteration 10: Critical Fixes ---
    
    # Fix 'pics' variable in join path (Critical for logic)
    # Matches join(dir_path, pics) with any spacing
    text = re.sub(r'join\s*\(\s*dir_path\s*,\s*pics\s*\)', 'join(dir_path, "pics")', text)

    # --- Iteration 11 (Round 3): Excessive Quotes Normalization ---
    
    # Fix start of docstring: match any 2+ quotes and spaces followed by not-quote
    # e.g. class X: """""Doc -> class X: """Doc
    # Regex: (optional prefix) (2+ quotes) (content)
    # We want to normalize to """
    # This handles `"""""Get` -> `"""Get`
    text = re.sub(r'([:=]\s*)["\']{2,}(?=[A-Z])', r'\1"""', text)
    
    # Fix end of docstring: match 2+ quotes at end of line (possibly with spaces)
    # e.g. scaling"" -> scaling"""
    text = re.sub(r'["\']{2,}\s*$', '"""', text, flags=re.MULTILINE)

    # --- Iteration 12 (Round 3 Refinements) ---
    
    # Direct replacement for excessive quotes (Safest first)
    text = text.replace('"""""', '"""')
    text = text.replace('""""', '"""')
    
    # Fix single quote at end of docstring line: ...scaling" -> ...scaling"""
    # Matches line starting with optional space, then """, then content, then single " at EOL
    text = re.sub(r'(^\s*"""[^"\n]+)"\s*$', r'\1"""', text, flags=re.MULTILINE)
    
    # Fix "dangling" single quote start: class X: "Doc -> class X: """Doc
    # (If previous regex missed it due to context)
    text = re.sub(r'(: |=)\s*"([A-Z])', r'\1"""\2', text)

    # --- Iteration 13: Final Cleanup (Round 3) ---
    
    # Re-apply SnippingTool fix which now matches part of the normalized text
    text = text.replace('class SnippingTool: "Overlay', 'class SnippingTool:\n    """Overlay')
    text = text.replace('class SnippingTool: """Overlay', 'class SnippingTool:\n    """Overlay')
    
    # Fix scaling"" -> scaling"""
    text = text.replace('scaling""', 'scaling"""')
    
    # General fix for "" at end of docstring -> """
    # (If it wasn't caught by regex)
    text = text.replace('""\n', '"""\n')
    text = re.sub(r'([a-z])""$', r'\1"""', text, flags=re.MULTILINE)

    # --- Iteration 14 (Round 4): Squashed Definitions ---
    
    # Fix squashed def with docstring and code on same line
    # Matches: def name(...): """Doc""" code
    # We want:
    # def name(...):
    #     """Doc"""
    #     code
    text = re.sub(r'(def\s+[^:]+:)\s*("""[^"]+""")\s+(.+)', r'\1\n    \2\n    \3', text)
    
    # Also handle single quoted docstrings in same pattern
    text = re.sub(r'(def\s+[^:]+:)\s*("[^"]+")\s+(.+)', r'\1\n    \2\n    \3', text)
    
    # Robust fix for class SnippingTool: "..." that refuses to split
    # Forces newline and triple quotes for class docstrings
    text = re.sub(r'(class [^:]+:)\s*("{1,3})([^"\n]+)("{1,3})', r'\1\n    """\3"""', text)

    # --- User Reported Fixes (17654985119631622) ---
    
    # Fix squashed comments: "code # Comment" -> "code\n    # Comment"
    text = re.sub(r'(\S) (# [A-Z])', r'\1\n    \2', text)
    
    # Fix prematurely closed functions with space: "name( )" -> "name("
    text = re.sub(r'(\w+)\(\s+\)', r'\1(', text)
    
    # Fix bad indentation step: 4 spaces followed by 8 spaces (generalized)
    # Pattern: Line A (4 spaces) NOT ending in colon/paren/etc -> Line B (8 spaces) indented too far
    # Run multiple times to handle consecutive lines and skip over comments
    for _ in range(3):
        text = re.sub(r'(^\s{4}(?!def|class|if|for|while|try|except|with|else|elif|return).*[a-zA-Z0-9_\)\]\}\"\'\.]\s*)((?:\n\s*#.*)*)\n\s{8}(\S)', r'\1\2\n    \3', text, flags=re.MULTILINE)

    # Fix function argument indentation: if line after open paren has same indentation as line with paren
    # Matches: (indent)(content...() \n (same_indent)(non-whitespace)
    # ONLY if line ends with ( or , (to avoid matching closed calls like func())
    text = re.sub(r'(^(\s+).*?\(.*[,\(]\s*)\n\2(\S)', r'\1\n\2    \3', text, flags=re.MULTILINE)
    
    # Fix missing closing parenthesis after width=... pattern if not present
    # Matches: width=digits (not followed by ) or comment)
    text = re.sub(r'(width=\d+)(?!\)|,)', r'\1)', text)

    # --- Final Cleanup: Excessive Quotes ---
    # Normalize any sequence of 4+ quotes at end of line to 3 quotes
    text = re.sub(r'"{4,}\s*$', '"""', text, flags=re.MULTILINE)

    return text


def process_image(image):
    """Process the captured image"""
    try:
        if image:
            image = enhance_image(image)
            image_path = save_image(image)
            ocr_text = ocr_me(image, image_path)
            save_text(ocr_text)
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            print("[{}] OCR completed! Text copied to clipboard.".format(current_time))
        else:
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            print("[{}] No image to process.".format(current_time))
    except Exception as e:
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        print("[{}] Error processing image: {}".format(current_time, str(e)))


def start_snipping():
    """Start the snipping tool overlay"""
    # Run in a new thread to avoid blocking
    def run_snip():
        snip = SnippingTool(process_image)
        snip.start()
    
    thread = threading.Thread(target=run_snip)
    thread.daemon = True
    thread.start()


def on_pause_key():
    """Callback for when Pause key is pressed"""
    current_time = datetime.datetime.now().strftime("%H:%M:%S")
    print("[{}] Pause key pressed - starting snipping tool...".format(current_time))
    start_snipping()


def create_tray_icon():
    """Create the system tray icon image"""
    # Create a simple icon (64x64)
    icon_size = 64
    icon_image = PILImage.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
    
    # Draw a simple circle with "T" for OCR/Text
    draw = ImageDraw.Draw(icon_image)
    # Blue background circle
    draw.ellipse([4, 4, icon_size-4, icon_size-4], fill=(30, 144, 255, 255))
    # Draw "T" for text/OCR
    draw.text((icon_size//2 - 8, icon_size//2 - 14), "T", fill=(255, 255, 255, 255))
    
    return icon_image


def reload_script(icon, item):
    """Reload the script"""
    global running
    running = False
    keyboard.unhook_all()
    icon.stop()
    os.execv(sys.executable, ['python'] + sys.argv)


def exit_app(icon, item):
    """Exit the application"""
    global running
    running = False
    keyboard.unhook_all()
    icon.stop()
    os._exit(0)


def setup_tray():
    """Setup and run the system tray icon"""
    global tray_icon
    
    menu = pystray.Menu(
        item('Reload', reload_script),
        item('Exit', exit_app)
    )
    
    tray_icon = pystray.Icon(
        "OCR Tool",
        create_tray_icon(),
        "OCR Tool (Press Pause to snip)",
        menu
    )
    
    return tray_icon


def main():
    """Main entry point"""
    global running
    
    print("OCR Tool started!")
    print("Press 'Pause' key to start snipping")
    print("Right-click system tray icon for options")
    
    # Register the Pause key hotkey
    keyboard.on_press_key('pause', lambda e: on_pause_key())
    
    # Setup and run system tray (this blocks)
    icon = setup_tray()
    icon.run()


if __name__ == "__main__":
    main()
