"""固定业务节点契约；模型输出不能新增执行工具。"""
KINDS = frozenset({'READ_FACTS', 'RESEARCH', 'REQUIRE_INPUT', 'ASSESS', 'PLAN', 'CONFIRM',
    'VERIFY', 'SUBMIT', 'SETTLEMENT', 'REREAD_CASH', 'BUY_PLAN', 'RESULT'})


def validate_graph(nodes: list[dict]) -> None:
    by_id = {node['node_id']: node for node in nodes}
    if len(by_id) != len(nodes) or not nodes:
        raise ValueError('任务节点为空或重复')
    visited, visiting = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError('任务依赖存在环')
        if key in visited:
            return
        if key not in by_id:
            raise ValueError('任务依赖不存在')
        row = by_id[key]
        if row['kind'] not in KINDS or not row.get('completion_condition'):
            raise ValueError('任务工具或完成条件不合法')
        visiting.add(key)
        for dependency in row.get('depends_on', []):
            visit(dependency)
        visiting.remove(key); visited.add(key)
    for key in by_id:
        visit(key)


def ready(nodes: list[dict]) -> list[dict]:
    done = {node['node_id'] for node in nodes if node['status'] == 'COMPLETED'}
    return [node for node in nodes if node['status'] in {'READY', 'WAITING'}
        and set(node.get('depends_on', [])) <= done]
