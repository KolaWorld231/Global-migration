"""Opportunity discovery framework.

Adapters turn a permitted source (official feed, API, public dataset, page that robots.txt allows)
into *candidate* records. Candidates always enter as UNVERIFIED and go through verification.
No authentication, paywall or anti-bot bypass; robots.txt and rate limits are enforced in `polite_get`.
"""
import time, urllib.robotparser
from urllib.parse import urlparse
import httpx

UA = "GlobalPathwaysBot/0.1 (+research; respects robots.txt)"
_last = {}
MIN_DELAY = 5.0


def allowed(url):
    p = urlparse(url)
    rp = urllib.robotparser.RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return False  # fail closed
    return rp.can_fetch(UA, url)


def polite_get(url, client=None):
    if not allowed(url):
        raise PermissionError(f"robots.txt disallows or is unreachable: {url}")
    host = urlparse(url).netloc
    wait = MIN_DELAY - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()
    return (client or httpx).get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)


class Adapter:
    """Subclass per source. `discover` yields dicts: {kind:'organization'|'opportunity', ...fields, source_url}."""
    name = "base"

    def discover(self, country_cfg):
        raise NotImplementedError


class OfficialSitesAdapter(Adapter):
    """Baseline adapter: confirms that configured official government sources are reachable and records them."""
    name = "official_sites"

    def discover(self, country_cfg):
        for url in country_cfg.get("official_sites", []):
            try:
                r = polite_get(url)
                yield {"kind": "source_check", "url": url, "http_status": r.status_code}
            except Exception as e:
                yield {"kind": "source_check", "url": url, "error": str(e)}


ADAPTERS = [OfficialSitesAdapter()]


def run(country_cfg):
    results = []
    for a in ADAPTERS:
        results += [{"adapter": a.name, **item} for item in a.discover(country_cfg)]
    return results
