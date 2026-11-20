"""{% demo_clock %}: the date and time box at the top of every page."""

from django import template
from django.utils import timezone

from config import demo_clock

register = template.Library()


@register.inclusion_tag("frontend/_demo_clock.html", takes_context=True)
def demo_clock_box(context):
    if not demo_clock.enabled():
        return {"enabled": False}
    now = timezone.localtime(timezone.now())
    request = context.get("request")
    offset = demo_clock.offset_seconds()
    return {
        "enabled": True,
        "csrf_token": context.get("csrf_token"),
        "now_ms": int(now.timestamp() * 1000),
        "offset_ms": offset * 1000,
        "now_text": now.strftime("%a %d %b %Y \u00b7 %H:%M:%S"),
        "input_value": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "shifted": offset != 0,
        "next": request.get_full_path() if request else "/",
    }
