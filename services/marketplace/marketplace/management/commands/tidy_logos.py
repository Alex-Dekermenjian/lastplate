"""LP-66: tidy logos uploaded before logos were tidied on upload.

    python manage.py tidy_logos

Safe to run more than once: a logo that is already a tidy badge is skipped.
"""

from django.core.management.base import BaseCommand
from PIL import Image, UnidentifiedImageError

from marketplace.logo_processing import SIZE, prepare_logo
from marketplace.models import Shop


class Command(BaseCommand):
    help = "Turn every existing shop logo into the square badge image the website shows."

    def handle(self, *args, **options):
        tidied = skipped = failed = 0
        for shop in Shop.objects.exclude(logo="").exclude(logo__isnull=True).order_by("id"):
            old_name = shop.logo.name
            try:
                with shop.logo.open("rb") as file:
                    with Image.open(file) as image:
                        if image.size == (SIZE, SIZE) and image.format == "PNG":
                            skipped += 1
                            continue
                    shop.logo.save("logo.png", prepare_logo(file), save=False)
            except (OSError, UnidentifiedImageError, ValueError):
                failed += 1
                self.stderr.write(f"Shop {shop.id}: its logo could not be read; left as it was.")
                continue
            Shop.objects.filter(pk=shop.pk).update(logo=shop.logo.name)
            shop.logo.storage.delete(old_name)
            tidied += 1
        self.stdout.write(self.style.SUCCESS(f"Tidied {tidied} logo(s); {skipped} already tidy; {failed} unreadable."))
