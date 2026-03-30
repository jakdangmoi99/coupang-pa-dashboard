"""
쿠팡 골드박스 크롤러
- Selenium (headless Chrome)으로 골드박스 페이지 크롤링
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
    """Headless Chrome 드라이버 생성"""
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

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # navigator.webdriver 감지 우회
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    return driver


def scroll_to_load_all(driver, max_scrolls=30):
    """페이지 끝까지 스크롤하여 모든 상품 로드"""
    last_height = driver.execute_script("return document.body.scrollHeight")

    for i in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(1.5, 3.0))

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            logger.info(f"스크롤 완료 (총 {i+1}회)")
            break
        last_height = new_height


def extract_number(text):
    """문자열에서 숫자만 추출"""
    if not text:
        return 0
    digits = re.sub(r'[^\d]', '', str(text))
    return int(digits) if digits else 0


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

        # 페이지 로드 대기
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(random.uniform(3, 5))

        # 팝업/모달 닫기 시도
        try:
            close_btns = driver.find_elements(By.CSS_SELECTOR, "[class*='close'], [class*='modal'] button")
            for btn in close_btns[:3]:
                try:
                    btn.click()
                    time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass

        # 스크롤하여 모든 상품 로드
        scroll_to_load_all(driver)

        # HTML 파싱
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # 디버깅: 페이지 구조 분석
        logger.info(f"페이지 타이틀: {driver.title}")
        all_links = soup.select("a[href*='/products/']")
        logger.info(f"상품 링크 수: {len(all_links)}")

        # 전체 클래스 덤프 (상위 구조 파악)
        body = soup.find("body")
        if body:
            top_divs = body.find_all(["div", "ul", "section"], recursive=False)
            for div in top_divs[:10]:
                cls = div.get("class", [])
                child_count = len(div.find_all(["li", "div", "a"], recursive=False))
                if cls:
                    logger.info(f"최상위 요소: <{div.name} class='{' '.join(cls)}'> 자식 {child_count}개")

        # ── 전략 1: 상품 링크 기반 파싱 (가장 확실) ──
        seen_ids = set()
        for link in all_links:
            href = link.get("href", "")

            # product_id 추출
            match = re.search(r'/products/(\d+)', href)
            if not match:
                continue
            pid = match.group(1)
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            # 링크를 감싸는 상위 컨테이너 찾기
            container = link
            for _ in range(5):
                parent = container.parent
                if parent and parent.name in ['li', 'div', 'article']:
                    # 컨테이너가 가격이나 이미지를 포함하면 이게 카드
                    if parent.find("img") or parent.select_one("[class*='price']"):
                        container = parent
                        break
                    container = parent
                else:
                    break

            product = {
                "product_id": pid,
                "product_url": f"https://www.coupang.com{href}" if href.startswith("/") else href,
            }

            # 상품명: 링크 내부 텍스트 또는 컨테이너 내 텍스트
            name_candidates = []
            # 컨테이너 내 모든 텍스트 요소
            for el in container.find_all(["span", "div", "p", "dd", "em", "strong"]):
                cls = " ".join(el.get("class", []))
                text = el.get_text(strip=True)
                if text and len(text) > 5 and not re.match(r'^[\d,%원]+$', text):
                    if any(k in cls.lower() for k in ["name", "title", "description", "product"]):
                        name_candidates.insert(0, text)  # 우선순위 높음
                    elif len(text) > 10:
                        name_candidates.append(text)

            product["product_name"] = name_candidates[0] if name_candidates else link.get_text(strip=True)[:100]

            # 이미지
            img = container.find("img")
            if img:
                src = img.get("src") or img.get("data-img-src") or img.get("data-src") or ""
                if src.startswith("//"):
                    src = "https:" + src
                product["image_url"] = src
            else:
                product["image_url"] = ""

            # 가격: 컨테이너 내 숫자 패턴 찾기
            prices = []
            for el in container.find_all(["span", "strong", "em", "del", "div"]):
                cls = " ".join(el.get("class", []))
                text = el.get_text(strip=True)
                num = extract_number(text)
                if 100 <= num <= 100000000:  # 100원 ~ 1억원
                    is_original = any(k in cls.lower() for k in ["base", "origin", "before", "regular"]) or el.name == "del"
                    is_sale = any(k in cls.lower() for k in ["sale", "discount", "final", "price-value"])
                    prices.append({"value": num, "is_original": is_original, "is_sale": is_sale, "cls": cls})

            if prices:
                orig_prices = [p["value"] for p in prices if p["is_original"]]
                sale_prices = [p["value"] for p in prices if p["is_sale"]]

                if orig_prices:
                    product["original_price"] = max(orig_prices)
                if sale_prices:
                    product["sale_price"] = min(sale_prices)

                # 명시적 구분 못 한 경우: 큰 값=원가, 작은 값=할인가
                if not orig_prices and not sale_prices and len(prices) >= 2:
                    vals = sorted(set(p["value"] for p in prices), reverse=True)
                    product["original_price"] = vals[0]
                    product["sale_price"] = vals[1] if len(vals) > 1 else vals[0]
                elif not orig_prices and sale_prices:
                    product["original_price"] = product.get("sale_price", 0)
                elif orig_prices and not sale_prices:
                    product["sale_price"] = product.get("original_price", 0)

            product.setdefault("original_price", 0)
            product.setdefault("sale_price", 0)

            # 할인율
            discount_el = container.find(lambda tag: tag.name in ["span", "div", "em", "strong"] and
                any(k in " ".join(tag.get("class", [])).lower() for k in ["discount", "rate", "percent", "badge"]))
            if discount_el:
                product["discount_rate"] = min(extract_number(discount_el.get_text()), 100)
            elif product["original_price"] > 0 and product["sale_price"] > 0:
                product["discount_rate"] = round((1 - product["sale_price"] / product["original_price"]) * 100)
            else:
                product["discount_rate"] = 0

            # 소진율
            sold_rate = 0
            # style width 기반
            progress = container.select_one("[class*='gauge'] [style*='width'], [class*='progress'] [style*='width'], [class*='sold'] [style*='width']")
            if progress:
                style = progress.get("style", "")
                match = re.search(r'width:\s*([\d.]+)%', style)
                if match:
                    sold_rate = round(float(match.group(1)))

            # 텍스트 기반
            if not sold_rate:
                sold_el = container.find(lambda tag: tag.name in ["span", "div", "em"] and
                    any(k in " ".join(tag.get("class", [])).lower() for k in ["sold", "gauge", "progress", "qty"]))
                if sold_el:
                    sold_rate = min(extract_number(sold_el.get_text()), 100)

            product["sold_rate"] = sold_rate

            # 브랜드
            brand_el = container.find(lambda tag: tag.name in ["span", "div", "em"] and
                any(k in " ".join(tag.get("class", [])).lower() for k in ["brand"]))
            product["brand_name"] = brand_el.get_text(strip=True) if brand_el else ""

            # 카테고리
            cat_el = container.find(lambda tag: tag.name in ["span", "div"] and
                any(k in " ".join(tag.get("class", [])).lower() for k in ["category", "type", "badge-text"]))
            product["category"] = cat_el.get_text(strip=True) if cat_el else ""

            products.append(product)
            logger.info(f"  상품 수집: {pid} | {product['product_name'][:30]} | {product['sale_price']}원 | 소진 {product['sold_rate']}%")

        # ── 전략 2: JavaScript로 직접 추출 시도 ──
        if not products:
            logger.info("HTML 파싱 실패, JavaScript로 재시도")
            try:
                js_products = driver.execute_script("""
                    var items = [];
                    document.querySelectorAll('a[href*="/products/"]').forEach(function(a) {
                        var m = a.href.match(/\\/products\\/(\\d+)/);
                        if (!m) return;
                        var card = a.closest('li') || a.closest('div');
                        if (!card) return;
                        var img = card.querySelector('img');
                        var texts = Array.from(card.querySelectorAll('*')).map(function(e){return {t:e.textContent.trim(), c:e.className}});
                        items.push({
                            pid: m[1],
                            href: a.href,
                            img: img ? (img.src || img.dataset.imgSrc || '') : '',
                            html: card.innerHTML.substring(0, 3000),
                            texts: texts.slice(0, 30)
                        });
                    });
                    return items;
                """)
                logger.info(f"JS로 {len(js_products or [])}개 요소 발견")

                if js_products:
                    # 첫 번째 아이템의 구조 로깅
                    logger.info(f"샘플 데이터: {json.dumps(js_products[0].get('texts', [])[:10], ensure_ascii=False)[:500]}")

            except Exception as e:
                logger.error(f"JS 추출 실패: {e}")

        logger.info(f"총 {len(products)}개 상품 수집 완료")

        # 디버깅용: 파싱 실패 시 HTML 저장
        if len(products) < 5:
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"디버깅용 HTML 저장: {debug_path}")

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
            # 1. goldbox_products 테이블 upsert
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

            # 2. goldbox_snapshots 테이블에 스냅샷 추가
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
