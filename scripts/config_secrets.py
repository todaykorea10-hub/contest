# -*- coding: utf-8 -*-
"""
민감정보는 전부 환경변수에서 읽는다.
로컬 테스트 시에는 .env 파일을 만들고 (git에는 올리지 않음)
`python-dotenv`로 로드하거나, 쉘에서 export 해서 사용한다.

GitHub Actions에서는 리포지토리 Settings > Secrets and variables > Actions
에 아래 이름 그대로 등록하면 workflow(.github/workflows/auto_post.yml)가
자동으로 주입한다.
"""
import os
import sys

REQUIRED_KEYS = [
    "GEMINI_API_KEY",
    "BLOG_ID",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
]


def _get(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"[설정 오류] 환경변수 {name} 가(이) 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)
    return val


def load():
    for k in REQUIRED_KEYS:
        _get(k)  # 존재 확인 (없으면 여기서 바로 종료)
    return {
        "GEMINI_API_KEY": os.environ["GEMINI_API_KEY"],
        "BLOG_ID": os.environ["BLOG_ID"],
        "GOOGLE_CLIENT_ID": os.environ["GOOGLE_CLIENT_ID"],
        "GOOGLE_CLIENT_SECRET": os.environ["GOOGLE_CLIENT_SECRET"],
        "GOOGLE_REFRESH_TOKEN": os.environ["GOOGLE_REFRESH_TOKEN"],
    }
