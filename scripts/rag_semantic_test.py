"""RAG 语义检索质量验证脚本。

用途：验证 DashScope 真实 Embedding 接入后，向量检索能否基于自然语言查询
     命中语义最相关的种子知识，而不是依赖关键词字面匹配。

使用：
    python scripts/rag_semantic_test.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.tools.rag_service import (  # noqa: E402
    BaseEmbeddingProvider,
    DashScopeEmbeddingProvider,
    RAGService,
    _ChromaDefaultEmbedding,
    _select_embedding_provider,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("rag_semantic_test")


# ------------------------------------------------------------------ #
#  测试用例：自然语言查询 → 预期命中的种子知识 source
# ------------------------------------------------------------------ #
TEST_CASES: List[Tuple[str, str, str]] = [
    # (query, expected_source, 说明)
    (
        "我的代码里有个地方会抛异常，怎么处理才规范？",
        "best_exception",
        "异常处理语义查询，应命中 best_exception 种子",
    ),
    (
        "听说 eval 这种动态执行代码不安全，具体有什么风险？",
        "best_security",
        "安全风险语义查询，应命中 best_security 种子",
    ),
    (
        "我想给一个函数写测试，规范的做法是什么？",
        "best_pytest",
        "测试规范语义查询，应命中 best_pytest 种子",
    ),
    (
        "怎么读取一个文件的内容，推荐用哪种方式？",
        "api_pathlib",
        "路径/文件操作语义查询，应命中 api_pathlib 种子",
    ),
    (
        "需要对一段数据做不可逆的摘要，用什么算法？",
        "api_hashlib",
        "哈希语义查询，应命中 api_hashlib 种子",
    ),
]


def _provider_name(p: BaseEmbeddingProvider) -> str:
    return type(p).__name__


def run_case(rag: RAGService, query: str, expected: str, desc: str) -> dict:
    docs = rag.query(query, top_k=3)
    top1 = docs[0] if docs else None
    top1_source = (top1.metadata.get("source", "") if top1 else "")
    top1_score = (top1.score if top1 else 0.0)
    hit = top1_source == expected
    return {
        "desc": desc,
        "query": query,
        "expected": expected,
        "top1_source": top1_source,
        "top1_score": round(top1_score, 4),
        "hit": hit,
        "top3": [
            {"source": d.metadata.get("source", ""), "score": round(d.score, 4)}
            for d in docs
        ],
    }


def print_result(case: dict, idx: int) -> None:
    mark = "OK" if case["hit"] else "MISS"
    print(f"\n[{idx}] {mark}  {case['desc']}")
    print(f"    query    : {case['query']}")
    print(f"    expected : {case['expected']}")
    print(f"    top1     : {case['top1_source']}  (score={case['top1_score']})")
    print(f"    top3     : {case['top3']}")


def main() -> int:
    provider = _select_embedding_provider()
    print("=" * 60)
    print(f"RAG 语义检索质量验证")
    print(f"当前 Embedding Provider: {_provider_name(provider)}")
    print(f"测试用例数: {len(TEST_CASES)}")
    print("=" * 60)

    # 用全局 rag 单例（它的 provider 与上面一致）
    from app.tools.rag_service import rag  # noqa: E402

    if not rag.is_available():
        print("ChromaDB 不可用，无法测试。")
        return 1

    results = [run_case(rag, q, e, d) for q, e, d in TEST_CASES]
    for i, r in enumerate(results, 1):
        print_result(r, i)

    hit_count = sum(1 for r in results if r["hit"])
    avg_score = sum(r["top1_score"] for r in results) / len(results) if results else 0.0
    print("\n" + "=" * 60)
    print(f"命中率: {hit_count}/{len(results)}  ({hit_count * 100 // len(results)}%)")
    print(f"平均 top1 score: {avg_score:.4f}")
    print("=" * 60)

    # 命中率 >= 60% 视为通过（语义检索不可能 100% 命中预期）
    if hit_count >= len(results) * 0.6:
        print("结论: PASS  语义检索可用")
        return 0
    print("结论: FAIL  语义检索质量不达标，请检查 Embedding 配置")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
