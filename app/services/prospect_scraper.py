"""Evidence-backed company discovery and business contact enrichment."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "SoukSaaSProspectFinder/0.1"
MAX_PAGE_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 64_000
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


class DiscoveryError(ValueError):
    """The submitted public page cannot be safely or usefully inspected."""


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.description = ""
        self.jsonld: list[str] = []
        self.visible_text: list[str] = []
        self.mailto: list[str] = []
        self.telephones: list[str] = []
        self._title = False
        self._h1 = False
        self._script = False
        self._script_type = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            href = values.get("href") or ""
            if href.lower().startswith("mailto:"):
                self.mailto.append(href[7:].split("?", 1)[0].strip())
            elif href.lower().startswith("tel:"):
                self.telephones.append(href[4:].strip())
        if tag == "title":
            self._title = True
        elif tag == "h1":
            self._h1 = True
        elif tag == "meta" and (
            values.get("name", "").lower() == "description"
            or values.get("property", "").lower() == "og:description"
        ):
            self.description = values.get("content") or self.description
        elif tag == "script":
            self._script_type = values.get("type", "").lower()
            self._script = self._script_type == "application/ld+json"
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title = False
        elif tag == "h1":
            self._h1 = False
        elif tag == "script":
            if self._script and self._parts:
                self.jsonld.append("".join(self._parts))
            self._script = False
            self._script_type = ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title += data
        elif self._h1:
            self.h1 += data
        if self._script:
            self._parts.append(data)
        elif data.strip():
            self.visible_text.append(data.strip())


def _validate_public_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise DiscoveryError("The source URL is malformed.") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise DiscoveryError("Use a public http or https page URL.")
    if parts.username or parts.password:
        raise DiscoveryError("URLs with embedded usernames or passwords are not allowed.")
    if port is not None and port != (80 if parts.scheme == "http" else 443):
        raise DiscoveryError("Only standard web ports are allowed.")
    host = parts.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise DiscoveryError("Local and private network URLs are not allowed.")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, None)})
        except OSError as exc:
            raise DiscoveryError("The source host could not be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DiscoveryError("Local and private network URLs are not allowed.")
    clean = parts._replace(fragment="")
    return urlunsplit(clean)


def _read_limited(client: httpx.Client, url: str, cap: int) -> tuple[int, httpx.Headers, bytes]:
    try:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > cap:
                raise DiscoveryError("The source response is larger than the allowed size limit.")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes(8192):
                size += len(chunk)
                if size > cap:
                    raise DiscoveryError("The source response exceeded the size limit.")
                chunks.append(chunk)
            return response.status_code, response.headers, b"".join(chunks)
    except httpx.HTTPError as exc:
        raise DiscoveryError("The public source could not be fetched.") from exc


def _robots_allow(client: httpx.Client, url: str) -> bool:
    parts = urlsplit(url)
    robots = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    status, _headers, content = _read_limited(client, robots, MAX_ROBOTS_BYTES)
    if status in {404, 410}:
        return True
    if status != 200:
        return False
    parser = RobotFileParser()
    parser.set_url(robots)
    parser.parse(content.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _fetch_html(url: str) -> tuple[str, bytes]:
    original_host = (urlsplit(url).hostname or "").lower()
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            url = _validate_public_url(url)
            if urlsplit(url).hostname != original_host:
                raise DiscoveryError("Cross-site redirects are not followed.")
            if not _robots_allow(client, url):
                raise DiscoveryError("The source does not allow this page to be crawled.")
            status, headers, content = _read_limited(client, url, MAX_PAGE_BYTES)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    break
                url = urljoin(url, location)
                continue
            if status != 200:
                raise DiscoveryError(f"The source page returned HTTP {status}.")
            content_type = headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise DiscoveryError("The source URL must return an HTML page.")
            return url, content
    raise DiscoveryError("The source redirected too many times or to another site.")


def _safe_link(value: object, base_url: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parts = urlsplit(urljoin(base_url, value.strip()))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        if parts.username or parts.password:
            return ""
        host = parts.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return urlunsplit(parts._replace(fragment=""))[:500]
    except ValueError:
        return ""


class ResearchError(ValueError):
    """Company search or evidence collection could not be completed."""


SIGNALS = {
    "funding": re.compile(r"\b(funding|funded|raises?\b|raised\b|series [a-z]|seed round|investment round)\b", re.IGNORECASE),
    "acquisition": re.compile(r"\b(acquir(?:e|es|ed|ing)|acquisition|merger|bought|takeover)\b", re.IGNORECASE),
    "hiring": re.compile(r"\b(hir(?:e|es|ing)|jobs?|careers|open roles?|recruiting|vacanc(?:y|ies))\b", re.IGNORECASE),
}
DECISION_ROLE = re.compile(
    r"\b(CEO|CTO|COO|CFO|founder|co-founder|owner|president|chief executive officer|"
    r"chief technology officer|chief operating officer|corporate development|corp dev|"
    r"partnerships?|business development|strateg(?:y|ic) director)\b",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PERSON_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?:\s+[A-Z][a-z]+){1,2}\b")
GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me"}
CAPABILITY_TERMS = (
    (r"digital menu|menu app|qr menu", "digital menus"),
    (r"\bpos\b|point.of.sale|cash register", "POS"),
    (r"reservation|booking", "reservations"),
    (r"loyalty|rewards", "loyalty apps"),
    (r"online ordering|online order|delivery app", "online ordering"),
)


def _idea_capabilities(product: dict) -> str:
    profile = " ".join(
        [str(product.get("name") or ""), str(product.get("tagline") or ""),
         str(product.get("description") or "")]
        + [str(value) for value in product.get("features", [])]
        + [str(plan.get("name") or "") for plan in product.get("plans", []) if isinstance(plan, dict)]
    )
    extracted = [label for pattern, label in CAPABILITY_TERMS if re.search(pattern, profile, re.IGNORECASE)]
    return ", ".join(extracted) or ", ".join(str(value) for value in (product.get("features") or [])[:4])


def build_search_queries(product: dict, location: str = "") -> list[str]:
    """Build three source queries from the selected product's audience and idea profile."""
    audience = product.get("audience") or []
    description = str(product.get("description") or "")
    name = str(product.get("name") or "SaaS product")
    vertical = ", ".join(str(item) for item in audience[:3]) or description[:100]
    capabilities = _idea_capabilities(product)
    target_area = f" in {location.strip()}" if location.strip() else ""
    subject = f"{vertical} companies{target_area}"
    return [
        f"{subject} recent funding or funding rounds related to {name} {capabilities}",
        f"{subject} recent acquisitions or acquired startups related to {name} {capabilities}",
        f"{subject} recent hiring for roles related to {name} {capabilities}",
    ]


