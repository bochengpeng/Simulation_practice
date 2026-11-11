from station.python_cell import CellExecutionError, PythonCell


def test_reset_start_state():
    cell = PythonCell()
    state = cell.read_state()
    assert state["parts"]["BOLT"] == "SLOT_1"
    assert state["parts"]["SPACER_SHORT"] == "SLOT_2"
    assert state["parts"]["SPACER_LONG"] == "BIN_3"
    assert state["tool_holds"] is None


def test_pick_and_place_round_trip():
    cell = PythonCell()
    cell.pick("SPACER_SHORT", "SLOT_2")
    assert cell.locate("SPACER_SHORT") == "TOOL"
    assert cell.read_state()["tool_holds"] == "SPACER_SHORT"
    cell.place("SPACER_SHORT", "RETURN")
    assert cell.locate("SPACER_SHORT") == "RETURN"
    assert cell.read_state()["tool_holds"] is None


def test_pick_wrong_source_raises():
    cell = PythonCell()
    try:
        cell.pick("SPACER_SHORT", "BIN_2")
        assert False, "expected CellExecutionError"
    except CellExecutionError:
        pass


def test_place_without_holding_raises():
    cell = PythonCell()
    try:
        cell.place("BOLT", "RETURN")
        assert False, "expected CellExecutionError"
    except CellExecutionError:
        pass


def test_inject_failure_is_visible_in_state():
    cell = PythonCell()
    cell.inject_failure("SPACER_LONG", "BIN_2")
    assert cell.locate("SPACER_LONG") == "BIN_2"


def test_run_function_dispatch_allowlist():
    cell = PythonCell()
    result = cell.run_function("locate", {"part": "BOLT"})
    assert result["result"] == "SLOT_1"
    try:
        cell.run_function("delete_everything", {})
        assert False, "expected CellExecutionError"
    except CellExecutionError:
        pass
