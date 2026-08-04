"""隐藏测试执行器测试。

覆盖范围：
- app/eval/hidden_eval.py：run_hidden_eval / HiddenEvalResult
- 验证黄金用例（input → expected）的真实通过率计算
- 验证异常用例（raises）与失败用例的判定
- 验证无文件 / AST 校验失败等边界场景
"""
from __future__ import annotations

from app.eval.hidden_eval import run_hidden_eval


def _file(path: str, content: str) -> dict:
    """构造代码文件 dict。"""
    return {"path": path, "content": content, "language": "python"}


def test_hidden_eval_all_pass() -> None:
    """正确实现：全部黄金用例通过，golden_pass_rate=1.0。"""
    files = [
        _file(
            "main.py",
            "def add(a, b):\n    return a + b\n",
        )
    ]
    golden = {
        "import": "from main import add",
        "cases": [
            {"call": "add(2, 3)", "expected": 5},
            {"call": "add(1.5, 2.5)", "expected": 4.0},
            {"call": "add(-1, 1)", "expected": 0},
        ],
    }

    r = run_hidden_eval(files, golden=golden)

    assert r.error == ""
    assert r.golden_total == 3
    assert r.golden_passed == 3
    assert r.golden_pass_rate == 1.0
    assert r.failed_cases == []


def test_hidden_eval_partial_fail() -> None:
    """部分失败实现：golden_pass_rate 按真实通过率计算。"""
    files = [
        _file(
            "main.py",
            "def add(a, b):\n    return a + b + 1\n",  # 错误实现
        )
    ]
    golden = {
        "import": "from main import add",
        "cases": [
            {"call": "add(2, 3)", "expected": 5},
            {"call": "add(1, 1)", "expected": 2},
        ],
    }

    r = run_hidden_eval(files, golden=golden)

    assert r.error == ""
    assert r.golden_total == 2
    assert r.golden_passed == 0
    assert r.golden_pass_rate == 0.0
    assert len(r.failed_cases) == 2


def test_hidden_eval_raises_case() -> None:
    """raises 用例：除零应抛 ZeroDivisionError。"""
    files = [
        _file(
            "main.py",
            "class Calculator:\n"
            "    def divide(self, a, b):\n"
            "        return a / b\n",
        )
    ]
    golden = {
        "import": "from main import Calculator",
        "cases": [
            {"call": "Calculator().divide(10, 2)", "expected": 5.0},
            {"call": "Calculator().divide(10, 0)", "raises": "ZeroDivisionError"},
        ],
    }

    r = run_hidden_eval(files, golden=golden)

    assert r.error == ""
    assert r.golden_total == 2
    assert r.golden_passed == 2
    assert r.golden_pass_rate == 1.0


def test_hidden_eval_no_python_files() -> None:
    """无 Python 文件：返回 error。"""
    r = run_hidden_eval([_file("readme.txt", "hello")], golden={"import": "", "cases": []})

    assert r.error == "无 Python 文件可验证"
    assert r.golden_total == 0


def test_hidden_eval_ast_invalid() -> None:
    """生成代码 AST 校验失败：无文件可执行，返回 error。"""
    files = [
        _file("main.py", "def broken(:\n    pass\n"),  # 语法错误
    ]
    golden = {"import": "from main import broken", "cases": []}

    r = run_hidden_eval(files, golden=golden)

    assert r.error == "生成代码未通过 AST 校验，无文件可执行"
    assert r.golden_total == 0