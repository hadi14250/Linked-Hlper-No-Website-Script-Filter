# LinkedIn Company Website Verifier

This tool takes a Linked Helper CSV export, finds each profile's current company, opens that company's LinkedIn About page using your logged in Chrome, and extracts the real company website. It outputs three files:

1) A CSV of profiles whose current company has no website
2) An Excel audit file with the per-profile decision and detected website
3) An Excel summary file with profile name, company, role, website, page, and headcount

The run is resumable. Progress is saved to disk and the script picks up where it left off if you stop it or hit a navigation cap.

---

## Files in this folder

verify_no_website.py
original.csv (your Linked Helper export)
requirements.txt

requirements.txt contains:

pandas
playwright
openpyxl

---

## Step 1. Create a virtual environment (do once)

Open Terminal in the project folder and run:

python3 -m venv .venv

---

## Step 2. Activate the virtual environment (every time)

source .venv/bin/activate

You should see (.venv) in your terminal.

---

## Step 3. Install dependencies (do once)

python3 -m pip install -U pip
python3 -m pip install -r requirements.txt

---

## Step 4. Install Playwright browser (do once)

python3 -m playwright install chromium

---

## Step 5. Start Chrome in LinkedIn scraping mode (every run)

Close Chrome first:

pkill -f "Google Chrome"
pkill -f "Chrome Helper"

Then start Chrome in debug mode:

open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

A new Chrome window opens.

Log into LinkedIn in that Chrome window.
Leave it open.

Check that it is running:

curl http://127.0.0.1:9222/json/version

If it prints JSON, it is working.

---

## Step 6. Run the script

python3 verify_no_website.py original.csv

If your file is named differently:

python3 verify_no_website.py "yourfile.csv"

The script processes up to 150 navigations per run and pauses for a longer human-like break every 30 navigations. If it stops early (cap reached, checkpoint, or you Ctrl+C), just run the same command again to resume.

---

## Step 7. Output files

After it finishes you will get three outputs alongside your input CSV:

original_CONFIRMED_NO_WEBSITE.csv
The full Linked Helper rows for profiles whose current company has no detectable website.

original_LATEST_COMPANY_AUDIT.xlsx
One row per processed profile with: profile link, current company slot, company name, company LinkedIn page, detected website, and status (e.g. excluded_latest_company_has_website, kept_latest_company_no_website, kept_latest_company_page_missing).

original_LATEST_COMPANY_SUMMARY.xlsx
One row per processed profile with: profile_name, company_name, role, company_website, company_linkedin_page, headcount.

The script also writes progress files (_PROGRESS.json, _AUDIT_PROGRESS.csv, _KEPT_PROGRESS.csv, _SUMMARY_PROGRESS.csv) so it can resume across runs. Delete them if you want to start fresh.

---

## How it picks the company

For each profile, the script looks at the Linked Helper "current company" field and finds the matching organization_X slot in that row. It only verifies that one company — not every past job. If no slot matches the current company, the profile is kept as no-website by default and flagged in the audit.

If Linked Helper already lists a real website (or domain) for that company in the CSV, the script trusts it and skips the LinkedIn lookup. Otherwise it opens the company's LinkedIn About page and pulls the website link near the "Website" label, falling back to the first external link in the main content.

---

## Common problems

If you get ECONNREFUSED 127.0.0.1:9222
Chrome is not running in debug mode. Redo Step 5.

If it says openpyxl missing
Run:
python3 -m pip install openpyxl

If LinkedIn shows a security or verification page
The script detects this, saves progress, and stops. Solve the challenge in the Chrome window, then rerun the same command to resume.

If you hit the per-run navigation cap
The script prints a message and saves progress. Just rerun to keep going.

---

## Fast run checklist

1) source .venv/bin/activate
2) open Chrome in debug mode
3) log into LinkedIn
4) python3 verify_no_website.py original.csv
