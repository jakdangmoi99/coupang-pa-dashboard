"""
쿠팡 골드박스 크롤러
- 쿠팡 메인 → 골드박스 자연스러운 이동 (봇 감지 우회)
- 네트워크 API 캡처 + HTML 파싱
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
KST = timezone(timedelta(hours=9))


def create_driver():
    """실제 브라우저처럼 보이는 Chrome 설정"""
    ua = UserAgent(browsers=["chrome"], os=["linux"])
    user_agent = ua.random

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--accept-lang=ko-KR,ko;q=0.9,en-US;q=0.8")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # 자동화 탐지 우회
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US', 'en']});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
        """
    })
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    })

    return driver


def human_scroll(driver):
    """사람처럼 자연스럽게 스크롤"""
    total_height = driver.execute_script("return document.body.scrollHeight")
    position = 0
    while position < total_height:
        scroll_amount = random.randint(300, 700)
        position += scroll_amount
        driver.execute_script(f"window.scrollTo({{top: {position}, behavior: 'smooth'}});")
        time.sleep(random.uniform(0.5, 1.5))
        # 중간중간 멈춤 (읽는 척)
        if random.random() < 0.3:
            time.sleep(random.uniform(1, 3))
        total_height = driver.execute_script("return document.body.scrollHeight")
    logger.info("스크롤 완료")


def extract_number(text):
    if not text:
        return 0
    digits = re.sub(r'[^\d]', '', str(text))
    return int(digits) if digits else 0


def warm_up(driver):
    """쿠팡 메인 페이지 방문하여 쿠키/세션 확보"""
    logger.info("쿠팡 메인 페이지 방문 (쿠키 획득)")
    driver.get("https://www.coupang.com")
    time.sleep(random.uniform(3, 5))

    # 쿠키 동의 팝업 처리
    try:
        for sel in ["#cookieAcceptBtn", "[class*='cookie'] button", "[class*='consent'] button"]:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in btns:
                try:
                    btn.click()
                    time.sleep(0.5)
                except Exception:
                    pass
    except Exception:
        pass

    # 메인 페이지 살짝 스크롤 (사람 흉내)
    for _ in range(3):
        driver.execute_script(f"window.scrollBy(0, {random.randint(200, 500)});")
        time.sleep(random.uniform(0.5, 1))

    title = driver.title
    logger.info(f"메인 페이지 타이틀: {title}")

    cookies = driver.get_cookies()
    logger.info(f"쿠키 {len(cookies)}개 획득")

    return "Access Denied" not in title


def navigate_to_goldbox(driver):
    """메인 → 골드박스 자연스럽게 이동"""
    time.sleep(random.uniform(2, 4))

    # 방법 1: 골드박스 링크 직접 클릭
    try:
        gb_link = driver.find_element(By.CSS_SELECTOR, "a[href*='goldbox']")
        gb_link.click()
        logger.info("골드박스 링크 클릭으로 이동")
        time.sleep(random.uniform(3, 5))
        return True
    except Exception:
        pass

    # 방법 2: JS로 이동 (referrer 유지)
    logger.info("골드박스 URL로 직접 이동")
    driver.execute_script("window.location.href = 'https://www.coupang.com/np/goldbox';")
    time.sleep(random.uniform(4, 6))

    title = driver.title
    logger.info(f"골드박스 페이지 타이틀: {title}")

    return "Access Denied" not in title and "Denied" not in title


def capture_api_products(driver):
    """네트워크 로그에서 상품 데이터 추출"""
    products = []
    try:
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                if msg["method"] != "Network.responseReceived":
                    continue
                url = msg["params"]["response"]["url"]
                if not any(k in url.lower() for k in ["goldbox", "deal", "timesale", "product"]):
                    continue
                if msg["params"]["response"]["mimeType"] not in ["application/json", "text/json"]:
                    continue

                request_id = msg["params"]["requestId"]
                logger.info(f"JSON API: {url[:150]}")

                body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                data = json.loads(body.get("body", "{}"))

                items = extract_items_from_json(data)
                if items:
                    logger.info(f"  → {len(items)}개 아이템")
                    for item in items:
                        p = parse_api_item(item)
                        if p:
                            products.append(p)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"API 캡처 실패: {e}")
    return products


def extract_items_from_json(data, depth=0):
    """JSON에서 상품 리스트 재귀 탐색"""
    if depth > 5:
        return []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        if any(k in data[0] for k in ["productId", "itemId", "productName", "salePrice", "basePrice"]):
            return data
    if isinstance(data, dict):
        for key in ["products", "items", "deals", "data", "rData", "result", "list",
                     "productList", "dealProducts", "goldboxProducts"]:
            if key in data:
                result = extract_items_from_json(data[key], depth + 1)
                if result:
                    return result
        # 모든 value 탐색
        for v in data.values():
            if isinstance(v, (dict, list)):
                result = extract_items_from_json(v, depth + 1)
                if result:
                    return result
    return []


