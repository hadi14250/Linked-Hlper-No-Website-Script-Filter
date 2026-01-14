import os, sys, csv, pandas as pd

def detect_delimiter(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(50000)
    try:
        return csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"]).delimiter
    except Exception:
        return ";"

def norm(x):
    if x is None:
        return ""
    s = str(x).strip()
    return "" if s.lower() in ["nan", "none", "null"] else s

def main():
    path = sys.argv[1]
    delim = detect_delimiter(path)
    df = pd.read_csv(path, sep=delim, dtype=str, keep_default_na=False, engine="python")

    needed = ["organization_1", "organization_website_1", "organization_domain_1", "organization_url_1"]
    for c in needed:
        if c not in df.columns:
            raise SystemExit(f"Missing column: {c}")

    keep = (
        df["organization_1"].map(norm).ne("")
        & df["organization_website_1"].map(norm).eq("")
        & df["organization_domain_1"].map(norm).eq("")
        & df["organization_url_1"].map(norm).eq("")
    )

    out = os.path.splitext(path)[0] + "_NO_WEBSITE.csv"
    df.loc[keep].to_csv(out, index=False, encoding="utf-8-sig")

    print("Input rows:", len(df))
    print("Filtered rows:", int(keep.sum()))
    print("Saved:", out)

if __name__ == "__main__":
    main()
