from __future__ import annotations

import pytest
from agents import schema, tools


class TestExecuteToolCall:
    def test_place_troops(self) -> None:
        result = tools.execute_tool_call(tool_name="place_troops", arguments={"territory": "Brazil", "count": 5})
        assert result == {"territory": "Brazil", "count": 5}

    def test_redeem_cards(self) -> None:
        cards = ["Infantry", "Cavalry", "Artillery"]
        result = tools.execute_tool_call(tool_name="redeem_cards", arguments={"cards": cards})
        assert result == {"action": "redeem_cards", "cards": cards}

    def test_attack_maps_from_to_from_territory(self) -> None:
        result = tools.execute_tool_call(
            tool_name="attack",
            arguments={"from": "Brazil", "to": "Peru", "attackers": 3},
        )
        assert result == {"from": "Brazil", "to": "Peru", "attackers": 3}

    def test_end_attacks(self) -> None:
        result = tools.execute_tool_call(tool_name="end_attacks", arguments={})
        assert result == {"action": "end_attacks"}

    def test_fortify_maps_from_to_from_territory(self) -> None:
        result = tools.execute_tool_call(
            tool_name="fortify",
            arguments={"from": "Brazil", "to": "Argentina", "count": 2},
        )
        assert result == {"from": "Brazil", "to": "Argentina", "count": 2}

    def test_skip_fortify(self) -> None:
        result = tools.execute_tool_call(tool_name="skip_fortify", arguments={})
        assert result == {"action": "skip"}

    def test_say(self) -> None:
        result = tools.execute_tool_call(tool_name="say", arguments={"text": "hello"})
        assert result == {"text": "hello"}

    def test_pass_turn(self) -> None:
        result = tools.execute_tool_call(tool_name="pass_turn", arguments={})
        assert result == {"action": "pass"}

    def test_unknown_tool_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            tools.execute_tool_call(tool_name="nonexistent", arguments={})


class TestAnthropicTools:
    def test_structure(self) -> None:
        result = schema.anthropic_tools(functions=[tools.place_troops])
        tool = result[0]
        assert tool["name"] == "place_troops"
        assert "description" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"

    def test_properties_and_required(self) -> None:
        result = schema.anthropic_tools(functions=[tools.place_troops])
        input_schema = result[0]["input_schema"]
        assert input_schema["properties"]["territory"]["type"] == "string"
        assert input_schema["properties"]["count"]["type"] == "integer"
        assert input_schema["required"] == ["territory", "count"]

    def test_schema_name_override(self) -> None:
        result = schema.anthropic_tools(functions=[tools.attack])
        properties = result[0]["input_schema"]["properties"]
        assert "from" in properties
        assert "from_territory" not in properties

    def test_no_params_produces_empty_properties(self) -> None:
        result = schema.anthropic_tools(functions=[tools.end_attacks])
        input_schema = result[0]["input_schema"]
        assert input_schema["properties"] == {}
        assert "required" not in input_schema

    def test_array_type(self) -> None:
        result = schema.anthropic_tools(functions=[tools.redeem_cards])
        cards_prop = result[0]["input_schema"]["properties"]["cards"]
        assert cards_prop["type"] == "array"
        assert cards_prop["items"] == {"type": "string"}

    def test_descriptions_included(self) -> None:
        result = schema.anthropic_tools(functions=[tools.place_troops])
        territory_prop = result[0]["input_schema"]["properties"]["territory"]
        assert "description" in territory_prop
        assert territory_prop["description"] == "Territory name to reinforce"

    def test_all_game_tools(self) -> None:
        result = schema.anthropic_tools(functions=tools.GAME_TOOLS)
        names = [t["name"] for t in result]
        assert names == ["place_troops", "redeem_cards", "attack", "end_attacks", "fortify", "skip_fortify"]

    def test_all_chat_tools(self) -> None:
        result = schema.anthropic_tools(functions=tools.CHAT_TOOLS)
        names = [t["name"] for t in result]
        assert names == ["say", "pass_turn"]


class TestOpenAITools:
    def test_structure(self) -> None:
        result = schema.openai_tools(functions=[tools.place_troops])
        tool = result[0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "place_troops"
        assert "parameters" in tool["function"]

    def test_schema_name_override(self) -> None:
        result = schema.openai_tools(functions=[tools.fortify])
        properties = result[0]["function"]["parameters"]["properties"]
        assert "from" in properties
        assert "from_territory" not in properties

    def test_no_params_produces_empty_properties(self) -> None:
        result = schema.openai_tools(functions=[tools.skip_fortify])
        parameters = result[0]["function"]["parameters"]
        assert parameters["properties"] == {}
        assert "required" not in parameters

    def test_all_game_tools(self) -> None:
        result = schema.openai_tools(functions=tools.GAME_TOOLS)
        names = [t["function"]["name"] for t in result]
        assert names == ["place_troops", "redeem_cards", "attack", "end_attacks", "fortify", "skip_fortify"]


class TestPromptSchemaText:
    def test_includes_all_tool_names(self) -> None:
        text = schema.prompt_schema_text(functions=tools.GAME_TOOLS)
        for fn in tools.GAME_TOOLS:
            assert fn.__name__ in text  # type: ignore[union-attr]

    def test_no_param_tool_shows_return_value(self) -> None:
        text = schema.prompt_schema_text(functions=[tools.end_attacks])
        assert '"action": "end_attacks"' in text

    def test_skip_fortify_shows_return_value(self) -> None:
        text = schema.prompt_schema_text(functions=[tools.skip_fortify])
        assert '"action": "skip"' in text

    def test_param_tool_shows_type_placeholders(self) -> None:
        text = schema.prompt_schema_text(functions=[tools.place_troops])
        assert '"territory"' in text
        assert '"<string>"' in text
        assert "<integer>" in text

    def test_schema_name_in_prompt(self) -> None:
        text = schema.prompt_schema_text(functions=[tools.attack])
        assert '"from"' in text
        assert "from_territory" not in text

    def test_array_type_placeholder(self) -> None:
        text = schema.prompt_schema_text(functions=[tools.redeem_cards])
        assert '["<string>", ...]' in text
