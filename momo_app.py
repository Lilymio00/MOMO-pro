# momo_app.py
# 執行方式：streamlit run momo_app.py
import re
import time

import pandas as pd
import plotly.express as px
import streamlit as st
from curl_cffi import requests

# ------------------------------------------------------------------
# 基本設定
# ------------------------------------------------------------------
st.set_page_config(page_title="MOMO 商品價格查詢", page_icon="🛒", layout="wide")

# 中文字型：圖表由 Plotly 在「瀏覽器」端渲染，直接使用使用者系統內建的中文字型，
# 不需要 wget / 下載 ttf，也不用 matplotlib 的 fontManager。
CJK_FONT = (
    "Microsoft JhengHei, PingFang TC, Noto Sans TC, Noto Sans CJK TC, "
    "Heiti TC, WenQuanYi Micro Hei, sans-serif"
)

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HOME_URL = "https://www.momoshop.com.tw/"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ------------------------------------------------------------------
# 爬蟲（CODE01）
# ------------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


def parse_price(text) -> float:
    """'$1,299' -> 1299；'1,299~1,599' 取第一個數字；無法解析回傳 NaN"""
    m = re.search(r"\d[\d,]*", str(text))
    return float(m.group().replace(",", "")) if m else float("nan")


@st.cache_data(ttl=600, show_spinner=False)
def crawl_momo(keyword: str, pages: int, delay: float = 1.0) -> pd.DataFrame:
    """抓取 1~pages 頁，回傳整理後的 DataFrame（10 分鐘內相同查詢走快取）"""
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(HOME_URL, headers=HEADERS, timeout=30)  # 先取得 cookie

    rows = []
    for page in range(1, pages + 1):
        resp = session.post(
            API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=30
        )
        if resp.status_code != 200:
            raise RuntimeError(f"第 {page} 頁請求失敗：HTTP {resp.status_code}\n{resp.text[:300]}")

        products = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not products:  # 沒有更多資料就提早結束
            break
        for p in products:
            code = p.get("goodsCode", "")
            rows.append(
                {
                    "頁數": page,
                    "商品代碼": code,
                    "品名": p.get("goodsName", ""),
                    "價格": parse_price(p.get("goodsPrice", "")),
                    "連結": f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={code}"
                    if code
                    else "",
                }
            )
        time.sleep(delay)  # 禮貌性延遲，避免被擋

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["價格"])
    df["價格"] = df["價格"].astype(int)
    df = df.drop_duplicates(subset=["商品代碼", "品名"]).reset_index(drop=True)
    return df


# ------------------------------------------------------------------
# 資料處理 / 圖表（CODE02）
# ------------------------------------------------------------------
def add_price_range(df: pd.DataFrame, step: int) -> pd.DataFrame:
    df = df.copy()
    top = int(df["價格"].max())
    bins = list(range(0, (top // step + 2) * step, step))
    labels = [f"{bins[i]:,}~{bins[i + 1] - 1:,}" for i in range(len(bins) - 1)]
    df["價格區間"] = pd.cut(df["價格"], bins=bins, labels=labels, right=False)
    df["價格區間"] = df["價格區間"].astype(str)
    return df


def style_fig(fig):
    fig.update_layout(font=dict(family=CJK_FONT, size=14), title_font_size=22)
    return fig


def line_chart(df: pd.DataFrame, keyword: str):
    mean_price = df["價格"].mean()
    fig = px.line(
        df.reset_index(),
        x="index",
        y="價格",
        markers=True,
        hover_data={"品名": True, "index": False},
        labels={"index": "商品序號（store id）", "價格": "價格 (NT$)"},
        title=f"MOMO「{keyword}」售價折線圖",
    )
    fig.add_hline(
        y=mean_price,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均 {mean_price:,.0f}",
        annotation_position="top left",
    )
    return style_fig(fig)


def pie_chart(df: pd.DataFrame, keyword: str):
    order = df.groupby("價格區間")["價格"].min().sort_values().index.tolist()
    cnt = df["價格區間"].value_counts().reindex(order).reset_index()
    cnt.columns = ["價格區間", "商品數"]
    fig = px.pie(
        cnt,
        names="價格區間",
        values="商品數",
        hole=0.3,
        title=f"MOMO「{keyword}」價格區間占比",
    )
    fig.update_traces(sort=False, textinfo="percent+label")
    return style_fig(fig)


def sunburst_chart(df: pd.DataFrame, keyword: str, top_n: int):
    d = df.head(top_n).copy()
    d["商品"] = d["品名"].str.slice(0, 18)
    # 同一區間下若有重名，加上序號避免 Plotly 合併節點
    d["商品"] = d["商品"] + d.groupby(["價格區間", "商品"]).cumcount().map(
        lambda n: "" if n == 0 else f" ({n + 1})"
    )
    fig = px.sunburst(
        d,
        path=["價格區間", "商品"],
        values="價格",
        hover_data={"品名": True},
        title=f"MOMO「{keyword}」價格區間 → 商品（旭日圖，前 {len(d)} 筆）",
    )
    fig.update_layout(height=750)
    return style_fig(fig)


# ------------------------------------------------------------------
# Streamlit 介面
# ------------------------------------------------------------------
st.title("🛒 MOMO 商品價格查詢與視覺化")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    delay = st.slider("每頁間隔秒數", 0.5, 5.0, 1.0, 0.5)
    st.divider()
    chart_type = st.radio("圖表類型", ["折線圖", "圓餅圖", "旭日圖"])
    step = st.select_slider(
        "價格區間級距 (NT$)", options=[100, 200, 300, 500, 1000, 2000, 5000], value=500
    )
    top_n = st.slider("旭日圖顯示商品數", 20, 300, 100, 10)
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
    else:
        with st.spinner(f"正在爬取「{keyword}」前 {pages} 頁..."):
            try:
                st.session_state["df"] = crawl_momo(keyword.strip(), int(pages), delay)
                st.session_state["keyword"] = keyword.strip()
            except Exception as e:
                st.session_state.pop("df", None)
                st.error(f"爬取失敗：{e}")

df = st.session_state.get("df")
kw = st.session_state.get("keyword", "")

if df is None:
    st.info("請在左側輸入關鍵字與頁數後，按下「開始查詢」。")
elif df.empty:
    st.warning("查無資料，請換個關鍵字或稍後再試。")
else:
    df = add_price_range(df, step)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("商品數", f"{len(df):,}")
    c2.metric("平均價", f"{df['價格'].mean():,.0f}")
    c3.metric("最高價", f"{df['價格'].max():,}")
    c4.metric("最低價", f"{df['價格'].min():,}")

    if chart_type == "折線圖":
        fig = line_chart(df, kw)
    elif chart_type == "圓餅圖":
        fig = pie_chart(df, kw)
    else:
        fig = sunburst_chart(df, kw, top_n)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("商品明細")
    st.dataframe(
        df[["頁數", "品名", "價格", "價格區間", "連結"]],
        use_container_width=True,
        column_config={"連結": st.column_config.LinkColumn("連結", display_text="前往")},
    )
    st.download_button(
        "⬇️ 下載 CSV",
        df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=f"MOMO_{kw}_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
