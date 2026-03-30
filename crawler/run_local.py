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


def classify_category(name):
    """제품명 키워드 기반 카테고리 자동 분류"""
    n = name.lower()

    # 신선식품 (로켓프레시 품목)
    fresh_kw = ['우유', '계란', '달걀', '두부', '요거트', '요구르트', '치즈', '버터', '생수',
                '과일', '채소', '야채', '고기', '돼지', '소고기', '닭', '연어', '새우', '오징어',
                '김치', '된장', '고추장', '반찬', '두유', '주스', '스무디', '샐러드']
    for kw in fresh_kw:
        if kw in n:
            return '신선식품'

    # 푸드 (가공식품, 음료, 커피 등)
    food_kw = ['커피', '아메리카노', '라떼', '음료', '차', '물', '콜라', '사이다', '맥주',
               '과자', '초콜릿', '젤리', '캔디', '빵', '케이크', '쿠키', '시리얼', '그래놀라',
               '라면', '국수', '파스타', '소스', '짜장', '카레', '밥', '죽', '떡', '만두',
               '참치', '햄', '소시지', '닭가슴살', '프로틴', '단백질', '견과', '아몬드', '호두',
               '김', '미역', '멸치', '꿀', '잼', '오일', '식초', '설탕', '소금', '후추',
               '비타민', '영양제', '유산균', '콜라겐', '홍삼', '오메가', '철분', '칼슘', '마그네슘',
               '건강기능식품', '보충제', '다이어트', '밀키트', '냉동', '즉석', '통조림',
               '믹스', '분말', '티백', '캡슐커피', '드립', '원두', '에너지바',
               '탄산', '이온', '스포츠음료', '식이섬유', '효소', '루테인']
    for kw in food_kw:
        if kw in n:
            return '푸드'

    # 여행/티켓
    travel_kw = ['입장권', '이용권', '여행', '호텔', '숙박', '리조트', '항공', '티켓',
                 '파크', '놀이공원', '아쿠아리움', '워터파크', '스파', '골프', '캠핑']
    for kw in travel_kw:
        if kw in n:
            return '여행/티켓'

    # 가전/디지털
    tech_kw = ['노트북', '태블릿', '아이패드', '갤럭시탭', '스마트폰', '이어폰', '헤드폰',
               '블루투스', '스피커', '충전기', '케이블', '보조배터리', '키보드', '마우스',
               'tv', '모니터', '냉장고', '세탁기', '에어컨', '건조기', '청소기', '공기청정기',
               '가습기', '제습기', '전자레인지', '오븐', '인덕션', '밥솥', '정수기',
               'led', '조명', '전구', '카메라', 'usb', 'ssd', '하드', '메모리',
               '드라이기', '헤어', '다리미', '면도기', '전동칫솔', '체중계', '혈압계',
               '로봇청소기', '무선청소기', '스팀', '안마기', '마사지']
    for kw in tech_kw:
        if kw in n:
            return '가전/디지털'

    # 패션/뷰티
    fashion_kw = ['티셔츠', '바지', '원피스', '자켓', '코트', '점퍼', '패딩', '셔츠',
                  '양말', '속옷', '브라', '팬티', '런닝', '레깅스', '스포츠', '운동화', '신발',
                  '가방', '백팩', '지갑', '벨트', '모자', '선글라스', '시계', '악세사리',
                  '화장', '스킨', '로션', '크림', '세럼', '에센스', '파운데이션', '립',
                  '마스카라', '아이', '섀도우', '클렌징', '선크림', '자외선', '썬',
                  '샴푸', '컨디셔너', '트리트먼트', '바디워시', '바디로션', '핸드크림',
                  '향수', '디퓨저', '탈모', '염색', '왁스', '젤', '데오도란트',
                  '네일', '패드', '마스크팩', '필링', '토너', '앰플', '미스트',
                  '설화수', '아이크림', '뷰티', 'beauty']
    for kw in fashion_kw:
        if kw in n:
            return '패션/뷰티'

    # 펫/유아/레저
    pet_baby_kw = ['강아지', '고양이', '펫', '사료', '간식', '장난감', '목줄', '하네스',
                   '캣타워', '고양이모래', '모래', '패드', '배변', '애견',
                   '기저귀', '분유', '젖병', '유모차', '카시트', '보행기', '아기', '유아',
                   '캠핑', '텐트', '등산', '자전거', '킥보드', '스케이트', '수영',
                   '낚시', '운동기구', '덤벨', '요가', '매트', '폼롤러']
    for kw in pet_baby_kw:
        if kw in n:
            return '펫/유아/레저'

    # 완구/문구/도서
    toy_kw = ['레고', '블록', '퍼즐', '인형', '피규어', '보드게임', '장난감', '완구',
              '색연필', '크레파스', '스케치북', '노트', '필기', '펜', '볼펜', '연필',
              '도서', '책', '소설', '만화', '학습지', '교재', '다이어리', '플래너', '스티커']
    for kw in toy_kw:
        if kw in n:
            return '완구/문구/도서'

    # 홈/키친
    home_kw = ['수건', '타올', '이불', '베개', '매트리스', '침대', '커튼', '러그', '카펫',
               '수납', '선반', '행거', '옷걸이', '바구니', '정리함', '서랍',
               '냄비', '프라이팬', '도마', '칼', '그릇', '접시', '컵', '텀블러', '보온병',
               '밀폐용기', '지퍼백', '랩', '호일', '쓰레기봉투', '빨래', '건조대']
    for kw in home_kw:
        if kw in n:
            return '홈/키친'

    # 생활용품 (기본)
    living_kw = ['세제', '섬유유연제', '표백', '세정', '청소', '걸레', '수세미', '스펀지',
                 '휴지', '화장지', '키친타올', '물티슈', '티슈', '면봉', '칫솔', '치약',
                 '비누', '핸드워시', '손세정', '방향제', '탈취', '습기제거',
                 '배터리', '건전지', '전선', '멀티탭', '우산', '장갑', '마스크',
                 '보관', '포장', '테이프', '접착', '공구', '드릴', '못']
    for kw in living_kw:
        if kw in n:
            return '생활용품'

    return '기타'


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
            var cardHTML = card.innerHTML || '';
            var cardText = card.innerText || '';
            // 1. 전체 HTML에서 로켓 관련 텍스트 검색 (img alt, title, 텍스트 모두 포함)
            var allText = cardText + ' ' + cardHTML;
            if (allText.indexOf('로켓프레시') !== -1) deliveryType = '로켓프레시';
            else if (allText.indexOf('로켓직구') !== -1 || allText.indexOf('로켓 직구') !== -1) deliveryType = '로켓직구';
            else if (allText.indexOf('판매자로켓') !== -1 || allText.indexOf('판매자 로켓') !== -1 || allText.indexOf('로켓그로스') !== -1) deliveryType = '판매자로켓';
            else if (allText.indexOf('로켓내일') !== -1 || allText.indexOf('로켓 내일') !== -1) deliveryType = '로켓내일';
            else if (allText.indexOf('로켓와우') !== -1) deliveryType = '로켓와우';
            else if (allText.indexOf('로켓배송') !== -1) deliveryType = '로켓배송';
            // 2. img alt 속성 개별 체크
            if (!deliveryType) {
                card.querySelectorAll('img').forEach(function(img) {
                    if (deliveryType) return;
                    var alt = (img.alt || '').toLowerCase();
                    var src = (img.src || '').toLowerCase();
                    var combo = alt + ' ' + src;
                    if (combo.indexOf('프레시') !== -1 || combo.indexOf('fresh') !== -1) deliveryType = '로켓프레시';
                    else if (combo.indexOf('직구') !== -1 || combo.indexOf('global') !== -1) deliveryType = '로켓직구';
                    else if (combo.indexOf('판매자') !== -1 || combo.indexOf('seller') !== -1 || combo.indexOf('그로스') !== -1 || combo.indexOf('growth') !== -1) deliveryType = '판매자로켓';
                    else if (combo.indexOf('내일') !== -1 || combo.indexOf('tomorrow') !== -1) deliveryType = '로켓내일';
                    else if (combo.indexOf('와우') !== -1 || combo.indexOf('wow') !== -1) deliveryType = '로켓와우';
                    else if (combo.indexOf('로켓') !== -1 || combo.indexOf('rocket') !== -1) deliveryType = '로켓배송';
                });
            }
            if (!deliveryType) deliveryType = '일반배송';

            // 제품명 추출 개선: 노이즈 라인 제거 후 가장 긴 한국어 라인 우선
            var skipWords = ['R.LUX', 'R.LUX혜택', '남음', '판매됨', '로켓', '혜택'];
            var candidates = lines.filter(function(l) {
                var t = l.trim();
                if (t.length < 5) return false;
                if (/^[\\d,%원\\s.\\-]+$/.test(t)) return false;
                if (/^\\d{1,2}:\\d{2}/.test(t)) return false;
                if (/^\\d{1,3}%/.test(t)) return false;
                for (var si = 0; si < skipWords.length; si++) {
                    if (t === skipWords[si]) return false;
                }
                return true;
            });
            // 한국어 포함 + 콤마/단위 있는 라인 우선 (진짜 제품명)
            var productName = '';
            var brandFromCard = '';
            var koreanLines = candidates.filter(function(l) { return /[가-힣]/.test(l); });
            var specLines = koreanLines.filter(function(l) { return /[,]/.test(l) || /\\d+(ml|g|kg|L|개|매|입|정|포|ea)/i.test(l); });
            if (specLines.length > 0) {
                productName = specLines.reduce(function(a, b) { return a.length >= b.length ? a : b; });
            } else if (koreanLines.length > 0) {
                productName = koreanLines.reduce(function(a, b) { return a.length >= b.length ? a : b; });
            } else if (candidates.length > 0) {
                productName = candidates.reduce(function(a, b) { return a.length >= b.length ? a : b; });
            }
            // 브랜드 추출: 제품명 아닌 짧은 후보 라인 (영문 브랜드명 등)
            candidates.forEach(function(l) {
                if (l !== productName && !brandFromCard && l.length < 30) {
                    brandFromCard = l.trim();
                }
            });

            results.push({
                pid: m[1], href: href, img: imgSrc,
                name: productName,
                brandFromCard: brandFromCard,
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

                name = (item.get("name") or "")[:200]
                # 브랜드: 카드에서 추출한 것 우선, 없으면 상품명 첫 단어
                brand = item.get("brandFromCard", "")
                if not brand:
                    brand = name.strip().split()[0] if name.strip() else ""

                # 카테고리 자동 분류 (제품명 키워드 기반)
                category = classify_category(name)

                products.append({
                    "product_id": pid,
                    "product_name": name,
                    "image_url": item.get("img", ""),
                    "product_url": item.get("href", ""),
                    "original_price": orig,
                    "sale_price": sale,
                    "discount_rate": min(discount, 100),
                    "sold_rate": item.get("soldRate", 0),
                    "delivery_type": item.get("deliveryType", ""),
                    "brand_name": brand,
                    "category": category,
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
