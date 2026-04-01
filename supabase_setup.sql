-- ============================================
-- 쿠팡 골드박스 크롤러 - Supabase 테이블 생성
-- Supabase SQL Editor에서 실행하세요
-- ============================================

-- 1. 상품 기본 정보 테이블
CREATE TABLE IF NOT EXISTS goldbox_products (
  product_id TEXT PRIMARY KEY,
  product_name TEXT NOT NULL,
  brand_name TEXT DEFAULT '',
  category TEXT DEFAULT '',
  image_url TEXT DEFAULT '',
  product_url TEXT DEFAULT '',
  first_seen_date DATE NOT NULL DEFAULT CURRENT_DATE,
  last_seen_date DATE NOT NULL DEFAULT CURRENT_DATE
);

-- 2. 시계열 스냅샷 테이블
CREATE TABLE IF NOT EXISTS goldbox_snapshots (
  id BIGSERIAL PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES goldbox_products(product_id) ON DELETE CASCADE,
  crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  original_price BIGINT DEFAULT 0,
  sale_price BIGINT DEFAULT 0,
  discount_rate INTEGER DEFAULT 0,
  sold_rate INTEGER DEFAULT 0
);

-- 인덱스: 조회 성능 최적화
CREATE INDEX IF NOT EXISTS idx_snapshots_product_id ON goldbox_snapshots(product_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_crawled_at ON goldbox_snapshots(crawled_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_product_crawled ON goldbox_snapshots(product_id, crawled_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_brand ON goldbox_products(brand_name);
CREATE INDEX IF NOT EXISTS idx_products_category ON goldbox_products(category);
CREATE INDEX IF NOT EXISTS idx_products_last_seen ON goldbox_products(last_seen_date DESC);

-- 3. RLS (Row Level Security) 설정 - 읽기는 공개, 쓰기는 service_role만
ALTER TABLE goldbox_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE goldbox_snapshots ENABLE ROW LEVEL SECURITY;

-- 누구나 읽기 가능
CREATE POLICY "goldbox_products_read" ON goldbox_products
  FOR SELECT USING (true);

CREATE POLICY "goldbox_snapshots_read" ON goldbox_snapshots
  FOR SELECT USING (true);

-- service_role만 쓰기 가능 (크롤러용)
CREATE POLICY "goldbox_products_insert" ON goldbox_products
  FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "goldbox_products_update" ON goldbox_products
  FOR UPDATE USING (auth.role() = 'service_role');

CREATE POLICY "goldbox_snapshots_insert" ON goldbox_snapshots
  FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- ============================================
-- 스키마 업데이트 (기존 테이블에 컬럼 추가)
-- ============================================
ALTER TABLE goldbox_products ADD COLUMN IF NOT EXISTS delivery_type TEXT DEFAULT '';
ALTER TABLE goldbox_snapshots ADD COLUMN IF NOT EXISTS crawl_order INTEGER DEFAULT 0;

-- 5. 브랜드 즐겨찾기 테이블
CREATE TABLE IF NOT EXISTS brand_favorites (
  id BIGSERIAL PRIMARY KEY,
  brand_name TEXT NOT NULL,
  user_id TEXT NOT NULL DEFAULT 'default',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(brand_name, user_id)
);

ALTER TABLE brand_favorites ENABLE ROW LEVEL SECURITY;

-- 즐겨찾기: 누구나 읽기/쓰기 가능 (인증 시스템 없으므로)
CREATE POLICY "brand_favorites_read" ON brand_favorites
  FOR SELECT USING (true);

CREATE POLICY "brand_favorites_insert" ON brand_favorites
  FOR INSERT WITH CHECK (true);

CREATE POLICY "brand_favorites_delete" ON brand_favorites
  FOR DELETE USING (true);

CREATE INDEX IF NOT EXISTS idx_favorites_brand ON brand_favorites(brand_name);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON brand_favorites(user_id);

-- 6. 유용한 뷰: 오늘의 골드박스 (최신 스냅샷)
CREATE OR REPLACE VIEW goldbox_today AS
SELECT
  p.product_id,
  p.product_name,
  p.brand_name,
  p.category,
  p.image_url,
  p.product_url,
  p.delivery_type,
  s.original_price,
  s.sale_price,
  s.discount_rate,
  s.sold_rate,
  s.crawl_order,
  s.crawled_at
FROM goldbox_products p
INNER JOIN LATERAL (
  SELECT *
  FROM goldbox_snapshots gs
  WHERE gs.product_id = p.product_id
    AND gs.crawled_at >= CURRENT_DATE
  ORDER BY gs.crawled_at DESC
  LIMIT 1
) s ON true
WHERE p.last_seen_date = CURRENT_DATE
ORDER BY s.crawl_order ASC;
