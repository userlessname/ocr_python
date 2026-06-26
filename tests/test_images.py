import os
import sys
import glob
import difflib
from PIL import Image
from typing import List, Tuple

# Ensure the root directory is in the path so we can import core
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.processor import ImageProcessor
from core.inference import load_models


class TestImageProcessor(ImageProcessor):
    """
    Subclass of ImageProcessor to override saving behavior for testing.
    """
    def __init__(self, pics_dir: str, tests_dir: str):
        super().__init__(pics_dir)
        self.tests_dir = tests_dir
        self.captured_text = ""
        self.current_image_path = ""

    def save_image(self, image: Image.Image) -> str:
        # Skip saving image into @pics - just set timestamp and return original path
        import time
        self.timestamp = str(time.time()).replace(".", "")
        return self.current_image_path

    def save_text(self, text: str):
        # Capture text for comparison instead of writing to pics/
        self.captured_text = text


def run_tests():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    pics_dir = os.path.join(base_dir, 'pics')
    tests_dir = os.path.join(base_dir, 'tests')
    
    if not os.path.exists(tests_dir):
        os.makedirs(tests_dir)

    # Load Surya models once for all tests
    print("Loading Surya OCR models...")
    load_models()

    # Find all image files in pics
    image_paths = glob.glob(os.path.join(pics_dir, "*_clipboard_image.png"))
    
    if not image_paths:
        print(f"No images found in {pics_dir}")
        return

    processor = TestImageProcessor(pics_dir, tests_dir)
    
    results: List[Tuple[str, bool]] = []
    failures = 0

    for img_path in image_paths:
        base_name = os.path.basename(img_path).replace("_clipboard_image.png", "")
        print(f"\n--- Testing Image: {base_name} ---")
        
        # Load the expected text
        expected_txt_path = img_path.replace("_clipboard_image.png", "_clipboard_text.txt")
        expected_text = ""
        if os.path.exists(expected_txt_path):
            with open(expected_txt_path, 'r', encoding='utf-8') as f:
                expected_text = f.read()
        else:
            print(f"Warning: Expected text file not found at {expected_txt_path}")

        # Process the image
        img = Image.open(img_path)
        processor.current_image_path = img_path
        processor.captured_text = ""  # Reset
        
        processor.process_image(img)
        
        # Save actual output to tests/ for comparison
        output_txt_path = os.path.join(tests_dir, f"{base_name}_actual.txt")
        with open(output_txt_path, 'w', encoding='utf-8') as f:
            f.write(processor.captured_text)
        
        # Compare
        actual = processor.captured_text
        if expected_text:
            matcher = difflib.SequenceMatcher(None, expected_text, actual)
            similarity = matcher.ratio()
            # Show diff context if mismatch
            if similarity < 1.0:
                print(f"  Similarity: {similarity:.2%}")
                diff = list(difflib.unified_diff(expected_text.splitlines(keepends=True),
                                                  actual.splitlines(keepends=True),
                                                  fromfile='expected', tofile='actual'))
                if diff:
                    print("  Diff:")
                    print("".join(diff[-5:]))  # show last few lines of diff
            else:
                print("  Exact match.")
            if similarity >= 0.95:
                results.append((base_name, True))
            else:
                results.append((base_name, False))
                failures += 1
        else:
            # No expected text, just record as unknown
            print("  No expected text to compare.")
            results.append((base_name, False))
            failures += 1
    
    print("\n--- Test Summary ---")
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
    print(f"Total failures: {failures}")
    if failures > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
