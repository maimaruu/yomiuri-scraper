import time
import re
import json
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from oauth2client.service_account import ServiceAccountCredentials
import gspread

# --- Google Sheets設定 ---
SERVICE_ACCOUNT_FILE = "credentials.json"
SHEET_NAME = "yomiuri-politics-scraper" 

# --- Selenium設定 ---
def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

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
        # 1. 「続きを読む」ボタンがあればクリック（展開型への対応）
        try:
            read_more_buttons = driver.find_elements(By.XPATH, "//button[contains(., '続きを読む')] | //a[contains(., '続きを読む')] | //*[contains(@class, 'readmore-btn')]")
            for btn in read_more_buttons:
                if btn.is_displayed():
                    print(f"[DEBUG] Page {current_page}: Clicking 'Read More' button...")
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    break
        except Exception:
            pass

        # 2. 現在のページの本文を取得
        soup = BeautifulSoup(driver.page_source, "html.parser")
        article_div = soup.find("div", class_=re.compile(r"p-main-contents|article-body|uni-news-article"))
        
        if article_div:
            # 本文パラグラフを取得
            paragraphs = [p.get_text().strip() for p in article_div.find_all("p") if p.get_text().strip()]
            page_text = "\n".join(paragraphs)
            full_text += page_text + "\n"
            print(f"[DEBUG] Page {current_page}: Extracted {len(page_text)} chars.")
        
        # 3. 「次へ」ボタンがあるかチェック（複数ページ型への対応）
        # 読売新聞の次ページボタンは通常 class="p-pager__next" または "next" を含む
        try:
            next_buttons = driver.find_elements(By.CSS_SELECTOR, "a.p-pager__next, a.next, a[rel='next']")
            
            if next_buttons and next_buttons[0].is_displayed():
                next_url = next_buttons[0].get_attribute("href")
                print(f"[DEBUG] Found next page: {next_url}")
                driver.get(next_url)
                time.sleep(2) # ページ遷移待機
                current_page += 1
                
                # 無限ループ防止（念のため10ページまで）
                if current_page > 10:
                    break
            else:
                # 次のページがなければ終了
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
        
        # 初期ロード時のメタデータ取得用
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # 1. IDの抽出
        article_id_match = re.search(r'/([^/]+)/?$', url)
        article_id = article_id_match.group(1) if article_id_match else "NO_ID"

        # 2. メタデータの抽出 (LD-JSON優先)
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
            if h1: title = h1.get_text(strip=True)
        
        if not published_at:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                published_at = time_tag["datetime"]

        # 3. 本文の抽出（全文取得ロジックへ）
        # ここでページ遷移が発生しても、metadataは取得済みなので問題なし
        body = extract_body_content(driver)

        return article_id, title, source, published_at, url, category, body

    except Exception as e:
        print(f"[ERROR] Failed to extract from {url}: {e}")
        return None

# --- スプレッドシート書き込み ---
def append_to_sheet(data, existing_urls):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1

    existing = sheet.get_all_values()
    if not existing:
        headers = ["ID", "collected_at", "title", "source", "published_at", "url", "category", "body"]
        sheet.append_row(headers)
        existing_urls = set()

    new_rows = []
    for row in data:
        if row[5] not in existing_urls:
            new_rows.append(row)
            existing_urls.add(row[5])

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="RAW")
        print(f"[INFO] Added {len(new_rows)} new articles.")
    else:
        print("[INFO] No new articles found.")

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
        links = soup.select("h3.title a, .p-list-item__title a, .p-headline__title a") 
        
        article_urls = []
        for a in links:
            href = a.get("href")
            # 政治、選挙、地域(政治関連)
            if href and ("/politics/" in href or "/election/" in href or "/local/" in href):
                if "https" not in href:
                    href = "https://www.yomiuri.co.jp" + href
                if href not in article_urls:
                    article_urls.append(href)

        print(f"[INFO] Found {len(article_urls)} potential links on the index page.")

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        existing_vals = sheet.get_all_values()
        existing_urls = {row[5] for row in existing_vals[1:] if len(row) > 5}

        collected_data = []
        jst = timezone(timedelta(hours=9))
        timestamp = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S")

        for url in article_urls:
            if url in existing_urls:
                print(f"[SKIP] Already collected: {url}")
                continue
            
            info = extract_article_info(driver, url)
            if info:
                row = [
                    info[0],
                    timestamp,
                    info[1],
                    info[2],
                    info[3],
                    info[4],
                    info[5],
                    info[6] 
                ]
                collected_data.append(row)
                time.sleep(2)

        if collected_data:
            append_to_sheet(collected_data, existing_urls)

    except Exception as e:
        print(f"[CRITICAL ERROR] {e}")
    finally:
        if driver:
            driver.quit()
