import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
import io
import re

# --- 설정값 ---
SEARCH_PERIOD_DAYS = 90 # 인기 동영상 검색 기간 (일)

# --- 공통 함수: 유튜브 영상 길이(ISO 8601)를 초 단위로 변환 ---
def parse_iso8601_duration(duration):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration).groups()
    hours = int(match[0]) if match[0] else 0
    minutes = int(match[1]) if match[1] else 0
    seconds = int(match[2]) if match[2] else 0
    return hours * 3600 + minutes * 60 + seconds

# --- API 호출 함수: 유튜브 카테고리 목록 가져오기 ---
@st.cache_data
def get_video_categories(_youtube, region_code='KR'):
    request = _youtube.videoCategories().list(part="snippet", regionCode=region_code, hl='ko')
    response = request.execute()
    return {item['id']: item['snippet']['title'] for item in response['items']}

# --- 공통 함수: API 응답 결과를 데이터프레임으로 변환 ---
def process_video_items(items, category_map):
    video_data = []
    now_utc = datetime.now(timezone.utc)
    for item in items:
        stats = item.get('statistics', {})
        view_count = int(stats.get('viewCount', 0))
        like_count = int(stats.get('likeCount', 0))
        comment_count = int(stats.get('commentCount', 0))
        engagement_rate = (like_count / view_count) * 100 if view_count > 0 else 0
        video_url = f"https://www.youtube.com/watch?v={item['id']}"
        published_at_str = item['snippet']['publishedAt']
        published_at_dt = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
        time_since_published = now_utc - published_at_dt
        hours_since_published = max(1, time_since_published.total_seconds() / 3600)
        views_per_hour = int(view_count / hours_since_published)
        upload_date_display = published_at_dt.strftime('%Y-%m-%d')
        duration_iso = item['contentDetails']['duration']
        duration_seconds = parse_iso8601_duration(duration_iso)
        video_type = "숏폼 (Shorts)" if duration_seconds <= 60 else "롱폼 (Long-form)"
        category_id = item['snippet'].get('categoryId', '')
        category_name = category_map.get(category_id, "기타")
        video_data.append({
            '제목': item['snippet']['title'], '조회수': view_count, '시간당 조회수': views_per_hour,
            '좋아요 수': like_count, '댓글 수': comment_count, '반응률 (%)': engagement_rate,
            '게시일': upload_date_display, '채널명': item['snippet']['channelTitle'],
            '카테고리': category_name, '영상 종류': video_type, 'URL': video_url
        })
    df = pd.DataFrame(video_data)
    column_order = [
        '제목', '조회수', '시간당 조회수', '좋아요 수', '댓글 수', '반응률 (%)',
        '게시일', '채널명', '카테고리', '영상 종류', 'URL'
    ]
    return df[column_order]

# --- API 호출 함수 1: 키워드 검색 ---
def get_youtube_data(youtube, category_map, query, max_results=50):
    try:
        # ▼▼▼ [수정된 부분] regionCode='KR'을 추가하여 한국 영상으로 제한합니다. ▼▼▼
        search_request = youtube.search().list(
            q=query, part='id', type='video', 
            maxResults=max_results, order='relevance', regionCode='KR'
        )
        search_response = search_request.execute()
        video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
        if not video_ids: return None
        video_request = youtube.videos().list(part="snippet,statistics,contentDetails", id=','.join(video_ids))
        video_response = video_request.execute()
        df = process_video_items(video_response.get('items', []), category_map)
        return df.sort_values(by='조회수', ascending=False)
    except Exception as e:
        st.error(f"검색 중 오류: {e}")
        return None

