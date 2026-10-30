"""Phase 12: IR metrics, the TF-IDF vs BM25 evaluation, and manage.py."""

import pytest

from app.evaluation.metrics import average_precision, f1, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank


# ---------------------------------------------------------------- metrics (hand-computed)
RANKED = ["a", "x", "b", "y", "c"]
RELEVANT = {"a", "b", "c", "d"}


def test_precision_recall_f1():
    assert precision_at_k(RANKED, RELEVANT, 5) == pytest.approx(3 / 5)
    assert precision_at_k(RANKED, RELEVANT, 1) == 1.0
    assert recall_at_k(RANKED, RELEVANT, 5) == pytest.approx(3 / 4)
    assert f1(0.6, 0.75) == pytest.approx(2 * 0.6 * 0.75 / 1.35)
    assert f1(0, 0) == 0 and recall_at_k(RANKED, set(), 5) == 0 and precision_at_k(RANKED, RELEVANT, 0) == 0


def test_average_precision():
    # relevant at ranks 1, 3, 5 -> (1/1 + 2/3 + 3/5) / 4 relevant
    assert average_precision(RANKED, RELEVANT) == pytest.approx((1 + 2 / 3 + 3 / 5) / 4)
    assert average_precision(["x", "y"], {"a"}) == 0


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a"}) == 0


def test_ndcg():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == pytest.approx(1.0)        # perfect
    worse = ndcg_at_k(["x", "a"], {"a"}, 10)
    assert worse == pytest.approx(1 / __import__("math").log2(3))
    assert ndcg_at_k(["x"], {"a"}, 10) == 0


def test_order_matters_for_ap_but_not_set_precision():
    relevant = {"good"}
    first, last = ["good", "bad1", "bad2"], ["bad1", "bad2", "good"]
    assert precision_at_k(first, relevant, 3) == precision_at_k(last, relevant, 3)  # same set
    assert average_precision(first, relevant) > average_precision(last, relevant)  # order counts


# ---------------------------------------------------------------- evaluation run
def test_evaluation_report():
    from app.evaluation.evaluate import evaluate, format_report

    report = evaluate()
    assert report["benchmark"]["documents"] == 44 and report["benchmark"]["queries"] == 20
    for ranker in ("bm25", "tfidf"):
        m = report["rankers"][ranker]
        assert set(m) == {"P@5", "R@5", "F1@5", "MAP", "MRR", "nDCG@10"}
        assert all(0 <= v <= 1 for v in m.values())
    # Regression check of the documented result: BM25 is at least as good as TF-IDF here.
    assert report["rankers"]["bm25"]["MAP"] >= report["rankers"]["tfidf"]["MAP"]
    text = format_report(report)
    assert "TF-IDF" in text and "BM25" in text and "Benchmark" in text


def test_every_labelled_slug_exists():
    import json

    from app.services.sample_data import EVAL_FILE, read_sample

    slugs = {d["slug"] for d in read_sample()}
    for q in json.loads(EVAL_FILE.read_text(encoding="utf-8"))["queries"]:
        assert set(q["relevant"]) <= slugs, q


# ---------------------------------------------------------------- manage.py
@pytest.fixture
def cli(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cli.db"))
    get_settings.cache_clear()
    import manage
    yield manage
    get_settings.cache_clear()


def test_cli_load_search_stats(cli, capsys):
    assert cli.main(["load-sample"]) == 0
    assert "44 created" in capsys.readouterr().out
    cli.main(["search", "backpropogation"])
    assert "Did you mean: backpropagation" in capsys.readouterr().out
    cli.main(["search", "binary search", "--explain", "--limit", "1"])
    out = capsys.readouterr().out
    assert "Binary Search on a Sorted Array" in out and "idf" in out
    cli.main(["stats"])
    assert '"documents": 44' in capsys.readouterr().out
    cli.main(["rebuild"])
    assert "Rebuilt index from 44 documents" in capsys.readouterr().out
    cli.main(["index"])
    assert "Indexed 0" in capsys.readouterr().out
    cli.main(["clear-history"])
    assert "Deleted" in capsys.readouterr().out


def test_cli_requires_command(cli):
    with pytest.raises(SystemExit):
        cli.main([])
