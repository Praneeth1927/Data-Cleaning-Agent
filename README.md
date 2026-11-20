# 🧹 Data Cleaning Agent

A web app that takes a messy CSV, Excel or JSON file and returns clean, analysis-ready data, plus a report of every fix it made.

**Try it:** _(paste your Streamlit app link here)_

## What it fixes

| Problem | Example before | After |
|---|---|---|
| Messy column names | `  Full Name ` | `full_name` |
| Placeholder blanks | `N/A`, `null`, `-` | real blank |
| Duplicate and empty rows | same customer twice | one row |
| Numbers stored as text | `"$1,200"` | `1200` |
| Mixed date formats | `01/22/2024`, `Feb 10 2024` | `2024-01-22`, `2024-02-10` |
| Yes/No variations | `Yes`, `y`, `TRUE` | `True` |
| Inconsistent text | `MN`, `mn`, `Minnesota ` | `Minnesota` |
| Phone and email formats | `612.555.0105` | `(612) 555-0105` |
| Impossible values | age `250` | blank |
| Extreme values | spend `$98,000` | kept, flagged for review |
| Missing values | blank age | filled with the median |

## How it works

1. **Profile:** looks at every column (type, blanks, sample values)
2. **Plan:** decides what to fix, using built-in rules and, optionally, Claude AI
3. **Clean:** applies the fixes step by step
4. **Report:** lists every change so nothing happens silently

## Run it on your computer

```bash
pip install -r requirements.txt
streamlit run app.py
```

Or from the command line, without the web page:

```bash
python data_cleaning_agent.py yourfile.csv
```

## Optional AI mode

Turn on **Use AI** in the sidebar and paste your own Anthropic API key. Claude then reviews your columns and suggests fixes that are hard to write as rules (for example, that `Minn.` means `Minnesota`). The key is used only for that run and is never saved.

## Built with

Python · pandas · Streamlit · Claude API
