"""Prepare Original source for use as an in-process reference oracle."""

from __future__ import annotations

from agrefactor.cpp_interface import extract_top_interface, inspect_top_entry

_REFERENCE_ENTRY = "agrefactor_original_program_main"


def isolate_reference_program_entry(
    source_code: str,
    *,
    top_function: str | None = None,
    testbench_code: str | None = None,
    source_path=None,
    include_dirs: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    source_provenance: list[dict] | None = None,
) -> str:
    """Rename an Original program entry without changing the selected top."""

    if not isinstance(source_code, str):
        raise TypeError("source_code must be a string")
    if not source_code.strip():
        raise ValueError("source_code must not be empty")
    prepared = (
        f"#define main {_REFERENCE_ENTRY}\n"
        + source_code.rstrip()
        + "\n#undef main\n"
    )
    source_lines = len(source_code.rstrip().splitlines())
    if source_provenance is not None:
        source_provenance.append({
            "line_start": 2,
            "line_end": source_lines + 1,
            "owner": "original",
            "authority": "original_source",
        })
    if top_function is None:
        return prepared
    context = dict(source_path=source_path, include_dirs=include_dirs, compile_flags=compile_flags)
    interface = extract_top_interface(source_code, top_function, **context)
    facts = inspect_top_entry(source_code, top_function, **context)
    has_template = any(
        entry.get("kind") == "template"
        for entry in facts.get("entries", ())
    )
    if facts["status"] != "confirmed" and not (
        facts["status"] == "ambiguous" and has_template and testbench_code
    ):
        return prepared
    template = has_template
    if template and testbench_code:
        interface = extract_top_interface(testbench_code, top_function, require_definition=False, **context)
    if interface is None:
        return prepared
    state = (
        interface.global_state
        if interface.canonical_result_type == "void"
        and not any(parameter.mutable_output for parameter in interface.parameters)
        else ()
    )
    if not template and interface.linkage not in {2, 3} and not state:
        return prepared
    if any(variable.scalar_type is None for variable in state):
        raise ValueError("observable global state boundary is unknown: only scalar and fixed scalar arrays are supported")

    # The implementation and its bridge remain in the same translation unit.
    implementation = f"agrefactor_reference_impl_{top_function}"
    aliases = [f"using agrefactor_reference_result = {interface.result_type};"]
    arguments: list[str] = []
    parameters: list[str] = []
    for index, parameter in enumerate(interface.parameters):
        alias = f"agrefactor_reference_arg_{index}"
        name = parameter.name or f"arg_{index}"
        aliases.append(f"using {alias} = {parameter.type_spelling};")
        parameters.append(f"{alias} {name}")
        arguments.append(name)
    before: list[str] = []
    after: list[str] = []
    for index, variable in enumerate(state):
        alias = f"agrefactor_reference_state_{index}"
        name = f"agrefactor_state_{variable.name}_{index}"
        expression = variable.qualified_name or variable.name
        aliases.append(f"using {alias} = decltype({expression});")
        parameters.append(f"{alias} &{name}")
        indices = "".join(f"[agrefactor_i_{axis}]" for axis in range(len(variable.dimensions)))
        loops = "".join(f"for (int agrefactor_i_{axis}=0; agrefactor_i_{axis}<{size}; ++agrefactor_i_{axis}) " for axis, size in enumerate(variable.dimensions))
        before.append(f"{loops}{expression}{indices} = {name}{indices};")
        after.append(f"{loops}{name}{indices} = {expression}{indices};")
    call = f"{implementation}({', '.join(arguments)})"
    body = [*before, (call + ";") if interface.canonical_result_type == "void" else ("return " + call + ";"), *after]
    bridged = (
        f"#define {top_function} {implementation}\n" + prepared
        + f"#undef {top_function}\n" + "\n".join(aliases)
        + f"\nagrefactor_reference_result {top_function}({', '.join(parameters)}) {{\n"
        + "\n".join(body) + "\n}\n"
    )
    if source_provenance is not None:
        source_provenance[-1]["line_start"] += 1
        source_provenance[-1]["line_end"] += 1
        source_provenance.append({
            "line_start": source_lines + 5,
            "line_end": len(bridged.splitlines()),
            "owner": "testbench" if template and testbench_code else "unknown",
            "authority": "testbench_template_contract" if template and testbench_code else "generated_reference_bridge",
        })
    return bridged
