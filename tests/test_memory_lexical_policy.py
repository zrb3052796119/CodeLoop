from __future__ import annotations

from minicode.memory_lexical_policy import (
    CONSOLIDATION_LEXICAL_V1,
    RETRIEVAL_LEXICAL_V1,
    RETRIEVAL_LEXICAL_V2,
    MemoryLexicalProfile,
)


def test_versioned_profiles_preserve_intentionally_different_stemming() -> None:
    assert RETRIEVAL_LEXICAL_V1.profile is MemoryLexicalProfile.RETRIEVAL_V1
    assert (
        CONSOLIDATION_LEXICAL_V1.profile
        is MemoryLexicalProfile.CONSOLIDATION_V1
    )
    assert RETRIEVAL_LEXICAL_V1.stem("moving") == "move"
    assert CONSOLIDATION_LEXICAL_V1.stem("moving") == "mov"
    assert RETRIEVAL_LEXICAL_V1.stem("reduced") == "reduce"
    assert CONSOLIDATION_LEXICAL_V1.stem("reduced") == "reduc"


def test_profiles_preserve_normalization_and_cjk_token_boundaries() -> None:
    mixed = "  ＡＰＩ__Client/v1:Retry-Policy\n\t"
    assert RETRIEVAL_LEXICAL_V1.normalize_input(mixed) == (
        "API__Client/v1:Retry-Policy"
    )
    assert RETRIEVAL_LEXICAL_V1.normalize_match(mixed) == (
        "api client v1 retry policy"
    )
    assert CONSOLIDATION_LEXICAL_V1.normalize_scalar(mixed) == (
        "api__client/v1:retry-policy"
    )

    assert RETRIEVAL_LEXICAL_V1.tokenize_match("认证策略文件") == [
        "认",
        "证",
        "策",
        "略",
        "文",
        "件",
        "认证",
        "策略",
        "文件",
    ]
    assert CONSOLIDATION_LEXICAL_V1.unique_tokens("认证策略文件") == (
        "认证策略文件",
    )
    assert RETRIEVAL_LEXICAL_V1.tokenize_match("㐀㐁认证") == [
        "认",
        "证",
        "认证",
    ]
    assert CONSOLIDATION_LEXICAL_V1.unique_tokens("㐀㐁认证") == (
        "㐀㐁认证",
    )


def test_v2_sliding_cjk_bigrams_are_prefix_offset_invariant() -> None:
    base = RETRIEVAL_LEXICAL_V2.tokenize_match("认证策略文件")
    prefixed = RETRIEVAL_LEXICAL_V2.tokenize_match("请认证策略文件")

    base_bigrams = {term for term in base if len(term) == 2}
    prefixed_bigrams = {term for term in prefixed if len(term) == 2}

    assert base_bigrams == {"认证", "证策", "策略", "略文", "文件"}
    assert base_bigrams <= prefixed_bigrams


def test_v2_single_cjk_characters_are_candidate_only() -> None:
    assert "类" in RETRIEVAL_LEXICAL_V2.candidate_term_set("类")
    assert RETRIEVAL_LEXICAL_V2.admission_term_set("类") == set()


def test_v2_keeps_polarity_markers_outside_admission_terms() -> None:
    text = "不删除缓存，必须保留索引"

    assert RETRIEVAL_LEXICAL_V2.polarity_markers(text) == ("不", "必须")
    assert "不" not in RETRIEVAL_LEXICAL_V2.admission_term_set(text)


def test_retrieval_expansion_and_consolidation_limits_remain_distinct() -> None:
    assert RETRIEVAL_LEXICAL_V1.expand_terms(
        ["test"],
        active_domains=("testing",),
    ) == ["test", "测试", "测试", "spec", "assert"]
    assert RETRIEVAL_LEXICAL_V1.term_set(
        "component",
        active_domains=("frontend",),
    ) == {"component", "组件", "widget", "control", "element"}
    assert CONSOLIDATION_LEXICAL_V1.unique_tokens("component") == (
        "component",
    )

    bounded = " ".join([f"t{index}" for index in range(256)] + ["needle"])
    assert "needle" in RETRIEVAL_LEXICAL_V1.term_set(bounded)
    assert "needle" not in CONSOLIDATION_LEXICAL_V1.unique_tokens(bounded)


def test_v1_profiles_are_differentially_equal_to_compatibility_helpers() -> None:
    import minicode.memory_candidate_consolidation as consolidation
    import minicode.memory_retrieval as retrieval

    samples = (
        "",
        "  ＡＰＩ__Client/v1:Retry-Policy\n\t",
        "moving saving reduced included coding using",
        "认证策略文件",
        "㐀㐁认证",
        "alpha\n\t beta",
        "foo.bar/baz-qux:zap",
        "API api API",
        "services boxes studies",
    )
    stems = (
        "moving",
        "saving",
        "loving",
        "pacing",
        "argued",
        "loved",
        "reduced",
        "included",
        "using",
        "studies",
        "boxes",
        "services",
        "认证",
    )

    for sample in samples:
        assert RETRIEVAL_LEXICAL_V1.normalize_input(sample) == (
            retrieval._normalize_text(sample)
        )
        assert RETRIEVAL_LEXICAL_V1.normalize_match(sample) == (
            retrieval._normalized_match_text(sample)
        )
        assert RETRIEVAL_LEXICAL_V1.term_set(sample) == retrieval._token_set(
            sample
        )
        assert CONSOLIDATION_LEXICAL_V1.normalize_scalar(sample) == (
            consolidation._normalize(sample)
        )
        assert CONSOLIDATION_LEXICAL_V1.unique_tokens(sample) == (
            consolidation._tokens(sample)
        )

    for stem in stems:
        assert RETRIEVAL_LEXICAL_V1.stem(stem) == retrieval._stem(stem)
        assert CONSOLIDATION_LEXICAL_V1.stem(stem) == consolidation._stem(stem)
