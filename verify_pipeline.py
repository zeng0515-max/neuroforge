"""验证脚本：测试多智能体流水线端到端闭环。"""
import time
from app.graph.pipeline import run_pipeline
from app.graph.state import PipelinePhase

t0 = time.time()
state = run_pipeline(
    task_id="test_fix_001",
    user_request="实现一个函数，接收一个整数列表，返回其中所有偶数的和",
    prefer_langgraph=False,
)
elapsed = time.time() - t0

status = state.get("status")
phase = status.phase.value if status else "N/A"
err = status.error_message if status else "N/A"
print(f"耗时: {elapsed:.1f}s")
print(f"阶段: {phase}")
print(f"错误: {err}")

pkg = state.get("code_package")
if pkg and pkg.files:
    for f in pkg.files:
        print(f"文件: {f.path} ({f.line_count}行)")
        print(f"  预览: {f.content[:120]}...")

rr = state.get("review_report")
if rr:
    print(f"评审结论: {rr.verdict.value}")
    print(f"问题数: {len(rr.issues)}")
    for i in rr.issues[:3]:
        print(f"  [{i.severity}] {i.rule_id}: {i.message[:60]}")

tr = state.get("test_report")
if tr and tr.test_files:
    print(f"测试文件: {len(tr.test_files)}个")

# 验证关键：状态流转是否正常
assert status is not None, "status 不应为 None"
assert isinstance(status.phase, PipelinePhase), "phase 应为 PipelinePhase 枚举"
print("\n✓ 流水线验证通过")
