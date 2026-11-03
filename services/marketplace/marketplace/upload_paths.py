
import uuid


def listing_photo_upload_path(instance, filename):
    """
    Generate a unique filename for each listing photo.
    The original user-supplied filename is not reused.
    """

    extension = filename.rsplit(".", 1)[-1].lower()

    if extension == "jpeg":
        extension = "jpg"

    if extension not in ("jpg", "png"):
        extension = "jpg"

    unique_name = uuid.uuid4().hex

    return f"listing_photos/{unique_name}.{extension}"
