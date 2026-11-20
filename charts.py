"""
Charts for cleaned data.
Looks at the clean table, works out which columns are dates, numbers and
categories, and draws the charts that make sense for them.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BLUE = "#2a78d6"     # main series color (works in light and dark mode)
ORANGE = "#eb6834"   # second color, only for "Other"/comparison
SKIP_WORDS = ("id", "email", "phone", "mobile", "name", "address", "zip")


def _style(fig, height=360):
    """Shared look: thin marks, quiet grid, no clutter."""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=40, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        hoverlabel=dict(font_size=13),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, title_font_size=12, automargin=True, title_standoff=12)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False, title_font_size=12,
                     automargin=True, title_standoff=12)
    return fig


def classify_columns(df):
    """Split columns into dates, numbers and categories that are worth charting."""
    dates, numbers, cats = [], [], []
    for col in df.columns:
        name = col.lower().replace("_", "")
        if col.endswith("_outlier") or any(w in name for w in SKIP_WORDS):
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        if pd.api.types.is_bool_dtype(df[col]) or set(s.unique()) <= {True, False}:
            cats.append(col)
        elif pd.api.types.is_numeric_dtype(df[col]):
            numbers.append(col)
        elif s.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}$").mean() >= 0.8:
            dates.append(col)
        elif 2 <= s.astype(str).nunique() <= 30:
            cats.append(col)
    return dates, numbers, cats


def _measure(df, how, col):
    """Return (series to aggregate, aggregation name, label for the axis)."""
    if how == "Number of rows":
        return None, "size", "Rows"
    if how.startswith("Total"):
        return col, "sum", f"Total {col}"
    return col, "mean", f"Average {col}"


def _agg(df, by, how, col):
    value_col, func, label = _measure(df, how, col)
    g = df.groupby(by, dropna=True)
    out = g.size() if func == "size" else getattr(g[value_col], func)()
    return out.rename(label).reset_index(), label


def time_chart(df, date_col, how, col):
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d = d.dropna(subset=[date_col])
    if d.empty:
        return None
    span = (d[date_col].max() - d[date_col].min()).days
    freq, label = ("MS", "Month") if span > 90 else ("D", "Day")
    d["period"] = d[date_col].dt.to_period("M" if freq == "MS" else "D").dt.to_timestamp()
    data, ylab = _agg(d, "period", how, col)
    fig = px.line(data, x="period", y=ylab, markers=True, labels={"period": label})
    fig.update_traces(line=dict(color=BLUE, width=2), marker=dict(size=8, color=BLUE),
                      hovertemplate="%{x|%b %Y}<br>%{y:,.2f}<extra></extra>" if freq == "MS"
                      else "%{x|%b %d, %Y}<br>%{y:,.2f}<extra></extra>")
    return _style(fig)


def category_chart(df, cat_col, how, col, top=10):
    data, ylab = _agg(df.assign(**{cat_col: df[cat_col].astype(str)}), cat_col, how, col)
    data = data.sort_values(ylab, ascending=False)
    if len(data) > top:  # fold the long tail into "Other" instead of inventing more colors
        other = data.iloc[top:][ylab].sum() if not how.startswith("Average") else data.iloc[top:][ylab].mean()
        data = pd.concat([data.iloc[:top], pd.DataFrame({cat_col: ["Other"], ylab: [other]})])
    data = data.iloc[::-1]  # biggest bar on top
    colors = [ORANGE if v == "Other" else BLUE for v in data[cat_col]]
    fig = go.Figure(go.Bar(
        x=data[ylab], y=data[cat_col], orientation="h", marker_color=colors,
        marker_cornerradius=4, hovertemplate="%{y}<br>%{x:,.2f}<extra></extra>",
    ))
    fig.update_xaxes(title_text=ylab)
    return _style(fig, height=max(240, 34 * len(data) + 60))


def histogram(df, num_col):
    s = df[num_col].dropna().astype(float)
    if "outlier" in " ".join(df.columns) and f"{num_col}_outlier" in df.columns:
        s = df.loc[~df[f"{num_col}_outlier"].fillna(False).astype(bool), num_col].dropna().astype(float)
    fig = px.histogram(s, x=num_col, nbins=30)
    fig.update_traces(marker_color=BLUE, marker_cornerradius=4,
                      hovertemplate=f"{num_col}: %{{x}}<br>Rows: %{{y:,}}<extra></extra>")
    fig.update_yaxes(title_text="Rows")
    fig = _style(fig, height=300)
    fig.update_layout(bargap=0.05)  # histogram bars sit next to each other
    return fig


def completeness_chart(df):
    cols = [c for c in df.columns if not c.endswith("_outlier")]
    pct = (df[cols].notna().mean() * 100).round(1).sort_values()
    fig = go.Figure(go.Bar(
        x=pct.values, y=pct.index, orientation="h", marker_color=BLUE, marker_cornerradius=4,
        hovertemplate="%{y}<br>%{x:.1f}% filled<extra></extra>",
        text=[f"{v:.0f}%" for v in pct.values], textposition="outside", cliponaxis=False,
    ))
    fig.update_xaxes(range=[0, 110], title_text="% of rows filled", ticksuffix="%")
    return _style(fig, height=max(240, 30 * len(pct) + 60))


def show_charts(df):
    """Draw the whole charts section in the Streamlit page."""
    dates, numbers, cats = classify_columns(df)
    st.header("📊 Charts of your clean data")

    if not (dates or numbers or cats):
        st.info("No columns suitable for charts were found (charts need dates, numbers or categories).")
        return

    # One "measure" control drives the trend and category charts together
    options = ["Number of rows"] + [f"Total {c}" for c in numbers] + [f"Average {c}" for c in numbers]
    how = st.selectbox("What should the charts measure?", options,
                       help="Count rows, or add up / average one of your number columns.")
    col = how.split(" ", 1)[1] if how != "Number of rows" else None

    tab_names = []
    if dates:
        tab_names.append("📈 Trend over time")
    if cats:
        tab_names.append("📊 By category")
    if numbers:
        tab_names.append("📉 Distributions")
    tab_names += ["🛠️ Build your own", "✅ Data completeness"]
    tabs = dict(zip(tab_names, st.tabs(tab_names)))

    if dates:
        with tabs["📈 Trend over time"]:
            dcol = st.selectbox("Date column", dates, key="date_col")
            st.subheader(f"{how} over time")
            fig = time_chart(df, dcol, how, col)
            if fig:
                st.plotly_chart(fig, width="stretch", key="chart_1")
                st.caption("The first and last points can look low if your data starts or ends partway through a month.")
            else:
                st.info("No valid dates to chart.")

    if cats:
        with tabs["📊 By category"]:
            ccol = st.selectbox("Category column", cats, key="cat_col")
            st.subheader(f"{how} by {ccol}")
            st.plotly_chart(category_chart(df, ccol, how, col), width="stretch", key="chart_2")
            if df[ccol].astype(str).nunique() > 10:
                st.caption("Showing the top 10; the rest are grouped as 'Other' (orange).")

    if numbers:
        with tabs["📉 Distributions"]:
            ncol = st.selectbox("Number column", numbers, key="num_col")
            st.subheader(f"How {ncol} values are spread")
            st.plotly_chart(histogram(df, ncol), width="stretch", key="chart_3")
            s = df[ncol].dropna().astype(float)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Average", f"{s.mean():,.2f}")
            c2.metric("Median", f"{s.median():,.2f}")
            c3.metric("Smallest", f"{s.min():,.2f}")
            c4.metric("Largest", f"{s.max():,.2f}")
            if f"{ncol}_outlier" in df.columns:
                st.caption("Flagged outliers are left out of this chart so the shape stays readable.")

    with tabs["🛠️ Build your own"]:
        kinds = []
        if cats:
            kinds.append("Bar chart")
        if dates:
            kinds.append("Line chart (over time)")
        if numbers:
            kinds.append("Histogram")
        if len(numbers) >= 2:
            kinds.append("Scatter plot")
        kind = st.radio("Chart type", kinds, horizontal=True)
        if kind == "Bar chart":
            x = st.selectbox("Group by", cats, key="own_bar_x")
            h = st.selectbox("Measure", options, key="own_bar_m")
            st.plotly_chart(category_chart(df, x, h, h.split(" ", 1)[1] if h != "Number of rows" else None),
                            width="stretch", key="chart_4")
        elif kind == "Line chart (over time)":
            x = st.selectbox("Date column", dates, key="own_line_x")
            h = st.selectbox("Measure", options, key="own_line_m")
            fig = time_chart(df, x, h, h.split(" ", 1)[1] if h != "Number of rows" else None)
            if fig:
                st.plotly_chart(fig, width="stretch", key="chart_5")
        elif kind == "Histogram":
            x = st.selectbox("Number column", numbers, key="own_hist")
            st.plotly_chart(histogram(df, x), width="stretch", key="chart_6")
        elif kind == "Scatter plot":
            x = st.selectbox("X axis", numbers, key="own_sc_x")
            y = st.selectbox("Y axis", numbers, index=1, key="own_sc_y")
            if x == y:
                st.info("Pick two different columns to compare.")
                return
            sample = df[[x, y]].dropna().astype(float)
            if len(sample) > 5000:
                sample = sample.sample(5000, random_state=0)
                st.caption("Showing a random sample of 5,000 rows so the chart stays fast.")
            fig = px.scatter(sample, x=x, y=y, opacity=0.5)
            fig.update_traces(marker=dict(color=BLUE, size=8))
            st.plotly_chart(_style(fig), width="stretch", key="chart_7")
            st.caption(f"Correlation between {x} and {y}: **{sample[x].corr(sample[y]):.2f}** "
                       "(1 = move together, 0 = no relationship, -1 = move opposite)")

    with tabs["✅ Data completeness"]:
        st.subheader("How complete each column is after cleaning")
        st.plotly_chart(completeness_chart(df), width="stretch", key="chart_8")
        st.caption("Blanks that remain are values the agent wouldn't guess (like emails and dates).")
