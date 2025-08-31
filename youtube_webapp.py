import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
import io
import re
from pytrends.request import TrendReq

# --- 공통 함수: 유튜브 영상 길이(ISO 8601)를 초 단위로 변환 ---
def parse_iso8601_duration(duration):
    """ISO 8601 형식의 시간 문자열을 총 초 단위로 변환합니다."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration).groups()
    hours = int(match[0]) if match[0] else 0
    minutes = int(match[1]) if match[1] else 0
    seconds = int(match[2]) if match[2] else 0
    return hours * 3600 + minutes * 60 + seconds

# --- API 호출 함수: 유튜브 카테고리 목록 가져오기 ---
@st.cache_data
def get_video_categories(_youtube, region_code='KR'):
    """지정된 국가의 유튜브 동영상 카테고리 목록을 가져옵니다."""
    request = _youtube.videoCategories().list(part="snippet", regionCode=region_code, hl='ko')
    response = request.execute()
    return {item['id']: item['snippet']['title'] for item in response['items']}

# --- 공통 함수: API 응답 결과를 데이터프레임으로 변환 ---
def process_video_items(items, category_map):
    """API로부터 받은 영상 목록을 데이터프레임 형식으로 가공합니다."""
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
            '카테고리': category_name, '영상 종류': video_type, 'URL': video_url,
            'videoId': item['id'] # 나중에 데이터를 합치기 위한 숨은 ID
        })
    df = pd.DataFrame(video_data)
    column_order = [
        '제목', '조회수', '시간당 조회수', '좋아요 수', '댓글 수', '반응률 (%)',
        '게시일', '채널명', '카테고리', '영상 종류', 'URL', 'videoId'
    ]
    return df[column_order]

# --- API 호출 함수들 ---
def get_youtube_data(youtube, category_map, query, max_results=50):
    try:
        search_request = youtube.search().list(q=query, part='id', type='video', maxResults=max_results, order='relevance')
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

@st.cache_data
def get_trending_videos(_youtube, category_map):
    try:
        all_items = []
        next_page_token = None
        for _ in range(4):
            request = _youtube.videos().list(
                part="snippet,statistics,contentDetails", chart='mostPopular',
                regionCode='KR', maxResults=50, pageToken=next_page_token
            )
            response = request.execute()
            all_items.extend(response.get('items', []))
            next_page_token = response.get('nextPageToken')
            if not next_page_token: break
        return process_video_items(all_items, category_map)
    except Exception as e:
        st.error(f"인기 동영상 로딩 중 오류: {e}")
        return None

@st.cache_data
def get_trends_with_top_videos(_youtube, category_map):
    try:
        pytrends = TrendReq(hl='ko-KR', tz=540)
        # ▼▼▼ [수정된 부분] 'south_orea' 오타 수정 ▼▼▼
        trending_searches_df = pytrends.trending_searches(pn='south_korea')
        keywords = trending_searches_df[0].tolist()
        
        video_ids_map = {}
        for keyword in keywords:
            search_request = _youtube.search().list(q=keyword, part='id', type='video', maxResults=1, order='relevance')
            search_response = search_request.execute()
            if search_response.get('items'):
                video_ids_map[keyword] = search_response['items'][0]['id']['videoId']
        
        video_ids = list(video_ids_map.values())
        if not video_ids: return None
        video_request = _youtube.videos().list(part="snippet,statistics,contentDetails", id=','.join(video_ids))
        video_response = video_request.execute()
        
        videos_df = process_video_items(video_response.get('items', []), category_map)
        
        trend_data = []
        for keyword, video_id in video_ids_map.items():
            video_details = videos_df[videos_df['videoId'] == video_id]
            if not video_details.empty:
                details_dict = video_details.iloc[0].to_dict()
                details_dict['검색어'] = keyword
                trend_data.append(details_dict)
        
        df = pd.DataFrame(trend_data)
        # 검색어 순위와 관련된 열만 선택하여 순서 지정
        trend_column_order = [
            '검색어', '제목', '조회수', '시간당 조회수', '좋아요 수', '댓글 수', '반응률 (%)',
            '게시일', '채널명', '카테고리', '영상 종류', 'URL', 'videoId'
        ]
        return df[trend_column_order]
    except Exception as e:
        st.error(f"인기 검색어 로딩 중 오류: {e}")
        return None

# --- Streamlit 웹 UI 구성 ---
st.set_page_config(page_title="📈 유튜브 영상 분석기", page_icon="📈", layout="wide")
st.title("📈 유튜브 인기 영상 분석기"); st.markdown("---")
try: api_key = st.secrets["YOUTUBE_API_KEY"]
except KeyError: st.error("🔑 Streamlit Secrets에 API 키가 설정되지 않았습니다. 앱 설정(Manage app)에서 추가해주세요."); st.stop()

youtube = build('youtube', 'v3', developerKey=api_key); category_map = get_video_categories(youtube)
if 'trending_data' not in st.session_state:
    st.session_state.trending_data = get_trending_videos(youtube, category_map)

st.header("1. 키워드 검색 분석")
with st.form(key="search_form"):
    search_query = st.text_input("검색어 입력창", placeholder="🔍 분석하고 싶은 검색어를 입력하세요.", label_visibility="collapsed")
    submit_button = st.form_submit_button(label="📊 분석 시작!")
if submit_button and search_query:
    with st.spinner('데이터를 가져오는 중입니다...'): df_results = get_youtube_data(youtube, category_map, search_query)
    if df_results is not None and not df_results.empty:
        st.success(f"'{search_query}'에 대한 분석 완료!")
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df_results.to_excel(writer, index=False, sheet_name='Sheet1')
        excel_data = output.getvalue()
        col1, col2 = st.columns([0.8, 0.2]);
        with col1: st.header("분석 결과")
        with col2: st.download_button(label="📁 엑셀 파일 다운로드", data=excel_data, file_name=f"youtube_analysis_{search_query}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.dataframe(df_results, height=800, use_container_width=True, column_config={"조회수": st.column_config.NumberColumn(format="%d"), "시간당 조회수": st.column_config.NumberColumn(format="%d"),"좋아요 수": st.column_config.NumberColumn(format="%d"), "댓글 수": st.column_config.NumberColumn(format="%d"),"반응률 (%)": st.column_config.NumberColumn(format="%.2f%%"), "URL": st.column_config.LinkColumn("영상 링크", display_text="바로가기 ↗"), "videoId":None})
    else: st.warning(f"'{search_query}'에 대한 검색 결과가 없습니다.")

st.markdown("---"); st.header("2. 현재 대한민국 인기 동영상")
df_trending = st.session_state.trending_data
if df_trending is not None:
    all_categories = sorted(df_trending['카테고리'].unique()); all_categories.insert(0, "전체")
    selected_category = st.selectbox('🗂️ 표시할 카테고리를 선택하세요:', all_categories)
    if selected_category == "전체": display_df = df_trending
    else: display_df = df_trending[df_trending['카테고리'] == selected_category]
    st.dataframe(display_df, height=800, use_container_width=True, column_config={"조회수": st.column_config.NumberColumn(format="%d"), "시간당 조회수": st.column_config.NumberColumn(format="%d"),"좋아요 수": st.column_config.NumberColumn(format="%d"), "댓글 수": st.column_config.NumberColumn(format="%d"),"반응률 (%)": st.column_config.NumberColumn(format="%.2f%%"), "URL": st.column_config.LinkColumn("영상 링크", display_text="바로가기 ↗"), "videoId":None})

st.markdown("---")
st.header("3. 오늘의 인기 검색어 (Google Trends 기준)")
with st.spinner('오늘의 인기 검색어와 관련 영상을 불러오는 중입니다...'):
    df_trends = get_trends_with_top_videos(youtube, category_map)
if df_trends is not None:
    df_trends.insert(0, '순위', range(1, len(df_trends) + 1))
    st.dataframe(df_trends, height=800, use_container_width=True, column_config={"조회수": st.column_config.NumberColumn(format="%d"), "시간당 조회수": st.column_config.NumberColumn(format="%d"),"좋아요 수": st.column_config.NumberColumn(format="%d"), "댓글 수": st.column_config.NumberColumn(format="%d"),"반응률 (%)": st.column_config.NumberColumn(format="%.2f%%"), "URL": st.column_config.LinkColumn("영상 링크", display_text="바로가기 ↗"), "videoId":None})
