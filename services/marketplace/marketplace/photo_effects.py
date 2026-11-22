"""LP-54: photo effects. Normal, Blurred, or Blurred with magic.

For Blurred and Magic offers customers only ever get a strongly blurred copy
that the server makes here, never the original's address. With magic, a
signed-in customer taps one point once, and the server cuts a small circle of
the original around that point (transparent outside the circle). The page
lays it over the blurred photo at the same spot.
"""

import base64
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .models import Listing

NORMAL = Listing.PhotoEffect.NORMAL

# The blurred copy: at most this wide or tall. It keeps the original's shape,
# so a point given as percentages lands on the same spot in both.
BLURRED_MAX_SIDE = 640
# Shrunk to 1/24 of its size before it is blown up and blurred again, so no
# detail survives that a sharpening filter could bring back.
BLUR_SHRINK = 24

# The original is worked on at most this size when cutting a peek.
PEEK_WORK_SIDE = 800
# The peek circle's diameter, as a share of the photo's shorter side.
PEEK_DIAMETER = Decimal("0.28")

TWO_PLACES = Decimal("0.01")


def _open(file):
    # Closed again straight away, so the file can still be replaced or
    # deleted (Windows refuses to delete an open file).
    with file.open("rb") as handle:
        image = Image.open(handle)
        image.load()
    # Phone photos are often stored sideways with an orientation tag; the
    # browser turns them upright, so the copies must be upright too.
    return ImageOps.exif_transpose(image)


def blurred_copy(file):
    """A strongly blurred JPEG of the photo, the same shape as the original."""
    image = _open(file).convert("RGB")
    image.thumbnail((BLURRED_MAX_SIDE, BLURRED_MAX_SIDE))
    width, height = image.size

    tiny = image.resize(
        (max(1, width // BLUR_SHRINK), max(1, height // BLUR_SHRINK)),
        Image.Resampling.BILINEAR,
    )
    blurred = tiny.resize((width, height), Image.Resampling.BICUBIC).filter(
        ImageFilter.GaussianBlur(radius=max(width, height) / 40)
    )

    buffer = BytesIO()
    blurred.save(buffer, format="JPEG", quality=80)
    return ContentFile(buffer.getvalue(), name="blurred.jpg")


def sync_blurred_photo(listing, photo_changed=False):
    """Keep the blurred copy in step with the photo and the effect.

    Blurred or Magic with a photo: a blurred copy exists (made again when the
    photo changed). Normal or no photo: there is none.
    """
    old_name = listing.photo_blurred.name or ""
    wanted = bool(listing.photo) and listing.photo_effect != NORMAL

    if wanted and old_name and not photo_changed:
        return listing

    if wanted:
        listing.photo_blurred.save("blurred.jpg", blurred_copy(listing.photo), save=False)
    else:
        if not old_name:
            return listing
        listing.photo_blurred = ""

    Listing.objects.filter(pk=listing.pk).update(photo_blurred=listing.photo_blurred.name or "")

    if old_name and old_name != (listing.photo_blurred.name or ""):
        listing.photo_blurred.storage.delete(old_name)

    return listing


def customer_photo_path(listing):
    """The only photo address a customer may be sent, or None.

    Normal: the photo. Blurred and Magic: the blurred copy, never the
    original, even if the copy is missing.
    """
    if not listing.photo:
        return None
    if listing.photo_effect == NORMAL:
        return listing.photo.url
    return listing.photo_blurred.url if listing.photo_blurred else None


def make_peek(listing, x, y):
    """The circle of the original around (x%, y%): PNG bytes and diameter.

    The diameter is returned as a percentage of the photo's width, so the
    page can size it whatever size the photo is shown at.
    """
    image = _open(listing.photo).convert("RGBA")
    image.thumbnail((PEEK_WORK_SIDE, PEEK_WORK_SIDE))
    width, height = image.size

    radius = max(1, int(min(width, height) * PEEK_DIAMETER / 2))
    diameter = radius * 2
    centre_x = round(float(x) / 100 * width)
    centre_y = round(float(y) / 100 * height)

    # Near an edge the box goes past the photo; Pillow fills that part with
    # transparent pixels, so the circle is simply cut off there.
    square = image.crop((centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius))

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    circle = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    circle.paste(square, (0, 0), mask)

    buffer = BytesIO()
    circle.save(buffer, format="PNG", optimize=True)
    size = (Decimal(diameter) * 100 / Decimal(width)).quantize(TWO_PLACES, ROUND_HALF_UP)
    return buffer.getvalue(), size


def peek_payload(peek):
    """What the page needs to draw a saved peek."""
    return {
        "listing_id": peek.listing_id,
        "x": float(peek.x),
        "y": float(peek.y),
        "size": float(peek.size),
        "image": "data:image/png;base64," + base64.b64encode(bytes(peek.cutout)).decode("ascii"),
    }
