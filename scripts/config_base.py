# -*- coding: utf-8 -*-
"""
비민감(non-secret) 설정.
API 키, Blog ID, OAuth 정보 등 민감정보는 여기 두지 않고
환경변수(GitHub Secrets)에서 읽는다. -> config_secrets.py 참고
"""

# ── 검색 주제 ────────────────────────────────────────────────
# 두 주제를 한 블로그에서 함께 다룬다.
# 각 항목: (검색 키워드, 블로그에 붙일 라벨)
CONTEST_KEYWORDS = [
    "대학생 공모전",
    "청소년 공모전",
    "디자인 공모전",
    "아이디어 공모전",
    "영상 공모전",
    "글쓰기 공모전",
    "정부 공모전",
    "기업 공모전",
]

FESTIVAL_KEYWORDS = [
    "지역 축제",
    "가을 축제",
    "문화 축제",
    "음식 축제",
    "불꽃 축제",
    "전통 축제",
    "지자체 축제 일정",
]

TOPIC_LABELS = {
    "contest": "공모전",
    "festival": "축제",
}

# ── Google News RSS 설정 ────────────────────────────────────
NEWS_RECENCY_DAYS = 5          # when:Nd
NEWS_LANG = "ko"
NEWS_COUNTRY = "KR"
MAX_ARTICLES_PER_KEYWORD = 6   # 키워드당 최대 수집 기사 수

# ── 중복 방지 ────────────────────────────────────────────────
TITLE_SIMILARITY_THRESHOLD = 0.72   # difflib 유사도, 이 이상이면 중복 취급
RECENT_POSTS_TO_CHECK = 60          # Blogger에서 최근 몇 건 제목을 가져와 중복체크할지

# ── 게시 상한 (스팸 방지) ───────────────────────────────────
MAX_POSTS_PER_RUN = 2          # 한 번 실행(Actions 1회)당 최대 게시 수
MIN_SLEEP_BETWEEN_POSTS = 40   # 초
MAX_SLEEP_BETWEEN_POSTS = 110  # 초

# ── Gemini 설정 ──────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"

# 글 구조/각도를 매번 랜덤하게 섞어서 색인 다양성 확보 (기존 kpop 블로그 패턴과 동일)
WRITING_ANGLES = [
    "신청 방법과 마감일 중심으로 정리",
    "대상/참가자격을 가장 먼저 강조",
    "시상 내역과 혜택을 앞세워 소개",
    "왜 참가할 만한지 매력 포인트 중심 서술",
    "일정표 형태로 핵심 정보를 정리",
    "초보자가 준비하는 방법 팁 위주로 서술",
]

# ── Blogger 게시 라벨 ────────────────────────────────────────
def labels_for(topic_key: str):
    base = ["공모전축제정보", TOPIC_LABELS[topic_key]]
    return base
