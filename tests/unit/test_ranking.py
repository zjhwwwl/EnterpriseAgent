"""Retrieval ranking behavior with the actual BM25 implementation and Chinese text."""

import math

import pytest

from enterprise_agent.evaluation import document_metrics, percentile95, summarize
from enterprise_agent.rag.ranking import bm25_rank, reciprocal_rank_fusion, tokenize


def test_tokenizer_recognizes_chinese_terms_and_preserves_business_identifiers():
    tokens = tokenize("订单 ORDER10086，7天！")
    assert {"订", "单", "订单", "order10086", "7", "天"} <= set(tokens)
    assert "ORDER10086" not in tokens
    assert not {"，", "！", " "} & set(tokens)


def test_tokenizer_keeps_repeated_term_frequency_for_bm25():
    tokens = tokenize("退款退款")
    assert tokens.count("退款") == 2
    assert tokens.count("退") == 2
    assert tokenize(" ，！？ ") == []


def test_real_bm25_finds_the_business_policy_in_a_diverse_corpus():
    texts = {
        "refund": "退款申请需要确认订单与退款金额，客服随后审核。",
        "delivery": "包裹运输中，查看签收时间和运单号。",
        "member": "会员积分与活动提醒说明。",
        "coupon": "优惠券不可叠加使用，优惠券不能折现。",
        "products": "电子商品的规格与库存维护说明。",
        "security": "管理员登录审计记录。",
    }
    ranking = bm25_rank("优惠券能叠加吗", texts)
    assert ranking
    assert ranking[0][0] == "coupon"
    assert all(math.isfinite(score) and score > 0 for _, score in ranking)
    assert bm25_rank("银河天体火星轨道", texts) == []
    assert bm25_rank("优惠券叠加", texts, limit=1)[0][0] == "coupon"
    assert len(bm25_rank("退款订单客服", texts, limit=1)) <= 1


def test_bm25_handles_empty_corpus_and_punctuation_without_fabricated_hits():
    assert bm25_rank("退款", {}) == []
    assert bm25_rank("！！！", {"policy": "七天售后政策"}) == []


def test_rrf_agreement_across_signals_can_outweigh_a_single_first_place():
    ranking = reciprocal_rank_fusion(
        ["vector-only", "consensus"],
        ["lexical-only", "consensus"],
        ["another-only", "consensus"],
    )
    assert ranking[0][0] == "consensus"
    assert len({identifier for identifier, _ in ranking}) == len(ranking)


def test_rrf_duplicate_ids_within_one_signal_do_not_manufacture_votes():
    ranking = reciprocal_rank_fusion(
        ["duplicated"] * 100 + ["supported"],
        ["supported"],
    )
    assert ranking[0][0] == "supported"
    assert len(ranking) == 2


def test_rrf_empty_inputs_and_ties_are_deterministic():
    assert reciprocal_rank_fusion([], []) == []
    first = reciprocal_rank_fusion(["b", "a"], ["a", "b"])
    second = reciprocal_rank_fusion(["a", "b"], ["b", "a"])
    assert first == second
    assert [identifier for identifier, _ in first] == ["a", "b"]
    assert all(math.isfinite(score) and score > 0 for _, score in first)


@pytest.mark.parametrize("k", [0, -1])
def test_rrf_rejects_nonpositive_rank_constants(k):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion(["policy"], k=k)


def test_document_metrics_deduplicate_chunks_and_score_partial_relevance():
    measured = document_metrics(
        ["irrelevant", "refund", "refund", "membership"], ["refund", "membership"]
    )
    assert measured["recall_at_1"] == 0
    assert measured["recall_at_3"] == measured["recall_at_5"] == 1
    assert measured["mrr"] == 0.5
    assert measured["ndcg_at_5"] == pytest.approx(0.6934264036172708)


def test_document_metrics_do_not_invent_recall_for_unanswerable_queries():
    assert all(value is None for value in document_metrics(["irrelevant"], []).values())
    assert all(value == 0 for value in document_metrics([], ["refund"]).values())


def test_latency_percentile_uses_documented_nearest_rank():
    assert percentile95([]) is None
    assert percentile95([1.0, 9.0]) == 9.0
    assert percentile95([float(value) for value in range(1, 21)]) == 19.0


def test_failed_queries_stay_in_the_metric_denominator():
    common = {
        "split": "test",
        "mode": "vector",
        "answerable": True,
        "citation_checks": [],
        "llm_calls": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
    }
    records = [
        common
        | {
            "outcome": "completed",
            "status": "evidence_found",
            "gate_correct": True,
            "elapsed_ms": 10.0,
            "metrics": document_metrics(["refund"], ["refund"]),
        },
        common
        | {
            "outcome": "error",
            "status": "error",
            "gate_correct": False,
            "elapsed_ms": 100.0,
            "metrics": document_metrics([], ["refund"]),
        },
    ]
    result = summarize(records)["test"]["vector"]
    assert result["queries"] == result["answerable_queries"] == 2
    assert result["errors"] == 1
    assert result["recall_at_5"] == result["execution_success_rate"] == 0.5
    assert result["mean_latency_ms"] == 55.0
    assert result["citation_validity_rate"] is None
