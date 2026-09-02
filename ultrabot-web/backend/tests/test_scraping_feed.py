import pytest
from bs4 import BeautifulSoup


def test_html_parsing_helpers():
    """Verify BeautifulSoup parsing used in scraping feeds."""
    sample_html = """
    <html>
        <body>
            <a href="/quote/NSE:RELIANCE">RELIANCE +1.45%</a>
            <a href="/quote/NSE:INFY">INFY -0.85%</a>
        </body>
    </html>
    """
    soup = BeautifulSoup(sample_html, "html.parser")
    links = [a.get("href") for a in soup.find_all("a") if "/quote/" in (a.get("href") or "")]
    assert len(links) == 2
    assert "/quote/NSE:RELIANCE" in links
