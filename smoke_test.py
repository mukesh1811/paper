"""Check that the deployed Paper actually works, from outside it.

Paper's unit tests run against the source and against FastAPI in-process. Two
things live outside that reach and have both broken in production while every
test passed: the site is served from a different base path than it is developed
at, and the running service carries configuration that no test supplies. Both
are only visible by asking the deployed thing to do a real job.

So this fetches the real pages, and prepares a real document through the real
API, and fails loudly if it cannot.

    python smoke_test.py
    python smoke_test.py --site http://127.0.0.1:8000 --api http://127.0.0.1:8000

Deliberately uses only the standard library, so it can run anywhere Python
exists without installing anything first.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SITE = "https://mukesh1811.github.io/paper"
DEFAULT_API = "https://paper-api-608308929005.asia-south1.run.app"
# Small, public, and already the site's own example, so a smoke run costs about
# a tenth of a cent and finishes in seconds.
SAMPLE_SOURCE = "https://tolstoyarchive.org/Fiction/books/The%20Two%20Old%20Men.pdf"
READ_TIMEOUT_SECONDS = 180
USER_AGENT = "Paper-smoke-test/1.0"

failures: list[str] = []
passes = 0


def report(name: str, ok: bool, note: str = "", hint: str = "") -> bool:
    """Record one check and print it as it happens.

    ``note`` is what was measured and reads usefully either way. ``hint``
    explains why a failure matters, so it is only worth printing on one.
    """

    global passes
    if ok:
        passes += 1
        print(f"  PASS  {name}" + (f"  {note}" if note else ""))
    else:
        reason = " ".join(part for part in (note, hint) if part)
        failures.append(f"{name}: {reason}")
        print(f"  FAIL  {name}  {reason}")
    return ok


def fetch(url: str, *, timeout: int = 30) -> tuple[int, str]:
    """Return the status and body of one URL, without raising on an error code."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - any failure to reach it is a failure
        return 0, str(error)


def skip(name: str, why: str) -> None:
    """Say plainly that a check does not apply here, rather than passing it quietly."""

    print(f"  SKIP  {name}  {why}")


def check_pages(site: str, *, split_origin: bool) -> None:
    """The pages a visitor lands on, at the paths the deployed site really uses."""

    print("\nSite")

    status, home = fetch(f"{site}/")
    report("home page loads", status == 200, f"HTTP {status}")
    report("home has the URL form", 'id="url-form"' in home)

    # The reader lives at this path only after the deploy rewrites links. A 404
    # here is what a visitor got for months while every test passed.
    status, reader = fetch(f"{site}/read")
    report("reader page loads at /read", status == 200, f"HTTP {status}")
    report("reader has the preparation screen", 'id="preparation"' in reader)

    status, script = fetch(f"{site}/static/app.js")
    report("app.js loads", status == 200, f"HTTP {status}")

    # The source keeps a deployment token that the deploy substitutes. When the
    # API serves the site itself the token is meant to survive, because the
    # reader then calls its own origin - so this only means anything when the
    # site and the API are deployed apart.
    if split_origin:
        report(
            "app.js points at a real API",
            "__PAPER_API_URL__" not in reader,
            hint="the deploy did not substitute PAPER_API_URL",
        )
    else:
        skip("app.js points at a real API", "the API serves this site, so it calls its own origin")

    # The site is served from /paper/ in production and from / in development.
    # A site-absolute link built in the script is right in one and a 404 in the
    # other, and the deploy's link rewriting never touches the script.
    report(
        "app.js builds no site-absolute reader link",
        not re.search(r"""["'`]/read\?""", script),
        hint="a root-absolute /read? link will 404 under a base path",
    )

    # Local-only surface. Serving it would expose the event feed publicly.
    status, _ = fetch(f"{site}/telemetry.html")
    report("local telemetry viewer is not published", status == 404, f"HTTP {status}")


