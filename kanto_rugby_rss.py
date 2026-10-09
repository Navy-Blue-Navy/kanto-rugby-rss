import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree
from email.utils import format_datetime
from datetime import datetime, timezone, timedelta
import hashlib
import os
import re
import time

BASE_URL = "https://www.rugby.or.jp"
LIST_URL = "https://www.rugby.or.jp/news/"
OUTPUT_FILE = "kanto_rugby.xml"

JST = timezone(timedelta(hours=9))

headers = {
    "User-Agent": "Mozilla/5.0"
}


# --------------------------------------------------
# ページ取得
# --------------------------------------------------

def get_page(url):
    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        r.raise_for_status()
        return r

    except requests.RequestException as e:
        print("取得失敗:", url)
        print("理由:", e)
        return None


# --------------------------------------------------
# 既存RSSを読み込む
# --------------------------------------------------

old_items = {}

if os.path.exists(OUTPUT_FILE):
    try:
        old_tree = ElementTree()
        old_tree.parse(OUTPUT_FILE)
        old_root = old_tree.getroot()

        for item in old_root.findall("./channel/item"):
            link = item.findtext("link", "").strip()
            title = item.findtext("title", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            if link:
                old_items[link] = {
                    "title": title,
                    "pubDate": pub_date
                }

    except Exception as e:
        print("既存XML読み込みエラー:", e)

print("既存RSS件数:", len(old_items))


# --------------------------------------------------
# ニュース一覧を取得
# --------------------------------------------------

r = get_page(LIST_URL)

if r is None:
    print()
    print("ニュース一覧を取得できなかったため、今回は更新しません。")
    print("既存RSSをそのまま維持します。")
    raise SystemExit(0)

print("一覧 HTTP:", r.status_code)

soup = BeautifulSoup(r.text, "html.parser")

articles = []
seen_urls = set()

for a in soup.find_all("a", href=True):

    link = urljoin(BASE_URL, a["href"])

    # 個別ニュース記事だけを対象にする
    if not re.fullmatch(
        r"https://www\.rugby\.or\.jp/news/article/\d+/",
        link
    ):
        continue

    # 同じ記事が複数回出てきた場合は1回だけ
    if link in seen_urls:
        continue

    text = " ".join(a.stripped_strings).strip()

    # 一覧ページから掲載日を取得
    date_match = re.search(
        r"\b(20\d{2})/(\d{1,2})/(\d{1,2})\b",
        text
    )

    if not date_match:
        print("掲載日取得失敗:", link)
        continue

    year = int(date_match.group(1))
    month = int(date_match.group(2))
    day = int(date_match.group(3))

    # 時刻は掲載されていないため正午として扱う
    published = datetime(
        year,
        month,
        day,
        12,
        0,
        0,
        tzinfo=JST
    )

    seen_urls.add(link)

    articles.append({
        "link": link,
        "published": published
    })


print("一覧から取得:", len(articles), "件")

# 0件の場合はサイト構造変更の可能性があるため
# 既存XMLを上書きしない
if len(articles) == 0:
    print()
    print("記事を1件も取得できませんでした。")
    print("サイト構造変更の可能性があるため、今回は更新しません。")
    print("既存RSSをそのまま維持します。")
    raise SystemExit(0)


# --------------------------------------------------
# 個別記事から正式タイトルを取得
# --------------------------------------------------

current_items = []

for i, article in enumerate(articles, 1):

    link = article["link"]

    r = get_page(link)

    if r is None:
        print()
        print(f"個別記事取得失敗: {link}")
        print("今回はRSSを更新しません。")
        print("既存RSSをそのまま維持します。")
        raise SystemExit(0)

    soup = BeautifulSoup(r.text, "html.parser")

    title = ""

    # OGタイトルを優先
    og = soup.find("meta", property="og:title")

    if og and og.get("content"):
        title = og["content"].strip()

    # タイトル末尾のサイト名を除去
    # 例：
    # 「記事タイトル：関東ラグビーフットボール協会」
    # 「記事タイトル｜関東ラグビーフットボール協会」
    title = re.sub(
        r"\s*(?:[|｜]|：)\s*関東ラグビーフットボール協会.*$",
        "",
        title
    ).strip()

    # OGタイトルが取れない場合はh1を使用
    if not title:
        h1 = soup.find("h1")

        if h1:
            title = " ".join(
                h1.stripped_strings
            ).strip()

    # タイトル取得失敗時は
    # 不完全なRSSで上書きしない
    if not title:
        print()
        print("タイトル取得失敗:", link)
        print("今回はRSSを更新しません。")
        print("既存RSSをそのまま維持します。")
        raise SystemExit(0)

    current_items.append({
        "title": title,
        "link": link,
        "pubDate": format_datetime(article["published"])
    })

    print(
        f"[{i}] "
        f"{article['published'].strftime('%Y/%m/%d')} "
        f"{title}"
    )

    # サイトへ短時間に大量アクセスしない
    time.sleep(0.2)


# --------------------------------------------------
# 全件正常に取得できたか確認
# --------------------------------------------------

if len(current_items) != len(articles):
    print()
    print("記事URL数とタイトル取得数が一致しません。")
    print("今回はRSSを更新しません。")
    print("既存RSSをそのまま維持します。")
    raise SystemExit(0)


# --------------------------------------------------
# 現在の記事＋過去RSSを統合
#
# 一覧から消えた過去記事もRSSには残す
# --------------------------------------------------

all_items = {}

# 過去RSSを入れる
for link, data in old_items.items():

    all_items[link] = {
        "title": data["title"],
        "link": link,
        "pubDate": data["pubDate"]
    }


# 現在の公式一覧を優先して上書き
for data in current_items:

    all_items[data["link"]] = data


# --------------------------------------------------
# pubDateをdatetimeに変換
# --------------------------------------------------

def parse_pubdate(value):
    try:
        return datetime.strptime(
            value,
            "%a, %d %b %Y %H:%M:%S %z"
        )

    except Exception:
        return datetime(
            1970,
            1,
            1,
            tzinfo=timezone.utc
        )


# --------------------------------------------------
# 新しい記事順に並べる
#
# 同じ掲載日の場合は記事番号が大きい方を上にする
# --------------------------------------------------

rss_items = list(all_items.values())

rss_items.sort(
    key=lambda x: (
        parse_pubdate(x["pubDate"]),
        int(
            re.search(
                r"/article/(\d+)/",
                x["link"]
            ).group(1)
        )
    ),
    reverse=True
)


# --------------------------------------------------
# RSS作成
# --------------------------------------------------

rss = Element("rss", version="2.0")

channel = SubElement(rss, "channel")

SubElement(
    channel,
    "title"
).text = "関東ラグビーフットボール協会 ニュース"

SubElement(
    channel,
    "link"
).text = LIST_URL

SubElement(
    channel,
    "description"
).text = "関東ラグビーフットボール協会の新着ニュース"

SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS item作成
# --------------------------------------------------

for data in rss_items:

    item = SubElement(channel, "item")

    SubElement(
        item,
        "title"
    ).text = data["title"]

    SubElement(
        item,
        "link"
    ).text = data["link"]

    guid = SubElement(
        item,
        "guid",
        isPermaLink="false"
    )

    # URLから固定GUIDを作成
    # 同じ記事をFeedlyが新着として重複認識するのを防ぐ
    guid.text = hashlib.sha256(
        data["link"].encode("utf-8")
    ).hexdigest()

    SubElement(
        item,
        "pubDate"
    ).text = data["pubDate"]


# --------------------------------------------------
# XML保存
#
# ここまで正常に取得できた場合だけ書き換える
# --------------------------------------------------

tree = ElementTree(rss)

tree.write(
    OUTPUT_FILE,
    encoding="utf-8",
    xml_declaration=True
)

print()
print("RSS総件数:", len(rss_items))
print("保存:", OUTPUT_FILE)
print("RSS更新完了")