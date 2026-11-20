"""
Data Cleaning Agent
===================
Takes in a messy CSV / Excel / JSON file, cleans it, and writes out:
  1. <name>_cleaned.csv      -> the clean, analysis-ready data
  2. <name>_cleaning_report.md -> a log of every fix the agent made

How it works (the "agent loop"):
  1. PROFILE  - look at every column: type, blanks, sample values
  2. PLAN     - decide what to fix (rules, plus optional AI via Claude API)
  3. CLEAN    - apply the fixes step by step
  4. VALIDATE - re-check the result and report what's left

Usage:
  python data_cleaning_agent.py messy.csv
  python data_cleaning_agent.py messy.xlsx --ai          # also use Claude to plan fixes
  python data_cleaning_agent.py messy.csv --missing drop # drop rows with blanks instead of filling

Requirements:
  pip install pandas openpyxl
  pip install anthropic          # only needed for --ai
  export ANTHROPIC_API_KEY=...   # only needed for --ai
"""

import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

# Values that really mean "nothing here"
NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "missing", "#n/a", "nil"}


_ST = ("AL Alabama|AK Alaska|AZ Arizona|AR Arkansas|CA California|CO Colorado|CT Connecticut|DE Delaware|"
       "FL Florida|GA Georgia|HI Hawaii|ID Idaho|IL Illinois|IN Indiana|IA Iowa|KS Kansas|KY Kentucky|"
       "LA Louisiana|ME Maine|MD Maryland|MA Massachusetts|MI Michigan|MN Minnesota|MS Mississippi|"
       "MO Missouri|MT Montana|NE Nebraska|NV Nevada|NH New Hampshire|NJ New Jersey|NM New Mexico|NY New York|"
       "NC North Carolina|ND North Dakota|OH Ohio|OK Oklahoma|OR Oregon|PA Pennsylvania|RI Rhode Island|"
       "SC South Carolina|SD South Dakota|TN Tennessee|TX Texas|UT Utah|VT Vermont|VA Virginia|WA Washington|"
       "WV West Virginia|WI Wisconsin|WY Wyoming|DC District of Columbia")
US_STATES = {}
for _pair in _ST.split("|"):
    _ab, _full = _pair.split(" ", 1)
    US_STATES[_ab] = _full
    US_STATES[_full.upper()] = _full
US_STATES.update({"MINN": "Minnesota", "WIS": "Wisconsin", "WISC": "Wisconsin", "CALIF": "California", "MASS": "Massachusetts"})


