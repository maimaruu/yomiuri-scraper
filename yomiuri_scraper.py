# Scraper v2: history + current top-5 snapshot
import time
import re
import json
from datetime import datetime, timezone, timedelta

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
        "Chrome/91.0.4472.124 Safari/537.36"
    )

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


# --- 本文抽出関数（続きを読む ＆ 複数ページ 両対応版）---
def extract_body_content(driver):
    """
    「続きを読む」があればクリックし、
    さらに「次へ（ページ送り）」があれば巡回して全文を取得します。
    """
    full_text = ""
    current_page = 1

    while True:
        try:
            read_more_buttons = driver.find_elements(
                By.XPATH,
                "//button[contains(., '続きを読む')] | "
                "//a[contains(., '続きを読む')] | "
                "//*[contains(@class, 'readmore-btn')]",
            )
            for btn in read_more_buttons:
                if btn.is_displayed():
                    print(
                        f"[DEBUG] Page {current_page}: "
                        "Clicking 'Read More' button..."
                    )
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    break
        except Exception:
            pass

        soup = BeautifulSoup(driver.page_source, "html.parser")
        article_div = soup.find(
            "div",
            class_=re.compile(r"p-main-contents|article-body|uni-news-article"),
        )

        if article_div:
            paragraphs = [
                p.get_text().strip()
                for p in article_div.find_all("p")
                if p.get_text().strip()
            ]
            page_text = "\n".join(paragraphs)
            full_text += page_text + "\n"
            print(
                f"[DEBUG] Page {current_page}: "
                f"Extracted {len(page_text)} chars."
            )

        try:
            next_buttons = driver.find_elements(
                By.CSS_SELECTOR, "a.p-pager__next, a.next, a[rel='next']"
            )

            if next_buttons and next_buttons[0].is_displayed():
                next_url = next_buttons[0].get_attribute("href")
                print(f"[DEBUG] Found next page: {next_url}")
                driver.get(next_url)
                time.sleep(2)
                current_page += 1

                if current_page > 10:
                    break
            else:
                break
        except Exception as e:
            print(f"[DEBUG] Pagination check error: {e}")
            break

    return full_text.strip()


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
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = data[0]

                if isinstance(data, dict) and data.get("@type") == "NewsArticle":
                    if "headline" in data:
                        title = data["headline"]
                    if "datePublished" in data:
                        published_at = data["datePublished"]
                    if "articleSection" in data:
                        category = data["articleSection"]
                    if "author" in data and isinstance(data["author"], dict):
                        source = data["author"].get("name", "読売新聞")
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
    existing = history_sheet.get_all_values()
    if not existing:
        history_sheet.append_row(HISTORY_HEADERS)
        existing_urls = set()

    new_rows = []
    for row in rows:
        if row[5] not in existing_urls:
            new_rows.append(row)
            existing_urls.add(row[5])

    if not new_rows:
        print("[INFO] No new articles found.")
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
                if "https" not in href:
                    href = "https://www.yomiuri.co.jp" + href
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

        existing_by_url = {
            row[5]: row
            for row in existing_vals[1:]
            if len(row) > 5 and row[5]
        }
        existing_urls = set(existing_by_url.keys())

        jst = timezone(timedelta(hours=9))
        timestamp = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

        collected_data = []
        fresh_by_url = {}

        # これまで通り、一覧に出ている未取得記事は履歴へ追加する。
        for url in article_urls:
            if url in existing_urls:
                print(f"[SKIP] Already collected: {url}")
                continue

            info = extract_article_info(driver, url)
            if info:
                row = info_to_history_row(info, timestamp)
                collected_data.append(row)
                fresh_by_url[url] = row
                time.sleep(2)

        append_history_rows(
            history_sheet,
            collected_data,
            existing_urls,
        )

        # 読売政治面に現在表示されている順番の上位5件を latest に保存する。
        latest_rows = []
        for url in article_urls[:LATEST_COUNT]:
            if url in fresh_by_url:
                latest_rows.append(fresh_by_url[url])
                continue

            if url in existing_by_url:
                latest_rows.append(existing_by_url[url])
                continue

            # 念のため、履歴にも fresh_by_url にも無い場合はその場で取得する。
            info = extract_article_info(driver, url)
            if info:
                row = info_to_history_row(info, timestamp)
                latest_rows.append(row)
                append_history_rows(history_sheet, [row], existing_urls)
                existing_by_url[url] = row
                time.sleep(2)

        update_latest_sheet(spreadsheet, latest_rows, timestamp)

    except Exception as e:
        print(f"[CRITICAL ERROR] {e}")
        # GitHub Actions上でも失敗として分かるようにする。
        raise
    finally:
        if driver:
            driver.quit()
