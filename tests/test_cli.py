"""命令行入口的基础测试。"""

from desktop_memo.cli import build_parser, main


def test_parser_has_version_flag():
    parser = build_parser()
    assert any(action.dest == "version" for action in parser._actions)


def test_main_returns_zero(capsys):
    code = main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "桌面备忘录已启动" in out
