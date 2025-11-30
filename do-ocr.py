#!/usr/bin/env python3

from pathlib import Path
import subprocess
import json
import sys
import threading
import platform
import numpy as np
from concurrent.futures import ThreadPoolExecutor

# Detect platform (macOS vs Linux/Docker)
IS_MACOS = platform.system() == 'Darwin'
IS_LINUX = platform.system() == 'Linux'

# Import OCR engines based on platform
if IS_MACOS:
    pass  # macOS uses binary OCR
elif IS_LINUX:
    try:
        import pytesseract
        from PIL import Image
        print("Tesseract OCR available for Linux", file=sys.stderr)
    except ImportError as e:
        print(f"Failed to import pytesseract: {e}", file=sys.stderr)
        sys.exit(1)

lock = threading.Lock()


def safe_pad(img):
    """Pad images smaller than 50x50 to ensure minimum image size for Tesseract."""
    h, w = img.shape[:2]
    if h < 50 or w < 50:
        pad_h = max(0, 50 - h)
        pad_w = max(0, 50 - w)
        img = np.pad(img, ((0, pad_h), (0, pad_w)), mode="constant", constant_values=255)
    return img


def ocr_file(image):
    bucket_key = image.stem.replace("snap_", '')

    try:
        lock.acquire()
        # check if the file name is already in the dictionary, and skip it if so
        if bucket_key in ocr_dict:
            lock.release()
            return

        lock.release()

        recognized_text = ""
        err = ""

        if IS_MACOS:
            # Use macOS binary for macOS platforms
            # !! mac m1/m2 only: use the version from https://github.com/glowinthedark/macOCR/releases or the OCR binary in this repo
            # https://github.com/xulihang/macOCR/blob/main/OCR/main.swift
            proc = subprocess.run(["./OCR", "en-US", "false", "true", str(image.absolute())],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)

            recognized_text = proc.stdout.decode()
            err = proc.stderr.decode()

            if err:
                print("😱", err)
        elif IS_LINUX:
            # Use Tesseract OCR for Linux/Docker platforms (lightweight and stable)
            try:
                # Open image and convert to grayscale for better Tesseract performance
                img = Image.open(str(image.absolute())).convert('L')
                img_array = np.array(img)

                # Validate image before processing
                if img_array.size == 0:
                    raise ValueError(f"Invalid image size for {bucket_key}")

                # Pad small images to ensure minimum size
                img_array = safe_pad(img_array)

                # Tesseract optimization: PSM 7 (single text line) + OEM 1 (LSTM only)
                # PSM 7 is 3-5x faster than default PSM 3 for subtitle text
                custom_config = r'--oem 1 --psm 7'

                recognized_text = pytesseract.image_to_string(
                    img_array,
                    lang='eng',
                    config=custom_config
                )

                # Clean up result
                if not recognized_text.strip():
                    recognized_text = ""
            except Exception as e:
                err = str(e)
                print(f"Tesseract OCR processing failed for {bucket_key}: {e}", file=sys.stderr)
        else:
            err = "No OCR engine available (not macOS and Tesseract not initialized)"
            print(f"😱 {err}")

        if not err:
            lock.acquire()

            print(bucket_key, recognized_text)
            ocr_dict[bucket_key] = recognized_text
            sorted_data = {k: ocr_dict[k] for k in sorted(ocr_dict)}
            with open(results_file, "w") as f:
                json.dump(sorted_data, f, ensure_ascii=False, indent=1)
    finally:
        # Only release lock if it was successfully acquired
        try:
            if lock.locked():
                lock.release()
        except RuntimeError:
            # Lock was never acquired
            pass


if __name__ == '__main__':

    folder_name = sys.argv[1]
    results_file = sys.argv[2]

    # load the existing dictionary from json file, or create an empty one
    res_file = Path(results_file)
    if res_file.exists():
        ocr_dict = json.load(res_file.open(encoding='utf-8'))
    else:
        ocr_dict = {}

    ##### TODO: tweak the threadpool size to your liking depending on available system resources
    # Tesseract is lightweight (~50-100MB per worker), safe to use multiple workers
    # Start with 4 workers for good performance with reasonable memory usage
    max_workers = 4
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        img: Path
        for img in Path(folder_name).glob("*.png"):
            executor.submit(ocr_file, img)
