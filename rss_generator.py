import os
import re
import json
import hashlib
import ssl
import urllib3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from feedgen.feed import FeedGenerator
from bs4 import BeautifulSoup
import requests

# SSL警告を無効化（自己署名証明書対応）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 設定
BASE_URL = "https://www.amt-law.com"
INSIGHTS_URL = "https://www.amt-law.com/insights/"
OUTPUT_DIR = Path(".")
OUTPUT_FILE = OUTPUT_DIR / "feed.xml"
CACHE_FILE = OUTPUT_DIR / "cache.json"
TIMEZONE_OFFSET = 9  # JST (UTC+9)


def get_page_content(url):
    """ページのHTMLを取得する（SSL検証をスキップ）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=30, verify=False)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_date(date_text):
    """日付文字列をパースし、タイムゾーン付きのdatetimeを返す"""
    if not date_text:
        return None
    
    date_text = date_text.strip()
    result_date = None
    
    # "2026.09.04" 形式
    if "." in date_text:
        parts = date_text.split(".")
        if len(parts) == 3:
            try:
                year, month, day = parts
                result_date = datetime(int(year), int(month), int(day))
            except ValueError:
                pass
    
    # "2026年9月4日" 形式
    if result_date is None:
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_text)
        if match:
            try:
                year, month, day = match.groups()
                result_date = datetime(int(year), int(month), int(day))
            except ValueError:
                pass
    
    # "2026-09-04" 形式
    if result_date is None:
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_text)
        if match:
            try:
                year, month, day = match.groups()
                result_date = datetime(int(year), int(month), int(day))
            except ValueError:
                pass
    
    # "2026/09/04" 形式
    if result_date is None:
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})", date_text)
        if match:
            try:
                year, month, day = match.groups()
                result_date = datetime(int(year), int(month), int(day))
            except ValueError:
                pass
    
    # タイムゾーン情報を追加（JST）
    if result_date is not None:
        result_date = result_date.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSET)))
    
    return result_date


def parse_article_card(card):
    """記事カード要素から情報を抽出する"""
    result = {
        "title": "",
        "link": "",
        "date": None,
        "description": "",
        "tags": []
    }
    
    # タイトル
    title_elem = card.find("p", class_="article_title")
    if title_elem:
        result["title"] = title_elem.get_text(strip=True)
    
    # リンク（aタグから直接取得）
    if card.name == "a" and card.get("href"):
        href = card.get("href")
    else:
        link_elem = card.find("a", href=True)
        href = link_elem.get("href") if link_elem else None
    
    if href:
        if href.startswith("/"):
            href = BASE_URL + href
        result["link"] = href
    
    # 日付
    date_elem = card.find("div", class_="card_date")
    if date_elem:
        date_text = date_elem.get_text(strip=True)
        result["date"] = parse_date(date_text)
    
    # 説明
    desc_elem = card.find("p", class_="article_description")
    if desc_elem:
        result["description"] = desc_elem.get_text(strip=True)
    
    # カテゴリ（タグ）
    tag_elems = card.find_all("div", class_="newsTag")
    for tag in tag_elems:
        tag_text = tag.get_text(strip=True)
        if tag_text:
            result["tags"].append(tag_text)
    
    return result


def extract_articles_from_section(section):
    """セクションから記事を抽出する"""
    articles = []
    
    # .section_body の中の .article_card を探す
    body = section.find("div", class_="section_body")
    if not body:
        return articles
    
    # a.article_card または div.article_card を検索
    cards = body.find_all(["a", "div"], class_="article_card")
    for card in cards:
        data = parse_article_card(card)
        if data.get("title") and data.get("link"):
            articles.append(data)
    
    return articles


def extract_articles(html):
    """HTMLからすべての記事を抽出する"""
    soup = BeautifulSoup(html, "html.parser")
    all_articles = []
    seen_links = set()
    
    # 各セクションを探索
    sections = soup.find_all("div", class_="section")
    
    for section in sections:
        articles = extract_articles_from_section(section)
        for article in articles:
            link = article.get("link")
            if link and link not in seen_links:
                seen_links.add(link)
                all_articles.append(article)
    
    # 日付でソート（新しい順）
    all_articles.sort(key=lambda x: x.get("date") or datetime.min.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSET))), reverse=True)
    
    return all_articles


def load_cache():
    """キャッシュを読み込む"""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {"items": []}
    return {"items": []}


def save_cache(cache_data):
    """キャッシュを保存する"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def get_item_id(article):
    """記事の一意なIDを生成する"""
    text = f"{article.get('title', '')}|{article.get('link', '')}"
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def generate_feed(articles):
    """RSSフィードを生成する"""
    fg = FeedGenerator()
    fg.title("アンダーソン・毛利・友常法律事務所 - インサイト")
    fg.link(href=INSIGHTS_URL, rel="alternate")
    fg.description("当事務所の所属弁護士等が執筆した著書・論文・ニュースレターやセミナー・講演情報のご案内です。")
    fg.language("ja")
    
    # 現在時刻（JST、タイムゾーン付き）
    now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))
    fg.pubDate(now)
    fg.lastBuildDate(now)
    
    # キャッシュを読み込む
    cache = load_cache()
    cached_ids = set(item.get("id", "") for item in cache.get("items", []))
    
    new_count = 0
    updated_count = 0
    
    for article in articles:
        item_id = get_item_id(article)
        
        # エントリを作成
        fe = fg.add_entry()
        
        title = article.get("title", "（タイトルなし）")
        fe.title(title)
        
        link = article.get("link", "")
        if link:
            fe.link(href=link)
            fe.guid(link, permalink=True)
        
        # 説明
        description = article.get("description", "")
        tags = article.get("tags", [])
        if tags:
            tag_str = " ".join([f"#{tag}" for tag in tags])
            description = f"{description}\n\nタグ: {tag_str}"
        fe.description(description)
        
        # 日付（タイムゾーン付き）
        date_obj = article.get("date")
        if date_obj:
            # すでにタイムゾーン付きなのでそのまま使用
            fe.pubDate(date_obj)
        else:
            # 日付がない場合は現在時刻を使用
            fe.pubDate(now)
        
        # 新着チェック
        if item_id not in cached_ids:
            new_count += 1
    
    # キャッシュを更新
    current_ids = [{"id": get_item_id(a)} for a in articles]
    cache["items"] = current_ids
    save_cache(cache)
    
    return fg, new_count


