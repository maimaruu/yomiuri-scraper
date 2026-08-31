# Scraper v2: history + current top-5 snapshot
import time
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from oauth2client.service_account import ServiceAccountCredentials
import gspread

# --- Google Sheets設定 ---
SERVICE_ACCOUNT_FILE = "credentials.json"
SHEET_NAME = "yomiuri-politics-scraper"
HISTORY_SHEET_NAME = "シート1"
LATEST_SHEET_NAME = "latest"

LATEST_COUNT = 5
HISTORY_ROW_BUFFER = 1000
MAX_ARTICLE_PAGES = 10

# 本文ではなく会員案内だけが取得された記事は採用しない。
REJECT_BODY_MARKERS = [
    "読売新聞をご購読の方が、お名前やメールアドレスなどの必要項目を入力すると",
    "会員ステータスが「読者会員（申請中）」から「読者会員」に変わります",
    "読者会員の同居のご家族が対象です",
    "家族の登録は３人まで",
]

HISTORY_HEADERS = [
    "ID",
    "collected_at",
    "title",
    "source",
    "published_at",
    "url",
    "category",
    "body",
]

LATEST_HEADERS = [
    "rank",
    "ID",
    "collected_at",
    "title",
    "source",
    "published_at",
    "url",
    "category",
    "body",
]


def open_spreadsheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        SERVICE_ACCOUNT_FILE, scope
    )
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME)


def get_or_create_worksheet(spreadsheet, title, rows=100, cols=10):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        print(f"[INFO] Creating worksheet: {title}")
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_history_capacity(sheet, rows_to_append):
    """履歴シートの末尾が近ければ、自動で行を追加する。"""
    used_rows = len(sheet.get_all_values())
    required_rows = used_rows + max(rows_to_append, 0)
    target_rows = required_rows + HISTORY_ROW_BUFFER

    if sheet.row_count < target_rows:
        add_count = target_rows - sheet.row_count
        sheet.add_rows(add_count)
        print(
            f"[INFO] Expanded history sheet by {add_count} rows "
            f"(row_count={sheet.row_count})."
        )


# --- Selenium設定 ---
def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Selenium ManagerにChromeDriverの解決を任せる。
    return webdriver.Chrome(options=chrome_options)


def normalize_url(url):
    """#fragment を除いた比較用URL。"""
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def click_expand_controls(driver):
    """
    記事内の「続きを読む」「詳しくはこちら」のうち、
    クリックで本文が展開されるものを可能な範囲で開く。
    """
    xpaths = [
        "//button[contains(normalize-space(.), '続きを読む')]",
        "//a[contains(normalize-space(.), '続きを読む')]",
        "//*[contains(@class, 'readmore-btn')]",
        "//button[contains(normalize-space(.), '詳しくはこちら')]",
        "//a[contains(normalize-space(.), '詳しくはこちら')]",
    ]

    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            for element in elements:
                try:
                    if not element.is_displayed():
                        continue

                    old_url = driver.current_url
                    href = element.get_attribute("href")

                    # 別記事・会員登録ページへのリンクは本文展開として扱わない。
                    if href:
                        href = urljoin(old_url, href)
                        old_path = urlparse(old_url).path.rstrip("/")
                        new_path = urlparse(href).path.rstrip("/")
                        same_article = (
                            new_path == old_path
                            or new_path.startswith(old_path + "/")
                            or old_path.startswith(new_path + "/")
                        )
                        if not same_article:
                            continue

                    driver.execute_script("arguments[0].click();", element)
                    time.sleep(1.5)

                    if normalize_url(driver.current_url) != normalize_url(old_url):
                        print(
                            "[DEBUG] Followed article continuation: "
                            f"{driver.current_url}"
                        )
                    else:
                        print("[DEBUG] Expanded article content.")
                except Exception:
                    continue
        except Exception:
            continue


def extract_page_text(driver):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    article_div = soup.find(
        "div",
        class_=re.compile(r"p-main-contents|article-body|uni-news-article"),
    )

    if not article_div:
        # DOM変更時の保険として article 要素も見る。
        article_div = soup.find("article")

    if not article_div:
        return ""

    paragraphs = [
        p.get_text(" ", strip=True)
        for p in article_div.find_all("p")
        if p.get_text(" ", strip=True)
    ]
    return "\n".join(paragraphs)


