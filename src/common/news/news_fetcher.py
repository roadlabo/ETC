import json
from pathlib import Path
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_URL = "https://etc.roadlabo.com/wp-json/wp/v2/posts?categories=16"

APP_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATE_PATH = APP_BASE_DIR / "userdata" / "news" / "news_state.json"


def load_state():
    if not STATE_PATH.exists():
        return {"seen_keys": []}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return {"seen_keys": []}

        if "seen_keys" not in state or not isinstance(state["seen_keys"], list):
            state["seen_keys"] = []

        return state

    except Exception:
        return {"seen_keys": []}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def make_seen_key(news_item):
    return f'{news_item["id"]}:{news_item["modified"]}'

# ニュース取得は公開情報（バージョン情報・ブログ案内）のみを対象とする。
# 一部職場PCでは社内SSL検査により証明書エラーが発生するため、
# 運用性を優先して verify=False を使用する。
# 認証情報・個人情報を扱う通信にはこの方式を使用しないこと。
def fetch_news():
    print("[news] SSL verification disabled (public info only)")

    try:
        response = requests.get(
            API_URL,
            timeout=15,
            verify=False,
            headers={"User-Agent": "ETCAnalyzer-NewsFetcher/1.0"},
        )
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            print("[news] API response is not a list")
            return []

        news_list = []

        for post in data:
            news = {
                "id": post["id"],
                "title": post["title"]["rendered"],
                "link": post["link"],
                "modified": post["modified"],
            }
            news["seen_key"] = make_seen_key(news)
            news_list.append(news)

        return news_list

    except requests.exceptions.RequestException as e:
        print(f"[news] request error: {e}")
        return []

    except Exception as e:
        print(f"[news] unexpected error: {e}")
        return []


def get_unseen_news():
    state = load_state()
    seen_keys = set(state.get("seen_keys", []))
    all_news = fetch_news()
    unseen = [n for n in all_news if n["seen_key"] not in seen_keys]
    return unseen


def mark_as_seen(news_item):
    state = load_state()
    seen_keys = set(state.get("seen_keys", []))
    seen_keys.add(news_item["seen_key"])
    state["seen_keys"] = sorted(seen_keys)
    save_state(state)


def mark_all_unseen_as_seen():
    unseen_news = get_unseen_news()
    for news_item in unseen_news:
        mark_as_seen(news_item)


def reset_seen_state():
    save_state({"seen_keys": []})


if __name__ == "__main__":
    print("news_fetcher.py 開始")
    print(f"既読管理ファイル: {STATE_PATH}")
    print("-" * 60)

    unseen_news = get_unseen_news()

    print(f"未読件数: {len(unseen_news)}")
    for n in unseen_news:
        print("タイトル:", n["title"])
        print("URL:", n["link"])
        print("更新日時:", n["modified"])
        print("既読キー:", n["seen_key"])
        print("------")