def save_feed(fg):
    """フィードを保存する"""
    rss_str = fg.rss_str(pretty=True)
    with open(OUTPUT_FILE, "wb") as f:
        f.write(rss_str)
    print(f"[ステッカー] RSSフィードを保存しました: {OUTPUT_FILE}")
    if OUTPUT_FILE.exists():
        print(f"[ステッカー] ファイルサイズ: {OUTPUT_FILE.stat().st_size} bytes")


def main():
    print("[ステッカー] インサイトページを取得中...")
    print(f"[ステッカー] URL: {INSIGHTS_URL}")
    
    try:
        html = get_page_content(INSIGHTS_URL)
        print("[ステッカー] ページの取得に成功しました")
    except Exception as e:
        print(f"[ステッカー] ページの取得に失敗しました: {e}")
        return
    
    print("[ステッカー] 記事を抽出中...")
    articles = extract_articles(html)
    print(f"[ステッカー] {len(articles)}件の記事を検出しました")
    
    if articles:
        # 最新の記事情報を表示
        latest = articles[0]
        title = latest.get('title', '')[:50]
        print(f"[ステッカー] 最新記事: {title}...")
        date_obj = latest.get('date')
        if date_obj:
            print(f"[ステッカー] 日付: {date_obj.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print("[ステッカー] 日付: 不明")
    
    print("[ステッカー] RSSフィードを生成中...")
    fg, new_count = generate_feed(articles)
    
    save_feed(fg)
    
    if new_count > 0:
        print(f"[ステッカー] {new_count}件の新着記事があります！")
    else:
        print("[ステッカー] 新着記事はありませんでした")
    
    print("[ステッカー] 完了！")


if __name__ == "__main__":
    main()
