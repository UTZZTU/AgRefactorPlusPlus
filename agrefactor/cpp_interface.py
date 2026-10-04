"""Best-effort C/C++ interface facts from the compiler AST.

Extraction failure is deliberately represented by ``None``.  Callers must
defer to the real compile/link/synthesis stages instead of rejecting source.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


_DEFAULT_LIBCLANG = Path(
    "/data/Xilinx/Vitis_HLS/2023.2/lnx64/tools/clang-3.9/lib/libclang.so"
)
_FUNCTION_DECL = 8
_METHOD_DECL = 21
_FUNCTION_TEMPLATE = 30
_VARIABLE_DECL = 9
_TRANSLATION_UNIT = 300
_CHILD_VISIT_RECURSE = 2
_POINTER_TYPE = 101
_REFERENCE_TYPES = frozenset({103, 104})
_ARRAY_TYPES = frozenset({112, 113, 114, 115})
_FUNCTION_TYPES = frozenset({110, 111})

# libclang CXLinkage values used by the vendor libclang shipped with Vitis.
# Keep the numeric value in the evidence as well; the name is only a readable
# projection and must not be used as a case-specific gate.
_LINKAGE_NAMES = {
    0: "invalid",
    1: "no_linkage",
    2: "internal",
    3: "unique_external",
    4: "external",
    5: "module",
}


class _CXString(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("private_flags", ctypes.c_uint),
    ]


class _CXCursor(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint),
        ("xdata", ctypes.c_int),
        ("data", ctypes.c_void_p * 3),
    ]


class _CXType(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_int),
        ("data", ctypes.c_void_p * 2),
    ]


class _CXUnsavedFile(ctypes.Structure):
    _fields_ = [
        ("filename", ctypes.c_char_p),
        ("contents", ctypes.c_char_p),
        ("length", ctypes.c_ulong),
    ]


class _CXSourceLocation(ctypes.Structure):
    _fields_ = [
        ("ptr_data", ctypes.c_void_p * 2),
        ("int_data", ctypes.c_uint),
    ]


class _CXSourceRange(ctypes.Structure):
    _fields_ = [
        ("ptr_data", ctypes.c_void_p * 2),
        ("begin_int_data", ctypes.c_uint),
        ("end_int_data", ctypes.c_uint),
    ]


@dataclass(frozen=True, slots=True)
class CppParameter:
    name: str
    type_spelling: str
    canonical_type: str
    pointer_like: bool
    mutable_output: bool = False


@dataclass(frozen=True, slots=True)
class CppFunctionInterface:
    name: str
    result_type: str
    parameters: tuple[CppParameter, ...]
    source_declaration: str | None = None
    canonical_result_type: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    linkage: int | None = None
    # ``clang_getCursorLinkage`` reports both C and C++ external linkage as
    # ``external``. Keep the source-level language linkage separately so an
    # ABI declaration cannot silently lose an ``extern "C"`` requirement.
    language_linkage: str | None = None
    global_state: tuple[CppVariable, ...] = ()


@dataclass(frozen=True, slots=True)
class CppVariable:
    name: str
    type_spelling: str
    canonical_type: str
    pointer_like: bool
    dimensions: tuple[int, ...] = ()
    scalar_type: str | None = None
    qualified_name: str | None = None


def interfaces_equivalent(
    left: CppFunctionInterface,
    right: CppFunctionInterface,
    *,
    require_parameter_names: bool = True,
) -> bool:
    if left.name != right.name or left.result_type != right.result_type:
        return False
    if (
        left.language_linkage is not None
        and right.language_linkage is not None
        and left.language_linkage != right.language_linkage
    ):
        return False
    if len(left.parameters) != len(right.parameters):
        return False
    return all(
        first.canonical_type == second.canonical_type
        and (
            not require_parameter_names
            or first.name == second.name
        )
        for first, second in zip(left.parameters, right.parameters)
    )


class _LibClang:
    def __init__(self, path: Path) -> None:
        # Load the Python environment's C++ runtime before the vendor RPATH
        # can select its older runtime for the whole host process.
        runtime = Path(sys.prefix) / "lib" / "libstdc++.so.6"
        if runtime.is_file():
            ctypes.CDLL(str(runtime), mode=ctypes.RTLD_GLOBAL)
        library = ctypes.CDLL(str(path))
        self.library = library
        self.visitor_type = ctypes.CFUNCTYPE(
            ctypes.c_uint,
            _CXCursor,
            _CXCursor,
            ctypes.c_void_p,
        )
        library.clang_createIndex.argtypes = [ctypes.c_int, ctypes.c_int]
        library.clang_createIndex.restype = ctypes.c_void_p
        library.clang_disposeIndex.argtypes = [ctypes.c_void_p]
        library.clang_parseTranslationUnit.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_int,
            ctypes.POINTER(_CXUnsavedFile),
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        library.clang_parseTranslationUnit.restype = ctypes.c_void_p
        library.clang_disposeTranslationUnit.argtypes = [ctypes.c_void_p]
        library.clang_getNumDiagnostics.argtypes = [ctypes.c_void_p]
        library.clang_getNumDiagnostics.restype = ctypes.c_uint
        library.clang_getDiagnostic.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        library.clang_getDiagnostic.restype = ctypes.c_void_p
        library.clang_getDiagnosticSeverity.argtypes = [ctypes.c_void_p]
        library.clang_getDiagnosticSeverity.restype = ctypes.c_uint
        library.clang_getDiagnosticSpelling.argtypes = [ctypes.c_void_p]
        library.clang_getDiagnosticSpelling.restype = _CXString
        library.clang_getDiagnosticLocation.argtypes = [ctypes.c_void_p]
        library.clang_getDiagnosticLocation.restype = _CXSourceLocation
        library.clang_disposeDiagnostic.argtypes = [ctypes.c_void_p]
        library.clang_getTranslationUnitCursor.argtypes = [ctypes.c_void_p]
        library.clang_getTranslationUnitCursor.restype = _CXCursor
        library.clang_visitChildren.argtypes = [
            _CXCursor,
            self.visitor_type,
            ctypes.c_void_p,
        ]
        library.clang_visitChildren.restype = ctypes.c_uint
        library.clang_getCursorSpelling.argtypes = [_CXCursor]
        library.clang_getCursorSpelling.restype = _CXString
        library.clang_getCursorType.argtypes = [_CXCursor]
        library.clang_getCursorType.restype = _CXType
        library.clang_getCursorLinkage.argtypes = [_CXCursor]
        library.clang_getCursorLinkage.restype = ctypes.c_int
        library.clang_getCursorReferenced.argtypes = [_CXCursor]
        library.clang_getCursorReferenced.restype = _CXCursor
        library.clang_getCursorDefinition.argtypes = [_CXCursor]
        library.clang_getCursorDefinition.restype = _CXCursor
        library.clang_getCursorSemanticParent.argtypes = [_CXCursor]
        library.clang_getCursorSemanticParent.restype = _CXCursor
        library.clang_hashCursor.argtypes = [_CXCursor]
        library.clang_hashCursor.restype = ctypes.c_uint
        library.clang_isConstQualifiedType.argtypes = [_CXType]
        library.clang_isConstQualifiedType.restype = ctypes.c_uint
        library.clang_getArraySize.argtypes = [_CXType]
        library.clang_getArraySize.restype = ctypes.c_longlong
        library.clang_getArrayElementType.argtypes = [_CXType]
        library.clang_getArrayElementType.restype = _CXType
        library.clang_getCursorLocation.argtypes = [_CXCursor]
        library.clang_getCursorLocation.restype = _CXSourceLocation
        library.clang_Location_isFromMainFile.argtypes = [_CXSourceLocation]
        library.clang_Location_isFromMainFile.restype = ctypes.c_int
        library.clang_getCanonicalType.argtypes = [_CXType]
        library.clang_getCanonicalType.restype = _CXType
        library.clang_getPointeeType.argtypes = [_CXType]
        library.clang_getPointeeType.restype = _CXType
        library.clang_getTypeSpelling.argtypes = [_CXType]
        library.clang_getTypeSpelling.restype = _CXString
        library.clang_getResultType.argtypes = [_CXType]
        library.clang_getResultType.restype = _CXType
        library.clang_Cursor_getNumArguments.argtypes = [_CXCursor]
        library.clang_Cursor_getNumArguments.restype = ctypes.c_int
        library.clang_Cursor_getArgument.argtypes = [_CXCursor, ctypes.c_uint]
        library.clang_Cursor_getArgument.restype = _CXCursor
        library.clang_isCursorDefinition.argtypes = [_CXCursor]
        library.clang_isCursorDefinition.restype = ctypes.c_uint
        library.clang_getCursorExtent.argtypes = [_CXCursor]
        library.clang_getCursorExtent.restype = _CXSourceRange
        library.clang_getRangeStart.argtypes = [_CXSourceRange]
        library.clang_getRangeStart.restype = _CXSourceLocation
        library.clang_getRangeEnd.argtypes = [_CXSourceRange]
        library.clang_getRangeEnd.restype = _CXSourceLocation
        library.clang_getSpellingLocation.argtypes = [
            _CXSourceLocation,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        library.clang_getFileName.argtypes = [ctypes.c_void_p]
        library.clang_getFileName.restype = _CXString
        library.clang_getCString.argtypes = [_CXString]
        library.clang_getCString.restype = ctypes.c_char_p
        library.clang_disposeString.argtypes = [_CXString]

    def text(self, value: _CXString) -> str:
        try:
            raw = self.library.clang_getCString(value)
            return "" if raw is None else raw.decode("utf-8", errors="replace")
        finally:
            self.library.clang_disposeString(value)


def _library_path() -> Path:
    configured = os.getenv("AGREFACTOR_LIBCLANG")
    return Path(configured) if configured else _DEFAULT_LIBCLANG


@lru_cache(maxsize=4)
def _compiler_include_flags(library_path: Path) -> tuple[str, ...]:
    driver = library_path.parent.parent / "bin" / "clang++"
    if not driver.is_file():
        return ()
    try:
        result = subprocess.run(
            [str(driver), f"--gcc-toolchain={driver.parent.parent}", "-E", "-x", "c++", "-v", "-"],
            input="", capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    flags: list[str] = []
    collecting = False
    for line in result.stderr.splitlines():
        if line.strip() == "#include <...> search starts here:":
            collecting = True
        elif line.strip() == "End of search list.":
            break
        elif collecting and Path(line.strip()).is_dir():
            flags.extend(("-isystem", line.strip()))
    return tuple(flags)


def extract_top_interface(
    source: str,
    function_name: str,
    *,
    require_definition: bool = True,
    source_path: str | Path | None = None,
    include_dirs: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    _entry_facts: dict | None = None,
) -> Optional[CppFunctionInterface]:
    """Return one compiler-resolved top definition, or ``None`` if unknown."""

    if not isinstance(source, str) or not source.strip():
        return None
    if not isinstance(function_name, str) or not function_name.strip():
        return None
    path = _library_path()
    if not path.is_file():
        return None

    try:
        clang = _LibClang(path)
        library = clang.library
        index = library.clang_createIndex(0, 0)
        if not index:
            return None
        translation_unit = None
        try:
            filename = str(source_path or "agrefactor_interface.cpp").encode("utf-8")
            contents = source.encode("utf-8")
            unsaved = _CXUnsavedFile(filename, contents, len(contents))
            flags = ["-x", "c++", "-std=c++14", *_compiler_include_flags(path)]
            flags.extend(f"-I{directory}" for directory in include_dirs)
            flags.extend(compile_flags)
            arguments = (ctypes.c_char_p * len(flags))(
                *(flag.encode("utf-8") for flag in flags)
            )
            translation_unit = library.clang_parseTranslationUnit(
                index,
                filename,
                arguments,
                len(arguments),
                ctypes.pointer(unsaved),
                1,
                0,
            )
            if not translation_unit:
                return None
            error_offsets: list[int] = []
            has_errors = False
            diagnostic_evidence: list[dict] = []
            for diagnostic_index in range(library.clang_getNumDiagnostics(translation_unit)):
                diagnostic = library.clang_getDiagnostic(translation_unit, diagnostic_index)
                try:
                    severity = library.clang_getDiagnosticSeverity(diagnostic)
                    has_errors = has_errors or severity >= 3
                    location = library.clang_getDiagnosticLocation(diagnostic)
                    file_handle = ctypes.c_void_p()
                    line = ctypes.c_uint()
                    column = ctypes.c_uint()
                    offset = ctypes.c_uint()
                    library.clang_getSpellingLocation(
                        location,
                        ctypes.byref(file_handle),
                        ctypes.byref(line),
                        ctypes.byref(column),
                        ctypes.byref(offset),
                    )
                    diagnostic_evidence.append(
                        {
                            "severity": int(severity),
                            "message": clang.text(
                                library.clang_getDiagnosticSpelling(diagnostic)
                            ),
                            "file": clang.text(
                                library.clang_getFileName(file_handle)
                            ) if file_handle.value else None,
                            "line": int(line.value) if line.value else None,
                            "column": int(column.value) if column.value else None,
                            "offset": int(offset.value),
                        }
                    )
                    if severity >= 3 and library.clang_Location_isFromMainFile(location):
                        error_offsets.append(offset.value)
                finally:
                    library.clang_disposeDiagnostic(diagnostic)
            if _entry_facts is not None:
                _entry_facts["diagnostics"] = diagnostic_evidence
            if any(diagnostic["severity"] >= 4 for diagnostic in diagnostic_evidence):
                if _entry_facts is not None:
                    _entry_facts.update(status="unknown", entries=[], translation_unit_complete=False)
                return None

            matches: list[_CXCursor] = []
            entries: list[dict] = []

            @clang.visitor_type
            def visit(
                cursor: _CXCursor,
                _parent: _CXCursor,
                _client_data: ctypes.c_void_p,
            ) -> int:
                location = library.clang_getCursorLocation(cursor)
                if cursor.kind in {_FUNCTION_DECL, _METHOD_DECL, _FUNCTION_TEMPLATE}:
                    name = clang.text(library.clang_getCursorSpelling(cursor))
                    if name == function_name and (
                        not require_definition
                        or library.clang_isCursorDefinition(cursor)
                    ):
                        entry_file = ctypes.c_void_p()
                        library.clang_getSpellingLocation(location, ctypes.byref(entry_file), None, None, None)
                        start, end = _source_offsets(clang, cursor)
                        declaration = _declaration_text(source, start, end) if library.clang_Location_isFromMainFile(location) else None
                        linkage = int(library.clang_getCursorLinkage(cursor))
                        entries.append({
                            "kind": {_FUNCTION_DECL: "function", _METHOD_DECL: "member", _FUNCTION_TEMPLATE: "template"}[cursor.kind],
                            "name": name,
                            "parent": clang.text(library.clang_getCursorSpelling(_parent)),
                            "linkage": linkage,
                            "linkage_name": _LINKAGE_NAMES.get(
                                linkage,
                                "unknown",
                            ),
                            "source_path": clang.text(library.clang_getFileName(entry_file)),
                            "signature_valid": declaration is not None and not any(start <= offset < start + len(declaration.encode("utf-8")) for offset in error_offsets),
                        })
                        if cursor.kind == _FUNCTION_DECL and library.clang_Location_isFromMainFile(location):
                            matches.append(cursor)
                return _CHILD_VISIT_RECURSE

            root = library.clang_getTranslationUnitCursor(translation_unit)
            library.clang_visitChildren(root, visit, None)
            if _entry_facts is not None:
                valid_entries = [entry for entry in entries if entry.pop("signature_valid") or not has_errors]
                _entry_facts.update(
                    status="confirmed" if len(valid_entries) == 1 else "ambiguous" if valid_entries else "missing" if not entries and not has_errors else "unknown",
                    entries=valid_entries,
                )
            interfaces = [
                _interface_from_cursor(clang, cursor, source)
                for cursor in matches
            ]
            interfaces = [
                item for item in interfaces
                if item is not None and item.source_declaration is not None
                and not any(
                    item.source_start <= offset < item.source_start + len(item.source_declaration.encode("utf-8"))
                    for offset in error_offsets
                )
            ]
            unique = {
                (
                    item.name,
                    item.result_type,
                    tuple(
                        (
                            parameter.name,
                            parameter.canonical_type,
                            parameter.pointer_like,
                        )
                        for parameter in item.parameters
                    ),
                ): item
                for item in interfaces
            }
            if len(unique) != 1:
                return None
            return next(iter(unique.values()))
        finally:
            if translation_unit:
                library.clang_disposeTranslationUnit(translation_unit)
            library.clang_disposeIndex(index)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _source_offsets(
    clang: _LibClang,
    cursor: _CXCursor,
) -> tuple[int, int]:
    library = clang.library
    extent = library.clang_getCursorExtent(cursor)
    offsets: list[int] = []
    for location in (
        library.clang_getRangeStart(extent),
        library.clang_getRangeEnd(extent),
    ):
        offset = ctypes.c_uint()
        library.clang_getSpellingLocation(
            location,
            None,
            None,
            None,
            ctypes.byref(offset),
        )
        offsets.append(offset.value)
    return offsets[0], offsets[1]


def _declaration_text(source: str, start: int, end: int) -> str | None:
    if start < 0 or end <= start or end > len(source.encode("utf-8")):
        return None
    fragment = source.encode("utf-8")[start:end].decode(
        "utf-8",
        errors="replace",
    )
    state = "code"
    quote = ""
    index = 0
    while index < len(fragment):
        char = fragment[index]
        following = fragment[index + 1] if index + 1 < len(fragment) else ""
        if state == "code":
            if char == "/" and following == "/":
                state = "line_comment"
                index += 2
                continue
            if char == "/" and following == "*":
                state = "block_comment"
                index += 2
                continue
            if char in {'"', "'"}:
                state = "literal"
                quote = char
            elif char == "{":
                value = fragment[:index].strip()
                return value or None
            elif char == ";":
                value = fragment[:index].strip()
                return value or None
        elif state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 2
                continue
        elif char == "\\":
            index += 2
            continue
        elif char == quote:
            state = "code"
        index += 1
    value = fragment.strip().rstrip(";").strip()
    return value or None


def _strip_cpp_comments_preserving_layout(source: str) -> str:
    """Mask C/C++ comments without changing line/offset layout."""

    def replace(match: re.Match[str]) -> str:
        return "".join(
            "\n" if character == "\n" else " "
            for character in match.group(0)
        )

    return re.sub(
        r"//[^\n]*|/\*.*?\*/",
        replace,
        source,
        flags=re.DOTALL,
    )


def _extern_c_block_spans(source: str) -> tuple[tuple[int, int], ...]:
    """Return balanced ``extern \"C\" { ... }`` spans."""

    cleaned = _strip_cpp_comments_preserving_layout(source)
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r'extern\s+"C"\s*\{', cleaned):
        opening = cleaned.find("{", match.start(), match.end())
        if opening < 0:
            continue
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(opening, len(cleaned)):
            character = cleaned[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {"\"", "'"}:
                quote = character
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    spans.append((match.start(), index + 1))
                    break
    return tuple(spans)


def _language_linkage_at_offset(source: str, byte_offset: int) -> str:
    """Infer C/C++ language linkage for a compiler cursor start offset.

    Clang's numeric linkage enum does not distinguish ``extern "C"`` from
    ordinary C++ external linkage. This lexical check is intentionally limited
    to the declaration's prefix and balanced ``extern "C"`` blocks; it does
    not infer ownership or alter compiler-resolved types.
    """

    encoded_prefix = source.encode("utf-8")[: max(0, byte_offset)]
    prefix = encoded_prefix.decode("utf-8", errors="replace")
    cleaned = _strip_cpp_comments_preserving_layout(source)
    prefix_clean = cleaned[: len(prefix)]
    boundary = max(
        prefix_clean.rfind(";"),
        prefix_clean.rfind("{"),
        prefix_clean.rfind("}"),
    )
    declaration_prefix = prefix_clean[boundary + 1 :]
    if re.search(r'\bextern\s+"C"', declaration_prefix):
        return "c"
    if any(start <= len(prefix_clean) < end for start, end in _extern_c_block_spans(source)):
        return "c"
    return "cpp"


def _interface_from_cursor(
    clang: _LibClang,
    cursor: _CXCursor,
    source: str,
) -> Optional[CppFunctionInterface]:
    library = clang.library
    function_type = library.clang_getCursorType(cursor)
    result_type = clang.text(
        library.clang_getTypeSpelling(
            library.clang_getResultType(function_type)
        )
    )
    canonical_result_type = clang.text(
        library.clang_getTypeSpelling(
            library.clang_getCanonicalType(
                library.clang_getResultType(function_type)
            )
        )
    )
    parameters: list[CppParameter] = []
    argument_count = library.clang_Cursor_getNumArguments(cursor)
    if argument_count < 0:
        return None
    for index_value in range(argument_count):
        argument = library.clang_Cursor_getArgument(cursor, index_value)
        name = clang.text(library.clang_getCursorSpelling(argument))
        argument_type = library.clang_getCursorType(argument)
        canonical = library.clang_getCanonicalType(argument_type)
        spelling = clang.text(library.clang_getTypeSpelling(argument_type))
        canonical_spelling = clang.text(
            library.clang_getTypeSpelling(canonical)
        )
        kind = canonical.kind
        pointee_kind = (
            library.clang_getCanonicalType(
                library.clang_getPointeeType(canonical)
            ).kind
            if kind == _POINTER_TYPE or kind in _REFERENCE_TYPES
            else -1
        )
        mutable_target = canonical
        if kind == _POINTER_TYPE or kind in _REFERENCE_TYPES:
            mutable_target = library.clang_getPointeeType(canonical)
        parameters.append(
            CppParameter(
                name=name,
                type_spelling=spelling,
                canonical_type=canonical_spelling,
                pointer_like=(
                    kind in _ARRAY_TYPES
                    or (
                        kind == _POINTER_TYPE
                        and pointee_kind not in _FUNCTION_TYPES
                    )
                    or (
                        kind in _REFERENCE_TYPES
                        and (
                            pointee_kind in _ARRAY_TYPES
                            or pointee_kind == _POINTER_TYPE
                        )
                    )
                ),
                mutable_output=(
                    (
                        kind in _ARRAY_TYPES
                        or kind == _POINTER_TYPE
                        or kind in _REFERENCE_TYPES
                    )
                    and not library.clang_isConstQualifiedType(mutable_target)
                ),
            )
        )
    start, end = _source_offsets(clang, cursor)
    return CppFunctionInterface(
        name=clang.text(library.clang_getCursorSpelling(cursor)),
        result_type=result_type,
        parameters=tuple(parameters),
        source_declaration=_declaration_text(source, start, end),
        canonical_result_type=canonical_result_type,
        source_start=start,
        source_end=end,
        linkage=library.clang_getCursorLinkage(cursor),
        language_linkage=_language_linkage_at_offset(source, start),
        global_state=_referenced_state(clang, cursor),
    )


def inspect_top_entry(source: str, function_name: str, **context) -> dict:
    facts: dict = {"status": "unknown", "entries": []}
    extract_top_interface(source, function_name, _entry_facts=facts, **context)
    return facts


def _referenced_state(clang: _LibClang, top: _CXCursor) -> tuple[CppVariable, ...]:
    library = clang.library
    variables: dict[int, CppVariable] = {}
    visited: set[int] = set()

    def walk(function: _CXCursor) -> None:
        key = library.clang_hashCursor(function)
        if key in visited:
            return
        visited.add(key)

        @clang.visitor_type
        def visit(cursor, _parent, _data):
            if cursor.kind == 101:  # CXCursor_DeclRefExpr
                declaration = library.clang_getCursorReferenced(cursor)
                if declaration.kind == _FUNCTION_DECL:
                    definition = library.clang_getCursorDefinition(declaration)
                    if library.clang_Location_isFromMainFile(library.clang_getCursorLocation(definition)):
                        walk(definition)
                elif declaration.kind == _VARIABLE_DECL:
                    parent = library.clang_getCursorSemanticParent(declaration)
                    value_type = library.clang_getCanonicalType(library.clang_getCursorType(declaration))
                    if parent.kind in {_TRANSLATION_UNIT, 22} and not library.clang_isConstQualifiedType(value_type):
                        names = [clang.text(library.clang_getCursorSpelling(declaration))]
                        scope = parent
                        while scope.kind == 22:  # CXCursor_Namespace
                            scope_name = clang.text(library.clang_getCursorSpelling(scope))
                            if scope_name:
                                names.insert(0, scope_name)
                            scope = library.clang_getCursorSemanticParent(scope)
                        dimensions: list[int] = []
                        element = value_type
                        while element.kind == 112:  # CXType_ConstantArray
                            dimensions.append(library.clang_getArraySize(element))
                            element = library.clang_getCanonicalType(library.clang_getArrayElementType(element))
                        variables[library.clang_hashCursor(declaration)] = CppVariable(
                            name=clang.text(library.clang_getCursorSpelling(declaration)),
                            type_spelling=clang.text(library.clang_getTypeSpelling(value_type)),
                            canonical_type=clang.text(library.clang_getTypeSpelling(value_type)),
                            pointer_like=value_type.kind in _ARRAY_TYPES or value_type.kind == _POINTER_TYPE,
                            dimensions=tuple(dimensions),
                            scalar_type=clang.text(library.clang_getTypeSpelling(element)) if 2 <= element.kind <= 30 else None,
                            qualified_name="::".join(names),
                        )
            return _CHILD_VISIT_RECURSE

        library.clang_visitChildren(function, visit, None)

    walk(top)
    return tuple(variables.values())


def extract_global_variables(source: str) -> Optional[tuple[CppVariable, ...]]:
    """Return compiler-observed file-scope variables, or ``None`` if unknown."""

    if not isinstance(source, str) or not source.strip():
        return None
    path = _library_path()
    if not path.is_file():
        return None
    try:
        clang = _LibClang(path)
        library = clang.library
        index = library.clang_createIndex(0, 0)
        if not index:
            return None
        translation_unit = None
        try:
            filename = b"agrefactor_globals.cpp"
            contents = source.encode("utf-8")
            unsaved = _CXUnsavedFile(filename, contents, len(contents))
            arguments = (ctypes.c_char_p * 3)(
                b"-x",
                b"c++",
                b"-std=c++14",
            )
            translation_unit = library.clang_parseTranslationUnit(
                index,
                filename,
                arguments,
                len(arguments),
                ctypes.pointer(unsaved),
                1,
                0,
            )
            if not translation_unit:
                if _entry_facts is not None:
                    _entry_facts.setdefault("diagnostics", [])
                return None
            variables: list[CppVariable] = []

            @clang.visitor_type
            def visit(
                cursor: _CXCursor,
                parent: _CXCursor,
                _client_data: ctypes.c_void_p,
            ) -> int:
                if (
                    cursor.kind == _VARIABLE_DECL
                    and parent.kind == _TRANSLATION_UNIT
                ):
                    name = clang.text(
                        library.clang_getCursorSpelling(cursor)
                    )
                    variable_type = library.clang_getCursorType(cursor)
                    canonical = library.clang_getCanonicalType(variable_type)
                    kind = canonical.kind
                    pointee_kind = (
                        library.clang_getCanonicalType(
                            library.clang_getPointeeType(canonical)
                        ).kind
                        if kind == _POINTER_TYPE
                        or kind in _REFERENCE_TYPES
                        else -1
                    )
                    if name:
                        variables.append(
                            CppVariable(
                                name=name,
                                type_spelling=clang.text(
                                    library.clang_getTypeSpelling(
                                        variable_type
                                    )
                                ),
                                canonical_type=clang.text(
                                    library.clang_getTypeSpelling(canonical)
                                ),
                                pointer_like=(
                                    kind in _ARRAY_TYPES
                                    or (
                                        kind == _POINTER_TYPE
                                        and pointee_kind
                                        not in _FUNCTION_TYPES
                                    )
                                ),
                            )
                        )
                return _CHILD_VISIT_RECURSE

            root = library.clang_getTranslationUnitCursor(translation_unit)
            library.clang_visitChildren(root, visit, None)
            return tuple(variables)
        finally:
            if translation_unit:
                library.clang_disposeTranslationUnit(translation_unit)
            library.clang_disposeIndex(index)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
