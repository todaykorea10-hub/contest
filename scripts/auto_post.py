name: 공모전/축제 블로그 자동 포스팅

on:
  schedule:
    # UTC 기준. 아래는 한국시간(KST=UTC+9)으로 하루 15회, 약 1시간 36분 간격
    - cron: "0 1 * * *"    # KST 10:00
    - cron: "0 2 * * *"    # KST 11:00
    - cron: "0 4 * * *"    # KST 13:00
    - cron: "0 5 * * *"    # KST 14:00
    - cron: "0 7 * * *"    # KST 16:00
    - cron: "0 9 * * *"    # KST 18:00
    - cron: "0 10 * * *"   # KST 19:00
    - cron: "0 12 * * *"   # KST 21:00
    - cron: "0 13 * * *"   # KST 22:00
    - cron: "0 15 * * *"   # KST 00:00 (다음날)
    - cron: "0 17 * * *"   # KST 02:00
    - cron: "0 18 * * *"   # KST 03:00
    - cron: "0 20 * * *"   # KST 05:00
    - cron: "0 21 * * *"   # KST 06:00
    - cron: "0 23 * * *"   # KST 08:00
  workflow_dispatch: {}   # 수동 실행 버튼 (Actions 탭에서 필요할 때 바로 실행 가능)

jobs:
  post:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: 코드 체크아웃
        uses: actions/checkout@v4

      - name: 파이썬 설정
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: 자동 포스팅 실행
        env:
          PYTHONUNBUFFERED: "1"
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          BLOG_ID: ${{ secrets.BLOG_ID }}
          GOOGLE_CLIENT_ID: ${{ secrets.GOOGLE_CLIENT_ID }}
          GOOGLE_CLIENT_SECRET: ${{ secrets.GOOGLE_CLIENT_SECRET }}
          GOOGLE_REFRESH_TOKEN: ${{ secrets.GOOGLE_REFRESH_TOKEN }}
        run: python scripts/auto_post.py
