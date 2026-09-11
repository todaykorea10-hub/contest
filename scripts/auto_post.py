# -*- coding: utf-8 -*-
"""
공모전·축제 정보 블로그 자동 포스팅 스크립트.

흐름:
  1) Google News RSS에서 '공모전'/'축제' 관련 최신 기사 수집
  2) Blogger에서 최근 게시글 제목을 가져와 중복(유사) 기사 제거
  3) 후보 기사 중 MAX_POSTS_PER_RUN개를 골라 Gemini로 블로그 글 재작성
     (본문 + 핵심 키워드(태그)까지 함께 생성)
  4) 원문 기사의 실제 URL을 googlenewsdecoder로 풀어낸 뒤 (AMP 페이지면
     일반 페이지 URL로 우선 변환), 헤더/광고/관련기사 영역을 제외한 본문에서
     이미지 후보들을 모으고, 실제로 다운로드해서 픽셀 크기를 비교한 뒤
     가장 큰 이미지 1장만 사용 (같은 사진의 다른 크기 버전이 여러 개
     섞여 있어도 결과적으로 1장만 남으므로 중복 표시 문제가 생기지 않음)
  5) Blogger API로 게시 (라벨 = 공통 라벨 + 주제 + 본문 키워드, 본문 하단에 해시태그),
     각 게시 사이에 무작위 대기 (스팸 방지)

GitHub Actions에서 스케줄 실행되며, 별도의 로컬 상태 파일 없이
Blogger에 이미 올라간 글 목록을 기준으로 중복을 판단하므로 무상태(stateless)로 동작한다.
"""
import os
import re
import sys
import time
import random
import difflib
import urllib.parse
import xml.etree.ElementTree as ET
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from PIL import Image
from googlenewsdecoder import new_decoderv1
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
    """검색 키워드가 여러 개라 같은 기사가 중복 수집될 수 있으므로,
    원문 링크(link) 기준으로 한 번만 후보에 넣는다."""
    candidates = []
    seen_links = set()

    for kw in cfg.CONTEST_KEYWORDS:
        for a in fetch_news(kw):
            if a["link"] in seen_links:
                continue
            seen_links.add(a["link"])
            a["topic_key"] = "contest"
            candidates.append(a)

    for kw in cfg.FESTIVAL_KEYWORDS:
        for a in fetch_news(kw):
            if a["link"] in seen_links:
                continue
            seen_links.add(a["link"])
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
# 원문 기사 URL 디코딩 + 대표 이미지(가장 큰 것 1장) 추출
# ────────────────────────────────────────────────────────────
def resolve_real_url(google_news_link: str) -> str:
    """Google News RSS 링크를 실제 언론사 기사 URL로 변환 (googlenewsdecoder 사용)"""
    try:
        result = new_decoderv1(google_news_link, interval=2)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
        print(f"[정보] 뉴스 링크 디코딩 실패(status=False): {result.get('message')}")
    except Exception as e:
        print(f"[정보] 뉴스 링크 디코딩 실패: {e}")
    return google_news_link  # 실패 시 원래 링크 그대로 사용


def _to_non_amp_url(url: str) -> str:
    """AMP 전용 페이지(articleViewAmp.html 등)는 메타태그가 부실한 경우가 많아
    가능하면 일반 페이지 URL로 바꿔서 먼저 시도한다."""
    return re.sub(r"(?i)ArticleViewAmp\.html", "ArticleView.html", url)


# 구글/광고 네트워크 도메인
BLOCKED_IMAGE_DOMAINS = (
    "google.com", "gstatic.com", "googleusercontent.com",
    "doubleclick.net", "googlesyndication.com", "adservice.google",
    "facebook.com", "fbcdn.net",
)
# 언론사 로고/워터마크로 추정되는 힌트 (파일명·alt 속성에 이 단어가 있으면 제외)
LOGO_HINT_WORDS = ("logo", "로고", "symbol", "ci_", "_ci.", "watermark", "masthead")

# 길고 특이해서 부분 문자열로 걸러도 안전한 힌트 (오탐 위험이 낮음)
LONG_SUBSTRING_HINTS = (
    "header", "footer", "sidebar", "topbar", "navbar",
    "banner", "relate", "recommend", "outbrain", "taboola",
    "widget", "promotion", "sponsor", "reporter", "byline",
    "copyright", "comment", "popular", "ranking",
)
# 짧고 흔해서 부분 문자열로 걸면 header/read/already 등을 오탐하는 힌트.
# '_' '-' 로 나눈 토큰이 정확히 일치할 때만 걸러낸다.
SHORT_EXACT_HINTS = {"ad", "ads", "nav", "sns", "gnb", "lnb", "share"}