class DataCleaningAgent:
    def __init__(self, use_ai=False, missing_strategy="fill", outlier_action="flag",
                 api_key=None, verbose=True):
        self.use_ai = use_ai
        self.api_key = api_key      # passed directly, never stored in shared environment variables
        self.verbose = verbose      # False in the web app, so users' data never appears in server logs
        self.missing_strategy = missing_strategy  # "fill" | "drop" | "keep"
        self.outlier_action = outlier_action      # "flag" | "remove" | "keep"
        self.log = []                              # every action, for the report
        self.ai_plan = {}

    # ------------------------------------------------------------------ helpers
    def note(self, step, message):
        self.log.append((step, message))
        if self.verbose:
            print(f"  [{step}] {message}")

    @staticmethod
    def _is_text(series):
        return series.dtype == object or pd.api.types.is_string_dtype(series)

    # ------------------------------------------------------------------ 1. INTAKE
    def load(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=object)
        elif ext == ".json":
            df = pd.read_json(path, dtype=False)
        else:
            df = pd.read_csv(path, dtype=object, keep_default_na=False, encoding_errors="replace")
        df = df.astype(object)
        self.note("intake", f"Loaded {len(df)} rows x {len(df.columns)} columns from {os.path.basename(path)}")
        return df

    # ------------------------------------------------------------------ 2. PROFILE
    def profile(self, df):
        prof = {}
        for col in df.columns:
            s = df[col]
            non_null = s.dropna()
            non_null = non_null[non_null.astype(str).str.strip() != ""]
            prof[col] = {
                "blanks": int(len(s) - len(non_null)),
                "unique": int(non_null.astype(str).nunique()),
                "samples": non_null.astype(str).drop_duplicates().head(15).tolist(),
            }
        return prof

    # ------------------------------------------------------------------ 3a. AI PLAN (optional)
    def ask_ai_for_plan(self, prof):
        """Ask Claude to look at the column profile and suggest fixes as JSON."""
        try:
            import anthropic
        except ImportError:
            self.note("ai", "anthropic package not installed - skipping AI step (pip install anthropic)")
            return {}
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            self.note("ai", "No API key provided - skipping AI step")
            return {}

        prompt = f"""You are a data-cleaning assistant. Here is a profile of a dataset's columns
(blank count, unique count, and sample values):

{json.dumps(prof, indent=2, default=str)}

Return ONLY a JSON object with this shape (omit keys you have nothing for):
{{
  "column_types": {{"<col>": "number|integer|date|text|category|email|phone|boolean"}},
  "value_mappings": {{"<col>": {{"<messy value>": "<standard value>"}}}},
  "valid_ranges": {{"<col>": [min, max]}},
  "notes": ["short notes about other issues you see"]
}}
Use value_mappings to merge spellings/abbreviations of the same thing
(e.g. "MN", "Minn." -> "Minnesota"). Use the column names exactly as given."""

        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        match = re.search(r"\{.*\}", text, re.S)
        try:
            plan = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            plan = {}
        self.note("ai", f"Claude suggested types for {len(plan.get('column_types', {}))} columns, "
                        f"mappings for {len(plan.get('value_mappings', {}))} columns")
        for n in plan.get("notes", []):
            self.note("ai", f"Note: {n}")
        return plan

    # ------------------------------------------------------------------ 3b. CLEAN steps
    def clean_column_names(self, df):
        old = list(df.columns)
        new = []
        for c in old:
            c2 = re.sub(r"[^0-9a-zA-Z]+", "_", str(c).strip()).strip("_").lower() or "column"
            while c2 in new:
                c2 += "_dup"
            new.append(c2)
        df.columns = new
        changed = [f"'{a}' -> '{b}'" for a, b in zip(old, new) if a != b]
        if changed:
            self.note("headers", f"Standardized {len(changed)} column names: " + ", ".join(changed[:8]))
        # keep AI plan keys in sync with renamed columns
        rename = dict(zip(old, new))
        for key in ("column_types", "value_mappings", "valid_ranges"):
            if key in self.ai_plan:
                self.ai_plan[key] = {rename.get(k, k): v for k, v in self.ai_plan[key].items()}
        return df

    def trim_and_nullify(self, df):
        count = 0
        for col in df.columns:
            def fix(v):
                nonlocal count
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    return np.nan
                if isinstance(v, str):
                    v2 = re.sub(r"\s+", " ", v).strip()
                    if v2.lower() in NULL_TOKENS:
                        count += 1
                        return np.nan
                    return v2
                return v
            df[col] = df[col].map(fix)
        self.note("whitespace", f"Trimmed extra spaces; converted {count} placeholder values (N/A, null, -, ...) to blank")
        return df

    def drop_empty(self, df):
        r0, c0 = df.shape
        df = df.dropna(how="all").dropna(axis=1, how="all")
        r1, c1 = df.shape
        if r0 - r1 or c0 - c1:
            self.note("empty", f"Removed {r0 - r1} fully empty rows and {c0 - c1} fully empty columns")
        return df

    def remove_duplicates(self, df):
        before = len(df)
        key = df.astype(str).apply(lambda s: s.str.lower())
        df = df[~key.duplicated()].reset_index(drop=True)
        removed = before - len(df)
        self.note("duplicates", f"Removed {removed} duplicate rows" if removed else "No duplicate rows found")
        return df

    @staticmethod
    def _to_number(v):
        if pd.isna(v):
            return np.nan
        if isinstance(v, (int, float, np.number)):
            return float(v)
        s = str(v).strip()
        neg = s.startswith("(") and s.endswith(")")
        pct = s.endswith("%")
        s = re.sub(r"[,$€£₹%()\s]", "", s)
        s = re.sub(r"(?i)(usd|inr|eur|k)$", "", s) if not re.fullmatch(r"-?\d+(\.\d+)?k", s, re.I) else s
        mult = 1
        if re.fullmatch(r"(?i)-?\d+(\.\d+)?k", s):
            mult, s = 1000, s[:-1]
        try:
            n = float(s) * mult
        except ValueError:
            return np.nan
        if pct:
            n = n / 100
        return -n if neg else n

    def detect_and_convert_types(self, df):
        ai_types = self.ai_plan.get("column_types", {})
        for col in df.columns:
            s = df[col]
            vals = s.dropna()
            if vals.empty:
                continue
            hint = ai_types.get(col, "")
            name = col.lower().replace("_", "")

            # --- numbers (also catches "$1,200", "15%", "(300)", "2k")
            nums = vals.map(self._to_number)
            num_rate = nums.notna().mean()
            looks_id = any(k in name for k in ("phone", "zip", "postal", "id", "code")) and hint not in ("number", "integer")
            if (hint in ("number", "integer") or num_rate >= 0.8) and not looks_id:
                bad = vals[nums.isna()]
                df[col] = s.map(self._to_number).round(2)
                if (df[col].dropna() % 1 == 0).all():
                    df[col] = df[col].astype("Int64")
                msg = f"'{col}': converted text to numbers"
                if len(bad):
                    msg += f"; {len(bad)} unreadable values set to blank (e.g. {list(bad.astype(str).unique()[:3])})"
                self.note("types", msg)
                continue

            # --- dates
            if hint == "date" or any(k in name for k in ("date", "time", "dob", "joined", "created", "updated")) \
                    or vals.astype(str).str.match(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}").mean() >= 0.8:
                parsed = vals.astype(str).map(self._parse_date)
                if parsed.notna().mean() >= 0.6:
                    bad = vals[parsed.isna()]
                    df[col] = s.map(lambda v: self._parse_date(v) if pd.notna(v) else pd.NaT)
                    df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
                    msg = f"'{col}': standardized dates to YYYY-MM-DD"
                    if len(bad):
                        msg += f"; {len(bad)} invalid dates set to blank (e.g. {list(bad.astype(str).unique()[:3])})"
                    self.note("types", msg)
                    continue

            # --- booleans
            low = vals.astype(str).str.lower()
            if low.isin({"yes", "no", "y", "n", "true", "false", "t", "f"}).all():
                df[col] = s.map(lambda v: np.nan if pd.isna(v) else str(v).lower() in {"yes", "y", "true", "t", "1"})
                self.note("types", f"'{col}': standardized yes/no values to True/False")
        return df

    @staticmethod
    def _parse_date(v):
        s = str(v).strip()
        for dayfirst in (False, True):
            try:
                d = pd.to_datetime(s, dayfirst=dayfirst, errors="raise")
                if 1900 <= d.year <= 2100:
                    return d
            except (ValueError, TypeError, OverflowError):
                continue
        return pd.NaT

    def standardize_text(self, df):
        mappings = self.ai_plan.get("value_mappings", {})
        for col in df.columns:
            if not self._is_text(df[col]):
                continue
            vals = df[col].dropna().astype(str)
            if vals.empty:
                continue
            name = col.lower().replace("_", "")

            # US states -> full name
            if "state" in name:
                before = df[col].copy()
                df[col] = df[col].map(lambda v: US_STATES.get(v.upper().strip("."), v.title() if v.isupper() or v.islower() else v)
                                      if isinstance(v, str) else v)
                n = int((before.fillna("") != df[col].fillna("")).sum())
                if n:
                    self.note("text", f"'{col}': standardized {n} state abbreviations/spellings to full names")

            # emails -> lowercase, flag invalid
            if "email" in name or vals.str.contains("@").mean() > 0.6:
                df[col] = df[col].map(lambda v: v.lower() if isinstance(v, str) else v)
                bad = df[col].dropna()[~df[col].dropna().str.match(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")]
                if len(bad):
                    df.loc[bad.index, col] = np.nan
                    self.note("text", f"'{col}': lowercased emails; {len(bad)} invalid emails set to blank (e.g. {list(bad.unique()[:3])})")
                else:
                    self.note("text", f"'{col}': lowercased emails")
                continue

            # phones -> digits only, US format if 10 digits
            if "phone" in name or "mobile" in name:
                def fmt(v):
                    if not isinstance(v, str):
                        return v
                    d = re.sub(r"\D", "", v)
                    if len(d) == 11 and d.startswith("1"):
                        d = d[1:]
                    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else np.nan
                before = df[col].notna().sum()
                df[col] = df[col].map(fmt)
                lost = before - df[col].notna().sum()
                self.note("text", f"'{col}': formatted phone numbers as (XXX) XXX-XXXX"
                          + (f"; {lost} invalid numbers set to blank" if lost else ""))
                continue

            # AI-suggested mappings first
            if col in mappings:
                m = {str(k).lower(): v for k, v in mappings[col].items()}
                changed = df[col].map(lambda v: isinstance(v, str) and v.lower() in m and m[v.lower()] != v).sum()
                df[col] = df[col].map(lambda v: m.get(v.lower(), v) if isinstance(v, str) else v)
                if changed:
                    self.note("text", f"'{col}': applied AI value mapping to {changed} values")

            # categories (few unique values) -> merge case variants, use most common spelling
            vals = df[col].dropna().astype(str)
            if vals.nunique() <= max(20, len(vals) * 0.5):
                groups = vals.groupby(vals.str.lower().str.replace(r"[^a-z0-9]", "", regex=True))
                canon = {}
                for _, g in groups:
                    best = g.value_counts().index[0]
                    if best.islower() or best.isupper() and len(best) > 3:
                        best = best.title()
                    for v in g.unique():
                        canon[v] = best
                changed = sum(1 for v in vals if canon.get(v, v) != v)
                if changed:
                    df[col] = df[col].map(lambda v: canon.get(v, v) if isinstance(v, str) else v)
                    self.note("text", f"'{col}': merged spelling/case variants ({changed} values fixed)")
            elif "name" in name:
                df[col] = df[col].map(lambda v: v.title() if isinstance(v, str) and (v.islower() or v.isupper()) else v)
                self.note("text", f"'{col}': fixed name capitalization")
        return df

    def handle_outliers(self, df):
        ranges = self.ai_plan.get("valid_ranges", {})
        self.outlier_cols = []
        for col in df.columns:
            if not pd.api.types.is_numeric_dtype(df[col]):
                continue
            s = df[col].astype(float)
            name = col.lower()
            if col in ranges:
                lo, hi = ranges[col]
            elif "age" in col.lower().split("_"):
                lo, hi = 0, 120
            else:
                q1, q3 = s.quantile([0.25, 0.75])
                iqr = q3 - q1
                if iqr == 0:
                    continue
                lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
            mask = (s < lo) | (s > hi)
            n = int(mask.sum())
            if not n:
                continue
            examples = s[mask].head(3).tolist()
            if self.outlier_action == "remove" or "age" in name.split("_"):
                df.loc[mask, col] = pd.NA
                self.note("outliers", f"'{col}': {n} impossible/extreme values set to blank {examples}")
            elif self.outlier_action == "flag":
                df[f"{col}_outlier"] = mask
                self.outlier_cols.append(col)
                self.note("outliers", f"'{col}': flagged {n} extreme values in '{col}_outlier' {examples} (kept, please review)")
        return df

    def handle_missing(self, df):
        if self.missing_strategy == "keep":
            return df
        if self.missing_strategy == "drop":
            before = len(df)
            df = df.dropna().reset_index(drop=True)
            self.note("missing", f"Dropped {before - len(df)} rows that had any blank value")
            return df
        for col in df.columns:
            n = int(df[col].isna().sum())
            if not n or col.endswith("_outlier"):
                continue
            name = col.lower().replace("_", "")
            if any(k in name for k in ("email", "phone", "date", "id", "name")):
                self.note("missing", f"'{col}': {n} blanks left as blank (can't safely guess this kind of value)")
            elif pd.api.types.is_numeric_dtype(df[col]):
                med = df[col].astype(float).median()
                fill = int(round(med)) if str(df[col].dtype) == "Int64" else med
                df[col] = df[col].fillna(fill)
                self.note("missing", f"'{col}': filled {n} blanks with the median ({fill})")
            else:
                df[col] = df[col].fillna("Unknown")
                self.note("missing", f"'{col}': filled {n} blanks with 'Unknown'")
        return df

    # ------------------------------------------------------------------ 4. RUN + VALIDATE
    def run(self, in_path, out_dir=None):
        if self.verbose:
            print(f"\nData Cleaning Agent -> {in_path}\n")
        df = self.load(in_path)
        raw_shape = df.shape
        raw_blanks = int(df.map(lambda v: pd.isna(v) or str(v).strip().lower() in NULL_TOKENS).sum().sum())

        if self.use_ai:
            self.ai_plan = self.ask_ai_for_plan(self.profile(df))

        df = self.clean_column_names(df)
        df = self.trim_and_nullify(df)
        df = self.drop_empty(df)
        df = self.remove_duplicates(df)
        df = self.detect_and_convert_types(df)
        df = self.standardize_text(df)
        df = self.remove_duplicates(df)  # second pass: rows may match now that text is standardized
        df = self.handle_outliers(df)
        df = self.handle_missing(df)

        base = os.path.splitext(os.path.basename(in_path))[0]
        out_dir = out_dir or os.path.dirname(os.path.abspath(in_path))
        out_csv = os.path.join(out_dir, f"{base}_cleaned.csv")
        out_md = os.path.join(out_dir, f"{base}_cleaning_report.md")
        df.to_csv(out_csv, index=False)
        self.write_report(out_md, in_path, raw_shape, raw_blanks, df)
        if self.verbose:
            print(f"\nDone. Clean data -> {out_csv}\n      Report     -> {out_md}\n")
        return df

    def write_report(self, path, in_path, raw_shape, raw_blanks, df):
        remaining = int(df.drop(columns=[c for c in df.columns if c.endswith("_outlier")]).isna().sum().sum())
        lines = [
            f"# Data Cleaning Report: {os.path.basename(in_path)}", "",
            "| | Before | After |", "|---|---|---|",
            f"| Rows | {raw_shape[0]} | {df.shape[0]} |",
            f"| Columns | {raw_shape[1]} | {df.shape[1]} |",
            f"| Blank / placeholder cells | {raw_blanks} | {remaining} |", "",
            "## What the agent did", "",
        ]
        lines += [f"- **{step}**: {msg}" for step, msg in self.log]
        if getattr(self, "outlier_cols", []):
            lines += ["", "## Needs your review", "",
                      f"- Rows flagged as extreme in: {', '.join(self.outlier_cols)} "
                      "(see the `_outlier` columns). They were kept, not deleted."]
        lines += ["", "## Final column types", "", "| Column | Type | Blanks |", "|---|---|---|"]
        lines += [f"| {c} | {df[c].dtype} | {int(df[c].isna().sum())} |" for c in df.columns]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main():
    p = argparse.ArgumentParser(description="AI data cleaning agent")
    p.add_argument("input", help="CSV, Excel (.xlsx) or JSON file to clean")
    p.add_argument("--ai", action="store_true", help="Use Claude (Anthropic API) to help plan fixes")
    p.add_argument("--missing", choices=["fill", "drop", "keep"], default="fill",
                   help="fill blanks (default), drop rows with blanks, or keep them")
    p.add_argument("--outliers", choices=["flag", "remove", "keep"], default="flag",
                   help="flag extreme numbers (default), blank them out, or leave them")
    p.add_argument("--out-dir", help="Folder for output files (default: same as input)")
    a = p.parse_args()
    if not os.path.exists(a.input):
        sys.exit(f"File not found: {a.input}")
    DataCleaningAgent(a.ai, a.missing, a.outliers).run(a.input, a.out_dir)


if __name__ == "__main__":
    main()
