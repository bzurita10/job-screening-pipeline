"""Job-posting intake: from a text file, stdin (paste), or a public URL.

No LinkedIn / authenticated scraping — that violates ToS and breaks constantly.
The reliable path is pasting the description or pointing at a genuinely public
posting page. URL fetch is best-effort text extraction for open pages.
"""

import sys
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def from_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def from_stdin() -> str:
    print("Paste the job description, then press Ctrl-D (Ctrl-Z on Windows):\n")
    return sys.stdin.read().strip()


def from_url(url: str) -> str:
    """Best-effort text extraction from a public posting page."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )
    if len(text) < 200:
        raise ValueError(
            "Extracted very little text — the page is likely behind auth or "
            "JavaScript-rendered (common for LinkedIn/Workday). Paste the "
            "description instead: python pipeline.py --paste"
        )
    return text


def from_search(query: str, client) -> str:
    """Find a job posting from a title/company (or a listing URL) using the
    Anthropic web_search tool, and return the full description text.

    Use this when you only have a role name, not a clean posting URL — e.g.
    a title spotted on a search-results page. Needs an API client with an
    ANTHROPIC_API_KEY, since the search runs server-side through the model.
    """
    import config

    system = (
        "You retrieve job postings. Search the web for the SPECIFIC posting the "
        "user names, open the best source, and return ONLY the posting's text: "
        "title, company, location, responsibilities, qualifications, and salary "
        "if listed. No commentary, no markdown headers you invented, no links. "
        "If you cannot find the specific posting, return the single line: "
        "NOT_FOUND"
    )
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=3000,
        system=system,
        tools=[config.WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content":
                   f"Find the current job posting for: {query}. "
                   f"Return its full description text."}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text or text.strip() == "NOT_FOUND":
        raise ValueError(
            f"Couldn't retrieve a full posting for '{query}'. Postings on "
            "JS-heavy career sites (Disney, Workday) often aren't fetchable — "
            "screenshot the description or paste it instead."
        )
    return text


def load(args) -> str:
    """Dispatch based on CLI args."""
    if getattr(args, "search", None):
        import anthropic
        client = anthropic.Anthropic()
        return from_search(args.search, client)
    if getattr(args, "url", None):
        return from_url(args.url)
    if getattr(args, "paste", False):
        return from_stdin()
    if getattr(args, "jobfile", None):
        return from_file(args.jobfile)
    raise ValueError("No job source given. Pass a file path, --paste, --url, or --search.")
