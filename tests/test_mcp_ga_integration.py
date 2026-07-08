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
