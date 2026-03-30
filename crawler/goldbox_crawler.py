"""
쿠팡 골드박스 크롤러
- Selenium (headless Chrome)으로 골드박스 페이지 크롤링
- 상품 정보 + 소진율 수집
- Supabase에 저장
"""

import os
import sys
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


def scroll_to_load_all(driver, max_scrolls=20):
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


def parse_products(html):
    """HTML에서 골드박스 상품 정보 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # 골드박스 상품 카드 선택자들 (쿠팡 구조 변경 대비 여러 패턴)
    selectors = [
        "ul.goldbox-product-list li",
        "div.goldbox-item",
        "li.baby-product",
        "div[class*='goldbox'] li",
        "ul[class*='product'] li.baby-product",
        "div.unit-item",
    ]

    items = []
    for selector in selectors:
        items = soup.select(selector)
        if items:
            logger.info(f"선택자 '{selector}'로 {len(items)}개 상품 발견")
            break

    if not items:
        # 폴백: 일반적인 상품 카드 패턴
        items = soup.select("li[class*='product'], div[class*='item']")
        if items:
            logger.info(f"폴백 선택자로 {len(items)}개 요소 발견")

    for item in items:
        try:
            product = parse_single_product(item)
            if product and product.get("product_id"):
                products.append(product)
        except Exception as e:
            logger.warning(f"상품 파싱 실패: {e}")
            continue

    return products


def parse_single_product(item):
    """단일 상품 카드에서 정보 추출"""
    product = {}

    # 상품 링크 + ID
    link = item.select_one("a[href*='/products/'], a[href*='/vp/products/']")
    if link:
        href = link.get("href", "")
        product["product_url"] = f"https://www.coupang.com{href}" if href.startswith("/") else href
        # URL에서 product_id 추출
        parts = href.split("/products/")
        if len(parts) > 1:
            pid = parts[1].split("?")[0].split("/")[0]
            product["product_id"] = pid

    if not product.get("product_id"):
        # data-product-id 속성 확인
        pid = item.get("data-product-id") or item.get("data-item-id")
        if pid:
            product["product_id"] = str(pid)
        else:
            return None

    # 상품명
    name_el = (
        item.select_one("[class*='name'], [class*='title']") or
        item.select_one("div.descriptions, span.descriptions") or
        item.select_one("dd.descriptions")
    )
    product["product_name"] = name_el.get_text(strip=True) if name_el else ""

    # 브랜드명
    brand_el = item.select_one("[class*='brand']")
    product["brand_name"] = brand_el.get_text(strip=True) if brand_el else ""

    # 카테고리
    cat_el = item.select_one("[class*='category'], [class*='type']")
    product["category"] = cat_el.get_text(strip=True) if cat_el else ""

    # 이미지
    img = item.select_one("img")
    if img:
        product["image_url"] = img.get("src") or img.get("data-img-src") or ""
        if product["image_url"].startswith("//"):
            product["image_url"] = "https:" + product["image_url"]

    # 가격 정보
    product["original_price"] = extract_price(item, ["[class*='base-price']", "[class*='origin']", "del", "span.base-price"])
    product["sale_price"] = extract_price(item, ["[class*='sale-price']", "[class*='discount']", "strong[class*='price']", "em.sale"])

    # 할인율
    discount_el = item.select_one("[class*='discount-rate'], [class*='discount-percentage'], span.discount-rate")
    if discount_el:
        text = discount_el.get_text(strip=True).replace("%", "").replace("-", "")
        product["discount_rate"] = parse_int(text)
    elif product.get("original_price") and product.get("sale_price") and product["original_price"] > 0:
        product["discount_rate"] = round((1 - product["sale_price"] / product["original_price"]) * 100)
    else:
        product["discount_rate"] = 0

    # 소진율
    sold_el = item.select_one(
        "[class*='sold-rate'], [class*='progress'], [class*='gauge'], "
        "span.sold-rate, div.progress-bar"
    )
    if sold_el:
        # 텍스트에서 숫자 추출
        text = sold_el.get_text(strip=True).replace("%", "")
        product["sold_rate"] = parse_int(text)
        # style width로 소진율 추정
        if not product.get("sold_rate"):
            style = sold_el.get("style", "")
            if "width" in style:
                width_val = style.split("width")[1].split("%")[0].replace(":", "").strip()
                product["sold_rate"] = parse_int(width_val)
    else:
        product["sold_rate"] = 0

    return product


def extract_price(item, selectors):
    """여러 선택자로 가격 추출 시도"""
    for sel in selectors:
        el = item.select_one(sel)
        if el:
            text = el.get_text(strip=True).replace(",", "").replace("원", "")
            val = parse_int(text)
            if val > 0:
                return val
    return 0


def parse_int(text):
    """문자열에서 정수 추출"""
    digits = "".join(c for c in str(text) if c.isdigit())
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
                    time.sleep(0.5)
                except Exception:
                    pass
        except Exception:
            pass

        # 스크롤하여 모든 상품 로드
        scroll_to_load_all(driver)

        # HTML 파싱
        html = driver.page_source
        products = parse_products(html)

        # 파싱 실패 시 디버깅용 HTML 일부 저장
        if not products:
            logger.warning("상품을 찾지 못했습니다. 페이지 구조 확인 필요")
            # 디버깅: HTML 일부 출력
            soup = BeautifulSoup(html, "html.parser")
            body_classes = [tag.get("class", []) for tag in soup.find_all(True, limit=50)]
            logger.info(f"페이지 상위 요소 클래스: {body_classes[:20]}")

            # 모든 링크에서 /products/ 패턴 찾기
            all_links = soup.select("a[href*='/products/']")
            logger.info(f"/products/ 링크 수: {len(all_links)}")

            if all_links:
                # 링크의 부모 요소를 기준으로 재파싱 시도
                for link in all_links:
                    parent = link.find_parent("li") or link.find_parent("div")
                    if parent:
                        p = parse_single_product(parent)
                        if p and p.get("product_id"):
                            products.append(p)

        logger.info(f"총 {len(products)}개 상품 수집 완료")

    except Exception as e:
        logger.error(f"크롤링 중 오류: {e}")
    finally:
        driver.quit()

    return products


def save_to_supabase(products):
    """Supabase에 데이터 저장"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.error("Supabase 환경변수가 설정되지 않았습니다")
        logger.info("SUPABASE_URL, SUPABASE_SERVICE_KEY 환경변수를 확인하세요")
        # 로컬 테스트용: JSON으로 저장
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

            # 기존 상품인지 확인
            existing = supabase.table("goldbox_products").select("product_id").eq(
                "product_id", p["product_id"]
            ).execute()

            if existing.data:
                # 기존 상품: last_seen_date만 업데이트
                supabase.table("goldbox_products").update({
                    "product_name": product_data["product_name"],
                    "brand_name": product_data["brand_name"],
                    "category": product_data["category"],
                    "image_url": product_data["image_url"],
                    "last_seen_date": today,
                }).eq("product_id", p["product_id"]).execute()
            else:
                # 신규 상품: first_seen_date 포함 삽입
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
