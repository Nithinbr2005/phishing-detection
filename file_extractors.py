"""Text extraction from various file types for phishing detection."""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Allowed MIME types and extensions
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_PDF_EXT = {".pdf"}
ALLOWED_TEXT_EXT = {".txt", ".text", ".eml", ".msg"}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXT | ALLOWED_PDF_EXT | ALLOWED_TEXT_EXT


def extract_from_text_file(content: bytes, filename: str) -> str:
    """Extract text from plain text file."""
    try:
        return content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Error decoding text file: {e}")
        raise ValueError("Could not read text file. Ensure it uses UTF-8 encoding.")


def extract_from_pdf(content: bytes, filename: str) -> str:
    """Extract text from PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF support requires 'pypdf' package. Install with: pip install pypdf")

    try:
        reader = PdfReader(io.BytesIO(content))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        text = "\n".join(text_parts).strip()
        if not text:
            raise ValueError("No text could be extracted from the PDF. It may be a scanned image.")
        return text
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        raise ValueError(f"Could not extract text from PDF: {str(e)}")


def extract_from_image(content: bytes, filename: str) -> str:
    """Extract text from image using OCR."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise ValueError(
            "Image OCR requires 'pytesseract' and 'Pillow'. Install with: pip install pytesseract Pillow. "
            "You also need Tesseract OCR installed on your system: https://github.com/tesseract-ocr/tesseract"
        ) from e

    try:
        image = Image.open(io.BytesIO(content))
        # Configure pytesseract path.
        # Priority: environment variables 'TESSERACT_CMD' or 'TESSERACT_PATH' if provided,
        # otherwise use a common Windows default if it exists.
        tesseract_cmd = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        else:
            default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            try:
                if os.path.exists(default_path):
                    pytesseract.pytesseract.tesseract_cmd = default_path
            except Exception:
                # If checking the default path fails, continue; pytesseract will raise a clearer error later.
                pass
        # Convert to RGB if necessary (e.g., RGBA, P mode)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        text = pytesseract.image_to_string(image)
        text = text.strip()
        if not text:
            raise ValueError("No text could be extracted from the image. Ensure the image contains readable text.")
        return text
    except Exception as e:
        if "tesseract" in str(e).lower() or "is not installed" in str(e).lower():
            raise ValueError(
                "Tesseract OCR is not installed. Download from: https://github.com/tesseract-ocr/tesseract"
            ) from e
        logger.error(f"Error extracting image text: {e}")
        raise ValueError(f"Could not extract text from image: {str(e)}") from e


def extract_text_from_file(content: bytes, filename: str) -> str:
    """
    Extract text from uploaded file based on type.
    Supports: .txt, .eml, .msg, .pdf, .jpg, .jpeg, .png, .gif, .webp, .bmp
    """
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Allowed: text (.txt, .eml), PDF (.pdf), images (.jpg, .png, .gif, .webp, .bmp)"
        )

    if ext in ALLOWED_TEXT_EXT:
        return extract_from_text_file(content, filename)
    if ext in ALLOWED_PDF_EXT:
        return extract_from_pdf(content, filename)
    if ext in ALLOWED_IMAGE_EXT:
        return extract_from_image(content, filename)

    raise ValueError(f"Unsupported file type: {ext}")
