-- Idempotent column additions for xy_catalog_items
SET @dbname = DATABASE();

SET @col_browse = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @dbname AND TABLE_NAME = 'xy_catalog_items' AND COLUMN_NAME = 'browse_count');
SET @sql_browse = IF(@col_browse = 0, 'ALTER TABLE xy_catalog_items ADD COLUMN browse_count INT DEFAULT 0 COMMENT "浏览量" AFTER is_polished', 'SELECT 1');
PREPARE stmt_browse FROM @sql_browse;
EXECUTE stmt_browse;
DEALLOCATE PREPARE stmt_browse;

SET @col_want = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @dbname AND TABLE_NAME = 'xy_catalog_items' AND COLUMN_NAME = 'want_count');
SET @sql_want = IF(@col_want = 0, 'ALTER TABLE xy_catalog_items ADD COLUMN want_count INT DEFAULT 0 COMMENT "想要数" AFTER browse_count', 'SELECT 1');
PREPARE stmt_want FROM @sql_want;
EXECUTE stmt_want;
DEALLOCATE PREPARE stmt_want;

SET @col_collect = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @dbname AND TABLE_NAME = 'xy_catalog_items' AND COLUMN_NAME = 'collect_count');
SET @sql_collect = IF(@col_collect = 0, 'ALTER TABLE xy_catalog_items ADD COLUMN collect_count INT DEFAULT 0 COMMENT "收藏数" AFTER want_count', 'SELECT 1');
PREPARE stmt_collect FROM @sql_collect;
EXECUTE stmt_collect;
DEALLOCATE PREPARE stmt_collect;

SET @col_desc = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @dbname AND TABLE_NAME = 'xy_catalog_items' AND COLUMN_NAME = 'full_desc');
SET @sql_desc = IF(@col_desc = 0, 'ALTER TABLE xy_catalog_items ADD COLUMN full_desc MEDIUMTEXT COMMENT "商品完整详情文案" AFTER collect_count', 'SELECT 1');
PREPARE stmt_desc FROM @sql_desc;
EXECUTE stmt_desc;
DEALLOCATE PREPARE stmt_desc;
