"""
쿠팡 골드박스 크롤러
- Selenium으로 골드박스 페이지 로드 + 네트워크 API 캡처
- 상품 정보 + 소진율 수집
- Supabase에 저장
"""

import os
import sys
import re
import time
import json
import random
import logging
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from supabase import create_client

# ── 로깅 설정 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── 환경변수 ──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GOLDBOX_URL = "https://www.coupang.com/np/goldbox"

# ── 한국 시간 ──
KST = timezone(timedelta(hours=9))


def create_driver():
    """Headless Chrome 드라이버 생성 (네트워크 로그 활성화)"""
    ua = UserAgent()
    user_agent = ua.random

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # 네트워크 로그 캡처
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    # Network tracking 활성화
    driver.execute_cdp_cmd("Network.enable", {})

    return driver


def extract_number(text):
    """문자열에서 숫자만 추출"""
    if not text:
        return 0
    digits = re.sub(r'[^\d]', '', str(text))
    return int(digits) if digits else 0


def scroll_and_wait(driver, max_scrolls=30):
    """스크롤하면서 상품 로드 대기"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(2, 4))
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            logger.info(f"스크롤 완료 ({i+1}회)")
            break
        last_height = new_height
    # 상단으로 돌아가서 다시 천천히 스크롤 (lazy load 트리거)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)
    total_height = driver.execute_script("return document.body.scrollHeight")
    for pos in range(0, total_height, 500):
        driver.execute_script(f"window.scrollTo(0, {pos});")
        time.sleep(0.3)


def capture_api_data(driver):
    """네트워크 로그에서 골드박스 API 응답 데이터 추출"""
    api_products = []

    try:
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]

                # Network.responseReceived 이벤트에서 goldbox 관련 API 찾기
                if msg["method"] == "Network.responseReceived":
                    url = msg["params"]["response"]["url"]
                    if any(k in url.lower() for k in ["goldbox", "dealset", "deal", "timesale", "flash"]):
                        request_id = msg["params"]["requestId"]
                        logger.info(f"API 발견: {url[:150]}")

                        try:
                            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                            data = json.loads(body.get("body", "{}"))
                            logger.info(f"API 응답 키: {list(data.keys()) if isinstance(data, dict) else type(data)}")

                            # 다양한 응답 구조 처리
                            items = []
                            if isinstance(data, list):
                                items = data
                            elif isinstance(data, dict):
                                for key in ["products", "items", "deals", "data", "rData", "result"]:
                                    if key in data:
                                        candidate = data[key]
                                        if isinstance(candidate, list):
                                            items = candidate
                                            break
                                        elif isinstance(candidate, dict):
                                            for k2 in ["products", "items", "deals", "list"]:
                                                if k2 in candidate and isinstance(candidate[k2], list):
                                                    items = candidate[k2]
                                                    break

                            if items:
                                logger.info(f"API에서 {len(items)}개 아이템 발견")
                                if items:
                                    logger.info(f"샘플 키: {list(items[0].keys()) if isinstance(items[0], dict) else 'not dict'}")
                                for item in items:
                                    if not isinstance(item, dict):
                                        continue
                                    p = parse_api_product(item)
                                    if p:
                                        api_products.append(p)
                        except Exception as e:
                            logger.warning(f"API 응답 파싱 실패: {e}")

            except Exception:
                continue

    except Exception as e:
        logger.warning(f"네트워크 로그 분석 실패: {e}")

    return api_products


def parse_api_product(item):
    """API 응답의 상품 데이터 파싱 (다양한 키 대응)"""
    pid = str(item.get("productId") or item.get("itemId") or item.get("id") or item.get("dealId") or "")
    if not pid:
        return None

    # 상품명
    name = (item.get("productName") or item.get("name") or item.get("title") or
            item.get("itemName") or item.get("dealName") or "")

    # 이미지
    img = item.get("productImage") or item.get("imageUrl") or item.get("image") or item.get("thumbnailUrl") or ""
    if img and img.startswith("//"):
        img = "https:" + img

    # 가격
    orig = item.get("basePrice") or item.get("originalPrice") or item.get("listPrice") or item.get("price") or 0
    sale = item.get("salePrice") or item.get("discountPrice") or item.get("finalPrice") or item.get("couponPrice") or orig
    if isinstance(orig, str):
        orig = extract_number(orig)
    if isinstance(sale, str):
        sale = extract_number(sale)

    # 할인율
    discount = item.get("discountRate") or item.get("discountPercent") or item.get("discountRatio") or 0
    if isinstance(discount, str):
        discount = extract_number(discount)
    if not discount and orig and sale and orig > sale:
        discount = round((1 - sale / orig) * 100)

    # 소진율
    sold = item.get("soldRate") or item.get("soldPercent") or item.get("soldOut") or item.get("progressRate") or 0
    if isinstance(sold, str):
        sold = extract_number(sold)
    if sold > 100:
        sold = 0

    # URL
    url = item.get("productUrl") or item.get("url") or item.get("landingUrl") or ""
    if url and not url.startswith("http"):
        url = f"https://www.coupang.com{url}"
    if not url:
        url = f"https://www.coupang.com/vp/products/{pid}"

    # 브랜드, 카테고리
    brand = item.get("brandName") or item.get("brand") or ""
    category = item.get("categoryName") or item.get("category") or item.get("categoryTitle") or ""

    return {
        "product_id": pid,
        "product_name": str(name),
        "image_url": str(img),
        "product_url": str(url),
        "original_price": int(orig) if orig else 0,
        "sale_price": int(sale) if sale else 0,
        "discount_rate": min(int(discount), 100) if discount else 0,
        "sold_rate": int(sold),
        "brand_name": str(brand),
        "category": str(category),
    }


def parse_html_products(html):
    """HTML에서 상품 파싱 (폴백)"""
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen = set()

    for link in soup.select("a[href*='/products/']"):
        href = link.get("href", "")
        match = re.search(r'/products/(\d+)', href)
        if not match:
            continue
        pid = match.group(1)
        if pid in seen:
            continue
        seen.add(pid)

        container = link
        for _ in range(6):
            parent = container.parent
            if parent and parent.name in ['li', 'div', 'article', 'section']:
                if parent.find("img") and len(parent.get_text(strip=True)) > 20:
                    container = parent
                    break
                container = parent
            else:
                break

        product = {
            "product_id": pid,
            "product_url": f"https://www.coupang.com{href}" if href.startswith("/") else href,
            "product_name": "",
            "image_url": "",
            "original_price": 0,
            "sale_price": 0,
            "discount_rate": 0,
            "sold_rate": 0,
            "brand_name": "",
            "category": "",
        }

        # 이름
        for sel in ["[class*='name']", "[class*='title']", "[class*='description']"]:
            el = container.select_one(sel)
            if el and len(el.get_text(strip=True)) > 3:
                product["product_name"] = el.get_text(strip=True)[:200]
                break
        if not product["product_name"]:
            product["product_name"] = link.get_text(strip=True)[:200]

        # 이미지
        img = container.find("img")
        if img:
            src = img.get("src") or img.get("data-img-src") or img.get("data-src") or ""
            if src.startswith("//"):
                src = "https:" + src
            product["image_url"] = src

        # 가격
        for el in container.find_all(["span", "strong", "em", "del"]):
            text = el.get_text(strip=True)
            num = extract_number(text)
            cls = " ".join(el.get("class", [])).lower()
            if 100 <= num <= 100000000:
                if "base" in cls or "origin" in cls or el.name == "del":
                    product["original_price"] = num
                elif "sale" in cls or "final" in cls:
                    product["sale_price"] = num
                elif not product["sale_price"]:
                    product["sale_price"] = num

        # 할인율
        if product["original_price"] > product["sale_price"] > 0:
            product["discount_rate"] = round((1 - product["sale_price"] / product["original_price"]) * 100)

        # 소진율 (width style)
        progress = container.select_one("[style*='width']")
        if progress:
            m = re.search(r'width:\s*([\d.]+)%', progress.get("style", ""))
            if m:
                product["sold_rate"] = min(round(float(m.group(1))), 100)

        products.append(product)

    return products


def crawl_goldbox():
    """메인 크롤링 함수"""
    logger.info("=" * 50)
    logger.info("쿠팡 골드박스 크롤링 시작")
    logger.info("=" * 50)

    driver = create_driver()
    products = []

    try:
        logger.info(f"페이지 로드 중: {GOLDBOX_URL}")
        driver.get(GOLDBOX_URL)

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(random.uniform(4, 6))

        # 팝업 닫기
        try:
            for sel in ["[class*='close']", "button[class*='modal']", "[class*='popup'] button"]:
                for btn in driver.find_elements(By.CSS_SELECTOR, sel)[:3]:
                    try:
                        btn.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
        except Exception:
            pass

        logger.info(f"페이지 타이틀: {driver.title}")

        # 스크롤하여 모든 상품 로드
        scroll_and_wait(driver)

        # ── 전략 1: 네트워크 API 데이터 캡처 ──
        products = capture_api_data(driver)
        if products:
            logger.info(f"API에서 {len(products)}개 상품 수집")

        # ── 전략 2: JS로 페이지 내 데이터 추출 ──
        if len(products) < 10:
            logger.info("API 데이터 부족, JS로 페이지 데이터 추출 시도")
            try:
                js_data = driver.execute_script("""
                    // window.__NEXT_DATA__ 또는 전역 상태에서 데이터 찾기
                    var data = null;

                    // Next.js 앱이면 __NEXT_DATA__에서
                    if (window.__NEXT_DATA__) {
                        data = JSON.stringify(window.__NEXT_DATA__);
                    }

                    // 전역 변수 탐색
                    var globalData = {};
                    ['__goldbox__', '__PRELOADED_STATE__', '__INITIAL_STATE__', 'goldboxData', 'dealData'].forEach(function(key) {
                        if (window[key]) globalData[key] = window[key];
                    });

                    // 페이지 내 script 태그에서 JSON 데이터 추출
                    var scripts = [];
                    document.querySelectorAll('script:not([src])').forEach(function(s) {
                        var t = s.textContent;
                        if (t.length > 100 && (t.includes('product') || t.includes('goldbox') || t.includes('deal'))) {
                            scripts.push(t.substring(0, 5000));
                        }
                    });

                    return {
                        nextData: data ? data.substring(0, 10000) : null,
                        globalData: JSON.stringify(globalData).substring(0, 5000),
                        scripts: scripts.slice(0, 5),
                        productLinks: document.querySelectorAll('a[href*="/products/"]').length,
                        allLinks: document.querySelectorAll('a[href]').length
                    };
                """)
                logger.info(f"JS 탐색 결과: 상품링크 {js_data.get('productLinks')}개, 전체링크 {js_data.get('allLinks')}개")
                if js_data.get("nextData"):
                    logger.info(f"__NEXT_DATA__ 발견: {js_data['nextData'][:500]}")
                if js_data.get("globalData") and len(js_data["globalData"]) > 5:
                    logger.info(f"전역 데이터: {js_data['globalData'][:500]}")
                for i, script in enumerate(js_data.get("scripts", [])):
                    logger.info(f"Script[{i}]: {script[:300]}")
            except Exception as e:
                logger.warning(f"JS 데이터 추출 실패: {e}")

        # ── 전략 3: HTML 파싱 (폴백) ──
        if len(products) < 10:
            logger.info("HTML 파싱 시도")
            html = driver.page_source
            html_products = parse_html_products(html)
            logger.info(f"HTML에서 {len(html_products)}개 상품 파싱")

            # API에서 못 얻은 상품만 추가
            existing_ids = {p["product_id"] for p in products}
            for p in html_products:
                if p["product_id"] not in existing_ids:
                    products.append(p)

        # 디버깅용 HTML 저장 (상품 부족 시)
        if len(products) < 10:
            html = driver.page_source
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"디버깅용 HTML 저장 ({len(html)} bytes)")

            # 추가 디버깅: 페이지 내 모든 고유 클래스 출력
            soup = BeautifulSoup(html, "html.parser")
            classes = set()
            for tag in soup.find_all(True):
                for c in tag.get("class", []):
                    if any(k in c.lower() for k in ["product", "item", "deal", "gold", "card", "list", "grid"]):
                        classes.add(f"{tag.name}.{c}")
            logger.info(f"관련 클래스: {sorted(classes)[:30]}")

        logger.info(f"총 {len(products)}개 상품 수집 완료")

        for p in products[:5]:
            logger.info(f"  [{p['product_id']}] {p['product_name'][:30]} | {p['sale_price']}원 | 할인 {p['discount_rate']}% | 소진 {p['sold_rate']}%")

    except Exception as e:
        logger.error(f"크롤링 중 오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

    return products


def save_to_supabase(products):
    """Supabase에 데이터 저장"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.error("Supabase 환경변수가 설정되지 않았습니다")
        save_to_json(products)
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    now = datetime.now(KST).isoformat()
    today = datetime.now(KST).strftime("%Y-%m-%d")

    success_count = 0

    for p in products:
        try:
            product_data = {
                "product_id": p["product_id"],
                "product_name": p.get("product_name", ""),
                "brand_name": p.get("brand_name", ""),
                "category": p.get("category", ""),
                "image_url": p.get("image_url", ""),
                "product_url": p.get("product_url", ""),
                "last_seen_date": today,
            }

            existing = supabase.table("goldbox_products").select("product_id").eq(
                "product_id", p["product_id"]
            ).execute()

            if existing.data:
                supabase.table("goldbox_products").update({
                    "product_name": product_data["product_name"],
                    "brand_name": product_data["brand_name"],
                    "category": product_data["category"],
                    "image_url": product_data["image_url"],
                    "last_seen_date": today,
                }).eq("product_id", p["product_id"]).execute()
            else:
                product_data["first_seen_date"] = today
                supabase.table("goldbox_products").insert(product_data).execute()

            snapshot_data = {
                "product_id": p["product_id"],
                "crawled_at": now,
                "original_price": p.get("original_price", 0),
                "sale_price": p.get("sale_price", 0),
                "discount_rate": p.get("discount_rate", 0),
                "sold_rate": p.get("sold_rate", 0),
            }
            supabase.table("goldbox_snapshots").insert(snapshot_data).execute()
            success_count += 1

        except Exception as e:
            logger.error(f"저장 실패 (product_id={p.get('product_id')}): {e}")

    logger.info(f"Supabase 저장 완료: {success_count}/{len(products)}개 성공")


def save_to_json(products):
    """로컬 테스트용 JSON 저장"""
    now = datetime.now(KST)
    filename = f"goldbox_{now.strftime('%Y%m%d_%H%M')}.json"
    filepath = os.path.join(os.path.dirname(__file__), filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "crawled_at": now.isoformat(),
            "count": len(products),
            "products": products
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"로컬 JSON 저장: {filepath} ({len(products)}개)")


def main():
    products = crawl_goldbox()

    if products:
        save_to_supabase(products)
    else:
        logger.warning("수집된 상품이 없습니다")
        sys.exit(1)

    logger.info("크롤링 작업 완료")


if __name__ == "__main__":
    main()