def _provider_json(
    provider: str,
    url: str,
    api_key: str,
    *,
    method: str,
    params: dict | None = None,
    body: dict | None = None,
) -> dict:
    try:
        response = httpx.request(
            method,
            url,
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=20.0,
            follow_redirects=False,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise ResearchError(f"{provider} could not be reached.") from exc
    if response.status_code != 200:
        raise ResearchError(f"{provider} returned HTTP {response.status_code}.")
    try:
        result = response.json()
    except ValueError as exc:
        raise ResearchError(f"{provider} returned invalid data.") from exc
    if not isinstance(result, dict):
        raise ResearchError(f"{provider} returned invalid data.")
    return result


def _hunter_companies(query: str, api_key: str, limit: int) -> list[dict]:
    response = _provider_json(
        "Company discovery provider",
        "https://api.hunter.io/v2/discover",
        api_key,
        method="POST",
        body={"query": query, "limit": limit},
    )
    data = response.get("data")
    return data if isinstance(data, list) else []


def _tavily_search(query: str, api_key: str) -> list[dict]:
    response = _provider_json(
        "Web search provider",
        "https://api.tavily.com/search",
        api_key,
        method="POST",
        body={
            "query": query,
            "topic": "news",
            "time_range": "year",
            "filter_by_published_date": True,
            "include_published_date": True,
            "include_answer": False,
            "include_raw_content": False,
            "max_results": 10,
            "search_depth": "basic",
        },
    )
    results = response.get("results")
    return results if isinstance(results, list) else []


def _tavily_official_site_search(company_name: str, api_key: str) -> list[dict]:
    response = _provider_json(
        "Web search provider",
        "https://api.tavily.com/search",
        api_key,
        method="POST",
        body={
            "query": f'"{company_name}" official website',
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "max_results": 5,
            "search_depth": "basic",
        },
    )
    results = response.get("results")
    return results if isinstance(results, list) else []


COMPANY_EVENT_TITLE = re.compile(
    r"\b(?P<company>[A-Z][A-Za-z0-9&.'-]*(?:\s+(?:[A-Z][A-Za-z0-9&.'-]*|of|and|the)){0,3})\s+"
    r"(?:raises?|raised|secures?|secured|acquires?|acquired|buys|bought|is hiring|are hiring|"
    r"hiring|is recruiting|plans to hire)\b"
)
NON_COMPANY_SITES = {
    "about.me", "bloomberg.com", "businesswire.com", "crunchbase.com", "facebook.com",
    "instagram.com", "linkedin.com", "prnewswire.com", "reuters.com", "techcrunch.com",
    "wikipedia.org", "x.com", "youtube.com",
}


def _candidate_names_from_results(results: list[dict], limit: int) -> list[dict]:
    found: dict[str, dict] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "")
        for match in COMPANY_EVENT_TITLE.finditer(title):
            name = match.group("company").strip(" .,:;–—-")
            if len(name) < 3 or name.casefold() in {"company", "startup", "restaurant", "cafe"}:
                continue
            found.setdefault(name.casefold(), {"organization": name})
            if len(found) >= limit:
                return list(found.values())
    return list(found.values())