def find_next_article_page_url(driver, current_page, visited_urls):
    """
    「次へ」だけでなく、1 2 3 4 5 型のページャーにも対応する。
    数字リンクは現在ページより大きい最小の番号を優先する。
    """
    # まず明示的な「次へ」を探す。
    selectors = [
        "a.p-pager__next",
        "a.next",
        "a[rel='next']",
        ".p-pager a",
        ".pagination a",
        ".pager a",
        "nav a",
    ]

    explicit_candidates = []
    numeric_candidates = []

    for selector in selectors:
        try:
            for a in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if not a.is_displayed():
                        continue
                    href = a.get_attribute("href")
                    if not href:
                        continue
                    href = normalize_url(urljoin(driver.current_url, href))
                    if href in visited_urls:
                        continue

                    text = (a.text or "").strip()
                    rel = (a.get_attribute("rel") or "").lower()
                    classes = (a.get_attribute("class") or "").lower()

                    if (
                        "next" in rel
                        or "next" in classes
                        or "次へ" in text
                        or text in {"次", ">", "›", "→"}
                    ):
                        explicit_candidates.append(href)
                        continue

                    if re.fullmatch(r"\d+", text):
                        page_number = int(text)
                        if page_number > current_page:
                            numeric_candidates.append((page_number, href))
                except Exception:
                    continue
        except Exception:
            continue

    if explicit_candidates:
        return explicit_candidates[0]

    if numeric_candidates:
        numeric_candidates.sort(key=lambda x: x[0])
        return numeric_candidates[0][1]

    return None


# --- 本文抽出関数（続きを読む・詳しくはこちら・複数ページ対応）---
def extract_body_content(driver):
    full_pages = []
    visited_urls = set()
    current_page = 1

    while current_page <= MAX_ARTICLE_PAGES:
        current_url = normalize_url(driver.current_url)
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        click_expand_controls(driver)

        page_text = extract_page_text(driver)
        if page_text:
            # 同じ本文がページ遷移の都合で重複した場合は二重追加しない。
            if not full_pages or page_text != full_pages[-1]:
                full_pages.append(page_text)
            print(
                f"[DEBUG] Page {current_page}: "
                f"Extracted {len(page_text)} chars."
            )
        else:
            print(f"[WARN] Page {current_page}: article body not found.")

        next_url = find_next_article_page_url(
            driver,
            current_page=current_page,
            visited_urls=visited_urls,
        )
        if not next_url:
            break

        print(f"[DEBUG] Found next article page: {next_url}")
        driver.get(next_url)
        time.sleep(2)
        current_page += 1

    return "\n".join(full_pages).strip()


def is_rejected_body(body):
    compact = re.sub(r"\s+", "", body or "")
    for marker in REJECT_BODY_MARKERS:
        if re.sub(r"\s+", "", marker) in compact:
            return True
    return False


# --- 記事情報取得関数 ---
def extract_article_info(driver, url):
    try:
        print(f"[DEBUG] Extracting info from: {url}")
        driver.get(url)
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        article_id_match = re.search(r"/([^/]+)/?$", url)
        article_id = article_id_match.group(1) if article_id_match else "NO_ID"

        title = "NO TITLE"
        published_at = ""
        category = ""
        source = "読売新聞"

        ld_json_scripts = soup.find_all("script", type="application/ld+json")
        for script in ld_json_scripts:
            try:
                if not script.string:
                    continue
                data = json.loads(script.string)
                if isinstance(data, list):
                    candidates = data
                else:
                    candidates = [data]

                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if candidate.get("@type") != "NewsArticle":
                        continue

                    title = candidate.get("headline", title)
                    published_at = candidate.get("datePublished", published_at)
                    category = candidate.get("articleSection", category)

                    author = candidate.get("author")
                    if isinstance(author, dict):
                        source = author.get("name", source)
                    elif isinstance(author, list) and author:
                        first_author = author[0]
                        if isinstance(first_author, dict):
                            source = first_author.get("name", source)
                    break
            except Exception:
                continue

        if title == "NO TITLE":
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        if not published_at:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                published_at = time_tag["datetime"]

        body = extract_body_content(driver)

        if not body:
            print(f"[REJECT] Empty article body: {url}")
            return None

        if is_rejected_body(body):
            print(f"[REJECT] Reader-membership notice detected: {url}")
            return None

        return article_id, title, source, published_at, url, category, body

    except Exception as e:
        print(f"[ERROR] Failed to extract from {url}: {e}")
        return None


def info_to_history_row(info, timestamp):
    return [
        info[0],
        timestamp,
        info[1],
        info[2],
        info[3],
        info[4],
        info[5],
        info[6],
    ]


