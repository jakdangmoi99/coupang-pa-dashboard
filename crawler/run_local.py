"""
로컬 Mac에서 실행하는 골드박스 크롤러
- 실제 크롬 브라우저 창이 뜸 (headless 아님 → 봇 감지 우회)
- macOS launchd로 자동 실행 가능
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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# ── Supabase 설정 (여기에 직접 입력하거나 환경변수로) ──
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://jzzwrlvtospuwntnafhn.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
KST = timezone(timedelta(hours=9))


def create_driver():
    """실제 크롬 창으로 열기 (봇 감지 안 됨)"""
    options = Options()
    # headless 아님 — 실제 브라우저 창이 뜸
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return driver


def extract_number(text):
    if not text:
        return 0
    digits = re.sub(r'[^\d]', '', str(text))
    return int(digits) if digits else 0


def crawl():
    logger.info("골드박스 크롤링 시작 (로컬)")
    driver = create_driver()
    products = []

    try:
        # 1. 메인 → 골드박스
        driver.get("https://www.coupang.com")
        time.sleep(random.uniform(3, 5))
        driver.get("https://www.coupang.com/np/goldbox")
        time.sleep(random.uniform(4, 6))

        logger.info(f"페이지: {driver.title}")

        if "Access Denied" in driver.title:
            logger.error("접근 차단!")
            driver.quit()
            return []

        # 2. 스크롤 — 끝까지 반복 (100개 전부 로드)
        prev_count = 0
        stale_rounds = 0
        for i in range(60):
            driver.execute_script(f"window.scrollBy(0, {random.randint(400,800)});")
            time.sleep(random.uniform(0.8, 1.5))
            # 상품 링크 수 체크
            cur_count = driver.execute_script("return document.querySelectorAll('a[href*=\"/products/\"]').length")
            if cur_count == prev_count:
                stale_rounds += 1
                if stale_rounds >= 5:
                    break
            else:
                stale_rounds = 0
                prev_count = cur_count
            if i % 10 == 0:
                logger.info(f"스크롤 {i}회, 상품 링크 {cur_count}개")
        logger.info(f"스크롤 완료 — 상품 링크 {prev_count}개")

        # 3. DOM에서 상품 추출
        js_products = driver.execute_script("""
            var results = [];
            var seen = {};
            document.querySelectorAll('a').forEach(function(a) {
                var href = a.href || '';
                var m = href.match(/\\/products\\/(\\d+)/);
                if (!m || seen[m[1]]) return;
                seen[m[1]] = true;

                var card = a;
                for (var i = 0; i < 8; i++) {
                    if (!card.parentElement) break;
                    card = card.parentElement;
                    if (card.querySelector('img') && card.offsetHeight > 100) break;
                }

                var img = card.querySelector('img');
                var imgSrc = img ? (img.src || img.dataset.imgSrc || '') : '';

                var lines = (card.innerText || '').split('\\n').filter(function(l) { return l.trim(); });

                var prices = [];
                card.querySelectorAll('*').forEach(function(el) {
                    var t = (el.textContent || '').trim().replace(/,/g, '');
                    var nm = t.match(/^(\\d{3,10})원?$/);
                    if (nm) prices.push(parseInt(nm[1]));
                });

                var soldRate = 0;
                card.querySelectorAll('[style*="width"]').forEach(function(el) {
                    var wm = (el.style.width || '').match(/(\\d+)/);
                    if (wm) { var w = parseInt(wm[1]); if (w > 0 && w <= 100) soldRate = w; }
                });

                var discountRate = 0;
                card.querySelectorAll('*').forEach(function(el) {
                    var t = (el.textContent || '').trim();
                    if (/^-?\\d{1,2}%$/.test(t)) discountRate = parseInt(t.replace(/[^\\d]/g, ''));
                });

                results.push({
                    pid: m[1], href: href, img: imgSrc,
                    name: lines.find(function(l) { return l.length > 5 && !/^[\\d,%원\\s]+$/.test(l); }) || '',
                    prices: prices, soldRate: soldRate, discountRate: discountRate
                });
            });
            return results;
        """)

        logger.info(f"DOM에서 {len(js_products or [])}개 상품 발견")

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

        logger.info(f"수집 완료: {len(products)}개")
        for p in products[:5]:
            logger.info(f"  [{p['product_id']}] {p['product_name'][:30]} | {p['sale_price']}원")

    except Exception as e:
        logger.error(f"오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

    return products


def save_to_supabase(products):
    if not SUPABASE_SERVICE_KEY:
        logger.error("SUPABASE_SERVICE_KEY가 없습니다!")
        logger.info("실행 방법: SUPABASE_SERVICE_KEY='your-key' python3 run_local.py")
        # JSON으로 저장
        now = datetime.now(KST)
        path = os.path.join(os.path.dirname(__file__), f"goldbox_{now.strftime('%Y%m%d_%H%M')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"count": len(products), "products": products}, f, ensure_ascii=False, indent=2)
        logger.info(f"JSON 저장: {path}")
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

    logger.info(f"Supabase 저장: {ok}/{len(products)}개")


if __name__ == "__main__":
    products = crawl()
    if products:
        save_to_supabase(products)
    else:
        logger.warning("수집된 상품 없음")
        sys.exit(1)
