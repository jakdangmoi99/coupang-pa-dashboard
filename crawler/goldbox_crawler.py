"""
쿠팡 골드박스 크롤러 (통합 버전)
- 서버(GitHub Actions, headless) / 로컬(Mac, visible) 자동 감지
- 스크롤하면서 수집 (쿠팡 가상 리스트 대응)
- 배송타입 · 브랜드 · 카테고리 자동 분류
- Supabase 저장 + JSON 폴백
"""

import os
import sys
import re
import time
import json
import random
import logging

# .env 파일 로드 (로컬 실행용)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://daukcarsixncmzhlhwok.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
KST = timezone(timedelta(hours=9))

# 수동 브랜드 보정 사전 로드 (product_id → 올바른 브랜드명)
BRAND_OVERRIDES = {}
_overrides_path = os.path.join(os.path.dirname(__file__), "brand_overrides.json")
if os.path.exists(_overrides_path):
    try:
        with open(_overrides_path, "r", encoding="utf-8") as f:
            _raw = json.load(f)
        BRAND_OVERRIDES = {k: v for k, v in _raw.items() if not k.startswith("_")}
        if BRAND_OVERRIDES:
            logging.info(f"브랜드 보정 사전 로드: {len(BRAND_OVERRIDES)}개")
    except Exception:
        pass

# ============================================================
# 브랜드 사전 (주요 쿠팡 골드박스 출현 브랜드)
# ============================================================
KNOWN_BRANDS = {
    # 식품/음료
    '카누': '카누', 'kanu': '카누', '맥심': '맥심', '네스카페': '네스카페',
    '아카페라': '아카페라', '동서': '동서', '남양': '남양', '서울우유': '서울우유',
    '매일': '매일유업', '매일유업': '매일유업', '파스퇴르': '파스퇴르',
    '빙그레': '빙그레', '풀무원': '풀무원', '오뚜기': '오뚜기', 'ottogi': '오뚜기',
    '농심': '농심', 'nongshim': '농심', '삼양': '삼양', '팔도': '팔도',
    'cj': 'CJ', 'CJ': 'CJ', '비비고': 'CJ비비고', '햇반': 'CJ',
    '백설': 'CJ백설', '다시다': 'CJ', '스팸': 'CJ',
    '동원': '동원', '사조': '사조', '해찬들': 'CJ해찬들',
    '코카콜라': '코카콜라', '펩시': '펩시', '칠성': '롯데칠성',
    '웅진': '웅진', '하이트진로': '하이트진로', '카스': '카스',
    '광동': '광동', '정관장': '정관장', '종근당': '종근당',
    '에버콜라겐': '에버콜라겐', '뉴트리원': '뉴트리원', '뉴트리데이': '뉴트리데이',
    '안국건강': '안국건강', '일양약품': '일양약품', '고려은단': '고려은단',
    '락토핏': '종근당 락토핏', '차오차이': '차오차이',
    '롯데': '롯데', '해태': '해태', '크라운': '크라운', '오리온': '오리온',
    '허쉬': '허쉬', '페레로': '페레로', '린트': '린트',
    '곰곰': '곰곰', '탐사': '탐사', '코멧': '코멧',

    # 가전/디지털
    '삼성': '삼성', 'samsung': '삼성', '엘지': 'LG', 'lg': 'LG', 'LG': 'LG',
    '아이폰': 'Apple', '아이패드': 'Apple', '맥북': 'Apple', '에어팟': 'Apple', 'airpods': 'Apple',
    '소니': 'Sony', 'sony': 'Sony', '필립스': '필립스', 'philips': '필립스',
    '다이슨': '다이슨', 'dyson': '다이슨', '샤오미': '샤오미', 'xiaomi': '샤오미',
    '보쉬': '보쉬', 'bosch': '보쉬', 'braun': '브라운',
    '로보락': '로보락', 'roborock': '로보락', '에코백스': '에코백스',
    '앤커': '앤커', 'anker': '앤커', '바스큐': 'BASQ', '제이비엘': 'JBL', 'jbl': 'JBL',
    '쿠쿠': '쿠쿠', 'cuckoo': '쿠쿠', '위닉스': '위닉스',
    '일렉트로룩스': '일렉트로룩스', '드롱기': '드롱기',

    # 패션/뷰티
    '나이키': '나이키', 'nike': '나이키', '아디다스': '아디다스', 'adidas': '아디다스',
    '뉴발란스': '뉴발란스', 'new balance': '뉴발란스',
    '노스페이스': '노스페이스', '컬럼비아': '컬럼비아',
    '유니클로': '유니클로', 'uniqlo': '유니클로',
    '설화수': '설화수', '이니스프리': '이니스프리', '라네즈': '라네즈',
    '헤라': '헤라', '에스티로더': '에스티로더', '클리니크': '클리니크',
    '아모레퍼시픽': '아모레퍼시픽', 'amorepacific': '아모레퍼시픽',
    'laneige': '라네즈', '미샤': '미샤', '더페이스샵': '더페이스샵',
    '닥터지': '닥터지', '메디힐': '메디힐', '라로슈포제': '라로슈포제',
    '비오템': '비오템', '도브': '도브', 'dove': '도브',
    '케라시스': '케라시스', '려': '려', '팬틴': '팬틴',
    'clinique': '클리니크', 'sulwhasoo': '설화수', 'lancome': '랑콤', 'lancôme': '랑콤',
    'davines': '다비네스', 'dolce': 'Dolce&Gabbana', 'chanel': '샤넬', 'dior': '디올',

    # 생활/유아/펫
    '피죤': '피죤', '다우니': '다우니', '퍼실': '퍼실', '아이깨끗해': '아이깨끗해',
    '깨끗한나라': '깨끗한나라', '유한킴벌리': '유한킴벌리', '크리넥스': '크리넥스',
    '하기스': '하기스', '팸퍼스': '팸퍼스', '보솜이': '보솜이',
    '로얄캐닌': '로얄캐닌', '퓨리나': '퓨리나', '뉴트로': '뉴트로',
    '템퍼': '템퍼', '이케아': '이케아', 'ikea': '이케아',

    # 기타 자주 등장
    '3m': '3M', '3M': '3M', '듀라셀': '듀라셀',
}


