"""命令行入口测试。"""

from desktop_memo.cli import build_parser


def test_parser_has_version_flag():
    parser = build_parser()
    assert any(action.dest == "version" for action in parser._actions)


def test_parser_has_hidden_flag():
    parser = build_parser()
    ns = parser.parse_args(["--hidden"])
    assert ns.hidden is True


def test_hidden_defaults_to_false():
    parser = build_parser()
    assert parser.parse_args([]).hidden is False