def parse_api_item(item):
    """API 상품 아이템 파싱"""
    if not isinstance(item, dict):
        return None
    pid = str(item.get("productId") or item.get("itemId") or item.get("id") or item.get("dealId") or "")
    if not pid or not pid.isdigit():
        return None

    name = str(item.get("productName") or item.get("name") or item.get("title") or item.get("itemName") or "")
    img = str(item.get("productImage") or item.get("imageUrl") or item.get("image") or item.get("thumbnailUrl") or "")
    if img.startswith("//"):
        img = "https:" + img

    orig = item.get("basePrice") or item.get("originalPrice") or item.get("listPrice") or 0
    sale = item.get("salePrice") or item.get("discountPrice") or item.get("finalPrice") or orig
    orig = extract_number(str(orig))
    sale = extract_number(str(sale))

    discount = item.get("discountRate") or item.get("discountPercent") or 0
    discount = extract_number(str(discount))
    if not discount and orig > sale > 0:
        discount = round((1 - sale / orig) * 100)

    sold = item.get("soldRate") or item.get("soldPercent") or item.get("progressRate") or 0
    sold = extract_number(str(sold))
    if sold > 100:
        sold = 0

    url = str(item.get("productUrl") or item.get("url") or item.get("landingUrl") or "")
    if url and not url.startswith("http"):
        url = f"https://www.coupang.com{url}"
    if not url:
        url = f"https://www.coupang.com/vp/products/{pid}"

    return {
        "product_id": pid,
        "product_name": name,
        "image_url": img,
        "product_url": url,
        "original_price": orig,
        "sale_price": sale,
        "discount_rate": min(discount, 100),
        "sold_rate": sold,
        "brand_name": str(item.get("brandName") or item.get("brand") or ""),
        "category": str(item.get("categoryName") or item.get("category") or ""),
    }


def parse_html_products(driver):
    """Selenium으로 DOM 직접 탐색하여 상품 추출"""
    products = []

    try:
        # JS로 모든 상품 카드 정보 한번에 추출
        js_products = driver.execute_script("""
            var results = [];
            var seen = {};

            // 모든 상품 링크 수집
            document.querySelectorAll('a').forEach(function(a) {
                var href = a.href || '';
                var m = href.match(/\\/products\\/(\\d+)/);
                if (!m || seen[m[1]]) return;
                seen[m[1]] = true;

                // 상위 카드 컨테이너 찾기
                var card = a;
                for (var i = 0; i < 8; i++) {
                    if (!card.parentElement) break;
                    card = card.parentElement;
                    if (card.querySelector('img') && card.offsetHeight > 100) break;
                }

                var img = card.querySelector('img');
                var imgSrc = img ? (img.src || img.dataset.imgSrc || img.dataset.src || '') : '';

                // 텍스트 수집
                var allText = card.innerText || '';
                var lines = allText.split('\\n').filter(function(l) { return l.trim().length > 0; });

                // 가격 패턴 찾기
                var prices = [];
                card.querySelectorAll('*').forEach(function(el) {
                    var t = (el.textContent || '').trim();
                    var numMatch = t.replace(/,/g, '').match(/^(\\d{3,10})원?$/);
                    if (numMatch) prices.push(parseInt(numMatch[1]));
                });

                // 소진율 (width style)
                var soldRate = 0;
                card.querySelectorAll('[style*="width"]').forEach(function(el) {
                    var wm = (el.style.width || '').match(/(\\d+)/);
                    if (wm) {
                        var w = parseInt(wm[1]);
                        if (w > 0 && w <= 100) soldRate = w;
                    }
                });

                // 할인율
                var discountRate = 0;
                card.querySelectorAll('*').forEach(function(el) {
                    var t = (el.textContent || '').trim();
                    if (t.match(/^\\d{1,2}%$/) || t.match(/^-?\\d{1,2}%$/)) {
                        discountRate = parseInt(t.replace(/[^\\d]/g, ''));
                    }
                });

                results.push({
                    pid: m[1],
                    href: href,
                    img: imgSrc,
                    name: lines.length > 0 ? lines.find(function(l) { return l.length > 5 && !/^[\\d,%원\\s]+$/.test(l); }) || lines[0] : '',
                    prices: prices,
                    soldRate: soldRate,
                    discountRate: discountRate,
                    textDump: lines.slice(0, 10).join(' | ')
                });
            });
            return results;
        """)

        logger.info(f"JS DOM에서 {len(js_products or [])}개 상품 추출")

        for item in (js_products or []):
            prices = sorted(set(item.get("prices", [])), reverse=True)
            orig = prices[0] if prices else 0
            sale = prices[1] if len(prices) > 1 else (prices[0] if prices else 0)

            discount = item.get("discountRate", 0)
            if not discount and orig > sale > 0:
                discount = round((1 - sale / orig) * 100)

            products.append({
                "product_id": item["pid"],
                "product_name": (item.get("name") or "")[:200],
                "image_url": item.get("img", ""),
                "product_url": item.get("href", ""),
                "original_price": orig,
                "sale_price": sale,
                "discount_rate": min(discount, 100),
                "sold_rate": item.get("soldRate", 0),
                "brand_name": "",
                "category": "",
            })

    except Exception as e:
        logger.error(f"JS DOM 파싱 실패: {e}")

    return products