def _official_company_domain(company_name: str, api_key: str) -> str:
    for result in _tavily_official_site_search(company_name, api_key):
        if not isinstance(result, dict):
            continue
        source_url = _safe_link(result.get("url"), "https://example.com")
        if not source_url:
            continue
        host = (urlsplit(source_url).hostname or "").lower().removeprefix("www.")
        if not host or any(host == site or host.endswith("." + site) for site in NON_COMPANY_SITES):
            continue
        title = str(result.get("title") or "")
        if company_name.casefold() not in title.casefold():
            continue
        try:
            final_url, content = _fetch_html(source_url)
        except DiscoveryError:
            continue
        parser = _PageParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        if company_name.casefold() not in f"{parser.title} {parser.h1}".casefold():
            continue
        final_host = (urlsplit(final_url).hostname or "").lower().removeprefix("www.")
        if final_host:
            return final_host
    return ""


def _event_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    now = datetime.now(UTC)
    return parsed if now - timedelta(days=365) <= parsed <= now else None


def _result_mentions_company(result: dict, name: str, domain: str) -> bool:
    raw_url = str(result.get("url") or "")
    try:
        host = (urlsplit(raw_url).hostname or "").lower()
    except ValueError:
        host = ""
    if host == domain or host.endswith("." + domain):
        return True
    text = " ".join(str(result.get(key) or "") for key in ("title", "content")).casefold()
    company = name.strip().casefold()
    return len(company) >= 4 and company in text


def _gather_recent_signals(
    company: dict,
    results: list[dict],
    *,
    limit: int = 3,
) -> list[dict]:
    name = str(company.get("organization") or company.get("name") or "").strip()
    domain = str(company.get("domain") or "").lower().strip()
    matches: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        if not isinstance(result, dict) or not _result_mentions_company(result, name, domain):
            continue
        published = _event_date(result.get("published_date"))
        if published is None:
            continue
        text = " ".join(str(result.get(key) or "") for key in ("title", "content"))
        title = str(result.get("title") or "Company activity")[:240]
        source_url = str(result.get("url") or "")
        for signal, pattern in SIGNALS.items():
            if not pattern.search(text):
                continue
            safe_source = _safe_link(source_url, f"https://{domain}")
            if not safe_source:
                continue
            key = (signal, safe_source)
            if key in seen:
                continue
            # Re-fetch only the cited article, and only if its robots.txt permits access.
            try:
                fetched_url, html = _fetch_html(safe_source)
            except DiscoveryError:
                continue
            page = _PageParser()
            page.feed(html.decode("utf-8", errors="replace"))
            page_text = " ".join([page.title, page.description, *page.visible_text])
            if not pattern.search(page_text):
                continue
            if name.casefold() not in page_text.casefold() and domain not in (urlsplit(fetched_url).hostname or ""):
                continue
            seen.add(key)
            matches.append({
                "type": signal,
                "date": published.date().isoformat(),
                "title": title,
                "source_url": fetched_url,
            })
            if len(matches) >= limit:
                return matches
    return matches


def _email_matches_domain(value: object, domain: str) -> str:
    if not isinstance(value, str) or "@" not in value:
        return ""
    email = value.strip().lower().removeprefix("mailto:")
    try:
        address_domain = email.rsplit("@", 1)[1].rstrip(".")
    except IndexError:
        return ""
    if address_domain in GENERIC_DOMAINS:
        return ""
    if address_domain != domain and not address_domain.endswith("." + domain):
        return ""
    return email


