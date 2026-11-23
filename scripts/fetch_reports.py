"""Download the core-company annual reports into data/raw/<TICKER>/<FY>/ with a source record.
Public company-website PDFs only. data/ is git-ignored. Run: python3 scripts/fetch_reports.py"""
import hashlib, json, urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = "https://cement.dangote.com/wp-content/uploads"
P = "https://www.presco-plc.com/prescoPLC/assets/File"
FILES = [
    ("DANGCEM", "FY2025", f"{D}/2026/06/Dangote-Cememt-Plc-2025-Annual-Report.pdf", "Dangote Cement annual report FY2025"),
    ("DANGCEM", "FY2024", f"{D}/2025/05/Dangote-Cement-FY-2024-Annual-Report.pdf", "Dangote Cement annual report FY2024"),
    ("DANGCEM", "FY2023", f"{D}/2024/04/DCP-Annual-Report_AR23_full.pdf", "Dangote Cement annual report FY2023"),
    ("PRESCO", "FY2025", f"{P}/Audited%20Financial%20Statements.pdf", "Presco audited financial statements FY2025"),
    ("PRESCO", "FY2024", f"{P}/PRESCO%202024%20ANNUAL%20REPORT_Update.pdf", "Presco annual report FY2024"),
    ("PRESCO", "FY2023", f"{P}/PRESCO%20%20Annual%20Report%20and%20Account%202023.pdf", "Presco annual report FY2023"),
]
for ticker, fy, url, desc in FILES:
    folder = ROOT / "data" / "raw" / ticker / fy
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{ticker}_{fy}_annual_report.pdf"
    if dest.exists():
        print("exists, skipped:", dest.name); continue
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=180).read()
    dest.write_bytes(data)
    (folder / "source.json").write_text(json.dumps({
        "company": ticker, "fiscal_year": fy, "description": desc, "url": url,
        "downloaded": date.today().isoformat(), "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data), "file": dest.name,
        "note": "Public filing from the company's own website. Linked, never copied into the public repo.",
    }, indent=2))
    print(f"downloaded {ticker} {fy}: {len(data)/1e6:.1f} MB  sha256 {hashlib.sha256(data).hexdigest()[:12]}")