def crawl_goldbox():
    """메인 크롤링"""
    logger.info("=" * 50)
    logger.info("쿠팡 골드박스 크롤링 시작")
    logger.info("=" * 50)

    driver = create_driver()
    products = []

    try:
        # Step 1: 메인 페이지 방문 → 쿠키 획득
        if not warm_up(driver):
            logger.error("메인 페이지 접근 실패")
            # 직접 시도
            driver.get("https://www.coupang.com/np/goldbox")
            time.sleep(5)

        # Step 2: 골드박스로 이동
        if not navigate_to_goldbox(driver):
            logger.warning("골드박스 Access Denied — 재시도")
            time.sleep(random.uniform(5, 10))
            driver.delete_all_cookies()
            if warm_up(driver):
                navigate_to_goldbox(driver)

        title = driver.title
        logger.info(f"최종 페이지 타이틀: {title}")

        if "Access Denied" in title or "Denied" in title:
            logger.error("쿠팡 접근 차단됨")
            # 디버깅용
            html = driver.page_source
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            driver.quit()
            sys.exit(1)

        # Step 3: 스크롤
        time.sleep(random.uniform(2, 4))
        human_scroll(driver)
        time.sleep(random.uniform(2, 3))

        # Step 4: 데이터 수집
        # 4a. API 캡처
        products = capture_api_products(driver)
        logger.info(f"API에서 {len(products)}개 수집")

        # 4b. JS DOM 파싱
        if len(products) < 10:
            html_products = parse_html_products(driver)
            existing_ids = {p["product_id"] for p in products}
            for p in html_products:
                if p["product_id"] not in existing_ids:
                    products.append(p)
            logger.info(f"DOM 파싱 후 총 {len(products)}개")

        # 디버깅 (상품 부족 시)
        if len(products) < 10:
            html = driver.page_source
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"디버깅 HTML 저장 ({len(html)} bytes)")

            # 페이지 구조 로깅
            soup = BeautifulSoup(html, "html.parser")
            logger.info(f"a 태그 수: {len(soup.find_all('a'))}")
            logger.info(f"img 태그 수: {len(soup.find_all('img'))}")
            logger.info(f"상품 링크: {len(soup.select('a[href*=products]'))}")

            classes = set()
            for tag in soup.find_all(True, limit=200):
                for c in tag.get("class", []):
                    classes.add(f"{tag.name}.{c}")
            logger.info(f"클래스 샘플: {sorted(classes)[:40]}")

        logger.info(f"최종 수집: {len(products)}개")
        for p in products[:5]:
            logger.info(f"  [{p['product_id']}] {p['product_name'][:30]} | {p['sale_price']}원 | 소진 {p['sold_rate']}%")

    except Exception as e:
        logger.error(f"크롤링 오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

    return products


def save_to_supabase(products):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.error("Supabase 환경변수 없음")
        save_to_json(products)
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    now = datetime.now(KST).isoformat()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    ok = 0

    for p in products:
        try:
            existing = supabase.table("goldbox_products").select("product_id").eq("product_id", p["product_id"]).execute()
            if existing.data:
                supabase.table("goldbox_products").update({
                    "product_name": p["product_name"], "brand_name": p["brand_name"],
                    "category": p["category"], "image_url": p["image_url"], "last_seen_date": today,
                }).eq("product_id", p["product_id"]).execute()
            else:
                supabase.table("goldbox_products").insert({
                    "product_id": p["product_id"], "product_name": p["product_name"],
                    "brand_name": p["brand_name"], "category": p["category"],
                    "image_url": p["image_url"], "product_url": p["product_url"],
                    "first_seen_date": today, "last_seen_date": today,
                }).execute()

            supabase.table("goldbox_snapshots").insert({
                "product_id": p["product_id"], "crawled_at": now,
                "original_price": p["original_price"], "sale_price": p["sale_price"],
                "discount_rate": p["discount_rate"], "sold_rate": p["sold_rate"],
            }).execute()
            ok += 1
        except Exception as e:
            logger.error(f"저장 실패 ({p['product_id']}): {e}")

    logger.info(f"Supabase 저장: {ok}/{len(products)}개 성공")


def save_to_json(products):
    now = datetime.now(KST)
    path = os.path.join(os.path.dirname(__file__), f"goldbox_{now.strftime('%Y%m%d_%H%M')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"crawled_at": now.isoformat(), "count": len(products), "products": products}, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 저장: {path}")


def main():
    products = crawl_goldbox()
    if products:
        save_to_supabase(products)
    else:
        logger.warning("수집 상품 없음")
        sys.exit(1)
    logger.info("완료")


if __name__ == "__main__":
    main()