# --- API 호출 함수 2: 카테고리별 종합 인기 동영상 ---
@st.cache_data
def get_comprehensive_popular_videos(_youtube, category_map):
    try:
        excluded_categories = ['음악', '게임']
        excluded_ids = [cat_id for cat_id, cat_name in category_map.items() if cat_name in excluded_categories]
        all_video_ids = set()
        start_date = (datetime.now(timezone.utc) - timedelta(days=SEARCH_PERIOD_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for cat_id, cat_name in category_map.items():
            if cat_id in excluded_ids: continue
            search_request = _youtube.search().list(
                part='id', type='video', videoCategoryId=cat_id,
                maxResults=15, order='viewCount', regionCode='KR',
                publishedAfter=start_date
            )
            search_response = search_request.execute()
            for item in search_response.get('items', []):
                all_video_ids.add(item['id']['videoId'])
        if not all_video_ids: return None
        video_ids_list = list(all_video_ids)
        video_request = _youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=','.join(video_ids_list)
        )
        video_response = video_request.execute()
        df = process_video_items(video_response.get('items', []), category_map)
        return df.sort_values(by='조회수', ascending=False).head(100)
    except Exception as e:
        st.error(f"카테고리별 인기 동영상 로딩 중 오류: {e}")
        return None

# --- Streamlit 웹 UI 구성 ---
st.set_page_config(page_title="📈 유튜브 영상 분석기", page_icon="📈", layout="wide")
st.title("📈 유튜브 인기 영상 분석기"); st.markdown("---")
try: api_key = st.secrets["YOUTUBE_API_KEY"]
except KeyError: st.error("🔑 Streamlit Secrets에 API 키가 설정되지 않았습니다. 앱 설정(Manage app)에서 추가해주세요."); st.stop()
youtube = build('youtube', 'v3', developerKey=api_key); category_map = get_video_categories(youtube)
if 'comprehensive_data' not in st.session_state:
    st.session_state.comprehensive_data = get_comprehensive_popular_videos(youtube, category_map)
st.header("1. 키워드 검색 분석")
with st.form(key="search_form"):
    search_query = st.text_input("검색어 입력창", placeholder="🔍 분석하고 싶은 검색어를 입력하세요.", label_visibility="collapsed")
    submit_button = st.form_submit_button(label="📊 분석 시작!")
if submit_button and search_query:
    with st.spinner('데이터를 가져오는 중입니다...'): df_results = get_youtube_data(youtube, category_map, search_query)
    if df_results is not None and not df_results.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df_results.to_excel(writer, index=False, sheet_name='Sheet1')
        excel_data = output.getvalue()
        col1, col2 = st.columns([0.8, 0.2])
        with col1: st.header("분석 결과")
        with col2: st.download_button(label="📁 엑셀 파일 다운로드", data=excel_data, file_name=f"youtube_analysis_{search_query}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.dataframe(df_results, height=800, column_config={"조회수": st.column_config.NumberColumn(format="%d"), "시간당 조회수": st.column_config.NumberColumn(format="%d"),"좋아요 수": st.column_config.NumberColumn(format="%d"), "댓글 수": st.column_config.NumberColumn(format="%d"),"반응률 (%)": st.column_config.NumberColumn(format="%.2f%%"), "URL": st.column_config.LinkColumn("영상 링크", display_text="바로가기 ↗")})
    else: st.warning(f"'{search_query}'에 대한 검색 결과가 없습니다.")
st.markdown("---");
st.header(f"2. 카테고리별 종합 인기 동영상 (최근 {SEARCH_PERIOD_DAYS}일, TOP 100)")
df_popular = st.session_state.comprehensive_data
if df_popular is not None:
    all_categories = sorted(df_popular['카테고리'].unique())
    all_categories.insert(0, "전체")
    selected_category = st.selectbox('🗂️ 표시할 카테고리를 선택하세요:', all_categories)
    if selected_category == "전체": display_df = df_popular
    else: display_df = df_popular[df_popular['카테고리'] == selected_category]
    st.dataframe(display_df, height=800, column_config={"조회수": st.column_config.NumberColumn(format="%d"), "시간당 조회수": st.column_config.NumberColumn(format="%d"),"좋아요 수": st.column_config.NumberColumn(format="%d"), "댓글 수": st.column_config.NumberColumn(format="%d"),"반응률 (%)": st.column_config.NumberColumn(format="%.2f%%"), "URL": st.column_config.LinkColumn("영상 링크", display_text="바로가기 ↗")})