def check_api(api: str, site_origin: str, *, split_origin: bool) -> None:
    """The API surface the browser depends on, including its CORS answer."""

    print("\nAPI")

    status, _ = fetch(f"{api}/api/read", timeout=20)
    # A missing query parameter is the point: it proves routing works without
    # asking the pipeline to do anything.
    report("API is reachable", status in {200, 422}, f"HTTP {status}")

    if not split_origin:
        skip("telemetry endpoints accept the site origin", "same origin, so the browser never sends a preflight")
        return

    for path in ("reader-opened", "reading-progress"):
        request = urllib.request.Request(
            f"{api}/api/telemetry/{path}",
            method="OPTIONS",
            headers={
                "Origin": site_origin,
                "Access-Control-Request-Method": "POST",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                allowed = response.headers.get("access-control-allow-origin", "")
            report(f"telemetry/{path} accepts the site origin", allowed == site_origin, allowed or "no CORS header")
        except Exception as error:  # noqa: BLE001
            report(f"telemetry/{path} accepts the site origin", False, str(error))


def check_real_read(api: str) -> None:
    """Prepare a real document, which is the only check that exercises everything.

    This is the one that catches configuration. A missing model key, an expired
    credit balance, or a broken provider all look perfect to every other check
    and fail here.
    """

    print("\nA real read")

    query = urllib.parse.urlencode({"url": SAMPLE_SOURCE, "origin": "link"})
    request = urllib.request.Request(
        f"{api}/api/read/events?{query}",
        headers={"Accept": "text/event-stream", "User-Agent": USER_AGENT},
    )

    started = time.monotonic()
    stages: list[str] = []
    error_detail = None
    document = None

    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
            buffer = ""
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                while "\n\n" in buffer:
                    message, buffer = buffer.split("\n\n", 1)
                    event = "message"
                    data: list[str] = []
                    for line in message.split("\n"):
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        if line.startswith("data:"):
                            data.append(line[5:].strip())
                    try:
                        payload = json.loads("\n".join(data))
                    except json.JSONDecodeError:
                        continue
                    if event == "progress" and payload.get("stage") not in stages:
                        stages.append(payload.get("stage"))
                    if event == "error":
                        error_detail = payload.get("detail")
                    if event == "complete":
                        document = payload.get("document")
                if document or error_detail:
                    break
    except Exception as error:  # noqa: BLE001
        error_detail = f"the stream failed: {error}"

    elapsed = time.monotonic() - started

    if not report("the read completed", document is not None, f"{elapsed:.1f}s", error_detail or ""):
        print(f"        stages reached: {' -> '.join(s for s in stages if s) or 'none'}")
        return

    report("stages were reported", len(stages) >= 3, " -> ".join(s for s in stages if s))
    blocks = document.get("blocks") or []
    report("the document has text", len(blocks) > 0, f"{len(blocks)} blocks in {elapsed:.1f}s")
    report("the document is the current schema", document.get("schema") == "paper.document.v1", hint=str(document.get("schema")))
    # The whole promise: every block came out of the source, none was written
    # for it. A block with no locator is not grounded in anything.
    report(
        "every block carries a source locator",
        all(block.get("locator") for block in blocks),
        hint=f"{sum(1 for block in blocks if not block.get('locator'))} without one",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default=DEFAULT_SITE, help="base URL of the deployed site")
    parser.add_argument("--api", default=DEFAULT_API, help="base URL of the deployed API")
    parser.add_argument("--skip-read", action="store_true", help="skip the real read, which costs a model call")
    parser.add_argument("--wait-seconds", type=int, default=0, help="wait before starting, to let a deploy finish")
    arguments = parser.parse_args()

    site = arguments.site.rstrip("/")
    api = arguments.api.rstrip("/")
    parsed = urllib.parse.urlparse(site)
    site_origin = f"{parsed.scheme}://{parsed.netloc}"

    if arguments.wait_seconds:
        print(f"Waiting {arguments.wait_seconds}s for the deploy to finish...")
        time.sleep(arguments.wait_seconds)

    # In production the site and the API are on different hosts, which is what
    # makes CORS and the API-URL substitution matter. When the API serves the
    # site itself neither exists, so those checks are skipped rather than
    # reported as failures of a setup that is working correctly.
    api_origin = urllib.parse.urlparse(api)
    split_origin = f"{api_origin.scheme}://{api_origin.netloc}" != site_origin

    print(f"Site: {site}")
    print(f"API : {api}" + ("" if split_origin else "  (same origin)"))

    check_pages(site, split_origin=split_origin)
    check_api(api, site_origin, split_origin=split_origin)
    if arguments.skip_read:
        print("\nA real read\n  SKIP  --skip-read was passed")
    else:
        check_real_read(api)

    print()
    if failures:
        print(f"{len(failures)} of {len(failures) + passes} checks failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"All {passes} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