MIN_IMAGE_PIXELS = 200          # 가로/세로 중 작은 쪽이 이보다 작으면 아이콘류로 간주
MAX_IMAGE_CANDIDATES = 8        # 실제 다운로드해서 크기를 확인할 후보 수 상한 (과도한 요청 방지)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 이미지 하나당 최대 5MB까지만 내려받아 크기 확인


def _is_non_content_tag(tag) -> bool:
    """클래스/id를 보고 헤더·광고·관련기사 등 본문이 아닌 영역인지 정밀하게 판단.
    'ad'처럼 짧은 단어는 토큰이 정확히 일치할 때만, 길고 특이한 단어만 부분 문자열로 검사한다."""
    raw_values = list(tag.get("class") or [])
    if tag.get("id"):
        raw_values.append(tag["id"])

    for raw in raw_values:
        low = raw.lower()
        if any(h in low for h in LONG_SUBSTRING_HINTS):
            return True
        tokens = re.split(r"[_\-]+", low)
        if any(t in SHORT_EXACT_HINTS for t in tokens):
            return True
    return False


def _remove_non_content_elements(scope):
    """헤더/내비/푸터 태그 + 광고·관련기사·공유 위젯 등을 통째로 제거"""
    for tag in scope.find_all(["header", "nav", "footer", "aside"]):
        tag.decompose()
    for tag in scope.find_all(_is_non_content_tag):
        tag.decompose()


def _fetch_html(url: str, timeout: int = 12):
    return requests.get(
        url,
        headers={"User-Agent": UA, "Referer": "https://news.google.com/"},
        timeout=timeout,
        allow_redirects=True,
    )


