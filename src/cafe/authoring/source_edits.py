"""Conservative YAML source-span editor; unaffected bytes are never serialized."""

from __future__ import annotations

import re

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .requests import Operation, UniqueLoader, decode_request


def dump(value):
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=1000)


def source_text(path):
    """Decode source bytes without universal-newline translation."""
    return path.read_bytes().decode("utf-8")


def source_newlines(text):
    """Use a reversible LF editing view; ambiguous newline shapes fail closed."""
    stripped = text.replace("\r\n", "")
    if "\r" in stripped or "\r\n" in text and "\n" in stripped:
        raise ValueError("Mixed or bare-CR source newlines require a manual edit")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline


def _line_end(text, index):
    end = text.find("\n", index)
    return len(text) if end < 0 else end + 1


def _safe(text, node, *, replacement=True):
    span = text[node.start_mark.index : node.end_mark.index]
    # Ambiguous comments and YAML identity/merge constructs are deliberately unsupported.
    if node.flow_style if isinstance(node, (MappingNode, SequenceNode)) else False:
        if span not in ("{}", "[]"):
            raise ValueError("Editing nonempty flow collections is unsupported")
    tokens = [
        token
        for token in yaml.scan(text, Loader=UniqueLoader)
        if node.start_mark.index <= token.start_mark.index < node.end_mark.index
    ]
    if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for t in tokens):
        raise ValueError("Anchors/aliases in the edited span are unsupported")
    if re.search(r"(?:^|\n)\s*<<:", span) or replacement and re.search(r"(^|\s)#", span):
        raise ValueError("Comments/merge keys in replacement spans require a manual edit")


def _node(text, path):
    node = yaml.compose(text, Loader=UniqueLoader)
    for part in path:
        if not isinstance(node, MappingNode):
            raise ValueError("Path traverses a non-mapping; use a keyed collection operation")
        matches = [v for k, v in node.value if k.value == part]
        if not matches:
            return None
        node = matches[0]
    return node


def _replace(text, node, value):
    _safe(text, node)
    if isinstance(node, ScalarNode) and not isinstance(value, (dict, list)):
        encoded = dump(value).removesuffix("...\n").rstrip("\n")
        if "\n" not in encoded:
            separator = "\n" if node.style in {"|", ">"} else ""
            return text[: node.start_mark.index] + encoded + separator + text[node.end_mark.index :]
    start = node.start_mark.index
    end = node.end_mark.index
    if isinstance(node, ScalarNode) or node.flow_style:
        # Inline scalar/empty collection to a block value.
        indent = node.start_mark.column
        line_start = text.rfind("\n", 0, start) + 1
        key_indent = len(text[line_start:]) - len(text[line_start:].lstrip(" "))
        rendered = "\n" + "".join(
            " " * (key_indent + 2) + line for line in dump(value).splitlines(True)
        )
        rendered = (
            rendered
            if isinstance(node, ScalarNode) and node.style in {"|", ">"}
            else rendered.rstrip("\n")
        )
        return text[:start] + rendered + text[end:]
    indent = node.start_mark.column
    if value in ([], {}):
        indent = max(indent, 2)
    start = text.rfind("\n", 0, start) + 1
    if end > 0 and text[end - 1] != "\n":
        end = _line_end(text, end)
    rendered = "".join(" " * indent + line for line in dump(value).splitlines(True))
    return text[:start] + rendered + text[end:]


def _append(text, node, addition):
    if not isinstance(node, (MappingNode, SequenceNode)):
        raise ValueError("Upsert target must be a mapping or sequence")
    if not node.value:
        return _replace(text, node, addition)
    # Do not alter existing entries (or their comments).
    if node.flow_style:
        raise ValueError("Appending to nonempty flow collections is unsupported")
    _safe(text, node, replacement=False)
    last = node.value[-1][1] if isinstance(node, MappingNode) else node.value[-1]
    while isinstance(last, (MappingNode, SequenceNode)) and last.value:
        last = last.value[-1][1] if isinstance(last, MappingNode) else last.value[-1]
    index = last.end_mark.index
    if index > 0 and text[index - 1] != "\n":
        index = _line_end(text, index)
    rendered = "".join(
        " " * node.start_mark.column + line for line in dump(addition).splitlines(True)
    )
    return text[:index] + rendered + text[index:]


def edit_yaml(text: str, operation: Operation) -> str:
    data = decode_request(text)
    node = _node(text, operation.path)
    old = data
    for part in operation.path:
        if not isinstance(old, dict):
            raise ValueError("Path must traverse named mappings")
        old = old.get(part)
    if node is None:
        if operation.op == "replace":
            raise ValueError("Replacement target does not exist")
        parent = _node(text, operation.path[:-1])
        if parent is None:
            raise ValueError("Parent does not exist; declare its bounded container explicitly")
        value = operation.value
        if _array_path(operation.path) and not isinstance(value, list):
            value = [value]
        return _append(text, parent, {operation.path[-1]: value})
    if operation.op == "replace":
        if old == operation.value:
            return text
        if old != operation.expected:
            raise ValueError("Expected old value does not match; preview fresh source")
        return _replace(text, node, operation.value)
    if old == operation.value:
        return text
    if isinstance(old, list):
        if operation.key:
            if not isinstance(operation.value, dict) or operation.key not in operation.value:
                raise ValueError("Keyed upsert value must declare its stable key")
            matches = [
                v
                for v in old
                if isinstance(v, dict) and v.get(operation.key) == operation.value[operation.key]
            ]
        else:
            matches = [v for v in old if v == operation.value]
        if matches:
            if matches == [operation.value]:
                return text
            raise ValueError("Upsert conflicts with existing identity; use explicit replacement")
        return _append(text, node, [operation.value])
    raise ValueError(
        "Upsert would replace existing content; use replace with expected and overwrite"
    )


def edit_section(text, section, value, expected, overwrite):
    if not overwrite:
        raise ValueError("Section replacement requires overwrite: true")
    headings = list(re.finditer(r"^## ([^\n]+)\n", text, re.M))
    matches = [i for i, h in enumerate(headings) if h.group(1) == section]
    if len(matches) != 1:
        raise ValueError("Section target is missing or ambiguous")
    i = matches[0]
    start = headings[i].end()
    end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
    old = text[start:end].strip()
    if old == value.strip():
        return text
    if old != expected:
        raise ValueError("Section expected content does not match")
    return text[:start] + "\n" + value.rstrip() + "\n\n" + text[end:]


def _array_path(path):
    """Project list shape from current runtime schema, including keyed step maps."""
    from cafe.core.playbook import PlaybookDefinition
    from cafe.skills.contracts import SkillWorkflowDeclaration

    phase = path[0] == "workflow"
    schema = (SkillWorkflowDeclaration if phase else PlaybookDefinition).model_json_schema()
    node = schema
    for part in path[1:] if phase else path:
        while "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        if "anyOf" in node:
            node = next((item for item in node["anyOf"] if item.get("type") != "null"), {})
            while "$ref" in node:
                node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        node = node.get("properties", {}).get(part, node.get("additionalProperties", {}))
        if not isinstance(node, dict):
            return False
    if "anyOf" in node:
        return any(item.get("type") == "array" for item in node["anyOf"])
    return node.get("type") == "array"
