"""The GICS hierarchy down to industry level (11 sectors, 25 industry groups, 74 industries), used to CHECK names.

A model may draft a classification, but a sector, industry group or industry that is not in this list, or that sits
under the wrong parent, is rejected by code. Sub-industry (163 names) is not listed here: it is recorded as drafted and
marked unchecked until the analyst approves it. Structure as revised in 2023; confirm against MSCI/S&P when publishing.
"""
GICS = {
    "Energy": {"Energy": ["Energy Equipment & Services", "Oil, Gas & Consumable Fuels"]},
    "Materials": {"Materials": ["Chemicals", "Construction Materials", "Containers & Packaging", "Metals & Mining", "Paper & Forest Products"]},
    "Industrials": {
        "Capital Goods": ["Aerospace & Defense", "Building Products", "Construction & Engineering", "Electrical Equipment",
                          "Industrial Conglomerates", "Machinery", "Trading Companies & Distributors"],
        "Commercial & Professional Services": ["Commercial Services & Supplies", "Professional Services"],
        "Transportation": ["Air Freight & Logistics", "Passenger Airlines", "Marine Transportation", "Ground Transportation", "Transportation Infrastructure"],
    },
    "Consumer Discretionary": {
        "Automobiles & Components": ["Automobile Components", "Automobiles"],
        "Consumer Durables & Apparel": ["Household Durables", "Leisure Products", "Textiles, Apparel & Luxury Goods"],
        "Consumer Services": ["Hotels, Restaurants & Leisure", "Diversified Consumer Services"],
        "Consumer Discretionary Distribution & Retail": ["Distributors", "Broadline Retail", "Specialty Retail"],
    },
    "Consumer Staples": {
        "Consumer Staples Distribution & Retail": ["Consumer Staples Distribution & Retail"],
        "Food, Beverage & Tobacco": ["Beverages", "Food Products", "Tobacco"],
        "Household & Personal Products": ["Household Products", "Personal Care Products"],
    },
    "Health Care": {
        "Health Care Equipment & Services": ["Health Care Equipment & Supplies", "Health Care Providers & Services", "Health Care Technology"],
        "Pharmaceuticals, Biotechnology & Life Sciences": ["Biotechnology", "Pharmaceuticals", "Life Sciences Tools & Services"],
    },
    "Financials": {
        "Banks": ["Banks"],
        "Financial Services": ["Financial Services", "Consumer Finance", "Capital Markets", "Mortgage Real Estate Investment Trusts (REITs)"],
        "Insurance": ["Insurance"],
    },
    "Information Technology": {
        "Software & Services": ["IT Services", "Software"],
        "Technology Hardware & Equipment": ["Communications Equipment", "Technology Hardware, Storage & Peripherals", "Electronic Equipment, Instruments & Components"],
        "Semiconductors & Semiconductor Equipment": ["Semiconductors & Semiconductor Equipment"],
    },
    "Communication Services": {
        "Telecommunication Services": ["Diversified Telecommunication Services", "Wireless Telecommunication Services"],
        "Media & Entertainment": ["Media", "Entertainment", "Interactive Media & Services"],
    },
    "Utilities": {"Utilities": ["Electric Utilities", "Gas Utilities", "Multi-Utilities", "Water Utilities", "Independent Power and Renewable Electricity Producers"]},
    "Real Estate": {
        "Equity Real Estate Investment Trusts (REITs)": ["Diversified REITs", "Industrial REITs", "Hotel & Resort REITs", "Office REITs",
                                                          "Health Care REITs", "Residential REITs", "Retail REITs", "Specialized REITs"],
        "Real Estate Management & Development": ["Real Estate Management & Development"],
    },
}


def counts() -> tuple[int, int, int]:
    return len(GICS), sum(len(g) for g in GICS.values()), sum(len(i) for g in GICS.values() for i in g.values())


def check(sector: str, group: str, industry: str) -> str | None:
    """None if the three names exist and nest correctly, else a plain reason."""
    if sector not in GICS:
        return f"unknown sector {sector!r}"
    if group not in GICS[sector]:
        return f"industry group {group!r} does not belong to sector {sector!r}"
    if industry not in GICS[sector][group]:
        return f"industry {industry!r} does not belong to industry group {group!r}"
    return None


def structure_text() -> str:
    return "\n".join(f"{s} > {g} > {', '.join(inds)}" for s, groups in GICS.items() for g, inds in groups.items())
