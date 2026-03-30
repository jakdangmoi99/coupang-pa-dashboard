"""
로컬 Mac에서 실행하는 골드박스 크롤러
- 실제 크롬 브라우저 창이 뜸 (headless 아님 → 봇 감지 우회)
- 페이지네이션(next 버튼) 지원 — 전체 100개 수집
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
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://jzzwrlvtospuwntnafhn.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
KST = timezone(timedelta(hours=9))


def create_driver():
    options = Options()
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


def extract_page_products(driver):
    """현재 페이지에서 상품 추출"""
    return driver.execute_script("""
        var results = [];
        document.querySelectorAll('a').forEach(function(a) {
            var href = a.href || '';
            var m = href.match(/\\/products\\/(\\d+)/);
            if (!m) return;

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

            var deliveryType = '';
            var cardText = card.innerText || '';
            if (cardText.indexOf('로켓프레시') !== -1) deliveryType = '로켓프레시';
            else if (cardText.indexOf('로켓와우') !== -1) deliveryType = '로켓배송';
            else if (cardText.indexOf('로켓배송') !== -1) deliveryType = '로켓배송';
            else if (cardText.indexOf('판매자로켓') !== -1 || cardText.indexOf('로켓그로스') !== -1) deliveryType = '판매자로켓';
            else deliveryType = '일반택배';

            results.push({
                pid: m[1], href: href, img: imgSrc,
                name: lines.find(function(l) { return l.length > 5 && !/^[\\d,%원\\s]+$/.test(l); }) || '',
                prices: prices, soldRate: soldRate, discountRate: discountRate,
                deliveryType: deliveryType
            });
        });
        return results;
    """) or []


def crawl():
    logger.info("골드박스 크롤링 시작 (로컬)")
    driver = create_driver()
    products = []
    seen_ids = set()

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

        # 2. 스크롤하면서 동시에 상품 수집 (가상 리스트 대응)
        #    쿠팡은 화면 밖 상품을 DOM에서 제거하므로, 스크롤 중 수집해야 함
        logger.info("스크롤하면서 상품 수집 시작")
        scroll_pos = 0
        max_height = driver.execute_script("return document.body.scrollHeight")

        while scroll_pos < max_height + 3000:
            # 현재 화면에 보이는 상품 수집
            js_items = extract_page_products(driver)
            new_in_batch = 0
            for item in js_items:
                pid = item["pid"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                new_in_batch += 1

                prices = sorted(set(item.get("prices", [])), reverse=True)
                orig = prices[0] if prices else 0
                sale = prices[1] if len(prices) > 1 else (prices[0] if prices else 0)
                discount = item.get("discountRate", 0)
                if not discount and orig > sale > 0:
                    discount = round((1 - sale / orig) * 100)

                products.append({
                    "product_id": pid,
                    "product_name": (item.get("name") or "")[:200],
                    "image_url": item.get("img", ""),
                    "product_url": item.get("href", ""),
                    "original_price": orig,
                    "sale_price": sale,
                    "discount_rate": min(discount, 100),
                    "sold_rate": item.get("soldRate", 0),
                    "delivery_type": item.get("deliveryType", ""),
                    "brand_name": "",
                    "category": "",
                })

            if new_in_batch > 0:
                logger.info(f"  +{new_in_batch}개 수집 (누적 {len(products)}개) @ {scroll_pos}px")

            # 조금씩 스크롤
            scroll_pos += random.randint(300, 500)
            driver.execute_script(f"window.scrollTo(0, {scroll_pos});")
            time.sleep(random.uniform(0.3, 0.6))

            # 높이 갱신
            if scroll_pos % 2000 < 500:
                max_height = driver.execute_script("return document.body.scrollHeight")

        logger.info(f"스크롤 완료 — 총 {len(products)}개 수집")

        # 이미 위에서 수집 완료, 아래 루프는 건너뜀
        js_items = []
        for item in js_items:
            pid = item["pid"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            prices = sorted(set(item.get("prices", [])), reverse=True)
            orig = prices[0] if prices else 0
            sale = prices[1] if len(prices) > 1 else (prices[0] if prices else 0)
            discount = item.get("discountRate", 0)
            if not discount and orig > sale > 0:
                discount = round((1 - sale / orig) * 100)

            products.append({
                "product_id": pid,
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
        logger.info("실행: SUPABASE_SERVICE_KEY='your-key' python3 run_local.py")
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

    for idx, p in enumerate(products):
        try:
            existing = supabase.table("goldbox_products").select("product_id").eq("product_id", p["product_id"]).execute()
            if existing.data:
                supabase.table("goldbox_products").update({
                    "product_name": p["product_name"], "brand_name": p["brand_name"],
                    "category": p["category"], "image_url": p["image_url"],
                    "delivery_type": p.get("delivery_type", ""), "last_seen_date": today,
                }).eq("product_id", p["product_id"]).execute()
            else:
                supabase.table("goldbox_products").insert({
                    "product_id": p["product_id"], "product_name": p["product_name"],
                    "brand_name": p["brand_name"], "category": p["category"],
                    "image_url": p["image_url"], "product_url": p["product_url"],
                    "delivery_type": p.get("delivery_type", ""),
                    "first_seen_date": today, "last_seen_date": today,
                }).execute()

            supabase.table("goldbox_snapshots").insert({
                "product_id": p["product_id"], "crawled_at": now,
                "original_price": p["original_price"], "sale_price": p["sale_price"],
                "discount_rate": p["discount_rate"], "sold_rate": p["sold_rate"],
                "crawl_order": idx + 1,
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
