import json, os, sys, pytest
from unittest.mock import Mock, patch, MagicMock

# Ensure project root is importable so `ga` and `agent_loop` resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# Test mcp_call tool schema
def test_mcp_call_schema_exists():
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'tools_schema.json')
    with open(schema_path) as f:
        schemas = json.load(f)
    names = [s['function']['name'] for s in schemas]
    assert 'mcp_call' in names


def test_mcp_call_schema_required_fields():
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'tools_schema.json')
    with open(schema_path) as f:
        schemas = json.load(f)
    mcp_schema = [s for s in schemas if s['function']['name'] == 'mcp_call'][0]
    props = mcp_schema['function']['parameters']['properties']
    assert 'server' in props
    assert 'tool' in props
    assert 'arguments' in props
    required = mcp_schema['function']['parameters']['required']
    assert 'server' in required
    assert 'tool' in required


# Test do_mcp_call with mock
def test_do_mcp_call_success():
    from ga import GenericAgentHandler
    handler = GenericAgentHandler.__new__(GenericAgentHandler)
    handler._mcp_manager = Mock()
    handler._mcp_manager.call_tool.return_value = {"result": "ok"}

    from agent_loop import exhaust
    gen = handler.do_mcp_call({'server': 'test', 'tool': 'echo', 'arguments': {}}, None)
    result = exhaust(gen)
    assert result.data == {"result": "ok"}


def test_do_mcp_call_no_manager():
    from ga import GenericAgentHandler
    handler = GenericAgentHandler.__new__(GenericAgentHandler)
    handler._mcp_manager = None

    from agent_loop import exhaust
    gen = handler.do_mcp_call({'server': 'test', 'tool': 'echo'}, None)
    result = exhaust(gen)
    assert result is None or result.data is None


def test_do_mcp_call_error():
    from ga import GenericAgentHandler
    handler = GenericAgentHandler.__new__(GenericAgentHandler)
    handler._mcp_manager = Mock()
    handler._mcp_manager.call_tool.side_effect = Exception("connection refused")

    from agent_loop import exhaust
    gen = handler.do_mcp_call({'server': 'test', 'tool': 'echo'}, None)
    result = exhaust(gen)
    assert result is None or result.data is None


# Test MCP system prompt injection (修复A: spec mcp-client SHALL inject tools)
def test_get_system_prompt_injects_mcp_servers():
    """Connected MCP server tools appear in system prompt under [MCP Servers]."""
    import agentmain
    from mcp_client import MCPToolRegistry

    registry = MCPToolRegistry()
    registry._tools = {'test_srv': [{'name': 'echo', 'description': 'echo tool'}]}
    mock_mgr = Mock()
    mock_mgr.get_all_tools_summary = registry.get_all_tools_summary

    with patch.object(agentmain.MCPClientManager, 'get_instance', return_value=mock_mgr), \
         patch('agentmain.get_global_memory', return_value=''):
        prompt = agentmain.get_system_prompt()

    assert '[MCP Servers]' in prompt
    assert 'test_srv' in prompt
    assert 'echo' in prompt


def test_get_system_prompt_no_servers_message():
    """When no MCP servers connected, prompt includes guidance message."""
    import agentmain

    mock_mgr = Mock()
    mock_mgr.get_all_tools_summary = Mock(return_value='')

    with patch.object(agentmain.MCPClientManager, 'get_instance', return_value=mock_mgr), \
         patch('agentmain.get_global_memory', return_value=''):
        prompt = agentmain.get_system_prompt()

    assert '[MCP] No servers connected' in prompt


# Test MCP distill hook (修复B: spec skill-discovery L2 distillation)
def test_mcp_distill_hook_logs_call(tmp_path):
    """tool_after hook records mcp_call to log file."""
    import plugins.mcp_distill as distill
    from agent_loop import StepOutcome

    log_file = tmp_path / 'mcp_call_log.md'
    with patch.object(distill, '_LOG_PATH', str(log_file)):
        ctx = {
            'tool_name': 'mcp_call',
            'args': {'server': 'fs', 'tool': 'read', 'arguments': {'path': '/x'}},
            'ret': StepOutcome(data='content', next_prompt=None),
        }
        distill._on_mcp_call_after(ctx)
    assert log_file.exists()
    content = log_file.read_text(encoding='utf-8')
    assert 'fs/read' in content
    assert 'OK' in content


