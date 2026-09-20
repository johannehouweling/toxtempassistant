"""Check that this container can reach the outside services the app needs.

Model limits and prices come from LiteLLM's published catalogue, and the
USD->EUR rate Azure bills at comes from the Azure Retail Prices API. Both have
to be reachable from wherever the app actually runs, which is a different
question from whether they are reachable from a developer's laptop or from the
Docker host.

A deployment that can already draft answers is reaching its
``AZURE_E*_ENDPOINT``, so a failure here alongside working drafts means a
per-host allowlist rather than blocked egress.

Usage::

    poetry run python manage.py check_egress
    docker compose exec djangoapp python manage.py check_egress
    docker compose exec djangoapp python manage.py check_egress --timeout 30

Exits non-zero when a required service is unreachable, so it can gate a deploy.
"""

from __future__ import annotations

import os
import socket
import time
from argparse import ArgumentParser
from urllib.parse import urlsplit

import httpx
from django.core.management.base import BaseCommand

# The catalogue is needed from either host; jsdelivr mirrors the same file and
# is often reachable where raw.githubusercontent.com is not.
CATALOGUE_URLS = {
    "github-raw": (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    ),
    "jsdelivr": (
        "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/"
        "model_prices_and_context_window.json"
    ),
}
# A filter that matches nothing, so the reachability check costs a few bytes.
AZURE_PRICES_URL = (
    "https://prices.azure.com/api/retail/prices"
    "?%24filter=serviceName%20eq%20%27ZZZ%27"
)


class Command(BaseCommand):
    help = "Check outbound access to the model catalogue and Azure price list."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the command's options."""
        parser.add_argument(
            "--timeout",
            type=float,
            default=15.0,
            help="Seconds to wait per request (default: 15).",
        )

    def _probe(self, name: str, url: str, timeout: float) -> bool:
        """Report whether ``url`` answers, separating DNS from connection failures."""
        host = urlsplit(url).hostname or ""
        try:
            socket.gethostbyname(host)
        except OSError as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"  {name:13} DNS FAILED for {host} "
                    f"({type(exc).__name__}: {exc})"
                )
            )
            return False
        started = time.monotonic()
        try:
            response = httpx.head(url, timeout=timeout, follow_redirects=True)
            if response.status_code >= 400:
                # Some CDNs refuse HEAD; a GET still answers the question.
                response = httpx.get(url, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"  {name:13} CONNECT FAILED ({type(exc).__name__}: {exc})"
                )
            )
            return False
        elapsed_ms = int((time.monotonic() - started) * 1000)
        etag = response.headers.get("etag", "-")
        self.stdout.write(
            self.style.SUCCESS(
                f"  {name:13} HTTP {response.status_code} in {elapsed_ms} ms "
                f"etag={etag[:20]}"
            )
        )
        return response.status_code < 400

    def handle(self, *args: object, **options: object) -> None:
        """Probe each service and exit non-zero if a required one is unreachable."""
        timeout = float(options["timeout"])  # type: ignore[arg-type]
        proxies = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
        self.stdout.write(f"proxy environment: {proxies or 'none'}")

        self.stdout.write(self.style.HTTP_INFO("model catalogue (either will do)"))
        catalogue_ok = [
            self._probe(name, url, timeout) for name, url in CATALOGUE_URLS.items()
        ]

        self.stdout.write(self.style.HTTP_INFO("Azure retail prices (for the FX rate)"))
        prices_ok = self._probe("azure-prices", AZURE_PRICES_URL, timeout)

        problems = []
        if not any(catalogue_ok):
            problems.append("no model catalogue host is reachable")
        if not prices_ok:
            problems.append("the Azure price list is not reachable")
        if problems:
            self.stdout.write(
                self.style.ERROR(
                    "\nFAILED: "
                    + "; ".join(problems)
                    + ".\nAsk for these hosts to be allowlisted, or keep a copy of "
                    "the catalogue in the image instead."
                )
            )
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nOK: every required service answered."))