def _collect_candidate_image_urls(article_url: str):
    """og:image/twitter:image + 본문(<img>, <amp-img>) 영역에서 이미지 후보 URL을 모은다.
    (아직 크기 비교는 하지 않음 - 실제 다운로드는 별도 단계에서 수행)"""
    non_amp_url = _to_non_amp_url(article_url)
    urls_to_try = [non_amp_url]
    if non_amp_url != article_url:
        urls_to_try.append(article_url)

    for try_url in urls_to_try:
        try:
            resp = _fetch_html(try_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            images = []
            for tag_name, attrs in (
                ("meta", {"property": "og:image"}),
                ("meta", {"name": "og:image"}),
                ("meta", {"name": "twitter:image"}),
            ):
                meta = soup.find(tag_name, attrs=attrs)
                if meta and meta.get("content"):
                    url_ = urllib.parse.urljoin(try_url, meta["content"])
                    if not any(w in url_.lower() for w in LOGO_HINT_WORDS):
                        images.append(url_)

            _remove_non_content_elements(soup)

            container = (
                soup.find("article")
                or soup.find(attrs={"itemprop": "articleBody"})
                or soup.find(class_=lambda c: c and (
                    "article" in c or "art_view" in c or "news_view" in c
                    or "articleView" in c or "art_photo" in c or "photo_view" in c
                ))
                or soup
            )

            for img in container.find_all(["img", "amp-img"]):
                src = img.get("src") or img.get("data-src") or img.get("data-original") or img.get("srcset")
                if not src:
                    continue
                src = src.split(",")[0].strip().split(" ")[0]  # srcset 첫 항목만 사용
                src = urllib.parse.urljoin(try_url, src)
                alt = (img.get("alt") or "").lower()

                if any(w in src.lower() for w in LOGO_HINT_WORDS) or any(w in alt for w in LOGO_HINT_WORDS):
                    continue
                images.append(src)

            # 이 URL에서 뭔가 찾았으면 여기서 종료, 없으면 다음 후보 URL(AMP 등)로 재시도
            if images:
                # 도메인 차단 + data URI 제외 + 문자열 그대로 중복 제거 (다운로드 낭비 방지)
                seen = set()
                cleaned = []
                for src in images:
                    if src.startswith("data:"):
                        continue
                    if any(d in src for d in BLOCKED_IMAGE_DOMAINS):
                        continue
                    if src in seen:
                        continue
                    seen.add(src)
                    cleaned.append(src)
                if cleaned:
                    return cleaned[:MAX_IMAGE_CANDIDATES]

        except Exception as e:
            print(f"  [이미지] 후보 수집 실패 ({try_url}): {e}")
            continue

    return []


def _get_image_pixel_size(url: str):
    """이미지를 실제로 내려받아 (width, height) 픽셀 크기를 확인. 실패 시 None."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": UA, "Referer": "https://news.google.com/"},
            timeout=10,
            stream=True,
        )
        resp.raise_for_status()
        data = bytearray()
        for chunk in resp.iter_content(16384):
            data += chunk
            if len(data) > MAX_DOWNLOAD_BYTES:
                break
        img = Image.open(BytesIO(bytes(data)))
        return img.size  # (width, height)
    except Exception as e:
        print(f"  [이미지] 크기 확인 실패 ({url}): {e}")
        return None


def extract_article_images(article_url: str):
    """후보 이미지들을 모아 실제로 다운로드해 픽셀 크기를 비교한 뒤,
    가장 큰 이미지 1장만 반환한다 (없으면 빈 리스트)."""
    if "news.google.com" in article_url:
        print("  [이미지] 원문 URL 디코딩이 안 풀려서 이미지 추출을 건너뜀")
        return []

    candidates = _collect_candidate_image_urls(article_url)
    print(f"  [이미지-디버그] 크기 확인할 후보 {len(candidates)}개: {candidates}")

    if not candidates:
        return []

    best_url = None
    best_area = 0
    for url in candidates:
        size = _get_image_pixel_size(url)
        if not size:
            continue
        w, h = size
        print(f"  [이미지-디버그] {url} -> {w}x{h}")
        if min(w, h) < MIN_IMAGE_PIXELS:
            continue  # 아이콘/로고류로 간주하고 제외
        area = w * h
        if area > best_area:
            best_area = area
            best_url = url

    if not best_url:
        print("  [이미지] 크기 확인 가능한 이미지가 없어 이미지 없이 게시")
        return []

    print(f"  [이미지] 채택(최대 크기): {best_url}")
    return [best_url]


# ────────────────────────────────────────────────────────────
# Gemini로 블로그 글 생성 (본문 + 태그)
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
TAGS: <본문 핵심 키워드 8~10개, 쉼표로 구분, 공백 없이 한 단어씩. 지역명/주최기관/분야명 등 실제 검색에 쓸만한 단어 위주>
"""

    resp = client.models.generate_content(model=cfg.GEMINI_MODEL, contents=prompt)
    text = resp.text or ""

    title, body, tags = None, None, []
    if "TITLE:" in text and "BODY:" in text:
        title = text.split("TITLE:", 1)[1].split("BODY:", 1)[0].strip()
        rest = text.split("BODY:", 1)[1]
        if "TAGS:" in rest:
            body_part, tags_part = rest.split("TAGS:", 1)
            body = body_part.strip()
            tags = [t.strip() for t in tags_part.strip().split(",") if t.strip()]
        else:
            body = rest.strip()
    else:
        # 형식이 어긋났을 경우 폴백
        title = article["title"]
        body = f"<p>{text.strip()}</p>"

    return title, body, tags


def build_html(body_html: str, image_urls, source_link: str, tags):
    """image_urls는 이제 0개 또는 1개(가장 큰 이미지)만 들어온다."""
    parts = []
    if image_urls:
        parts.append(
            f'<img src="{image_urls[0]}" referrerpolicy="no-referrer" '
            f'style="width:100%;height:auto;margin-bottom:8px;" alt="관련 이미지"/>'
        )
    parts.append(body_html)

    if tags:
        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:10])
        parts.append(f'<p style="color:#555;">{hashtags}</p>')

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
    print(f"  -> 후보 기사 {len(candidates)}건 수집 (링크 기준 중복 제거됨)")

    gemini_client = genai.Client(api_key=secrets["GEMINI_API_KEY"])

    posted = 0
    attempts = 0
    posted_titles_this_run = []
    seen_links_this_run = set()

    for article in candidates:
        if posted >= cfg.MAX_POSTS_PER_RUN:
            break
        if attempts >= cfg.MAX_ATTEMPTS_PER_RUN:
            print("  -> 최대 시도 횟수에 도달해 이번 실행을 종료합니다")
            break

        # 이번 실행 내에서 같은 원문 링크가 또 나오면 건너뜀 (이중 안전장치)
        if article["link"] in seen_links_this_run:
            continue
        seen_links_this_run.add(article["link"])

        if is_duplicate(article["title"], existing_titles + posted_titles_this_run):
            continue

        attempts += 1
        print(f"[4/4] 작성 중: {article['title']}")
        try:
            real_url = resolve_real_url(article["link"])
            print(f"  -> 원문 URL: {real_url}")

            title, body, tags = generate_post(gemini_client, article, article["topic_key"])

            if is_duplicate(title, existing_titles + posted_titles_this_run):
                print("  -> 재작성된 제목이 기존 글과 유사하여 건너뜀")
                continue

            image_urls = extract_article_images(real_url)
            print(f"  -> 이미지 {len(image_urls)}장 사용")
            html_content = build_html(body, image_urls, real_url, tags)
            labels = cfg.labels_for(article["topic_key"], tags)

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