# --- 履歴シートへの追記 ---
def append_history_rows(history_sheet, rows, existing_urls):
    if not rows:
        return

    existing = history_sheet.get_all_values()
    if not existing:
        history_sheet.append_row(HISTORY_HEADERS)
        existing_urls.clear()

    new_rows = []
    for row in rows:
        if row[5] not in existing_urls:
            new_rows.append(row)
            existing_urls.add(row[5])

    if not new_rows:
        return

    ensure_history_capacity(history_sheet, len(new_rows))
    history_sheet.append_rows(new_rows, value_input_option="RAW")
    print(f"[INFO] Added {len(new_rows)} new articles to history.")


# --- 現在の上位5記事を latest シートへ保存 ---
def update_latest_sheet(spreadsheet, history_rows, timestamp):
    latest_sheet = get_or_create_worksheet(
        spreadsheet,
        LATEST_SHEET_NAME,
        rows=max(20, LATEST_COUNT + 1),
        cols=len(LATEST_HEADERS),
    )

    output = [LATEST_HEADERS]
    for rank, row in enumerate(history_rows[:LATEST_COUNT], start=1):
        normalized = list(row[:8]) + [""] * max(0, 8 - len(row))
        normalized[1] = timestamp
        output.append([rank] + normalized[:8])

    latest_sheet.clear()
    latest_sheet.update(
        range_name=f"A1:I{len(output)}",
        values=output,
        value_input_option="RAW",
    )
    print(f"[INFO] Updated latest sheet with {len(output) - 1} articles.")


# --- メイン処理 ---
if __name__ == "__main__":
    print("[START] Yomiuri Politics Scraping")
    driver = None

    try:
        driver = init_driver()
        target_url = "https://www.yomiuri.co.jp/politics/"

        driver.get(target_url)
        time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        links = soup.select(
            "h3.title a, .p-list-item__title a, .p-headline__title a"
        )

        article_urls = []
        for a in links:
            href = a.get("href")
            if href and (
                "/politics/" in href
                or "/election/" in href
                or "/local/" in href
            ):
                href = normalize_url(urljoin(target_url, href))
                if href not in article_urls:
                    article_urls.append(href)

        print(
            f"[INFO] Found {len(article_urls)} potential links "
            "on the index page."
        )

        spreadsheet = open_spreadsheet()
        history_sheet = get_or_create_worksheet(
            spreadsheet,
            HISTORY_SHEET_NAME,
            rows=2000,
            cols=len(HISTORY_HEADERS),
        )

        existing_vals = history_sheet.get_all_values()
        if not existing_vals:
            history_sheet.append_row(HISTORY_HEADERS)
            existing_vals = [HISTORY_HEADERS]

        # 既存行のうち、会員案内本文になってしまっている行は再利用しない。
        existing_by_url = {}
        existing_urls = set()
        for row in existing_vals[1:]:
            if len(row) <= 5 or not row[5]:
                continue
            existing_urls.add(row[5])
            body = row[7] if len(row) > 7 else ""
            if body and not is_rejected_body(body):
                existing_by_url[row[5]] = row

        jst = timezone(timedelta(hours=9))
        timestamp = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

        collected_data = []
        fresh_by_url = {}

        # 未取得記事を履歴へ追加する。
        for url in article_urls:
            if url in existing_urls:
                continue

            info = extract_article_info(driver, url)
            if info:
                row = info_to_history_row(info, timestamp)
                collected_data.append(row)
                fresh_by_url[url] = row
                time.sleep(1)

        append_history_rows(history_sheet, collected_data, existing_urls)

        # 上から見て、有効な記事が5件揃うまで取得する。
        latest_rows = []
        for url in article_urls:
            if len(latest_rows) >= LATEST_COUNT:
                break

            if url in fresh_by_url:
                latest_rows.append(fresh_by_url[url])
                continue

            if url in existing_by_url:
                latest_rows.append(existing_by_url[url])
                continue

            # 既存URLでも過去に会員案内しか取れていなかったものは再取得する。
            info = extract_article_info(driver, url)
            if not info:
                continue

            row = info_to_history_row(info, timestamp)
            latest_rows.append(row)

            # 完全な新規URLだけ履歴へ追加。既存の不正行は残すが latest では採用しない。
            if url not in existing_urls:
                append_history_rows(history_sheet, [row], existing_urls)

            time.sleep(1)

        update_latest_sheet(spreadsheet, latest_rows, timestamp)

    except Exception as e:
        print(f"[CRITICAL ERROR] {e}")
        raise
    finally:
        if driver:
            driver.quit()
