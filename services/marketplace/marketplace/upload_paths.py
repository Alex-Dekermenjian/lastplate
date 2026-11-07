import uuid


def _unique_image_path(folder, filename):
    """A new random name in the folder; the user's filename is not reused."""

    extension = filename.rsplit(".", 1)[-1].lower()

    if extension == "jpeg":
        extension = "jpg"

    if extension not in ("jpg", "png"):
        extension = "jpg"

    return f"{folder}/{uuid.uuid4().hex}.{extension}"


def listing_photo_upload_path(instance, filename):
    """Generate a unique filename for each listing photo."""
    return _unique_image_path("listing_photos", filename)


# LP-37: Restaurant cover photos and logos
def shop_photo_upload_path(instance, filename):
    return _unique_image_path("shop_photos", filename)


def shop_logo_upload_path(instance, filename):
    return _unique_image_path("shop_logos", filename)
