import pytest
from unittest.mock import AsyncMock, MagicMock
from news.news_engine import NewsEngine


@pytest.mark.asyncio
async def test_news_engine_scan_structure():
    """Verify NewsEngine initializes and structures news items properly."""
    config = {"news": {"enabled": True, "sources": ["economic_times", "nse_corporate"]}}
    engine = NewsEngine(config)
    assert len(engine._sources) == 2

    # Test analyzer
    sample_news = {
        "headline": "RBI Keeps Repo Rate Unchanged at 6.5%",
        "summary": "Monetary policy committee maintains status quo for banking sector.",
        "source": "Economic Times",
        "url": "https://example.com/news/1",
        "published_at": "2025-08-19T09:00:00+05:30",
    }
    analyzed = engine.analyzer.classify_news(sample_news)
    assert analyzed is not None
    assert "sentiment" in analyzed
    assert "impact_level" in analyzed
    assert "relevant_symbols" in analyzed