def test_mcp_distill_hook_ignores_non_mcp(tmp_path):
    """tool_after hook ignores non-mcp_call tools."""
    import plugins.mcp_distill as distill

    log_file = tmp_path / 'mcp_call_log.md'
    with patch.object(distill, '_LOG_PATH', str(log_file)):
        ctx = {'tool_name': 'file_read', 'args': {}, 'ret': None}
        distill._on_mcp_call_after(ctx)
    assert not log_file.exists()


# Test get_skill_detail tool (lazy-load progressive disclosure)
# gap 收窄：覆盖 do_ 方法 + dispatch 端到端路径 + 双 schema 同步
def test_get_skill_detail_schema_exists_both_files():
    """get_skill_detail defined in both tools_schema.json and tools_schema_cn.json (避免重蹈 mcp_call 不同步)."""
    base = os.path.join(os.path.dirname(__file__), '..', 'assets')
    for fn in ('tools_schema.json', 'tools_schema_cn.json'):
        with open(os.path.join(base, fn)) as f:
            names = [s['function']['name'] for s in json.load(f)]
        assert 'get_skill_detail' in names, f'get_skill_detail missing in {fn}'


def test_get_skill_detail_schema_required_name():
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'tools_schema.json')
    with open(schema_path) as f:
        schemas = json.load(f)
    schema = [s for s in schemas if s['function']['name'] == 'get_skill_detail'][0]
    props = schema['function']['parameters']['properties']
    assert 'name' in props
    assert 'name' in schema['function']['parameters']['required']


def test_do_get_skill_detail_success():
    """do_get_skill_detail returns detail dict and next_prompt hints file_read for full content."""
    from agent_loop import exhaust
    from ga import GenericAgentHandler
    detail = {'name': 'comet', 'description': 'd', 'skill_md_path': '/x/SKILL.md',
              'has_scripts': True, 'has_references': False, 'has_assets': False}
    with patch('ga.get_skill_detail', return_value=detail):
        handler = GenericAgentHandler.__new__(GenericAgentHandler)
        handler._get_anchor_prompt = Mock(return_value='ANCHOR')
        gen = handler.do_get_skill_detail({'name': 'comet'}, None)
        result = exhaust(gen)
    assert result.data == detail
    assert 'file_read' in result.next_prompt


def test_do_get_skill_detail_not_found():
    """not-found returns error dict; next_prompt omits file_read hint."""
    from agent_loop import exhaust
    from ga import GenericAgentHandler
    err = {'status': 'error', 'msg': 'not found', 'available': ['a', 'b']}
    with patch('ga.get_skill_detail', return_value=err):
        handler = GenericAgentHandler.__new__(GenericAgentHandler)
        handler._get_anchor_prompt = Mock(return_value='ANCHOR')
        gen = handler.do_get_skill_detail({'name': 'nope'}, None)
        result = exhaust(gen)
    assert result.data == err
    assert 'file_read' not in result.next_prompt


def test_do_get_skill_detail_exception():
    """exception path returns error StepOutcome without touching _get_anchor_prompt."""
    from agent_loop import exhaust
    from ga import GenericAgentHandler
    with patch('ga.get_skill_detail', side_effect=Exception('boom')):
        handler = GenericAgentHandler.__new__(GenericAgentHandler)
        gen = handler.do_get_skill_detail({'name': 'x'}, None)
        result = exhaust(gen)
    assert result.data == {'status': 'error', 'msg': 'boom'}
    assert result.next_prompt == '\n'


def test_dispatch_routes_get_skill_detail():
    """End-to-end via handler.dispatch: tool_name -> do_get_skill_detail -> StepOutcome."""
    from agent_loop import exhaust
    from ga import GenericAgentHandler
    detail = {'name': 'vue', 'description': 'd', 'skill_md_path': '/y/SKILL.md'}
    with patch('ga.get_skill_detail', return_value=detail):
        handler = GenericAgentHandler.__new__(GenericAgentHandler)
        handler._get_anchor_prompt = Mock(return_value='ANCHOR')
        gen = handler.dispatch('get_skill_detail', {'name': 'vue'}, None)
        result = exhaust(gen)
    assert result.data == detail
    assert 'file_read' in result.next_prompt
