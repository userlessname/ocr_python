# OCR Clipboard Image

This is a Python script that performs OCR on images copied to the clipboard. The script uses the `pytesseract` library to perform OCR and the `Pillow` library to manipulate images.

## Requirements

- Python 3.6 or later
- `pytesseract` library
- `Pillow` library
- `pyperclip` library
- pip install opencv-python
- You need to install tesseract into your pc, you can find link in source
- You can find to exe in source, using "pip install pyinstaller" for .py to .exe

## Installation

1. Clone this repository to your local machine.
2. Install the required libraries using pip: `pip install pytesseract pillow pyperclip`.

## Usage

1. Run the script using the command `python main.py`.
2. Copy an image to the clipboard (e.g. a screenshot of text).
3. The script will automatically detect the new image in the clipboard and perform OCR on it.
4. The OCR output (image and text) will be copied to the clipboard and saved to a text file in the `pics` directory.

## Notes

- The script enhances the image before performing OCR to improve accuracy.
- The OCR output is saved to a text file with the same name as the image file, with a ".txt" extension.
- The timestamp is used to name the image file and the OCR text file.
- The `pyperclip` library is used to access the clipboard. This library is not installed by default, so it must be installed separately (e.g. using `pip install pyperclip`).

## License

This project is licensed under the terms of the MIT license.
