import os
import sys
import csv
import re
import time
import random
import json
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def normalize_name(s: str) -> str:
    t = norm(s).lower()
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t

def get_current_company_name(row) -> str:
    a = norm(row.get("original_current_company", ""))
    b = norm(row.get("current_company", ""))
    return a or b

def pick_target_org_slot_by_current_company(row, org_ids):
    target = normalize_name(get_current_company_name(row))
    if not target:
        return None

    matches = []
    for i in org_ids:
        org_name = norm(row.get(f"organization_{i}", ""))
        if not org_name:
            continue
        if normalize_name(org_name) == target:
            matches.append(i)

    if matches:
        return sorted(matches)[0]

    return None

def is_real_website(value: str) -> bool:
    v = norm(value).lower()
    if not v:
        return False
    if not (v.startswith("http://") or v.startswith("https://")):
        v = "http://" + v

    if "linkedin.com" in v:
        return False
    if "lnkd.in" in v:
        return False

    return True

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
        def key(c):
            i, present, end_dt, start_dt = c
            start_key = pd.Timestamp.min if pd.isna(start_dt) else start_dt
            return (start_key, -i)

        best = sorted(present_candidates, key=key, reverse=True)[0]
        return best[0]

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

def human_pause(short=False):
    if short:
        time.sleep(random.uniform(2.5, 6.0))
    else:
        time.sleep(random.uniform(4.0, 10.0))

def safe_inner_text(page, selector: str, timeout_ms: int = 1500) -> str:
    try:
        loc = page.locator(selector)
        loc.first.wait_for(state="attached", timeout=timeout_ms)
        txt = loc.first.inner_text(timeout=timeout_ms)
        return txt or ""
    except Exception:
        return ""

def page_looks_checkpoint_or_verify(page) -> bool:
    try:
        u = (page.url or "").lower()
    except Exception:
        u = ""
    if "checkpoint" in u or "security" in u or "verify" in u:
        return True

    title = ""
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if any(k in title for k in ["checkpoint", "verify", "security verification", "unusual activity"]):
        return True

    snippet = (safe_inner_text(page, "main") or "").lower()
    if not snippet:
        snippet = (safe_inner_text(page, "body") or "").lower()

    markers = [
        "checkpoint",
        "verify your identity",
        "security verification",
        "unusual activity",
        "confirm it's you",
        "confirm it’s you",
        "prove you’re a human",
        "prove you're a human",
        "captcha",
        "complete this security check",
    ]
    return any(m in snippet for m in markers)

def page_looks_missing_or_unavailable(page) -> bool:
    try:
        u = (page.url or "").lower()
    except Exception:
        u = ""
    if any(x in u for x in ["404", "not-found", "page-not-found"]):
        return True

    title = ""
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""

    if any(x in title for x in ["page not found", "this page doesn", "unavailable", "something went wrong"]):
        return True

    body = (safe_inner_text(page, "main") or "").lower()
    if not body:
        body = (safe_inner_text(page, "body") or "").lower()
    if not body:
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
          if (h.includes('linkedin.com')) return false;
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

def extract_company_website_from_about_fallback(page) -> str:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass

    if page_looks_missing_or_unavailable(page):
        return ""

    js = """
    () => {
      function isExternalHttp(href) {
        if (!href) return false;
        const h = href.toLowerCase();
        if (!h.startsWith('http')) return false;
        if (h.includes('linkedin.com')) return false;
        if (h.includes('lnkd.in')) return false;
        return true;
      }

      const main = document.querySelector('main') || document.body;
      if (!main) return '';

      const links = Array.from(main.querySelectorAll('a'))
        .map(a => a.href)
        .filter(isExternalHttp);

      return links[0] || '';
    }
    """
    try:
        href = page.evaluate(js)
        return href.strip() if href else ""
    except Exception:
        return ""

def ensure_parent_dir(path: str):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def append_dict_row_csv(path: str, row_dict: dict, fieldnames: list):
    ensure_parent_dir(path)
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row_dict)

def save_progress(progress_path: str, state: dict):
    ensure_parent_dir(progress_path)
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

def load_progress(progress_path: str) -> dict:
    if not os.path.exists(progress_path):
        return {}
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def goto_with_retries(page, url: str, timeout_ms: int = 30000, max_attempts: int = 3):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return True
        except Exception as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(random.uniform(5.0, 15.0))
                continue
            return False
    return False