# ============================================================
# 브랜드 → 카테고리 매핑 (키워드 분류보다 우선)
# ============================================================
BRAND_CATEGORY = {
    # 유아동
    '하기스': '유아동', '팸퍼스': '유아동', '보솜이': '유아동', '하기스네이처메이드': '유아동',
    '그린핑거': '유아동', '궁중비책': '유아동', '베베숲': '유아동', '에르고베이비': '유아동',
    '리빙코디': '유아동', '맘큐': '유아동', '보령': '유아동', '아이뱅크': '유아동',
    '누크': '유아동', '치코': '유아동', '닥터브라운': '유아동', '필립스아벤트': '유아동',
    # 생활용품
    '크리넥스': '생활용품', '깨끗한나라': '생활용품', '유한킴벌리': '생활용품',
    '피죤': '생활용품', '다우니': '생활용품', '퍼실': '생활용품', '스너글': '생활용품',
    '아이깨끗해': '생활용품', '테크': '생활용품', '비트': '생활용품',
    '코디': '생활용품', '바이엔클린': '생활용품', '쓱싹': '생활용품',
    '탐사': '생활용품', '프릴': '생활용품', '부쉬넬': '생활용품',
    # 홈/키친
    '테팔': '홈/키친', '이케아': '홈/키친', '템퍼': '홈/키친', '락앤락': '홈/키친',
    '글라스락': '홈/키친', '코렐': '홈/키친', '해피콜': '홈/키친', '키친아트': '홈/키친',
    '모던하우스': '홈/키친', '한샘': '홈/키친', '지누스': '홈/키친', '시몬스': '홈/키친',
    '에이스침대': '홈/키친', '일룸': '홈/키친',
    # 펫용품
    '로얄캐닌': '펫용품', '퓨리나': '펫용품', '뉴트로': '펫용품', '오리젠': '펫용품',
    '아카나': '펫용품', '하림펫푸드': '펫용품', '내추럴코어': '펫용품',
    # 가전/디지털
    '삼성': '가전/디지털', '엘지': '가전/디지털', '다이슨': '가전/디지털',
    '필립스': '가전/디지털', '보쉬': '가전/디지털', '드롱기': '가전/디지털',
    '일렉트로룩스': '가전/디지털', '브라운': '가전/디지털', '샤오미': '가전/디지털',
    '에코백스': '가전/디지털', '로보락': '가전/디지털',
    # 뷰티/헬스
    '설화수': '뷰티/헬스', '라네즈': '뷰티/헬스', '이니스프리': '뷰티/헬스',
    '에스티로더': '뷰티/헬스', '랑콤': '뷰티/헬스', '키엘': '뷰티/헬스',
    '닥터지': '뷰티/헬스', '메디힐': '뷰티/헬스', '아모레퍼시픽': '뷰티/헬스',
    # 스포츠/레저
    '나이키': '스포츠/레저', '아디다스': '스포츠/레저', '뉴발란스': '스포츠/레저',
    '휠라': '스포츠/레저', '콜맨': '스포츠/레저', '노스페이스': '스포츠/레저',
    '코베아': '스포츠/레저', '스노우피크': '스포츠/레저',
}


