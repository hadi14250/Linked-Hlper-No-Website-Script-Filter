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

def safe_read_csv(path):
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
        else:
            continue

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

def to_about_url(company_url: str) -> str:
    u = norm(company_url)
    if not u:
        return ""
    if not u.endswith("/"):
        u += "/"
    return u + "about/"

def extract_company_website_from_about(page) -> str:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    time.sleep(1.2)

    try:
        body = page.inner_text("body").lower()
    except Exception:
        return ""

    if "website" not in body:
        return ""

    js = """
    () => {
      function isExternalHttp(href) {
        if (!href) return false;
        const h = href.toLowerCase();
        if (!h.startsWith('http')) return false;
        if (h.includes('linkedin.com')) return false;
        return true;
      }

      const labelNodes = Array.from(document.querySelectorAll('*'))
        .filter(el => el && el.innerText && el.innerText.trim().toLowerCase() === 'website')
        .slice(0, 10);

      const candidates = [];
      for (const label of labelNodes) {
        let container = label.parentElement;
        for (let i = 0; i < 4 && container; i++) {
          const links = Array.from(container.querySelectorAll('a'))
            .map(a => a.href)
            .filter(isExternalHttp);
          for (const l of links) candidates.push(l);
          container = container.parentElement;
        }
      }

      if (candidates.length) return candidates[0];

      const any = Array.from(document.querySelectorAll('a'))
        .map(a => a.href)
        .filter(isExternalHttp);

      return any[0] || '';
    }
    """
    try:
        href = page.evaluate(js)
        return norm(href)
    except Exception:
        return ""

def get_profile_link(row):
    for key in ["profile_url", "profile_link", "linkedin_url", "url", "public_profile_url"]:
        if key in row and norm(row.get(key, "")):
            return norm(row.get(key, ""))
    public_id = norm(row.get("public_id", ""))
    if public_id:
        return f"https://www.linkedin.com/in/{public_id}/"
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
    out_audit_xlsx = os.path.splitext(input_csv)[0] + "_COMPANY_WEBSITE_AUDIT.xlsx"

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        kept_rows = []
        audit_rows = []

        for _, row in df.iterrows():
            any_org = False
            any_site_in_export = False

            org_slots = []
            for i in org_ids:
                org = norm(row.get(f"organization_{i}", ""))
                if not org:
                    continue
                any_org = True

                website = norm(row.get(f"organization_website_{i}", ""))
                domain = norm(row.get(f"organization_domain_{i}", ""))
                org_url = norm(row.get(f"organization_url_{i}", ""))

                if website or domain:
                    any_site_in_export = True
                    break

                if org_url:
                    org_slots.append((i, org, org_url))

            if not any_org:
                continue

            profile_link = get_profile_link(row)

            if any_site_in_export:
                for i, org_name, org_url in org_slots:
                    audit_rows.append({
                        "company_name": org_name,
                        "profile": profile_link,
                        "company_page": org_url,
                        "company_website": "(present in CSV via website/domain fields)"
                    })
                continue

            if not org_slots:
                continue

            found_external_site = ""
            found_company = ""
            found_company_url = ""

            for i, org_name, org_url in org_slots:
                about_url = to_about_url(org_url)
                if not about_url:
                    continue
                try:
                    page.goto(about_url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeoutError:
                    continue

                site = extract_company_website_from_about(page)
                if site:
                    found_external_site = site
                    found_company = org_name
                    found_company_url = org_url
                    break

            if found_external_site:
                audit_rows.append({
                    "company_name": found_company,
                    "profile": profile_link,
                    "company_page": found_company_url,
                    "company_website": found_external_site
                })
                continue

            for i, org_name, org_url in org_slots:
                audit_rows.append({
                    "company_name": org_name,
                    "profile": profile_link,
                    "company_page": org_url,
                    "company_website": ""
                })

            kept_rows.append(row)

        out_df = pd.DataFrame(kept_rows)
        out_df.to_csv(out_no_site_csv, index=False, encoding="utf-8-sig")

        audit_df = pd.DataFrame(audit_rows, columns=["company_name", "profile", "company_page", "company_website"])
        with pd.ExcelWriter(out_audit_xlsx, engine="openpyxl") as writer:
            audit_df.to_excel(writer, index=False, sheet_name="audit")

        print("Done.")
        print("Input rows:", len(df))
        print("Kept rows:", len(out_df))
        print("Saved:", out_no_site_csv)
        print("Audit saved:", out_audit_xlsx)

if __name__ == "__main__":
    main()
