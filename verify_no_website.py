import os
import sys
import csv
import re
import time
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def detect_delimiter(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(50000)
    try:
        return csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"]).delimiter
    except Exception:
        return ";"

def safe_read_csv(path: str) -> pd.DataFrame:
    delim = detect_delimiter(path)
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter=delim)
        for r in reader:
            rows.append(r)

    header = None
    for r in rows[:10]:
        if header is None or len(r) > len(header):
            header = r

    if not header or len(header) < 2:
        raise ValueError("Could not find a valid header row. The CSV header is likely corrupted.")

    data = []
    for r in rows[1:]:
        if len(r) == len(header):
            data.append(r)

    return pd.DataFrame(data, columns=header)

def norm(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() in ["nan", "none", "null"] else s

def find_org_ids(cols):
    ids = set()
    for c in cols:
        m = re.match(r"^organization_(\d+)$", c)
        if m:
            ids.add(int(m.group(1)))
    return sorted(ids)

def get_profile_link(row):
    for key in ["profile_url", "profile_link", "linkedin_url", "url", "public_profile_url"]:
        if key in row and norm(row.get(key, "")):
            return norm(row.get(key, ""))
    public_id = norm(row.get("public_id", "")) or norm(row.get("public-id", ""))
    if public_id:
        return f"https://www.linkedin.com/in/{public_id}/"
    return ""

def is_present_value(s: str) -> bool:
    t = norm(s).lower()
    if not t:
        return True
    return "present" in t or "current" in t or t in ["now", "today"]

def parse_date_loose(s: str):
    t = norm(s)
    if not t:
        return pd.NaT
    try:
        return pd.to_datetime(t, errors="coerce", infer_datetime_format=True, utc=False)
    except Exception:
        return pd.NaT

def pick_latest_org_slot(row, org_ids):
    candidates = []
    for i in org_ids:
        org_name = norm(row.get(f"organization_{i}", ""))
        if not org_name:
            continue

        end_val = norm(row.get(f"organization_end_{i}", "")) or norm(row.get(f"organization_end_date_{i}", ""))
        start_val = norm(row.get(f"organization_start_{i}", "")) or norm(row.get(f"organization_start_date_{i}", ""))

        present = is_present_value(end_val)

        end_dt = pd.Timestamp.max if present else parse_date_loose(end_val)
        start_dt = parse_date_loose(start_val)

        candidates.append((i, present, end_dt, start_dt))

    if not candidates:
        return None

    present_candidates = [c for c in candidates if c[1] is True]
    if present_candidates:
        # Deterministic: choose the smallest slot index among current roles
        return sorted(present_candidates, key=lambda x: x[0])[0][0]

    # No current job: pick most recent end date, then most recent start date, then smallest index
    candidates_sorted = sorted(
        candidates,
        key=lambda x: (
            pd.Timestamp.min if pd.isna(x[2]) else x[2],
            pd.Timestamp.min if pd.isna(x[3]) else x[3],
            -x[0]
        ),
        reverse=True
    )
    return candidates_sorted[0][0]

def to_about_url(company_url: str) -> str:
    u = norm(company_url)
    if not u:
        return ""
    if not u.endswith("/"):
        u += "/"
    return u + "about/"

def page_looks_missing_or_unavailable(page) -> bool:
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return True
    markers = [
        "this page doesn’t exist",
        "this page doesn't exist",
        "page not found",
        "doesn’t exist",
        "doesn't exist",
        "unavailable",
        "something went wrong",
        "profile not found",
        "we couldn't find",
    ]
    return any(m in body for m in markers)

def extract_company_website_from_about_strict(page) -> str:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    time.sleep(1.0)

    if page_looks_missing_or_unavailable(page):
        return ""

    js = """
    () => {
        function isExternalHttp(href) {
        if (!href) return false;
        const h = href.toLowerCase();
        if (!h.startsWith('http')) return false;

        // Exclude all LinkedIn links (they are not real websites)
        if (h.includes('linkedin.com')) return false;

        // Exclude shortened or redirect LinkedIn domains
        if (h.includes('lnkd.in')) return false;

        return true;
        }

      const labelNodes = Array.from(document.querySelectorAll('*'))
        .filter(el => el && el.innerText && el.innerText.trim().toLowerCase() === 'website')
        .slice(0, 30);

      const candidates = [];

      for (const label of labelNodes) {
        let container = label.parentElement;
        for (let i = 0; i < 6 && container; i++) {
          const links = Array.from(container.querySelectorAll('a'))
            .map(a => a.href)
            .filter(isExternalHttp);
          for (const l of links) candidates.push(l);
          container = container.parentElement;
        }
      }

      return candidates[0] || '';
    }
    """
    try:
        href = page.evaluate(js)
        return href.strip() if href else ""
    except Exception:
        return ""

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 verify_no_website.py <input_csv>")
        sys.exit(1)

    input_csv = sys.argv[1]
    if not os.path.exists(input_csv):
        print(f"File not found: {input_csv}")
        sys.exit(1)

    df = safe_read_csv(input_csv)
    org_ids = find_org_ids(df.columns)
    if not org_ids:
        print("No organization_X columns found in CSV.")
        sys.exit(1)

    out_no_site_csv = os.path.splitext(input_csv)[0] + "_CONFIRMED_NO_WEBSITE.csv"
    out_audit_xlsx = os.path.splitext(input_csv)[0] + "_LATEST_COMPANY_AUDIT.xlsx"

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        kept_rows = []
        audit_rows = []

        for _, row in df.iterrows():
            latest_i = pick_latest_org_slot(row, org_ids)
            profile_link = get_profile_link(row)

            if latest_i is None:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": "",
                    "company_name": "",
                    "company_page": "",
                    "company_website": "",
                    "status": "skipped_no_company_found"
                })
                continue

            company_name = norm(row.get(f"organization_{latest_i}", ""))
            company_page = norm(row.get(f"organization_url_{latest_i}", ""))

            website_csv = norm(row.get(f"organization_website_{latest_i}", ""))
            domain_csv = norm(row.get(f"organization_domain_{latest_i}", ""))

            # If CSV already contains website or domain for the latest company, exclude deterministically
            if website_csv or domain_csv:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": website_csv or domain_csv,
                    "status": "excluded_latest_company_has_website"
                })
                continue

            # If there is no company page URL for the latest company, keep
            if not company_page:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": "",
                    "company_website": "",
                    "status": "kept_latest_company_no_page_url"
                })
                kept_rows.append(row)
                continue

            about_url = to_about_url(company_page)
            if not about_url:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_no_page_url"
                })
                kept_rows.append(row)
                continue

            try:
                page.goto(about_url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_page_missing"
                })
                kept_rows.append(row)
                continue

            if page_looks_missing_or_unavailable(page):
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_page_missing"
                })
                kept_rows.append(row)
                continue

            site = extract_company_website_from_about_strict(page)
            if site:
                audit_rows.append({
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": site,
                    "status": "excluded_latest_company_has_website"
                })
                continue

            audit_rows.append({
                "profile": profile_link,
                "latest_company_slot": str(latest_i),
                "company_name": company_name,
                "company_page": company_page,
                "company_website": "",
                "status": "kept_latest_company_no_website"
            })
            kept_rows.append(row)

        out_df = pd.DataFrame(kept_rows)
        out_df.to_csv(out_no_site_csv, index=False, encoding="utf-8-sig")

        audit_df = pd.DataFrame(audit_rows, columns=[
            "profile",
            "latest_company_slot",
            "company_name",
            "company_page",
            "company_website",
            "status"
        ])
        with pd.ExcelWriter(out_audit_xlsx, engine="openpyxl") as writer:
            audit_df.to_excel(writer, index=False, sheet_name="audit")

        print("Done.")
        print("Input rows:", len(df))
        print("Kept rows:", len(out_df))
        print("Saved:", out_no_site_csv)
        print("Audit saved:", out_audit_xlsx)

if __name__ == "__main__":
    main()
