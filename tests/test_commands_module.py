"""測試 commands 模組的基本匯入功能"""

def test_commands_module_can_be_imported():
    """測試 commands 模組能被正確匯入"""
    try:
        import cafe.ui.commands
        assert True
    except ImportError:
        assert False, "commands 模組無法被匯入"
