"""Crawler endpoints: robots.txt, sitemap.xml, favicon, Google verification file."""

from django.contrib.sitemaps import Sitemap
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET

from toxtempass import config

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


class StaticViewSitemap(Sitemap):
    """The public pages worth indexing."""

    changefreq = "monthly"

    def items(self) -> list[str]:
        """Return the URL names of the public pages."""
        return ["overview", "about", "toxtemp_questions", "signup"]

    def location(self, item: str) -> str:
        """Resolve a URL name to its path."""
        return reverse(item)


SITEMAPS = {"static": StaticViewSitemap}


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    """Allow the public pages, keep crawlers out of the app, and point to the sitemap."""
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in ROBOTS_DISALLOWED_PREFIXES]
    lines.append(f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}")
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def google_verification(request: HttpRequest) -> HttpResponse:
    """Serve the file Google Search Console fetches to confirm we own the site."""
    return HttpResponse(
        f"google-site-verification: {config.google_verification_file}",
        content_type="text/html",
    )


@require_GET
def favicon(request: HttpRequest) -> HttpResponsePermanentRedirect:
    """Redirect /favicon.ico, which browsers and crawlers request by convention."""
    return HttpResponsePermanentRedirect(static("toxtempass/favicon.ico"))
