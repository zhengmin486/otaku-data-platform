-- 数据完整度体检：统计 work 表各字段的非空覆盖率。
--
-- 为什么要有这个脚本
--   W3（数据完整度）的结论性数字 —— 封面 2.7% / 评分 36.2% / 简介 84%+ ——
--   来自 PR #91 的一次性统计，没有可复用的工具。之后每次灌库、每次回填
--   （例如 W3-A 封面回填）都需要重新量一遍，否则「补了多少、到底补没补上」
--   只能靠感觉。这个脚本把口径固定下来，随时可复测。
--
-- 用法（只读，不修改任何数据）
--   mysql -u root otaku < scripts/data-coverage.sql
--   mysql -u root otaku -e "source scripts/data-coverage.sql"
--
-- 口径（与 docs/data-completeness-plan.md 一致）
--   - 空字符串与 NULL 都算「没有」（来源侧约定：缺失传 null，不传空串、不填 0）；
--   - rating_value 为 NULL = 无评分，**不是 0 分**；
--   - release_year 为 NULL = 来源未提供（该列用 SMALLINT 而非 YEAR，
--     就是为了避免 MySQL YEAR 的 0000 与「空缺」混淆）；
--   - tags 的 NULL 与 [] 语义不同：NULL = 来源未提供，[] = 明确无标签，
--     所以拆成 tags_provided 与 tags_nonempty 两列分别统计。
--
-- 输出中的 NULL 行是 WITH ROLLUP 产生的汇总行（不是数据缺失）。

SELECT '=== 1) 各字段覆盖率 %（按来源 / 类型，末尾带汇总）===' AS section;

SELECT
    source,
    work_type,
    COUNT(*)                                                                        AS total,
    ROUND(100 * SUM(title IS NOT NULL AND title <> '') / COUNT(*), 1)               AS title_pct,
    ROUND(100 * SUM(summary IS NOT NULL AND summary <> '') / COUNT(*), 1)           AS summary_pct,
    ROUND(100 * SUM(release_year IS NOT NULL) / COUNT(*), 1)                        AS year_pct,
    ROUND(100 * SUM(cover_source_url IS NOT NULL AND cover_source_url <> '') / COUNT(*), 1) AS cover_pct,
    ROUND(100 * SUM(rating_value IS NOT NULL) / COUNT(*), 1)                        AS rating_pct,
    ROUND(100 * SUM(tags IS NOT NULL) / COUNT(*), 1)                                AS tags_provided_pct,
    ROUND(100 * SUM(tags IS NOT NULL AND JSON_LENGTH(tags) > 0) / COUNT(*), 1)      AS tags_nonempty_pct,
    ROUND(100 * SUM(source_url IS NOT NULL AND source_url <> '') / COUNT(*), 1)     AS source_url_pct
FROM work
GROUP BY source, work_type WITH ROLLUP;

-- 2) 封面：有多少「有地址」、其中多少已经回源缓存落盘。
--    封面地址来自来源站，但出图要后端回源并缓存；两者不是一回事，
--    回填或排查出图问题时需要分开看。
SELECT '=== 2) 封面地址与缓存落盘情况 ===' AS section;

SELECT
    source,
    COUNT(*)                                                                   AS total,
    SUM(cover_source_url IS NOT NULL AND cover_source_url <> '')               AS has_cover_url,
    SUM(cover_cached_at IS NOT NULL)                                           AS cover_cached,
    ROUND(
        100 * SUM(cover_cached_at IS NOT NULL)
        / NULLIF(SUM(cover_source_url IS NOT NULL AND cover_source_url <> ''), 0), 1
    )                                                                          AS cached_of_has_url_pct
FROM work
GROUP BY source WITH ROLLUP;

-- 3) 总量与时间范围，用于确认体检的是不是预期的那批数据。
SELECT '=== 3) 总量与时间范围 ===' AS section;

SELECT
    COUNT(*)         AS total_works,
    MIN(created_at)  AS first_import_at,
    MAX(updated_at)  AS last_updated_at
FROM work;
