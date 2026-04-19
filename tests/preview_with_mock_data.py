"""
Standalone preview — renders the email with a hand-crafted mock payload.

Use this to:
  - QA the HTML/plain-text template without spending API tokens
  - Test email formatting across mobile/desktop/Gmail/Outlook
  - Demo the output shape to stakeholders before going live

Run:
    python tests/preview_with_mock_data.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Make 'src' and 'config' importable when running from /tests.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.generate import Bullet, CategorySection, EmailContent
from src.render import render_email


def build_mock() -> EmailContent:
    """Mock payload that mirrors the structure the LLM will produce."""
    return EmailContent(
        subject="Daily News Brief — Apr 17, 2026",
        hero=(
            "Restaurant Brands International raised full-year guidance on stronger Tim Hortons comps; "
            "3G-linked names broadly outperformed QSR peers in pre-market."
        ),
        categories=[
            CategorySection(
                name="QSR",
                bullets=[
                    Bullet(
                        text="Restaurant Brands International (QSR) lifted FY2026 adjusted EBITDA guidance after Tim Hortons Canada posted +6.2% same-store sales.",
                        source="Bloomberg",
                        url="https://www.bloomberg.com/news/articles/2026-04-17/qsr-guidance",
                    ),
                    Bullet(
                        text="McDonald's unveiled a revamped value menu in 12 test markets as traffic remains soft among sub-$50k households.",
                        source="WSJ",
                        url="https://www.wsj.com/business/hospitality/mcd-value-menu-test-2026",
                    ),
                    Bullet(
                        text="Kraft Heinz named a new North America president focused on Ore-Ida and Philadelphia cream cheese resets.",
                        source="Reuters",
                        url="https://www.reuters.com/business/khc-north-america-2026-04-17",
                    ),
                    Bullet(
                        text="Chipotle reported solid Q1 traffic; management flagged pressure from beef inflation in the back half.",
                        source="CNBC",
                        url="https://www.cnbc.com/2026/04/17/cmg-earnings.html",
                    ),
                ],
            ),
            CategorySection(
                name="Housing / Repair & Remodel (R&R)",
                bullets=[
                    Bullet(
                        text="Hunter Douglas secured a €220M revolving credit facility to fund North American distribution expansion.",
                        source="FT",
                        url="https://www.ft.com/content/hunter-douglas-rcf-2026-04-17",
                    ),
                    Bullet(
                        text="March housing starts came in at 1.39M annualized, modestly ahead of consensus; permits softened.",
                        source="Bloomberg",
                        url="https://www.bloomberg.com/news/articles/2026-04-17/march-housing-starts",
                    ),
                    Bullet(
                        text="Home Depot named new COO and flagged gross margin headwinds tied to lumber mix.",
                        source="Reuters",
                        url="https://www.reuters.com/business/hd-coo-2026-04-17",
                    ),
                ],
            ),
            CategorySection(
                name="Footwear / Apparel",
                bullets=[
                    Bullet(
                        text="Skechers (SKX) accelerated DTC buildout in Europe; CFO signaled FX neutral constant-currency revenue up low-teens.",
                        source="Barron's",
                        url="https://www.barrons.com/articles/skx-dtc-europe-2026-04-17",
                    ),
                    Bullet(
                        text="Nike completed final stage of its operating-model reorg, with GBG leadership now reporting into CEO.",
                        source="WSJ",
                        url="https://www.wsj.com/business/retail/nike-reorg-2026-04-17",
                    ),
                ],
            ),
            CategorySection(
                name="Private Equity / Investing",
                bullets=[
                    Bullet(
                        text="3G Capital exploring minority stake in European logistics software platform, per people familiar.",
                        source="Bloomberg",
                        url="https://www.bloomberg.com/news/articles/2026-04-17/3g-europe-logistics",
                    ),
                    Bullet(
                        text="KKR closed an $18B North America flagship fund — second-largest US buyout vintage in two years.",
                        source="WSJ",
                        url="https://www.wsj.com/finance/private-equity/kkr-fund-close-2026-04-17",
                    ),
                    Bullet(
                        text="Blackstone's GP Stakes unit took a 15% position in a mid-market industrials sponsor.",
                        source="Reuters",
                        url="https://www.reuters.com/business/finance/blackstone-gp-stakes-2026-04-17",
                    ),
                ],
            ),
            CategorySection(
                name="Technology",
                bullets=[
                    Bullet(
                        text="Nvidia confirmed updated H300 availability for Q3 shipments; hyperscaler commitments already covered the quarter.",
                        source="Bloomberg",
                        url="https://www.bloomberg.com/news/articles/2026-04-17/nvda-h300",
                    ),
                    Bullet(
                        text="Microsoft and OpenAI restructured their commercial arrangement; revenue share terms undisclosed.",
                        source="FT",
                        url="https://www.ft.com/content/msft-openai-2026-04-17",
                    ),
                ],
            ),
            CategorySection(
                name="Keyword alerts",
                bullets=[
                    Bullet(
                        text="Altius Capital was cited in coverage of cross-border PE secondaries as LPs rotate out of legacy vintage funds.",
                        implication="Raises visibility on firm brand; limited read-through to portfolio ops.",
                        source="Reuters",
                        url="https://www.reuters.com/business/altius-secondaries-2026-04-17",
                    ),
                ],
            ),
            CategorySection(
                name="Markets",
                market_snapshot={
                    "equity_futures": "S&P: 5,842.25 ▲ +0.32%; Nasdaq: 20,115.50 ▲ +0.41%; Dow: 42,890.00 ▲ +0.21%",
                    "movers": "QSR +2.1% pre-mkt on guidance raise; SKX +1.4% on DTC update",
                    "commodities": "WTI: 71.84 ▼ -0.48%; Gold: 2,391.20 ▲ +0.18%",
                    "yields": "10Y: 4.21% ▼ -3bps",
                    "fx": "DXY: 104.12 ▼ -0.15%; EUR/USD: 1.0892 ▲ +0.18%",
                    "crypto": "BTC: 68,420 ▲ +1.2%; ETH: 3,612 ▲ +0.9%",
                },
            ),
        ],
    )


def main() -> None:
    content = build_mock()
    today_str = "Apr 17, 2026"
    html, plain = render_email(
        content,
        today_str=today_str,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    out = ROOT / "data" / "preview"
    out.mkdir(parents=True, exist_ok=True)

    (out / "mock.html").write_text(html, encoding="utf-8")
    (out / "mock.txt").write_text(plain, encoding="utf-8")
    (out / "mock.subject.txt").write_text(content.subject, encoding="utf-8")

    print(f"✓ Preview written to {out}/mock.html")
    print(f"✓ Plain-text version at {out}/mock.txt")
    print(f"✓ Subject line: {content.subject}")


if __name__ == "__main__":
    main()