# ============================================================
# 카테고리 키워드 분류
# ============================================================
def classify_category(name):
    """제품명 키워드 기반 카테고리 자동 분류 (브랜드 매핑 우선)"""
    n = name.lower()

    # 최우선: 브랜드 → 카테고리 매핑
    for brand_kw, cat in BRAND_CATEGORY.items():
        if brand_kw.lower() in n:
            return cat

    # 신선식품 (로켓프레시 품목)
    fresh_kw = [
        '우유', '계란', '달걀', '두부', '요거트', '요구르트', '치즈', '버터', '생수',
        '과일', '채소', '야채', '고기', '돼지', '소고기', '닭고기', '닭가슴', '연어', '새우', '오징어',
        '김치', '된장', '고추장', '반찬', '두유', '샐러드',
        '복숭아', '포도', '사과', '딸기', '귤', '바나나', '토마토', '감자', '양파',
        '삼겹살', '목살', '갈비', '불고기', '한우', '돼지불백',
        '양배추', '브로콜리', '당근', '파프리카', '오이', '상추', '시금치',
        '천혜향', '한라봉', '레몬', '자몽', '키위', '망고', '수박', '참외', '멜론',
        '파인애플', '블루베리', '아보카도', '체리',
    ]
    for kw in fresh_kw:
        if kw in n:
            return '신선식품'

    # 푸드 (가공식품, 음료, 건강기능식품)
    food_kw = [
        '커피', '아메리카노', '라떼', '카푸치노', '에스프레소', '원두', '드립', '캡슐커피',
        '음료', '콜라', '사이다', '맥주', '소주', '와인',
        '주스', '스무디', '탄산', '이온', '스포츠음료', '에너지', '알카리',
        '과자', '초콜릿', '젤리', '캔디', '사탕', '빵', '케이크', '쿠키', '시리얼', '그래놀라',
        '라면', '국수', '파스타', '소스', '짜장', '카레', '밥', '죽', '떡', '만두', '밀키트',
        '참치', '햄', '소시지', '닭가슴살', '프로틴', '단백질', '견과', '아몬드', '호두', '땅콩',
        '김', '미역', '멸치', '꿀', '잼', '오일', '식초', '설탕', '소금', '후추', '양념',
        '비타민', '영양제', '유산균', '콜라겐', '홍삼', '오메가', '철분', '칼슘', '마그네슘',
        '건강기능식품', '보충제', '다이어트', '냉동', '즉석', '통조림',
        '믹스', '분말', '티백', '에너지바',
        '식이섬유', '효소', '루테인', '밀크씨슬', '크릴오일', '글루코사민',
        '마카', '아르기닌', '아연', '쏘팔메토', '프로바이오틱스',
        '인절미', '약과', '한과', '떡볶이', '순대', '어묵',
    ]
    for kw in food_kw:
        if kw in n:
            return '푸드'

    # 음료 단위 패턴 (ml, L 단위 + 개수 → 음료/식품 추정)
    if re.search(r'\d+\s*(ml|l|리터)', n, re.IGNORECASE) and re.search(r'\d+\s*(개|입|캔|병|팩)', n):
        return '푸드'

    # 여행/티켓
    travel_kw = [
        '입장권', '이용권', '여행', '호텔', '숙박', '리조트', '항공', '티켓',
        '파크', '놀이공원', '아쿠아리움', '워터파크', '스파', '골프', '캠핑장',
        '투어', '체험', '관람', '바우처',
    ]
    for kw in travel_kw:
        if kw in n:
            return '여행/티켓'

    # --- 충돌 방지: 구체적 카테고리를 먼저 검사 ---

    # 유아동 (기저귀/유아용품 → "팬티형 기저귀"가 패션으로 잡히지 않도록)
    baby_kw = [
        '기저귀', '분유', '젖병', '유모차', '카시트', '보행기', '아기', '유아',
        '이유식', '아기띠', '턱받이', '아기옷', '돌잔치', '유아용품',
        '워크북', '홈스쿨', '색칠놀이', '색칠',
    ]
    for kw in baby_kw:
        if kw in n:
            return '유아동'

    # 펫용품
    pet_kw = [
        '강아지', '고양이', '펫', '사료', '목줄', '하네스',
        '캣타워', '고양이모래', '배변패드', '배변', '애견', '반려',
        '펫푸드', '츄르', '강아지간식', '고양이간식',
    ]
    for kw in pet_kw:
        if kw in n:
            return '펫용품'

    # 생활용품 (화장지/물티슈 → "화장"이 뷰티로 잡히지 않도록)
    living_kw = [
        '세제', '섬유유연제', '표백', '세정', '청소', '걸레', '수세미', '스펀지',
        '휴지', '화장지', '키친타올', '물티슈', '티슈', '면봉',
        '비누', '핸드워시', '손세정', '방향제', '탈취', '습기제거',
        '배터리', '건전지', '전선', '멀티탭', '우산', '장갑', '마스크',
        '보관', '포장', '테이프', '접착', '공구', '드릴', '못',
        '방충', '살충', '모기', '해충', '쥐',
    ]
    for kw in living_kw:
        if kw in n:
            return '생활용품'

    # 홈/키친 (프라이팬/냄비 → "매트"가 스포츠로 잡히지 않도록)
    home_kw = [
        '수건', '타올', '이불', '베개', '매트리스', '침대', '커튼', '러그', '카펫',
        '수납', '선반', '행거', '옷걸이', '바구니', '정리함', '서랍',
        '냄비', '프라이팬', '도마', '칼', '그릇', '접시', '컵', '텀블러', '보온병',
        '밀폐용기', '지퍼백', '랩', '호일', '쓰레기봉투', '빨래', '건조대',
        '식기', '도자기', '유리컵', '머그컵', '와인잔',
        '토퍼', '메모리폼', '쿠션', '소파', '의자', '책상', '테이블',
        '수납장', '서랍장', '리빙박스', '정리함', '행거',
        '조리도구', '뒤집개', '국자', '집게', '행주', '그림', '액자',
    ]
    for kw in home_kw:
        if kw in n:
            return '홈/키친'

    # 가전/디지털
    tech_kw = [
        '노트북', '태블릿', '아이패드', '갤럭시탭', '스마트폰', '이어폰', '헤드폰', '헤드셋',
        '블루투스', '스피커', '충전기', '케이블', '보조배터리', '키보드', '마우스', '모니터',
        'tv', '냉장고', '세탁기', '에어컨', '건조기', '청소기', '공기청정기',
        '가습기', '제습기', '전자레인지', '오븐', '인덕션', '밥솥', '정수기',
        'led', '조명', '전구', '카메라', 'usb', 'ssd', '하드', '메모리카드',
        '드라이기', '헤어드라이', '고데기', '다리미', '면도기', '전동칫솔', '체중계', '혈압계',
        '로봇청소기', '무선청소기', '스팀', '안마기', '마사지건', '마사지기',
        '에어프라이어', '전기포트', '믹서기', '블렌더', '토스터', '커피머신',
        '선풍기', '히터', '온풍기', '전기장판', '전기매트',
        '빔프로젝터', '프로젝터', '태블릿pc', '스마트워치', '워치',
        '제빙기', '식기세척기', '전기밥솥', '음식물처리기',
    ]
    for kw in tech_kw:
        if kw in n:
            return '가전/디지털'

    # 패션의류/잡화 ("팬티"는 유아동에서 먼저 잡히므로 안전)
    fashion_kw = [
        '티셔츠', '반팔티', '반팔', '긴팔', '바지', '원피스', '자켓', '코트', '점퍼', '패딩',
        '셔츠', '블라우스', '니트', '가디건', '후드', '맨투맨', '조거팬츠', '청바지', '데님',
        '양말', '속옷', '브라', '팬티', '런닝', '레깅스', '스포츠웨어',
        '운동화', '신발', '슬리퍼', '샌들', '부츠', '구두', '스니커즈',
        '가방', '백팩', '지갑', '벨트', '모자', '선글라스', '시계', '악세사리',
        '크로스백', '토트백', '에코백', '캐리어', '여행가방',
        '크록스', '슬랙스', '스커트', '치마', '수영복', '래쉬가드',
        '나이키', '아디다스', '뉴발란스', '호보백', '클러치',
        '힙색', '웨이스트백', '휠라', '프로스펙스',
    ]
    for kw in fashion_kw:
        if kw in n:
            return '패션의류/잡화'

    # 뷰티/헬스 ("화장지"는 생활용품에서 먼저 잡히므로 안전)
    beauty_kw = [
        '화장품', '스킨', '로션', '크림', '세럼', '에센스', '파운데이션', '립', '립스틱',
        '마스카라', '아이라이너', '섀도우', '클렌징', '선크림', '자외선', '썬크림', '썬블록',
        '샴푸', '컨디셔너', '트리트먼트', '바디워시', '바디로션', '핸드크림',
        '향수', '디퓨저', '탈모', '염색', '왁스', '젤', '데오도란트', '데오드란트',
        '네일', '패드', '마스크팩', '필링', '토너', '앰플', '미스트',
        '아이크림', '뷰티', 'beauty', '스킨케어', '메이크업',
        '생리대', '탐폰', '팬티라이너', '여성용품',
        '칫솔', '치약', '구강', '가글', '치실',
    ]
    for kw in beauty_kw:
        if kw in n:
            return '뷰티/헬스'

    # 스포츠/레저 ("매트" → "요가매트"로 변경, 프라이팬 세트 오탐 방지)
    sports_kw = [
        '캠핑', '텐트', '등산', '자전거', '킥보드', '스케이트', '수영',
        '낚시', '운동기구', '덤벨', '요가', '요가매트', '폼롤러',
        '골프채', '골프공', '골프웨어', '배드민턴', '탁구', '축구', '농구',
        '런닝화', '트레킹', '등산화', '아웃도어',
        '스텝퍼', '실내자전거', '풀업바', '아쿠아슈즈',
    ]
    for kw in sports_kw:
        if kw in n:
            return '스포츠/레저'

    # 완구/문구/도서
    toy_kw = [
        '레고', '블록', '퍼즐', '인형', '피규어', '보드게임', '장난감', '완구',
        '색연필', '크레파스', '스케치북', '노트', '필기', '펜', '볼펜', '연필',
        '도서', '책', '소설', '만화', '학습지', '교재', '다이어리', '플래너', '스티커',
    ]
    for kw in toy_kw:
        if kw in n:
            return '완구/문구/도서'

    # g/kg 단위 + 개수 패턴 → 식품 추정
    if re.search(r'\d+\s*(g|kg)', n, re.IGNORECASE) and re.search(r'\d+\s*(개|입|봉|팩|세트)', n):
        return '푸드'

    return '기타'