def write_final_outputs(kept_progress_csv: str, audit_progress_csv: str, out_no_site_csv: str, out_audit_xlsx: str):
    if os.path.exists(kept_progress_csv) and os.path.getsize(kept_progress_csv) > 0:
        out_df = pd.read_csv(kept_progress_csv, encoding="utf-8", dtype=str, keep_default_na=False)
    else:
        out_df = pd.DataFrame()

    out_df.to_csv(out_no_site_csv, index=False, encoding="utf-8-sig")

    if os.path.exists(audit_progress_csv) and os.path.getsize(audit_progress_csv) > 0:
        audit_df = pd.read_csv(audit_progress_csv, encoding="utf-8", dtype=str, keep_default_na=False)
    else:
        audit_df = pd.DataFrame(columns=[
            "profile",
            "latest_company_slot",
            "company_name",
            "company_page",
            "company_website",
            "status"
        ])

    with pd.ExcelWriter(out_audit_xlsx, engine="openpyxl") as writer:
        audit_df.to_excel(writer, index=False, sheet_name="audit")

    return len(out_df), len(audit_df)

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

    base = os.path.splitext(input_csv)[0]
    out_no_site_csv = base + "_CONFIRMED_NO_WEBSITE.csv"
    out_audit_xlsx = base + "_LATEST_COMPANY_AUDIT.xlsx"

    progress_path = base + "_PROGRESS.json"
    audit_progress_csv = base + "_AUDIT_PROGRESS.csv"
    kept_progress_csv = base + "_KEPT_PROGRESS.csv"

    audit_fields = ["profile", "latest_company_slot", "company_name", "company_page", "company_website", "status"]
    kept_fields = list(df.columns)

    state = load_progress(progress_path)
    processed = set(state.get("processed_indices", []))
    nav_count = int(state.get("nav_count", 0))
    processed_count = int(state.get("processed_count", 0))
    kept_count = int(state.get("kept_count", 0))
    excluded_count = int(state.get("excluded_count", 0))

    flush_every = 25
    max_nav_per_run = 150

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        for idx, row in df.iterrows():
            if idx in processed:
                continue

            if nav_count >= max_nav_per_run:
                print(f"Navigation cap reached ({max_nav_per_run}). Stopping. Rerun later to continue.")
                save_progress(progress_path, {
                    "processed_indices": sorted(list(processed)),
                    "nav_count": nav_count,
                    "processed_count": processed_count,
                    "kept_count": kept_count,
                    "excluded_count": excluded_count
                })
                break

            latest_i = pick_target_org_slot_by_current_company(row, org_ids)
            profile_link = get_profile_link(row)
            current_company_name = get_current_company_name(row)

            if latest_i is None:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": "",
                    "company_name": current_company_name,
                    "company_page": "",
                    "company_website": "",
                    "status": "kept_current_company_not_found_in_organizations"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
                kept_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            if latest_i is None:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": "",
                    "company_name": "",
                    "company_page": "",
                    "company_website": "",
                    "status": "skipped_no_company_found"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            company_name = norm(row.get(f"organization_{latest_i}", ""))
            company_page = norm(row.get(f"organization_url_{latest_i}", ""))

            website_csv = norm(row.get(f"organization_website_{latest_i}", ""))
            domain_csv = norm(row.get(f"organization_domain_{latest_i}", ""))

            csv_site = website_csv if is_real_website(website_csv) else ""
            if not csv_site:
                csv_site = domain_csv if is_real_website(domain_csv) else ""

            if csv_site:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": csv_site,
                    "status": "excluded_latest_company_has_website"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                excluded_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            if not company_page:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": "",
                    "company_website": "",
                    "status": "kept_latest_company_no_page_url"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
                kept_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            about_url = to_about_url(company_page)
            if not about_url:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_no_page_url"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
                kept_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            ok = goto_with_retries(page, about_url, timeout_ms=30000, max_attempts=3)
            if ok:
                nav_count += 1
                human_pause()
                if nav_count % 30 == 0:
                    print("Long break (human-like pause).")
                    time.sleep(random.uniform(120, 300))
            else:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_browser_error"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
                kept_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            if page_looks_checkpoint_or_verify(page):
                print("LinkedIn checkpoint / verification detected. Stop now, solve it in Chrome, then rerun.")
                save_progress(progress_path, {
                    "processed_indices": sorted(list(processed)),
                    "nav_count": nav_count,
                    "processed_count": processed_count,
                    "kept_count": kept_count,
                    "excluded_count": excluded_count
                })
                break

            if page_looks_missing_or_unavailable(page):
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": "",
                    "status": "kept_latest_company_page_missing"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
                kept_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            site = extract_company_website_from_about_strict(page)
            if not site:
                site = extract_company_website_from_about_fallback(page)

            human_pause(short=True)

            if site:
                audit_row = {
                    "profile": profile_link,
                    "latest_company_slot": str(latest_i),
                    "company_name": company_name,
                    "company_page": company_page,
                    "company_website": site,
                    "status": "excluded_latest_company_has_website"
                }
                append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
                excluded_count += 1
                processed.add(idx)
                processed_count += 1
                if processed_count % flush_every == 0:
                    save_progress(progress_path, {
                        "processed_indices": sorted(list(processed)),
                        "nav_count": nav_count,
                        "processed_count": processed_count,
                        "kept_count": kept_count,
                        "excluded_count": excluded_count
                    })
                if processed_count % 10 == 0:
                    print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")
                continue

            audit_row = {
                "profile": profile_link,
                "latest_company_slot": str(latest_i),
                "company_name": company_name,
                "company_page": company_page,
                "company_website": "",
                "status": "kept_latest_company_no_website"
            }
            append_dict_row_csv(audit_progress_csv, audit_row, audit_fields)
            append_dict_row_csv(kept_progress_csv, {k: norm(row.get(k, "")) for k in kept_fields}, kept_fields)
            kept_count += 1
            processed.add(idx)
            processed_count += 1

            if processed_count % flush_every == 0:
                save_progress(progress_path, {
                    "processed_indices": sorted(list(processed)),
                    "nav_count": nav_count,
                    "processed_count": processed_count,
                    "kept_count": kept_count,
                    "excluded_count": excluded_count
                })

            if processed_count % 10 == 0:
                print(f"Processed: {processed_count} | Kept: {kept_count} | Excluded: {excluded_count} | Navigations: {nav_count}")

        save_progress(progress_path, {
            "processed_indices": sorted(list(processed)),
            "nav_count": nav_count,
            "processed_count": processed_count,
            "kept_count": kept_count,
            "excluded_count": excluded_count
        })

    kept_rows_written, audit_rows_written = write_final_outputs(
        kept_progress_csv=kept_progress_csv,
        audit_progress_csv=audit_progress_csv,
        out_no_site_csv=out_no_site_csv,
        out_audit_xlsx=out_audit_xlsx
    )

    print("Done.")
    print("Input rows:", len(df))
    print("Kept rows:", kept_rows_written)
    print("Saved:", out_no_site_csv)
    print("Audit saved:", out_audit_xlsx)

if __name__ == "__main__":
    main()
