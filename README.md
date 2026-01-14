# LinkedIn Company Website Verifier

This tool takes a Linked Helper CSV export, opens each company’s LinkedIn About page using your logged in Chrome, extracts the real company website, and outputs two files:

1) A CSV with only companies that truly have no website  
2) An Excel file listing every company, the profile, and the detected website  

---

## Files in this folder

verify_no_website.py  
original.csv (your Linked Helper export)  
requirements.txt  

Your requirements.txt should contain:

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

---

## Step 7. Output files

After it finishes, you will get:

original_CONFIRMED_NO_WEBSITE.csv  
Contains only companies that truly have no website.

original_COMPANY_WEBSITE_AUDIT.xlsx  
Contains company name, profile link, LinkedIn company page, and the extracted website.

---

## Common problems

If you get ECONNREFUSED 127.0.0.1:9222  
Chrome is not running in debug mode. Redo Step 5.

If it says openpyxl missing  
Run:  
python3 -m pip install openpyxl  

If LinkedIn shows a security or verification page  
Solve it in the Chrome window, then rerun the script.

---

## Fast run checklist

1) source .venv/bin/activate  
2) open Chrome debug mode  
3) log into LinkedIn  
4) python3 verify_no_website.py original.csv  
