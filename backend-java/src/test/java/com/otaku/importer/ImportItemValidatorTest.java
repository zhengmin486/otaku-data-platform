package com.otaku.importer;

import com.otaku.importer.dto.ImportBatchRequest;
import com.otaku.importer.dto.ImportBatchRequest.ImportItem;
import com.otaku.importer.dto.ImportBatchRequest.SourceRating;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 条目级校验必须"只拒收这一条"。用 Bean Validation 打在数组上会让整批 400，
 * 违反契约 5.6 与验收 D5。
 */
class ImportItemValidatorTest {

    private static ImportItem item(String sourceId, String type, String title, SourceRating rating) {
        return new ImportItem(sourceId, type, title, null, null, null, List.of(), rating, null);
    }

    private static ImportItem itemWithYear(Integer releaseYear) {
        return new ImportItem("476", "anime", "街角魔族", null, null, releaseYear, List.of(), null, null);
    }

    private static SourceRating rating(String value) {
        return rating(value, "0-10");
    }

    private static SourceRating rating(String value, String scale) {
        return new SourceRating(value == null ? null : new BigDecimal(value), scale, "otaku_site");
    }

    @Test
    @DisplayName("正常记录通过")
    void acceptsValid() {
        assertThat(ImportItemValidator.validate(item("476", "anime", "街角魔族 2丁目", rating("7.8"))))
                .isEmpty();
    }

    @Test
    @DisplayName("source_id 为空要拒收（Python 侧不校验这一项）")
    void rejectsBlankSourceId() {
        Optional<ImportItemValidator.Problem> problem =
                ImportItemValidator.validate(item("  ", "anime", "标题", null));

        assertThat(problem).isPresent();
        assertThat(problem.get().code()).isEqualTo(ImportItemValidator.SOURCE_ID_REQUIRED);
    }

    @Test
    @DisplayName("title 为空要拒收，对应错误码 TITLE_REQUIRED")
    void rejectsBlankTitle() {
        Optional<ImportItemValidator.Problem> problem =
                ImportItemValidator.validate(item("476", "anime", "   ", null));

        assertThat(problem).isPresent();
        assertThat(problem.get().code()).isEqualTo(ImportItemValidator.TITLE_REQUIRED);
    }

    @Test
    @DisplayName("title 超过 500 字拒收（契约要求不截断）")
    void rejectsTooLongTitle() {
        String longTitle = "长".repeat(501);

        Optional<ImportItemValidator.Problem> problem =
                ImportItemValidator.validate(item("476", "anime", longTitle, null));

        assertThat(problem).isPresent();
        assertThat(problem.get().code()).isEqualTo(ImportItemValidator.TITLE_TOO_LONG);
    }

    @Test
    @DisplayName("work_type 不在四值枚举内要拒收")
    void rejectsUnknownType() {
        Optional<ImportItemValidator.Problem> problem =
                ImportItemValidator.validate(item("476", "movie", "标题", null));

        assertThat(problem).isPresent();
        assertThat(problem.get().code()).isEqualTo(ImportItemValidator.INVALID_TYPE);
    }

    @Test
    @DisplayName("评分超出 0-10 尺度要拒收")
    void rejectsRatingOutOfScale() {
        Optional<ImportItemValidator.Problem> problem =
                ImportItemValidator.validate(item("476", "anime", "标题", rating("11")));

        assertThat(problem).isPresent();
        assertThat(problem.get().code()).isEqualTo(ImportItemValidator.RATING_OUT_OF_SCALE);
    }

    @Test
    @DisplayName("评分缺失不算错，也不是 0 分")
    void acceptsMissingRating() {
        assertThat(ImportItemValidator.validate(item("476", "anime", "标题", rating(null)))).isEmpty();
        assertThat(ImportItemValidator.validate(item("476", "anime", "标题", null))).isEmpty();
    }

    @Test
    @DisplayName("非 0-10 的尺度一律拒收：0-100 的 85 分不能当成「85 分」直接进库")
    void rejectsNonZeroToTenScale() {
        // 这是 W4 接 VNDB 时暴露的真问题：早期只对 0-10 做范围检查，0-100 原样放行，
        // 导致 sort=rating 把 90.6 排到 9.0 之前、min_rating=9 从 5 条变 31 条，且不报错。
        for (String scale : List.of("0-100", "1-5", "0-1", "percent", "10")) {
            Optional<ImportItemValidator.Problem> problem =
                    ImportItemValidator.validate(item("476", "anime", "标题", rating("8.5", scale)));

            assertThat(problem).as("尺度 %s 应被拒收", scale).isPresent();
            assertThat(problem.get().code()).isEqualTo(ImportItemValidator.RATING_OUT_OF_SCALE);
            assertThat(problem.get().message()).contains("尺度不支持");
        }
    }

    @Test
    @DisplayName("尺度缺省或带空白仍按 0-10 处理（契约的缺省语义不变）")
    void treatsMissingScaleAsZeroToTen() {
        assertThat(ImportItemValidator.validate(item("476", "anime", "标题", rating("8.5", null))))
                .isEmpty();
        assertThat(ImportItemValidator.validate(item("476", "anime", "标题", rating("8.5", "   "))))
                .isEmpty();
        assertThat(ImportItemValidator.validate(item("476", "anime", "标题", rating("8.5", " 0-10 "))))
                .isEmpty();
    }

    @Test
    @DisplayName("年份缺失合法；边界值 1900 / 2100 都收")
    void acceptsMissingAndBoundaryYear() {
        assertThat(ImportItemValidator.validate(itemWithYear(null))).isEmpty();
        assertThat(ImportItemValidator.validate(itemWithYear(1900))).isEmpty();
        assertThat(ImportItemValidator.validate(itemWithYear(2100))).isEmpty();
        assertThat(ImportItemValidator.validate(itemWithYear(1995))).isEmpty();
    }

    @Test
    @DisplayName("年份越界要拒收——0 和 99999 都不能当成「没有年份」混进来")
    void rejectsYearOutOfRange() {
        assertThat(ImportItemValidator.validate(itemWithYear(0)).orElseThrow().code())
                .isEqualTo(ImportItemValidator.INVALID_RELEASE_YEAR);
        assertThat(ImportItemValidator.validate(itemWithYear(1899)).orElseThrow().code())
                .isEqualTo(ImportItemValidator.INVALID_RELEASE_YEAR);
        assertThat(ImportItemValidator.validate(itemWithYear(2101)).orElseThrow().code())
                .isEqualTo(ImportItemValidator.INVALID_RELEASE_YEAR);
        assertThat(ImportItemValidator.validate(itemWithYear(99999)).orElseThrow().code())
                .isEqualTo(ImportItemValidator.INVALID_RELEASE_YEAR);
    }
}
