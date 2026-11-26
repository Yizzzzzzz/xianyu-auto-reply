-- 闲鱼后端：17 张缺 user_id 的表加 user_id 列并回填（账号间隔离）
-- 用途：测试环境已执行；同步到生产时按下面步骤执行。
-- 注意：
--   1. 后端启动时 common/db/init_database.py 的 COLUMN_MIGRATIONS 会自动
--      给这些表加 user_id 列和 idx_user_id 索引（幂等），所以生产上只需
--      同步代码并重启后端即可完成加列；本文件的 ALTER 部分仅供手工执行参考。
--   2. 回填 UPDATE 必须在加列后执行：按 account_id 关联 xy_accounts 取 owner_id；
--      账号已被删除的行无法回填，保持 user_id 为 NULL（表示未归属历史数据，
--      查询时与本人数据一起可见，不会丢失）。
--   3. 不删数据；回填 UPDATE 可重复执行（幂等）。
-- 涉及表（17 张）：
--   xy_auto_rate_configs, xy_confirm_receipt_messages, xy_cookie_refresh_schedules,
--   xy_default_replies, xy_delivery_block_rules, xy_message_filters,
--   xy_publish_addresses, xy_scheduled_api_cookie_renew_log,
--   xy_scheduled_close_notice_log, xy_scheduled_cookies_refresh_log,
--   xy_scheduled_login_renew_log, xy_scheduled_polish_log, xy_scheduled_rate_log,
--   xy_scheduled_red_flower_log, xy_scheduled_redelivery_log,
--   xy_scheduled_token_renewal_log, xy_shared_scan_workers

-- ============ 一、加列（后端自动迁移已覆盖，手工执行时取消注释） ============
-- ALTER TABLE xy_auto_rate_configs ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_confirm_receipt_messages ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_cookie_refresh_schedules ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_default_replies ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_delivery_block_rules ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_message_filters ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_publish_addresses ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据/全局共用' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_api_cookie_renew_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_close_notice_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_cookies_refresh_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_login_renew_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_polish_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_rate_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_red_flower_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_redelivery_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_scheduled_token_renewal_log ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);
-- ALTER TABLE xy_shared_scan_workers ADD COLUMN user_id BIGINT NULL COMMENT '归属平台用户ID(xy_users.id)，空表示未归属的历史数据' AFTER account_id, ADD INDEX idx_user_id (user_id);

-- ============ 二、回填：按 account_id 取 xy_accounts.owner_id ============
UPDATE xy_auto_rate_configs t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_confirm_receipt_messages t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_cookie_refresh_schedules t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_default_replies t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_delivery_block_rules t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_message_filters t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_publish_addresses t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_api_cookie_renew_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_close_notice_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_cookies_refresh_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_login_renew_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_polish_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_rate_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_red_flower_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_redelivery_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_scheduled_token_renewal_log t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
UPDATE xy_shared_scan_workers t JOIN xy_accounts a ON a.account_id = t.account_id SET t.user_id = a.owner_id;
