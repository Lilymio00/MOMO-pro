import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ------------------------------------------------------------------
# 基本設定
# ------------------------------------------------------------------
st.set_page_config(page_title="MOMO 商品價格查詢", page_icon="🛒", layout="wide")

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
}

# 價格區間（供圓餅圖、旭日圖使用）
PRICE_BINS = [0, 300, 500, 1000, 2000, 5000, float("inf")]
PRICE_LABELS = ["300以下", "300~500", "500~1000", "1000~2000", "2000~5000", "5000以上"]


# ------------------------------------------------------------------
# 爬蟲：單頁
# ------------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "specialGoodsType": "",
            "isBrandSeriesPage": "false",
            "authorNo": "",
            "originalCateCode": "",
            "cateType": "",
            "searchValue": keyword,
            "cateCode": "",
            "cateLevel": "-1",
            "cp": "N",
            "NAM": "N",
            "first": "N",
            "freeze": "N",
            "superstore": "N",
            "tvshop": "N",
            "china": "N",
            "tomorrow": "N",
            "stockYN": "N",
            "prefere": "N",
            "threeHours": "N",
            "video": "N",
            "cycle": "N",
            "cod": "N",
            "superstorePay": "N",
            "showType": "chessboardType",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "searchType": "1",
            "reduceKeyword": "",
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
            "adSource": "tenmax",
        },
    }


def fetch_page(keyword: str, page: int) -> list:
    resp = requests.post(
        API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rtnSearchData", {}).get("goodsInfoList", []) or []


# ------------------------------------------------------------------
# 爬蟲：多頁 + 清洗（快取 10 分鐘）
# ------------------------------------------------------------------
def extract_brand(name: str) -> str:
    """商品名稱多半以【品牌】開頭，抓不到就歸為『其他』。"""
    m = re.search(r"【(.+?)】", name)
    return m.group(1).strip() if m else "其他"


@st.cache_data(ttl=600, show_spinner=False)
def crawl(keyword: str, pages: int, delay: float = 0.8) -> pd.DataFrame:
    rows = []
    progress = st.progress(0.0, text="開始爬取…")
    for p in range(1, pages + 1):
        try:
            items = fetch_page(keyword, p)
        except Exception as e:  # 單頁失敗不中斷整體
            st.warning(f"第 {p} 頁爬取失敗：{e}")
            items = []
        if not items:
            progress.progress(1.0, text=f"第 {p} 頁沒有資料，提前結束")
            break
        for it in items:
            rows.append(
                {
                    "品名": it.get("goodsName", ""),
                    "價格": it.get("goodsPrice", ""),
                    "頁數": p,
                }
            )
        progress.progress(p / pages, text=f"已完成第 {p} / {pages} 頁")
        time.sleep(delay)  # 禮貌性延遲，避免被封鎖
    progress.empty()

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 價格清洗：去掉 $、逗號等非數字字元
    df["價格"] = (
        df["價格"].astype(str).str.replace(r"[^\d]", "", regex=True)
    )
    df = df[df["價格"] != ""].copy()
    df["價格"] = df["價格"].astype(int)

    df = df.drop_duplicates(subset=["品名", "價格"]).reset_index(drop=True)
    df["品牌"] = df["品名"].apply(extract_brand)
    df["價格區間"] = pd.cut(
        df["價格"], bins=PRICE_BINS, labels=PRICE_LABELS, right=True
    ).astype(str)
    df.index.name = "編號"
    return df


# ------------------------------------------------------------------
# 圖表
# ------------------------------------------------------------------
def pie_chart(df: pd.DataFrame):
    count = (
        df["價格區間"].value_counts().reindex(PRICE_LABELS).dropna().reset_index()
    )
    count.columns = ["價格區間", "商品數"]
    fig = px.pie(
        count, names="價格區間", values="商品數", hole=0.35,
        title="價格區間分布（圓餅圖）",
    )
    fig.update_traces(textinfo="percent+label")
    return fig


def line_chart(df: pd.DataFrame, max_n: int):
    sub = df.head(max_n).reset_index()
    mean_price = df["價格"].mean()
    fig = px.line(
        sub, x="編號", y="價格", markers=True, hover_data=["品名"],
        title=f"商品價格走勢（前 {len(sub)} 筆，折線圖）",
    )
    fig.add_hline(
        y=mean_price, line_dash="dash", line_color="red",
        annotation_text=f"平均 {mean_price:,.0f}",
    )
    fig.update_layout(xaxis_title="商品編號", yaxis_title="價格 (TWD)")
    return fig


def sunburst_chart(df: pd.DataFrame, top_brand: int):
    # 只取商品數最多的前 N 個品牌，其餘合併為「其他」，避免圖太擁擠
    top = df["品牌"].value_counts().head(top_brand).index
    tmp = df.copy()
    tmp["品牌"] = tmp["品牌"].where(tmp["品牌"].isin(top), "其他")
    g = tmp.groupby(["價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(
        g, path=["價格區間", "品牌"], values="商品數",
        title="價格區間 → 品牌（旭日圖）",
    )
    fig.update_traces(textinfo="label+percent parent")
    fig.update_layout(height=650)
    return fig


# ------------------------------------------------------------------
# 介面
# ------------------------------------------------------------------
st.title("🛒 MOMO 購物網商品價格查詢")
st.caption("輸入商品關鍵字與爬取頁數，即時查詢並視覺化價格分布")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=3, step=1)
    chart_type = st.radio("圖表類型", ["圓餅圖", "折線圖", "旭日圖"])
    if chart_type == "折線圖":
        max_n = st.slider("顯示前幾筆商品", 10, 500, 70, step=10)
    if chart_type == "旭日圖":
        top_brand = st.slider("顯示前幾大品牌", 3, 20, 8)
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.error("請輸入商品關鍵字")
        st.stop()
    with st.spinner("爬取中，請稍候…"):
        st.session_state["df"] = crawl(keyword.strip(), int(pages))
        st.session_state["keyword"] = keyword.strip()

df = st.session_state.get("df")

if df is None:
    st.info("請在左側輸入條件後按「開始查詢」")
elif df.empty:
    st.warning("沒有查到資料，請換個關鍵字或稍後再試")
else:
    kw = st.session_state["keyword"]
    st.subheader(f"「{kw}」查詢結果，共 {len(df)} 筆")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("商品數", f"{len(df):,}")
    c2.metric("平均價格", f"{df['價格'].mean():,.0f}")
    c3.metric("最高價格", f"{df['價格'].max():,}")
    c4.metric("最低價格", f"{df['價格'].min():,}")

    if chart_type == "圓餅圖":
        st.plotly_chart(pie_chart(df), use_container_width=True)
    elif chart_type == "折線圖":
        st.plotly_chart(line_chart(df, max_n), use_container_width=True)
    else:
        st.plotly_chart(sunburst_chart(df, top_brand), use_container_width=True)

    with st.expander("📋 查看原始資料", expanded=False):
        st.dataframe(df[["品名", "價格", "品牌", "價格區間", "頁數"]], use_container_width=True)

    st.download_button(
        "⬇️ 下載 CSV",
        data=df.to_csv(encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=f"MOMO_{kw}_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
