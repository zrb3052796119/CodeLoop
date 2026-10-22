"""Public compatibility checks for the existing filtered registry API."""

from pathlib import Path

from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=name, input_schema={"type": "object"},
        validator=lambda values: values,
        run=lambda values, context: ToolResult(ok=True, output=name),
    )


def test_filter_preserves_owner_and_selected_execution(tmp_path: Path) -> None:
    owner = ToolRegistry([tool("first"), tool("second")])
    child = owner.filtered(lambda entry: entry.name == "first")
    assert owner.find("first") is not None
    assert owner.find("second") is not None
    assert child.find("first") is not None
    assert child.find("second") is None
    result = child.execute("first", {}, ToolContext(cwd=str(tmp_path)))
    assert result.ok and result.output == "first"


def test_nested_filter_keeps_each_view_independent(tmp_path: Path) -> None:
    owner = ToolRegistry([tool("first"), tool("second"), tool("third")])
    child = owner.filtered(lambda entry: entry.name != "third")
    nested = child.filtered(lambda entry: entry.name == "second")
    assert set(owner.list_all()) == {"first", "second", "third"}
    assert set(child.list_all()) == {"first", "second"}
    assert set(nested.list_all()) == {"second"}
    result = nested.execute("second", {}, ToolContext(cwd=str(tmp_path)))
    assert result.ok and result.output == "second"


def test_filtered_empty_view_does_not_mutate_the_owner() -> None:
    owner = ToolRegistry([tool("first")])
    child = owner.filtered(lambda _entry: False)
    assert child.list_all() == []
    assert owner.list_all() == ["first"]
