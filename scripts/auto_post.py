# -*- coding: utf-8 -*-
"""
공모전·축제 정보 블로그 자동 포스팅 스크립트.

흐름:
  1) Google News RSS에서 '공모전'/'축제' 관련 최신 기사 수집
  2) Blogger에서 최근 게시글 제목을 가져와 중복(유사) 기사 제거
  3) 후보 기사 중 MAX_POSTS_PER_RUN개를 골라 Gemini로 블로그 글 재작성
  4) 원문 기사의 대표 이미지를 추출해 핫링크 우회 방식으로 삽입
  5) Blogger API로 게시, 각 게시 사이에 무작위 대기 (스팸 방지)

GitHub Actions에서 스케줄 실행되며, 별도의 로컬 상태 파일 없이
Blogger에 이미 올라간 글 목록을 기준으로 중복을 판단하므로 무상태(stateless)로 동작한다.
"""
import os
import sys
import time
import random
import difflib
import urllib.parse
import xml.etree.ElementTree as ET

import re

import requests
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google import genai

sys.path.append(os.path.dirname(__file__))
import config_base as cfg
import config_secrets

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# ────────────────────────────────────────────────────────────
# Blogger 인증
# ────────────────────────────────────────────────────────────
def get_blogger_service(secrets):
    creds = Credentials(
        token=None,
        refresh_token=secrets["GOOGLE_REFRESH_TOKEN"],
        client_id=secrets["GOOGLE_CLIENT_ID"],
        client_secret=secrets["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)


def get_recent_post_titles(service, blog_id):
    titles = []
    try:
        resp = service.posts().list(
            blogId=blog_id,
            maxResults=cfg.RECENT_POSTS_TO_CHECK,
            fetchBodies=False,
            status="LIVE",
        ).execute()
        for item in resp.get("items", []):
            titles.append(item.get("title", ""))
    except Exception as e:
        print(f"[경고] 최근 게시글 조회 실패 (중복체크 생략됨): {e}")
    return titles


def publish_post(service, blog_id, title, html_content, labels):
    body = {"title": title, "content": html_content, "labels": labels}
    return service.posts().insert(blogId=blog_id, body=body, isDraft=False).execute()


# ────────────────────────────────────────────────────────────
# 뉴스 수집 (Google News RSS)
# ────────────────────────────────────────────────────────────
def fetch_news(keyword: str, days: int = None):
    days = days or cfg.NEWS_RECENCY_DAYS
    q = f"{keyword} when:{days}d"
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": q})
        + f"&hl={cfg.NEWS_LANG}&gl={cfg.NEWS_COUNTRY}&ceid={cfg.NEWS_COUNTRY}:{cfg.NEWS_LANG}"
    )
    articles = []
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall("./channel/item")[: cfg.MAX_ARTICLES_PER_KEYWORD]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = source_el.text if source_el is not None else ""
            if title and link:
                articles.append({"title": title, "link": link, "pubDate": pub_date, "source": source})
    except Exception as e:
        print(f"[경고] 뉴스 수집 실패 ({keyword}): {e}")
    return articles


def collect_candidates():
    candidates = []
    for kw in cfg.CONTEST_KEYWORDS:
        for a in fetch_news(kw):
            a["topic_key"] = "contest"
            candidates.append(a)
    for kw in cfg.FESTIVAL_KEYWORDS:
        for a in fetch_news(kw):
            a["topic_key"] = "festival"
            candidates.append(a)
    random.shuffle(candidates)
    return candidates


# ────────────────────────────────────────────────────────────
# 중복 체크
# ────────────────────────────────────────────────────────────
def is_duplicate(title: str, existing_titles) -> bool:
    for t in existing_titles:
        ratio = difflib.SequenceMatcher(None, title, t).ratio()
        if ratio >= cfg.TITLE_SIMILARITY_THRESHOLD:
            return True
    return False


# ────────────────────────────────────────────────────────────
# 원문 기사 대표 이미지 추출
# ────────────────────────────────────────────────────────────

def resolve_real_url(google_news_link: str) -> str:
    """Google News RSS 링크를 실제 언론사 기사 URL로 변환"""
    try:
        resp = requests.get(google_news_link, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        if "news.google.com" not in resp.url:
            return resp.url
        m = re.search(r'<meta\s+http-equiv=["\']refresh["\']\s+content=["\']\d+;\s*url=([^"\']+)["\']', resp.text, re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return google_news_link  # 실패 시 원래 링크 그대로 사용
  
def extract_og_image(article_url: str):
    try:
        resp = requests.get(article_url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if tag and tag.get("content"):
            img_url = tag["content"]
            if "google.com" in img_url or "gstatic.com" in img_url:
                return None  # 구글 자체 로고/썸네일은 사용하지 않음
            return img_url
    except Exception as e:
        print(f"[정보] 대표 이미지 추출 실패: {e}")
    return None


# ────────────────────────────────────────────────────────────
# Gemini로 블로그 글 생성
# ────────────────────────────────────────────────────────────
def generate_post(client, article, topic_key):
    topic_label = cfg.TOPIC_LABELS[topic_key]
    angle = random.choice(cfg.WRITING_ANGLES)

    prompt = f"""너는 '{topic_label}' 정보를 소개하는 블로그의 에디터야.
아래 뉴스 기사를 참고해서, 독자(관심 있는 일반인)가 참고하기 좋은
블로그 글을 한국어로 새로 작성해줘. 기사를 그대로 베끼지 말고
핵심 정보를 재구성해서 써줘.

[작성 각도] {angle}

[참고 기사]
제목: {article['title']}
출처: {article.get('source', '')}
원문 링크: {article['link']}

[요구사항]
- 제목은 40자 이내, 클릭하고 싶게 자연스럽게
- 본문은 HTML로 작성 (h2, p, ul/li 태그 활용, 800~1200자 분량)
- 신청 자격, 접수 기간, 주최/문의처 등 실제 정보로 확인되는 것만 쓰고
  불확실한 정보(구체적 날짜·금액 등)는 추측해서 지어내지 말고
  "자세한 내용은 공식 공고를 확인하세요" 식으로 안내
- 마지막에 원문 출처를 문장으로 자연스럽게 언급 (링크는 넣지 않아도 됨)
- 과장된 광고 문구, 선정적 표현 금지

아래 형식으로만 응답해 (다른 설명 붙이지 말 것):
TITLE: <제목>
BODY:
<HTML 본문>
"""

    resp = client.models.generate_content(model=cfg.GEMINI_MODEL, contents=prompt)
    text = resp.text or ""

    title, body = None, None
    if "TITLE:" in text and "BODY:" in text:
        title = text.split("TITLE:", 1)[1].split("BODY:", 1)[0].strip()
        body = text.split("BODY:", 1)[1].strip()
    else:
        # 형식이 어긋났을 경우 폴백
        title = article["title"]
        body = f"<p>{text.strip()}</p>"

    return title, body


def build_html(body_html: str, image_url: str, source_link: str):
    parts = []
    if image_url:
        parts.append(
            f'<img src="{image_url}" referrerpolicy="no-referrer" '
            f'style="max-width:100%;height:auto;" alt="관련 이미지"/>'
        )
    parts.append(body_html)
    parts.append(
        f'<p style="color:#888;font-size:0.85em;">참고: '
        f'<a href="{source_link}" target="_blank" rel="noopener nofollow">원문 기사 보기</a></p>'
    )
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────
def main():
    secrets = config_secrets.load()

    print("[1/4] Blogger 인증 중...")
    service = get_blogger_service(secrets)
    blog_id = secrets["BLOG_ID"]

    print("[2/4] 최근 게시글 목록 조회 (중복 방지용)...")
    existing_titles = get_recent_post_titles(service, blog_id)
    print(f"  -> 최근 게시글 {len(existing_titles)}건 확인")

    print("[3/4] 뉴스 수집 중 (공모전/축제)...")
    candidates = collect_candidates()
    print(f"  -> 후보 기사 {len(candidates)}건 수집")

    gemini_client = genai.Client(api_key=secrets["GEMINI_API_KEY"])

    posted = 0
    posted_titles_this_run = []

    for article in candidates:
        if posted >= cfg.MAX_POSTS_PER_RUN:
            break

        if is_duplicate(article["title"], existing_titles + posted_titles_this_run):
            continue

        print(f"[4/4] 작성 중: {article['title']}")
        try:
            real_url = resolve_real_url(article["link"])

            title, body = generate_post(gemini_client, article, article["topic_key"])

            if is_duplicate(title, existing_titles + posted_titles_this_run):
                print("  -> 재작성된 제목이 기존 글과 유사하여 건너뜀")
                continue

            image_url = extract_og_image(real_url)
            html_content = build_html(body, image_url, real_url)
            labels = cfg.labels_for(article["topic_key"])

            result = publish_post(service, blog_id, title, html_content, labels)
            print(f"  -> 게시 완료: {result.get('url')}")

            posted += 1
            posted_titles_this_run.append(title)

            if posted < cfg.MAX_POSTS_PER_RUN:
                wait = random.randint(cfg.MIN_SLEEP_BETWEEN_POSTS, cfg.MAX_SLEEP_BETWEEN_POSTS)
                print(f"  -> {wait}초 대기 후 다음 글 처리")
                time.sleep(wait)

        except Exception as e:
            print(f"  -> 실패, 다음 기사로 넘어감: {e}")
            if "429" in str(e) or "rateLimitExceeded" in str(e):
                time.sleep(60)   # rate limit이면 더 길게 대기
            else:
                time.sleep(15)
            continue

    print(f"\n완료: 이번 실행에서 {posted}건 게시함")


if __name__ == "__main__":
    main()
