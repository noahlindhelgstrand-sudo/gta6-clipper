"""Kör LOKALT en gång för att få en YouTube refresh token.

    pip install google-auth-oauthlib
    python scripts/get_youtube_refresh_token.py path/till/client_secret.json

En webbläsare öppnas – logga in med kontot som äger YouTube-kanalen.
Kopiera sedan värdena som skrivs ut till GitHub Secrets. Spara aldrig dem i repot.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Användning: python scripts/get_youtube_refresh_token.py client_secret.json")
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    info = json.load(open(sys.argv[1]))["installed"]
    print("\nLägg in dessa som GitHub Secrets:\n")
    print(f"YOUTUBE_CLIENT_ID={info['client_id']}")
    print(f"YOUTUBE_CLIENT_SECRET={info['client_secret']}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