# ============================================================
# 브랜드 추출
# ============================================================
def extract_brand(product_name, brand_from_card=""):
    """
    제품명에서 브랜드명 추출 (다중 전략)
    우선순위: 알려진 브랜드 사전 → 카드 DOM (필터링) → 제품명 패턴
    """
    name = product_name.strip()

    # 브랜드가 아닌 노이즈 단어 블랙리스트
    BRAND_BLACKLIST = {
        '사은품', '쿠폰할인', '쿠폰', '본사정품', '정품', '한정수량', '한정수량마감',
        '한정수량 마감', '당일발송', '무료배송', '즉시할인', '특가', '최저가', '오늘의특가',
        '카드할인', '카드추가할인', '추가할인', '적립', '포인트', '리뷰', '평점',
        '품절임박', '대용량', '한정', '묶음', '개', '세트', '팩', '박스',
    }

    # 브랜드에 붙는 불필요한 접미사 (띄어쓰기 없이 붙은 경우)
    BRAND_SUFFIXES = [
        '여성용', '남성용', '아동용', '유아용', '공용', '키즈', '주니어', '시니어',
        '세트', '패키지', '기획', '한정판', '리미티드', '에디션', '스페셜',
    ]

    def _clean_brand(b):
        """브랜드명에서 접미사 분리"""
        for suffix in BRAND_SUFFIXES:
            if b.endswith(suffix) and len(b) > len(suffix) + 1:
                return b[:-len(suffix)].strip()
        return b

    def _is_noise(text):
        """브랜드가 아닌 노이즈인지 체크"""
        t = text.strip()
        if t in BRAND_BLACKLIST or t.lower() in {x.lower() for x in BRAND_BLACKLIST}:
            return True
        # URL 패턴
        if re.search(r'https?://|www\.|\.com|\.co\.kr|coupang', t, re.I):
            return True
        # 숫자+단위만
        if re.match(r'^[\d,]+\s*(원|개|매|입|세트|팩|%|ml|g|kg|L)$', t, re.I):
            return True
        # 순수 숫자
        if t.replace(',', '').replace('.', '').isdigit():
            return True
        return False

    # 전략 1: 알려진 브랜드 사전 매칭
    name_lower = name.lower()
    for keyword, brand in KNOWN_BRANDS.items():
        if keyword in name_lower:
            return brand

    # 전략 2: DOM 카드에서 추출한 짧은 텍스트 (필터링 강화)
    if brand_from_card and len(brand_from_card) < 30:
        cleaned = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', brand_from_card).strip()
        if cleaned and len(cleaned) >= 2 and not _is_noise(cleaned):
            return _clean_brand(cleaned)

    # 전략 3: 대괄호/괄호 안 브랜드 "[브랜드]" or "(브랜드)"
    bracket = re.match(r'[\[\(]([가-힣a-zA-Z0-9\s]+)[\]\)]', name)
    if bracket:
        b = bracket.group(1).strip()
        if not _is_noise(b):
            return _clean_brand(b)

    # 전략 4: 제품명 첫 단어 (한글 2자 이상 or 영문 2자 이상)
    first_word = name.split()[0] if name.split() else ""
    cleaned = re.sub(r'[^가-힣a-zA-Z0-9]', '', first_word).strip()

    if len(cleaned) < 2:
        return ""
    if _is_noise(cleaned):
        return ""

    return _clean_brand(cleaned)


