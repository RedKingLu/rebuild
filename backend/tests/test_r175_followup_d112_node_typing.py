"""R17.5-P4-FOLLOWUP / D-112：P3 节点分型 + P4 方案B 阶段级评审 gate。

覆盖：
- classify_node_type 保守启发式（选型/决策/PoC/验证 → 非 execution，其余 → execution）。
- P3 _persist_task_graph 按标题落 node_type（非硬编码 execution）。
- P4 非 execution 节点收集为待用户评审项、写 pending_review、不假通过（见 test_r11_c3_p4_handler）。

asyncio_mode=auto。
"""

from app.services.planning_service import classify_node_type, VALID_NODE_TYPES


def test_classify_execution_default():
    # 代码产出类任务默认 execution
    assert classify_node_type("迁移 MicroDBHelper.cs 到达梦 DM8") == "execution"
    assert classify_node_type("改写 SqlClient 数据访问层") == "execution"
    assert classify_node_type("") == "execution"
    assert classify_node_type("port ASP.NET WebForms page") == "execution"


def test_classify_decision():
    assert classify_node_type("目标数据库选型确认") == "decision"
    assert classify_node_type("技术路线抉择：ORM 方案") == "decision"
    assert classify_node_type("架构决策：分层结构") == "decision"
    assert classify_node_type("technical decision on runtime") == "decision"


def test_classify_poc():
    assert classify_node_type("达梦驱动连接 PoC") == "poc"
    assert classify_node_type("概念验证：ARM64 编译") == "poc"
    assert classify_node_type("spike: async pipeline") == "poc"


def test_classify_verification():
    assert classify_node_type("迁移结果回归测试") == "verification"
    assert classify_node_type("功能验证与验收") == "verification"
    assert classify_node_type("verify data integrity") == "verification"


def test_classify_precedence_decision_over_verification():
    # 同时含"决策"与"验证" → decision 优先（不因附带"验证"字样降级）
    assert classify_node_type("架构决策并验证可行性") == "decision"


def test_valid_node_types_enum():
    assert set(VALID_NODE_TYPES) == {"execution", "decision", "poc", "verification"}
    # classifier 只产出这四类之一
    for title in ["随便写点代码", "选型", "PoC", "回归测试"]:
        assert classify_node_type(title) in VALID_NODE_TYPES


def test_persist_task_graph_types_nodes_by_title():
    """P3 _persist_task_graph 按节点标题落 node_type（不再硬编码 execution）。"""
    from app.services.planning_service import PlanningService
    from app.core.database import get_session
    from app.models.task_graph import TaskGraph, TaskNode

    pid = "proj-d112-p3typing"
    svc = PlanningService()
    nodes = [
        {"node_id": "n0", "task_plan_ref": "tp0", "title": "迁移数据访问层代码",
         "input_refs": [], "required_resources": [], "model_policy_override": None,
         "permission_boundary": "workspace_read", "risk_level": "L1"},
        {"node_id": "n1", "task_plan_ref": "tp1", "title": "目标数据库选型确认",
         "input_refs": [], "required_resources": [], "model_policy_override": None,
         "permission_boundary": "workspace_read", "risk_level": "L1"},
        {"node_id": "n2", "task_plan_ref": "tp2", "title": "迁移结果回归测试",
         "input_refs": [], "required_resources": [], "model_policy_override": None,
         "permission_boundary": "workspace_read", "risk_level": "L1"},
    ]
    tgid = svc._persist_task_graph(pid, "run-d112", "p3", "sp-d112", nodes, [], "test-model")
    db = get_session()
    try:
        rows = {n.node_id: n.node_type
                for n in db.query(TaskNode).filter(TaskNode.task_graph_id == tgid).all()}
    finally:
        db.close()
    assert rows["n0"] == "execution"
    assert rows["n1"] == "decision"
    assert rows["n2"] == "verification"
