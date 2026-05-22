import cv2
import os
import uuid
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

IMAGES_DIR = os.getenv("IMAGES_DIR", "images")
CROPS_DIR = os.path.join(IMAGES_DIR, "crops")


def ensure_crops_dir():
    Path(CROPS_DIR).mkdir(parents=True, exist_ok=True)


ensure_crops_dir()


def normalize_coordinates(
    x, y, width, height,
    image_width: int,
    image_height: int,
):
    """
    If coordinates are between 0 and 1 they are normalized — multiply by
    image dimensions to get pixel values.
    If coordinates are already pixels (greater than 1) leave them as is.
    """
    if x <= 1.0 and y <= 1.0:
        x = int(x * image_width)
        y = int(y * image_height)
        width = int(width * image_width)
        height = int(height * image_height)
    else:
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
    return x, y, width, height


def crop_row_from_image(
    page_image_path: str,
    x: int,
    y: int,
    width: int,
    height: int,
    padding: int = 12,
) -> str:
    """Crop just the row out of the full page image with padding on all sides."""
    image = cv2.imread(page_image_path)

    image_height, image_width = image.shape[:2]

    x, y, width, height = normalize_coordinates(x, y, width, height, image_width, image_height)

    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(image_width, x + width + padding)
    y2 = min(image_height, y + height + padding)

    print(f"""
--- CROP DEBUG ---
page_image_path : {page_image_path}
raw x,y,w,h     : {x},{y},{width},{height}
image dimensions: {image_width}x{image_height}
padded x1,y1    : {x1},{y1}
padded x2,y2    : {x2},{y2}
------------------
""")

    cropped = image[y1:y2, x1:x2]

    filename = f"crop_{uuid.uuid4().hex[:8]}.jpg"
    save_path = os.path.join(CROPS_DIR, filename)
    cv2.imwrite(save_path, cropped)

    return save_path


def crop_row_highlighted(
    page_image_path: str,
    x: int,
    y: int,
    width: int,
    height: int,
    padding: int = 12,
) -> str:
    """Crop the row with a yellow semi-transparent highlight so the engineer clearly sees it."""
    image = cv2.imread(page_image_path)

    image_height, image_width = image.shape[:2]

    x, y, width, height = normalize_coordinates(x, y, width, height, image_width, image_height)

    highlighted = image.copy()

    cv2.rectangle(
        highlighted,
        (x, y),
        (x + width, y + height),
        (0, 255, 255),
        -1,
    )

    blended = cv2.addWeighted(
        highlighted, 0.3,
        image, 0.7,
        0,
    )

    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(image_width, x + width + padding)
    y2 = min(image_height, y + height + padding)

    print(f"""
--- CROP DEBUG ---
page_image_path : {page_image_path}
raw x,y,w,h     : {x},{y},{width},{height}
image dimensions: {image_width}x{image_height}
padded x1,y1    : {x1},{y1}
padded x2,y2    : {x2},{y2}
------------------
""")

    cropped = blended[y1:y2, x1:x2]

    filename = f"crop_{uuid.uuid4().hex[:8]}.jpg"
    save_path = os.path.join(CROPS_DIR, filename)
    cv2.imwrite(save_path, cropped)

    return save_path


def full_page_with_highlight(
    page_image_path: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> str:
    """Return the full page with a green border rectangle drawn around the current row."""
    image = cv2.imread(page_image_path)

    image_height, image_width = image.shape[:2]

    x, y, width, height = normalize_coordinates(x, y, width, height, image_width, image_height)

    print(f"""
--- CROP DEBUG ---
page_image_path : {page_image_path}
raw x,y,w,h     : {x},{y},{width},{height}
image dimensions: {image_width}x{image_height}
highlight box   : ({x},{y}) → ({x+width},{y+height})
------------------
""")

    copy = image.copy()

    cv2.rectangle(
        copy,
        (x, y),
        (x + width, y + height),
        (0, 200, 100),
        3,
    )

    filename = f"page_{uuid.uuid4().hex[:8]}.jpg"
    save_path = os.path.join(CROPS_DIR, filename)
    cv2.imwrite(save_path, copy)

    return save_path
