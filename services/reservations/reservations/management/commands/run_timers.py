"""LP-25: python manage.py run_timers [--every SECONDS]

Without --every: one run, then exit (for a scheduler such as cron or a hosted
cron job, once a minute). With --every 60: runs forever, once a minute (the
Compose "timers" container). Safe to run in several places at once.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from reservations.timers import run_timers


class Command(BaseCommand):
    help = "Expire late orders, consume Paid orders, clean up stuck orders and retry releases."

    def add_arguments(self, parser):
        parser.add_argument("--every", type=int, default=0, metavar="SECONDS",
                            help="Keep running, once every SECONDS (at least 5). Default: run once.")

    def handle(self, *args, every=0, **options):
        if every and every < 5:
            raise CommandError("--every must be at least 5 seconds.")
        while True:
            if every:
                # A long-running loop: drop a connection the database closed meanwhile.
                close_old_connections()
            counts = run_timers()
            self.stdout.write(" ".join(f"{name}={'error' if count is None else count}"
                                       for name, count in counts.items()))
            if not every:
                return
            time.sleep(every)