# ============================================================
# 데이터 품질 필터
# ============================================================
def extract_number(text):
    if not text:
        return 0
    digits = re.sub(r'[^\d]', '', str(text))
    return int(digits) if digits else 0


def is_valid_product(product):
    """플레이스홀더/무효 상품 필터링"""
    name = product.get("product_name", "")
    price = product.get("sale_price", 0)
    pid = product.get("product_id", "")

    if not pid or not pid.isdigit():
        return False
    if price <= 0:
        return False
    if not name or len(name) < 3:
        return False

    # 플레이스홀더 패턴
    placeholder = ['한정수량 마감', '품절', '판매종료', '일시품절', '재입고 예정']
    for p in placeholder:
        if p in name:
            return False

    return True


# ============================================================
# 드라이버 생성 (환경 자동 감지)
# ============================================================
def create_driver():
    """서버(headless) / 로컬(visible) 자동 감지"""
    is_headless = os.environ.get("HEADLESS", "").lower() in ("true", "1", "yes")
    is_server = not sys.platform.startswith("darwin")

    if is_server:
        is_headless = True

    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ko-KR")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    if is_headless:
        logger.info("모드: headless (서버)")
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        try:
            from fake_useragent import UserAgent
            ua = UserAgent(browsers=["chrome"], os=["linux"])
            options.add_argument(f"--user-agent={ua.random}")
        except Exception:
            options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            driver = webdriver.Chrome(options=options)
    else:
        logger.info("모드: visible (로컬)")
        driver = webdriver.Chrome(options=options)

    # 자동화 탐지 우회
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US', 'en']});
            window.chrome = {runtime: {}};
        """
    })

    if is_headless:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
            "headers": {
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
        })

    return driver


# ============================================================
# 상품 추출 JS (배송타입 + 브랜드 + 이름 정제 포함)
# ============================================================
EXTRACT_JS = """
var results = [];
var seen = {};
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

    var lines = (card.innerText || '').split('\\n').filter(function(l) { return l.trim(); });

    // 가격 추출
    var prices = [];
    card.querySelectorAll('*').forEach(function(el) {
        var t = (el.textContent || '').trim().replace(/,/g, '');
        var nm = t.match(/^(\\d{3,10})원?$/);
        if (nm) prices.push(parseInt(nm[1]));
    });

    // 소진율 (width style)
    var soldRate = 0;
    card.querySelectorAll('[style*="width"]').forEach(function(el) {
        var wm = (el.style.width || '').match(/(\\d+)/);
        if (wm) { var w = parseInt(wm[1]); if (w > 0 && w <= 100) soldRate = w; }
    });

    // 할인율
    var discountRate = 0;
    card.querySelectorAll('*').forEach(function(el) {
        var t = (el.textContent || '').trim();
        if (/^-?\\d{1,2}%$/.test(t)) discountRate = parseInt(t.replace(/[^\\d]/g, ''));
    });

    // === 배송타입 감지 ===
    var deliveryType = '';
    var cardHTML = card.innerHTML || '';
    var cardText = card.innerText || '';
    var allText = cardText + ' ' + cardHTML;

    if (allText.indexOf('로켓프레시') !== -1) deliveryType = '로켓프레시';
    else if (allText.indexOf('로켓직구') !== -1 || allText.indexOf('로켓 직구') !== -1) deliveryType = '로켓직구';
    else if (allText.indexOf('판매자로켓') !== -1 || allText.indexOf('판매자 로켓') !== -1 || allText.indexOf('로켓그로스') !== -1) deliveryType = '판매자로켓';
    else if (allText.indexOf('로켓내일') !== -1 || allText.indexOf('로켓 내일') !== -1) deliveryType = '로켓내일';
    else if (allText.indexOf('로켓와우') !== -1) deliveryType = '로켓와우';
    else if (allText.indexOf('로켓배송') !== -1) deliveryType = '로켓배송';

    // img alt/src 체크
    if (!deliveryType) {
        card.querySelectorAll('img').forEach(function(imgEl) {
            if (deliveryType) return;
            var combo = ((imgEl.alt || '') + ' ' + (imgEl.src || '')).toLowerCase();
            if (combo.indexOf('프레시') !== -1 || combo.indexOf('fresh') !== -1) deliveryType = '로켓프레시';
            else if (combo.indexOf('직구') !== -1 || combo.indexOf('global') !== -1) deliveryType = '로켓직구';
            else if (combo.indexOf('판매자') !== -1 || combo.indexOf('seller') !== -1 || combo.indexOf('그로스') !== -1 || combo.indexOf('growth') !== -1) deliveryType = '판매자로켓';
            else if (combo.indexOf('내일') !== -1 || combo.indexOf('tomorrow') !== -1) deliveryType = '로켓내일';
            else if (combo.indexOf('와우') !== -1 || combo.indexOf('wow') !== -1) deliveryType = '로켓와우';
            else if (combo.indexOf('로켓') !== -1 || combo.indexOf('rocket') !== -1) deliveryType = '로켓배송';
        });
    }
    if (!deliveryType) deliveryType = '일반배송';

    // === 제품명 추출 (노이즈 제거) ===
    var skipWords = ['R.LUX', 'R.LUX혜택', '남음', '판매됨', '로켓', '혜택', '무료배송'];
    var candidates = lines.filter(function(l) {
        var t = l.trim();
        if (t.length < 3) return false;
        if (/^[\\d,%원\\s.\\-]+$/.test(t)) return false;
        if (/^\\d{1,2}:\\d{2}/.test(t)) return false;
        if (/^\\d{1,3}%/.test(t)) return false;
        for (var si = 0; si < skipWords.length; si++) {
            if (t === skipWords[si]) return false;
        }
        return true;
    });

    var productName = '';
    var brandFromCard = '';
    var koreanLines = candidates.filter(function(l) { return /[가-힣]/.test(l); });
    var specLines = koreanLines.filter(function(l) {
        return /[,]/.test(l) || /\\d+(ml|g|kg|L|개|매|입|정|포|ea)/i.test(l);
    });

    if (specLines.length > 0) {
        productName = specLines.reduce(function(a, b) { return a.length >= b.length ? a : b; });
    } else if (koreanLines.length > 0) {
        productName = koreanLines.reduce(function(a, b) { return a.length >= b.length ? a : b; });
    } else if (candidates.length > 0) {
        productName = candidates.reduce(function(a, b) { return a.length >= b.length ? a : b; });
    }

    // 브랜드 후보: 제품명 아닌 짧은 라인 (노이즈 필터링 강화)
    var brandBlacklist = ['사은품', '쿠폰할인', '쿠폰', '본사정품', '정품', '한정수량', '한정수량마감',
        '한정수량 마감', '당일발송', '무료배송', '즉시할인', '특가', '최저가', '오늘의특가',
        '카드할인', '카드추가할인', '추가할인', '적립', '포인트', '리뷰', '평점',
        'R.LUX', 'R.LUX혜택', 'BEST', 'HOT', 'NEW', 'SALE', '품절임박'];
    candidates.forEach(function(l) {
        if (l !== productName && !brandFromCard && l.length < 30 && l.length >= 2) {
            var t = l.trim();
            var isBad = false;
            for (var bi = 0; bi < brandBlacklist.length; bi++) {
                if (t === brandBlacklist[bi] || t.toLowerCase() === brandBlacklist[bi].toLowerCase()) { isBad = true; break; }
            }
            if (/^https?:\/\//.test(t) || /\.com|www\.|coupang/.test(t)) isBad = true;
            if (/^[\d,]+\s*(원|개|매|입|세트|팩|%|ml|g|kg|L)$/i.test(t)) isBad = true;
            if (!isBad) brandFromCard = t;
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
"""


# ============================================================
# 메인 크롤링
# ============================================================
def crawl_goldbox():
    logger.info("=" * 50)
    logger.info("쿠팡 골드박스 크롤링 시작")
    logger.info("=" * 50)

    driver = create_driver()
    products = []
    seen_ids = set()

    try:
        # Step 1: 메인 페이지 방문 → 쿠키 획득
        logger.info("쿠팡 메인 페이지 방문")
        driver.get("https://www.coupang.com")
        time.sleep(random.uniform(3, 5))

        # 쿠키 동의 팝업 처리
        try:
            for sel in ["#cookieAcceptBtn", "[class*='cookie'] button", "[class*='consent'] button"]:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    try:
                        btn.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
        except Exception:
            pass

        # 메인 살짝 스크롤 (사람 흉내)
        for _ in range(3):
            driver.execute_script(f"window.scrollBy(0, {random.randint(200, 500)});")
            time.sleep(random.uniform(0.3, 0.7))

        logger.info(f"메인 타이틀: {driver.title}")

        # Step 2: 골드박스로 이동
        time.sleep(random.uniform(1, 3))

        # 링크 클릭 시도
        navigated = False
        try:
            gb_link = driver.find_element(By.CSS_SELECTOR, "a[href*='goldbox']")
            gb_link.click()
            logger.info("골드박스 링크 클릭으로 이동")
            time.sleep(random.uniform(3, 5))
            navigated = True
        except Exception:
            pass

        if not navigated:
            logger.info("골드박스 URL로 직접 이동")
            driver.execute_script("window.location.href = 'https://www.coupang.com/np/goldbox';")
            time.sleep(random.uniform(4, 6))

        logger.info(f"골드박스 타이틀: {driver.title}")

        if "Access Denied" in driver.title or "Denied" in driver.title:
            logger.warning("Access Denied — 재시도")
            time.sleep(random.uniform(5, 10))
            driver.delete_all_cookies()
            driver.get("https://www.coupang.com")
            time.sleep(random.uniform(3, 5))
            driver.get("https://www.coupang.com/np/goldbox")
            time.sleep(random.uniform(4, 6))

        if "Access Denied" in driver.title:
            logger.error("쿠팡 접근 차단됨")
            html = driver.page_source
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            driver.quit()
            sys.exit(1)

        # Step 3: 스크롤하면서 상품 수집 (가상 리스트 대응)
        logger.info("스크롤하면서 상품 수집 시작")
        scroll_pos = 0
        max_height = driver.execute_script("return document.body.scrollHeight")
        no_new_count = 0

        while scroll_pos < max_height + 3000:
            # 현재 화면 상품 수집
            try:
                js_items = driver.execute_script(EXTRACT_JS) or []
            except Exception as e:
                logger.warning(f"JS 실행 오류: {e}")
                js_items = []

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
                    discount = int((1 - sale / orig) * 100)

                name = (item.get("name") or "")[:200]
                brand_from_card = item.get("brandFromCard", "")
                # 수동 보정 사전 우선 적용
                if pid in BRAND_OVERRIDES:
                    brand = BRAND_OVERRIDES[pid]
                else:
                    brand = extract_brand(name, brand_from_card)
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
                    "delivery_type": item.get("deliveryType", "일반배송"),
                    "brand_name": brand,
                    "category": category,
                })

            if new_in_batch > 0:
                logger.info(f"  +{new_in_batch}개 수집 (누적 {len(products)}개) @ {scroll_pos}px")
                no_new_count = 0
            else:
                no_new_count += 1

            # 연속 15회 이상 새 상품 없으면 종료
            if no_new_count > 15:
                logger.info("더 이상 새 상품 없음 — 스크롤 종료")
                break

            # 스크롤
            scroll_pos += random.randint(300, 500)
            driver.execute_script(f"window.scrollTo(0, {scroll_pos});")
            time.sleep(random.uniform(0.3, 0.6))

            # 높이 갱신
            if scroll_pos % 2000 < 500:
                max_height = driver.execute_script("return document.body.scrollHeight")

        # 무효 상품 필터
        before = len(products)
        products = [p for p in products if is_valid_product(p)]
        if before != len(products):
            logger.info(f"무효 상품 {before - len(products)}개 제거")

        logger.info(f"최종 수집: {len(products)}개")

        # 디버깅 (10개 미만이면 페이지 저장)
        if len(products) < 10:
            html = driver.page_source
            debug_path = os.path.join(os.path.dirname(__file__), "debug_page.html")
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"디버깅 HTML 저장 ({len(html)} bytes)")

        # 샘플 출력
        for p in products[:5]:
            logger.info(
                f"  [{p['product_id']}] {p['brand_name'] or '?'} | {p['product_name'][:30]} "
                f"| {p['sale_price']:,}원 ({p['discount_rate']}%) "
                f"| {p['delivery_type']} | {p['category']}"
            )

    except Exception as e:
        logger.error(f"크롤링 오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

    return products


# ============================================================
# 저장
# ============================================================
def save_to_supabase(products):
    if not SUPABASE_SERVICE_KEY:
        logger.warning("SUPABASE_SERVICE_KEY 없음 → JSON으로 저장")
        save_to_json(products)
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
                    "product_name": p["product_name"],
                    "brand_name": p["brand_name"],
                    "category": p["category"],
                    "image_url": p["image_url"],
                    "delivery_type": p["delivery_type"],
                    "last_seen_date": today,
                }).eq("product_id", p["product_id"]).execute()
            else:
                supabase.table("goldbox_products").insert({
                    "product_id": p["product_id"],
                    "product_name": p["product_name"],
                    "brand_name": p["brand_name"],
                    "category": p["category"],
                    "image_url": p["image_url"],
                    "product_url": p["product_url"],
                    "delivery_type": p["delivery_type"],
                    "first_seen_date": today,
                    "last_seen_date": today,
                }).execute()

            supabase.table("goldbox_snapshots").insert({
                "product_id": p["product_id"],
                "crawled_at": now,
                "original_price": p["original_price"],
                "sale_price": p["sale_price"],
                "discount_rate": p["discount_rate"],
                "sold_rate": p["sold_rate"],
                "crawl_order": idx + 1,
            }).execute()
            ok += 1
        except Exception as e:
            logger.error(f"저장 실패 ({p['product_id']}): {e}")

    logger.info(f"Supabase 저장: {ok}/{len(products)}개 성공")


def save_to_json(products):
    now = datetime.now(KST)
    path = os.path.join(os.path.dirname(__file__), f"goldbox_{now.strftime('%Y%m%d_%H%M')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "crawled_at": now.isoformat(),
            "count": len(products),
            "products": products,
        }, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 저장: {path}")


# ============================================================
# 메인
# ============================================================
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
