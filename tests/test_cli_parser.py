from zddv.cli import build_parser, cmd_formal_bmc


def test_formal_bmc_subparser_is_registered_once():
    parser = build_parser()

    args = parser.parse_args(["formal-bmc", "--depth", "20"])

    assert args.command == "formal-bmc"
    assert args.depth == 20
    assert args.timeout is None
    assert args.func is cmd_formal_bmc