def _hunter_contacts(domain: str, api_key: str) -> list[dict]:
    try:
        response = _provider_json(
            "Contact enrichment provider",
            "https://api.hunter.io/v2/domain-search",
            api_key,
            method="GET",
            params={
                "domain": domain,
                "type": "personal",
                "provenance": "found",
                "decision_maker": "true",
                "required_field": "full_name,position",
                "limit": 10,
            },
        )
    except ResearchError:
        return []
    data = response.get("data") or {}
    rows = data.get("emails") if isinstance(data, dict) else []
    contacts = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("position") or "").strip()
        name = " ".join(str(row.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
        email = _email_matches_domain(row.get("value"), domain)
        if not email or not name or not role or not DECISION_ROLE.search(role):
            continue
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        source_url = ""
        for source in sources:
            candidate_url = source.get("uri") if isinstance(source, dict) else ""
            safe_source = _safe_link(candidate_url, f"https://{domain}")
            if safe_source:
                hostname = (urlsplit(safe_source).hostname or "").lower()
                if "linkedin.com" not in hostname:
                    source_url = safe_source
                    break
        verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
        valid = verification.get("status") == "valid"
        contacts.append({
            "name": name,
            "role": role,
            "email": email,
            "source_label": "verified by API" if valid else "API result, unverified",
            "confidence": row.get("confidence"),
            "source_url": source_url or f"https://{domain}",
        })
    contacts.sort(
        key=lambda item: (item["source_label"] != "verified by API", -int(item.get("confidence") or 0))
    )
    return contacts[:3]


def _walk_people(value: object, out: list[dict]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_people(item, out)
        return
    if not isinstance(value, dict):
        return
    raw_type = value.get("@type", [])
    types = [raw_type] if isinstance(raw_type, str) else raw_type
    if isinstance(types, list) and any(str(item).split("/")[-1] == "Person" for item in types):
        name = str(value.get("name") or "").strip()
        role = str(value.get("jobTitle") or "").strip()
        if not name and (value.get("givenName") or value.get("familyName")):
            name = " ".join(str(value.get(key) or "").strip() for key in ("givenName", "familyName")).strip()
        email = str(value.get("email") or "").strip()
        if email:
            out.append({"name": name, "role": role, "email": email})
    for key in ("@graph", "employee", "employees", "member", "members", "author", "mainEntity"):
        if key in value:
            _walk_people(value[key], out)


def _scrape_company_contacts(domain: str) -> tuple[list[dict], str, dict | None]:
    website = f"https://{domain}"
    contacts: list[dict] = []
    contact_page_url = ""
    main_phone = None
    paths = ("/contact", "/about", "/team")
    for path in paths:
        page_url = urljoin(website, path)
        try:
            final_url, content = _fetch_html(page_url)
        except DiscoveryError:
            continue
        parser = _PageParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        if not contact_page_url:
            contact_page_url = final_url
        page_contacts: list[dict] = []
        for raw in parser.jsonld:
            try:
                _walk_people(json.loads(raw), page_contacts)
            except (json.JSONDecodeError, RecursionError):
                continue
        page_text = " ".join(parser.visible_text)
        emails = set(parser.mailto)
        emails.update(EMAIL_PATTERN.findall(page_text))
        for raw_email in emails:
            email = _email_matches_domain(raw_email, domain)
            if not email:
                continue
            if not any(_email_matches_domain(person.get("email"), domain) == email for person in page_contacts):
                start = max(0, page_text.lower().find(email) - 180)
                context = page_text[start:start + 260]
                role_match = DECISION_ROLE.search(context)
                role = role_match.group(0) if role_match else ""
                name = ""
                if role_match:
                    possible_names = [
                        match.group(0) for match in PERSON_PATTERN.finditer(context[:role_match.start()])
                        if match.group(0).lower() not in {"contact", "about", "our team", "meet the"}
                    ]
                    name = possible_names[-1] if possible_names else ""
                page_contacts.append({"name": name, "role": role, "email": email})
        for contact in page_contacts:
            email = _email_matches_domain(contact.get("email"), domain)
            name = str(contact.get("name") or "").strip()
            role = str(contact.get("role") or "").strip()
            if email and not any(item.get("email") == email for item in contacts):
                contacts.append({
                    "name": name or None,
                    "role": role or None,
                    "email": email,
                    "source_label": "scraped, unverified",
                    "confidence": None,
                    "source_url": final_url,
                })
        if path == "/contact" and not main_phone:
            for raw_phone in parser.telephones:
                digits = re.sub(r"\D", "", raw_phone)
                if 7 <= len(digits) <= 15:
                    main_phone = {
                        "value": raw_phone[:40],
                        "label": "Company main line",
                        "source_url": final_url,
                    }
                    break
            if not main_phone:
                phone_match = re.search(
                    r"(?:main\s+(?:phone|line)|phone|telephone|tel|call us)[^+\d]{0,30}"
                    r"(\+?\d[\d\s().-]{5,}\d)",
                    page_text,
                    re.IGNORECASE,
                )
                if phone_match:
                    raw_phone = phone_match.group(1).strip()
                    digits = re.sub(r"\D", "", raw_phone)
                    if 7 <= len(digits) <= 15:
                        main_phone = {
                            "value": raw_phone[:40],
                            "label": "Company main line",
                            "source_url": final_url,
                        }
    return contacts[:3], contact_page_url, main_phone


def discover_buyers(
    product: dict,
    *,
    hunter_api_key: str,
    tavily_api_key: str,
    location: str = "",
    limit: int = 15,
) -> dict:
    """Discover companies, verify recent activity from permitted pages, and enrich business contacts."""
    if not tavily_api_key.strip():
        raise ResearchError("Set TAVILY_API_KEY in .env to search recent company activity.")
    if not 1 <= limit <= 15:
        raise ResearchError("The shortlist size must be between 1 and 15.")

    queries = build_search_queries(product, location)
    audience = ", ".join(str(value) for value in (product.get("audience") or [])[:3]) or "the product's target market"
    features = _idea_capabilities(product) or str(product.get("description") or "")[:200]
    product_name = str(product.get("name") or "the product")
    target_area = f" in {location.strip()}" if location.strip() else ""
    activity_results: list[dict] = []
    for query in queries:
        activity_results.extend(_tavily_search(query, tavily_api_key.strip()))

    if hunter_api_key.strip():
        discovery_query = (
            f"Companies in the {audience} vertical{target_area} that are potential buyers of {product_name}. "
            f"The product provides {features}. Prioritize companies with recent funding, acquisitions, "
            "or hiring for related capabilities. Return operating companies with public websites."
        )
        companies = _hunter_companies(discovery_query, hunter_api_key.strip(), limit)
    else:
        # Tavily results provide candidate company names; only keep a candidate when its
        # official site can be confirmed by both the search result and its page heading.
        companies = _candidate_names_from_results(activity_results, limit * 3)
        verified_companies = []
        for candidate in companies:
            domain = _official_company_domain(candidate["organization"], tavily_api_key.strip())
            if domain:
                verified_companies.append({**candidate, "domain": domain})
            if len(verified_companies) >= limit:
                break
        companies = verified_companies
    if not companies:
        return {"queries": queries, "target_count": limit, "count": 0, "companies": []}

    ranked: list[dict] = []
    for item in companies[:limit]:
        if not isinstance(item, dict):
            continue
        company_name = str(item.get("organization") or item.get("name") or "").strip()
        domain = str(item.get("domain") or "").strip().lower().rstrip(".")
        if not company_name or not domain or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
            continue
        try:
            _validate_public_url(f"https://{domain}")
        except DiscoveryError:
            continue
        signals = _gather_recent_signals(item, activity_results)
        if not signals:
            continue

        website = f"https://{domain}"
        # Use Hunter first; only inspect the company's public team/contact pages if it yields no qualifying contact.
        contacts = _hunter_contacts(domain, hunter_api_key.strip()) if hunter_api_key.strip() else []
        contact_page_url = ""
        phone = None
        if not contacts:
            contacts, contact_page_url, phone = _scrape_company_contacts(domain)
        elif not contact_page_url:
            _fallback_contacts, contact_page_url, phone = _scrape_company_contacts(domain)
        if not contacts and not contact_page_url:
            continue

        signal_names = sorted({signal["type"] for signal in signals})
        score = min(100, 48 + 18 * len(signal_names) + (8 if contacts else 0))
        matched_by = "Hunter" if hunter_api_key.strip() else "public search results"
        reason = (
            f"Matched to the product's {audience} vertical by {matched_by}; source pages confirm recent "
            f"{', '.join(signal_names)} activity. Product capabilities: {features}."
        )
        ranked.append({
            "company_name": company_name,
            "website": website,
            "why_likely_buyer": reason,
            "likelihood_score": score,
            "signals": signals,
            "contacts": contacts,
            "contact_page_url": contact_page_url or None,
            "phone": phone,
        })

    ranked.sort(key=lambda company: (-company["likelihood_score"], company["company_name"].casefold()))
    shortlist = ranked[:limit]
    return {
        "queries": queries,
        "target_count": limit,
        "count": len(shortlist),
        "companies": shortlist,
    }
