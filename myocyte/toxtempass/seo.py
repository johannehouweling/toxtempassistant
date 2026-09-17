"""Crawler-facing endpoints: robots.txt, sitemap.xml and the root favicon."""

from datetime import date

from django.contrib.sitemaps import Sitemap
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET

# Everything below these prefixes needs a login, so crawlers would only collect
# redirects to the landing page.
ROBOTS_DISALLOWED_PREFIXES = (
    "/admin/",
    "/add/",
    "/assay/",
    "/beta/",
    "/filter-",
    "/init/",
    "/investigation/",
    "/login/orcid/",
    "/orcid/",
    "/password-reset/",
    "/settings/",
    "/stats/",
    "/study/",
    "/workspace/",
)


# When each public page last changed; this order is also the sitemap's order.
# Google schedules recrawls from <lastmod> but distrusts a date that always
# claims "today", so bump these only when the page's content really changes.
# It ignores <changefreq> and <priority> entirely.
PAGE_LASTMOD = {
    "overview": date(2026, 9, 12),
    "about": date(2026, 9, 12),
    "toxtemp_questions": date(2026, 9, 12),
    "signup": date(2026, 9, 12),
}


class StaticViewSitemap(Sitemap):
    """The public pages worth indexing."""

    changefreq = "monthly"

    def items(self) -> list[str]:
        """Return the URL names of the public pages."""
        return list(PAGE_LASTMOD)

    def location(self, item: str) -> str:
        """Resolve a URL name to its path."""
        return reverse(item)

    def lastmod(self, item: str) -> date:
        """Return the date the page's content last changed."""
        return PAGE_LASTMOD[item]


SITEMAPS = {"static": StaticViewSitemap}


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    """Allow the public pages, keep crawlers out of the app, and point to the sitemap."""
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in ROBOTS_DISALLOWED_PREFIXES]
    lines.append(f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}")
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def favicon(request: HttpRequest) -> HttpResponsePermanentRedirect:
    """Redirect /favicon.ico, which browsers and crawlers request by convention."""
    return HttpResponsePermanentRedirect(static("toxtempass/favicon.ico"))
