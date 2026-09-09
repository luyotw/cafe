"""Complete delivery facts shared by Driver lifecycle fixtures."""


def delivery_contract():
    return {
        "schema_version": 1,
        "outcome": "Readers can export a complete report.",
        "motivation": "Support offline review.",
        "in_scope": ["Export text and citations.", "Handle an empty report."],
        "out_of_scope": ["Automatic publication."],
        "acceptance_invariants": ["Exports preserve every citation and existing format."],
        "required_evidence": ["Verify populated and empty exports against expected files."],
        "implementation_direction": "Reuse the existing export path.",
        "constraints": {
            "architecture": ["Keep the existing export interface."],
            "dependencies": ["No new dependencies."],
            "compatibility": ["Keep the existing format."],
            "quality": ["Cover empty reports."],
            "permissions": ["Local files only."],
            "external_side_effects": ["No automatic publication."],
            "cost": ["No paid services."],
        },
        "allowed_variations": ["Fewer files or abstractions with equivalent coverage."],
        "deviation_triggers": ["Any omitted requirement or new integration requires the user."],
    }
