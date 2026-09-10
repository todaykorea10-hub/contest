# -*- coding: utf-8 -*-
"""
최초 1회, 로컬 PC에서만 실행하는 스크립트.

목적: Blogger API에 접근할 수 있는 refresh_token을 발급받는다.
발급받은 refresh_token은 GitHub Secrets(GOOGLE_REFRESH_TOKEN)에 등록하고,
이 스크립트와 client_secret.json은 이후 로컬에만 보관하고 git에는 올리지 않는다.

사전 준비:
1. Google Cloud Console에서 프로젝트 생성 (기존 kpop/baldal 프로젝트 재사용 가능)
2. "Blogger API v3" 활성화
3. OAuth 동의 화면 설정 (테스트 사용자에 본인 계정 추가)
4. OAuth 클라이언트 ID 생성 (유형: "데스크톱 앱")
5. 다운로드한 JSON을 이 스크립트와 같은 폴더에 client_secret.json 이름으로 저장

실행: python oauth_helper.py
브라우저가 열리면 baldal2026(기존 발달장애인 뉴스) 블로그를 소유한
구글 계정으로 로그인 후 권한을 허용한다.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/blogger"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n===== 아래 값을 GitHub Secrets에 등록하세요 =====")
    print(f"GOOGLE_CLIENT_ID     = {creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET = {creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
    print("==================================================")
    print("\n※ BLOG_ID는 Blogger 관리 화면 > 설정 > 블로그 ID 에서 확인 (baldal2026 블로그의 ID)")

if __name__ == "__main__":
    main()
