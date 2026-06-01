from datetime import datetime

from pydantic import BaseModel


class Pick(BaseModel):
    ticker: str
    company_name: str
    rationale: str
    conviction: str  # "core" or "moonshot"
    member: str
    variant_perception: str | None = None
    current_price: float | None = None
    mean_target: float | None = None
    median_target: float | None = None
    mean_upside_pct: float | None = None
    median_upside_pct: float | None = None


class PortfolioHolding(BaseModel):
    ticker: str
    company_name: str
    conviction: str
    weight: float  # percentage, sums to 100 across portfolio
    nominated_by: list[str]  # which committee members picked it
    rationale: str
    current_price: float | None = None
    mean_upside_pct: float | None = None
    median_upside_pct: float | None = None


class WebSource(BaseModel):
    url: str
    title: str


class CommitteeRun(BaseModel):
    run_id: str
    timestamp: datetime
    claude_picks: list[Pick]
    gpt_picks: list[Pick]
    gemini_picks: list[Pick] = []
    portfolio: list[PortfolioHolding]
    claude_sources: list[WebSource] = []


class AdvisorResponse(BaseModel):
    ticker: str
    company_name: str
    recommendation: str  # "strong buy", "buy", "watch", "pass", "already in portfolio", "not enough opinions"
    claude_take: str
    gpt_take: str
    gemini_take: str
    claude_rec: str | None = None
    gpt_rec: str | None = None
    gemini_rec: str | None = None
    fits_philosophy: bool
    suggested_allocation_pct: float | None = None
    mean_upside_pct: float | None = None
    median_upside_pct: float | None = None


class PortfolioPosition(BaseModel):
    ticker: str
    shares: float
    avg_cost: float | None = None


class TrackedPortfolio(BaseModel):
    name: str
    positions: list[PortfolioPosition]
