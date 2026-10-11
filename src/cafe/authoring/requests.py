"""Versioned authoring intent, separate from authoritative runtime declarations."""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class UniqueLoader(yaml.SafeLoader):
    pass


# Keep YAML's `on` key a string, consistent with runtime YAML decoding.
UniqueLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF")
)


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise ValueError(f"Duplicate or unsupported key at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def decode_request(text: str) -> dict:
    if len(text.encode()) > 1024 * 1024:
        raise ValueError("Request exceeds the 1 MiB bound")
    value = yaml.load(text, Loader=UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError("Authoring request must be a YAML/JSON object")
    return value


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["upsert", "replace"]
    path: list[str]
    value: Any
    key: str | None = None
    expected: Any = None
    overwrite: bool = False

    @model_validator(mode="after")
    def bounded(self):
        if not self.path or any(not part or part.isdigit() for part in self.path):
            raise ValueError("Use nonempty named paths; ordinal edits are unsupported")
        if self.op == "replace" and not self.overwrite:
            raise ValueError("Replacement requires overwrite: true and expected old value")
        if self.op == "replace" and "expected" not in self.model_fields_set:
            raise ValueError("Replacement requires expected old value")
        return self


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    target: str
    mode: Literal["create", "patch"]
    declaration: dict | None = None
    sections: dict[str, str] = Field(default_factory=dict)
    references: dict[str, str] = Field(default_factory=dict)
    operations: list[Operation] = Field(default_factory=list)
    companions: list["Request"] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete(self):
        if type(self.version) is not int:
            raise ValueError("version must be integer 1")
        if self.mode == "create" and (self.declaration is None or self.operations):
            raise ValueError("Create requires declaration and no patch operations")
        if self.mode == "patch" and (self.declaration is not None or not self.operations):
            raise ValueError("Patch requires bounded operations and no declaration")
        if self.mode == "patch" and (self.sections or self.references):
            raise ValueError("Patch sections/resources with explicit replacement operations")
        if any(child.companions for child in self.companions):
            raise ValueError("Nested companion transactions are unsupported")
        return self
