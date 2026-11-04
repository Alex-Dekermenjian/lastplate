
from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_FORMATS = {"JPEG", "PNG"}


def validate_listing_photo(uploaded_file):
    if uploaded_file is None:
        return

    if uploaded_file.size > MAX_IMAGE_SIZE:
        raise ValidationError(
            "Image must not exceed 5 MB."
        )

    if uploaded_file.size == 0:
        raise ValidationError(
            "Image cannot be empty."
        )

    try:
        uploaded_file.seek(0)
        image_bytes = uploaded_file.read()

        # Check the file's actual content, not its extension.
        with Image.open(BytesIO(image_bytes)) as image:
            image_format = image.format
            image.verify()

        if image_format not in ALLOWED_FORMATS:
            raise ValidationError(
                "Only JPEG and PNG images are allowed."
            )

        # Verify that the image can actually be decoded.
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()

    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(
            "The uploaded file is not a valid image."
        )
    finally:
        uploaded_file.seek(0)
