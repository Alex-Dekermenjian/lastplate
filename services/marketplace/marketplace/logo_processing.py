"""LP-66: tidy a restaurant's logo when it is uploaded.

Every logo becomes a small square PNG with the logo centred inside the
circle the website shows it in, so any shape looks right and loads fast:

- empty margins are trimmed, so the logo itself fills the badge;
- a logo with its own background (a red square, a photo) keeps that colour
  around it, so the badge looks seamless;
- a logo on a transparent background goes on white, or on dark brown when
  the logo itself is white or very light, so it never disappears.

The original file is not kept. Run after validate_listing_photo.
"""

from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageChops, ImageOps

SIZE = 256
# The badge is a circle: a centred square of 72% of its width fits inside it.
CONTENT = round(SIZE * 0.72)
WHITE = (255, 255, 255)
DARK = (58, 32, 22)  # --text in lastplate.css
LIGHT_LOGO = 200  # average brightness above which a logo needs a dark badge
TRANSPARENT = 16  # alpha at or below this counts as see-through


def _brightness(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def _pixels(image):
    """Every pixel as a tuple (Pillow 13 renamed getdata)."""
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if flattened else image.getdata())


def _visible_colour(rgba):
    """Average colour of the logo's solid pixels, measured at full size.

    Shrinking first would blend the logo with its see-through surroundings
    and make a white logo look grey, so up to ~20,000 pixels are sampled.
    """
    pixels = _pixels(rgba)
    step = max(1, len(pixels) // 20000)
    solid = [pixel[:3] for pixel in pixels[::step] if pixel[3] > 200]
    if not solid:
        return WHITE
    count = len(solid)
    return tuple(round(sum(channel) / count) for channel in zip(*solid))


def _corner_colour(image):
    """The colour in the four corners, if they agree; else None."""
    width, height = image.size
    corners = [image.getpixel(point) for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    first = corners[0]
    if all(max(abs(a - b) for a, b in zip(first, corner)) <= 12 for corner in corners):
        return first
    return None


def _trim(image, background):
    """Crop away a uniform border of the given colour (with a little tolerance)."""
    plain = Image.new("RGB", image.size, background)
    difference = ImageChops.difference(image, plain).convert("L").point(lambda v: 255 if v > 24 else 0)
    box = difference.getbbox()
    return image.crop(box) if box else image


def prepare_logo(uploaded_file):
    """Return the tidied logo as a ContentFile named logo.png."""
    uploaded_file.seek(0)
    with Image.open(uploaded_file) as original:
        image = ImageOps.exif_transpose(original)
        image.load()
    uploaded_file.seek(0)

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    see_through = sum(1 for value in _pixels(alpha.resize((64, 64))) if value <= TRANSPARENT) / (64 * 64)

    if see_through > 0.05:
        # A transparent logo: crop to what is visible, then choose a badge
        # colour it stands out on.
        box = alpha.point(lambda v: 255 if v > TRANSPARENT else 0).getbbox()
        if box:
            rgba = rgba.crop(box)
            alpha = rgba.getchannel("A")
        background = DARK if _brightness(_visible_colour(rgba)) > LIGHT_LOGO else WHITE
    else:
        # A logo with its own background: keep that colour around it.
        rgb = rgba.convert("RGB")
        background = _corner_colour(rgb) or WHITE
        rgb = _trim(rgb, background)
        rgba = rgb.convert("RGBA")

    # Scale up or down so the logo's longer side fills the circle's square.
    scale = CONTENT / max(rgba.width, rgba.height)
    rgba = rgba.resize((max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))), Image.LANCZOS)
    badge = Image.new("RGBA", (SIZE, SIZE), background + (255,))
    offset = ((SIZE - rgba.width) // 2, (SIZE - rgba.height) // 2)
    badge.alpha_composite(rgba, offset)

    output = BytesIO()
    badge.convert("RGB").save(output, format="PNG", optimize=True)
    return ContentFile(output.getvalue(), name="logo.png")
