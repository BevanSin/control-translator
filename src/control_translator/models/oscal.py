"""Minimal OSCAL catalog subset.

Only the slice of the OSCAL catalog model we need: catalog -> groups -> controls,
where each control carries an id, a title, and statement prose. Serialises to/from
OSCAL-shaped JSON so catalogues stay interoperable with the wider OSCAL toolchain.
Full model: https://pages.nist.gov/OSCAL/
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Control:
    id: str
    title: str
    prose: str = ""
    family: str = ""           # convenience: owning group title, used as policy group category
    props: dict[str, str] = field(default_factory=dict)

    def to_oscal(self) -> dict:
        parts = []
        if self.prose:
            parts.append({"name": "statement", "prose": self.prose})
        out: dict = {"id": self.id, "title": self.title}
        if self.props:
            out["props"] = [{"name": k, "value": v} for k, v in self.props.items()]
        if parts:
            out["parts"] = parts
        return out

    @classmethod
    def from_oscal(cls, data: dict, family: str = "") -> "Control":
        prose = ""
        for part in data.get("parts", []):
            if part.get("name") == "statement" and part.get("prose"):
                prose = part["prose"]
                break
        props = {p["name"]: p.get("value", "") for p in data.get("props", [])}
        return cls(id=data["id"], title=data.get("title", ""), prose=prose,
                   family=family, props=props)


@dataclass
class Group:
    id: str
    title: str
    controls: list[Control] = field(default_factory=list)

    def to_oscal(self) -> dict:
        return {"id": self.id, "title": self.title,
                "controls": [c.to_oscal() for c in self.controls]}

    @classmethod
    def from_oscal(cls, data: dict) -> "Group":
        title = data.get("title", "")
        controls = [Control.from_oscal(c, family=title) for c in data.get("controls", [])]
        return cls(id=data["id"], title=title, controls=controls)


@dataclass
class Catalog:
    uuid: str
    title: str
    version: str
    groups: list[Group] = field(default_factory=list)

    def controls(self):
        for g in self.groups:
            yield from g.controls

    def to_oscal(self) -> dict:
        return {
            "catalog": {
                "uuid": self.uuid,
                "metadata": {"title": self.title, "version": self.version,
                             "oscal-version": "1.1.2"},
                "groups": [g.to_oscal() for g in self.groups],
            }
        }

    @classmethod
    def from_oscal(cls, data: dict) -> "Catalog":
        cat = data["catalog"]
        meta = cat.get("metadata", {})
        groups = [Group.from_oscal(g) for g in cat.get("groups", [])]
        return cls(uuid=cat.get("uuid", ""), title=meta.get("title", ""),
                   version=meta.get("version", ""), groups=groups)
