package com.otaku.importer;

import com.otaku.importer.dto.ImportBatchRequest.ImportItem;
import com.otaku.importer.dto.ImportBatchRequest.SourceRating;
import com.otaku.work.WorkType;

import java.math.BigDecimal;
import java.util.Optional;

/**
 * 逐条校验。
 *
 * 为什么不用 Bean Validation 打在 items 数组上：那样一条坏数据会抛出
 * MethodArgumentNotValidException，整批返回 400，违反契约"一条坏记录不该拖垮整批"
 * （5.6 节、验收 D5）。所以批次级校验用 Spring，条目级校验在这里手写。
 */
final class ImportItemValidator {

    static final String SOURCE_ID_REQUIRED = "SOURCE_ID_REQUIRED";
    static final String TITLE_REQUIRED = "TITLE_REQUIRED";
    static final String TITLE_TOO_LONG = "TITLE_TOO_LONG";
    static final String INVALID_TYPE = "INVALID_TYPE";
    static final String INVALID_RELEASE_YEAR = "INVALID_RELEASE_YEAR";
    static final String RATING_OUT_OF_SCALE = "RATING_OUT_OF_SCALE";

    static final int MAX_SOURCE_ID_LENGTH = 128;
    static final int MAX_TITLE_LENGTH = 500;
    private static final String SCALE_ZERO_TO_TEN = "0-10";

    /** 与 Python 侧 `clean_year` 同一区间；越界说明数据没走清洗层，宁可拒收也不入库。 */
    static final int MIN_RELEASE_YEAR = 1900;
    static final int MAX_RELEASE_YEAR = 2100;

    private ImportItemValidator() {
    }

    record Problem(String code, String message) {
    }

    static Optional<Problem> validate(ImportItem item) {
        String sourceId = item.sourceId();
        if (sourceId == null || sourceId.isBlank()) {
            // Python 侧 validate() 不校验 source_id，这里必须兜住（契约 2.3 的四必填之一）。
            return Optional.of(new Problem(SOURCE_ID_REQUIRED, "source_id 为空"));
        }
        if (sourceId.length() > MAX_SOURCE_ID_LENGTH) {
            return Optional.of(new Problem(SOURCE_ID_REQUIRED,
                    "source_id 超过 " + MAX_SOURCE_ID_LENGTH + " 字符"));
        }

        String title = item.title() == null ? "" : item.title().trim();
        if (title.isEmpty()) {
            return Optional.of(new Problem(TITLE_REQUIRED, "title 为空"));
        }
        if (title.length() > MAX_TITLE_LENGTH) {
            return Optional.of(new Problem(TITLE_TOO_LONG,
                    "title 超过 " + MAX_TITLE_LENGTH + " 字符，契约要求不做截断，故整条拒收"));
        }

        if (!WorkType.isValid(item.workType())) {
            return Optional.of(new Problem(INVALID_TYPE, "work_type 非法：" + item.workType()));
        }

        Integer releaseYear = item.releaseYear();
        if (releaseYear != null
                && (releaseYear < MIN_RELEASE_YEAR || releaseYear > MAX_RELEASE_YEAR)) {
            // null 是合法值（来源未提供）；只有"给了但不像年份"才拒。
            return Optional.of(new Problem(INVALID_RELEASE_YEAR,
                    "release_year 应为 " + MIN_RELEASE_YEAR + "–" + MAX_RELEASE_YEAR
                            + "，实际 " + releaseYear));
        }

        SourceRating rating = item.mainRating();
        if (rating != null && rating.value() != null) {
            String scale = rating.scale() == null || rating.scale().isBlank()
                    ? SCALE_ZERO_TO_TEN : rating.scale().trim();
            if (!SCALE_ZERO_TO_TEN.equals(scale)) {
                // openapi 的 SourceRating.scale 只有 "0-10" 一个枚举值，这里对齐契约。
                //
                // 早期实现只对 0-10 做范围检查，其它尺度原样放行——结果是 0-100 的分值
                // 直接进库，把 sort=rating 与 min_rating 静默弄错（W4 接 VNDB 实测：
                // 90.6 排到 9.0 之前、min_rating=9 从 5 条变 31 条，且不报任何错）。
                // 拒收比放行好：错在适配器就该在适配器报错，不该让读接口替它兜。
                return Optional.of(new Problem(RATING_OUT_OF_SCALE,
                        "评分尺度不支持：" + scale + "（当前只接受 " + SCALE_ZERO_TO_TEN
                                + "，请由适配器换算后提交）"));
            }
            BigDecimal value = rating.value();
            if (value.compareTo(BigDecimal.ZERO) < 0
                    || value.compareTo(BigDecimal.TEN) > 0) {
                return Optional.of(new Problem(RATING_OUT_OF_SCALE,
                        "评分 " + value.toPlainString() + " 超出尺度 " + scale));
            }
        }
        return Optional.empty();
    }
}
