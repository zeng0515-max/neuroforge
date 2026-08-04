"""向量知识库初始化脚本（首次部署执行）。

用途：向 ChromaDB 灌入种子知识（PEP8 摘要 / Python 标准库 API / 最佳实践），
     使 RAG 检索在无外部知识时也能返回有意义的上下文。

使用：
    python scripts/init_vector_db.py
    python scripts/init_vector_db.py --clear   # 先清空再灌入
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 把项目根目录加入 sys.path，便于直接 `python scripts/xxx.py` 运行
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.tools.rag_service import rag  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("init_vector_db")


# ------------------------------------------------------------------ #
#  种子知识：编码规范 / API 文档 / 最佳实践
# ------------------------------------------------------------------ #
SEED_DOCUMENTS: list[tuple[str, str]] = [
    # ---------- 编码规范 ----------
    (
        "PEP8 命名约定：函数与变量名使用 snake_case；类名使用 CamelCase；常量使用"
        " UPPER_SNAKE_CASE；私有成员以下划线开头；避免使用 l / I / O 等易混淆单字符名。",
        "pep8_naming",
    ),
    (
        "PEP8 类型标注规范：函数参数与返回值应标注类型；可选参数用 Optional[T] 或 T | None；"
        "集合类型用 list[int] / dict[str, Any] 而非 List / Dict（Python 3.9+）。",
        "pep8_type_hint",
    ),
    (
        "PEP8 文档字符串：模块 / 类 / 公开函数必须有 docstring；推荐 Google 风格，"
        "包含 Args / Returns / Raises 三段；单行不超过 79 字符，多行换行。",
        "pep8_docstring",
    ),
    # ---------- Python 标准库 API ----------
    (
        "pathlib.Path：面向对象的路径操作。Path('a') / 'b' 拼接；.read_text(encoding='utf-8') "
        "读取；.write_text() 写入；.exists() / .is_file() / .mkdir(parents=True, exist_ok=True) "
        "判断与创建。优先使用 pathlib 而非 os.path。",
        "api_pathlib",
    ),
    (
        "json 模块：json.dumps(obj, ensure_ascii=False, indent=2) 序列化为中文友好字符串；"
        "json.loads(text) 反序列化；处理文件用 json.dump / json.load。"
        "反序列化不可信数据时用 json.loads 而非 eval。",
        "api_json",
    ),
    (
        "hashlib：hashlib.sha256(data.encode()).hexdigest() 计算哈希；"
        "hashlib.md5 仅用于非安全场景；密码哈希用 hashlib.pbkdf2_hmac 或 bcrypt。",
        "api_hashlib",
    ),
    (
        "functools.lru_cache(maxsize=128)：对纯函数自动缓存结果；"
        "functools.wraps：装饰器保留原函数元信息；"
        "itertools.chain / groupby / islice：高效迭代器工具。",
        "api_functools_itertools",
    ),
    # ---------- 最佳实践 ----------
    (
        "异常处理最佳实践：捕获具体异常而非裸 except；用 try/except/else/finally 完整结构；"
        "记录异常用 logger.exception() 自动带堆栈；不要吞掉异常（except: pass）；"
        "对外抛出前用 raise ... from err 保留原因链。",
        "best_exception",
    ),
    (
        "安全编码最佳实践：禁止 eval / exec / compile 执行动态代码；禁止 subprocess(shell=True) "
        "防止命令注入；文件路径用 pathlib 校验防止路径穿越（.. / 绝对路径）；"
        "不可信输入做白名单校验；密钥与凭据放环境变量而非硬编码。",
        "best_security",
    ),
    (
        "pytest 单元测试规范：测试函数以 test_ 开头；断言用 assert 而非 self.assertEqual；"
        "用 fixture 复用前置数据；用 parametrize 做参数化测试；"
        "覆盖 normal / edge / error 三类路径；mock 外部依赖用 monkeypatch。",
        "best_pytest",
    ),
    (
        "类型驱动开发：先定义 dataclass / pydantic BaseModel 描述数据结构，再写处理函数；"
        "用类型标注让 IDE 与 mypy 静态检查；Optional 显式表达可空；"
        "Literal 限定取值集合；TypedDict 描述 dict 结构。",
        "best_typing",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 ChromaDB 向量知识库种子数据")
    parser.add_argument(
        "--clear", action="store_true", help="灌入前先清空当前 collection",
    )
    args = parser.parse_args()

    if not rag.is_available():
        logger.error("ChromaDB 不可用，无法灌入。请检查依赖与 CHROMA_PERSIST_DIR 配置。")
        return 1

    before = rag.count()
    logger.info("当前 collection 文档数：%d", before)

    if args.clear and before > 0:
        logger.warning("执行清空 ...")
        rag.clear()
        logger.info("清空完成，剩余：%d", rag.count())

    added = 0
    for content, source in SEED_DOCUMENTS:
        ok = rag.add_texts(
            texts=[content],
            metadatas=[{"source": source, "category": "seed"}],
            source=source,
        )
        if ok > 0:
            added += ok
        else:
            logger.warning("灌入失败：%s", source)

    after = rag.count()
    logger.info("灌入完成：新增 %d 条，当前总数 %d 条", added, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
