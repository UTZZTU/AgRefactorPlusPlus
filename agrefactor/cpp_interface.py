"""Best-effort C/C++ interface facts from the compiler AST.

Extraction failure is deliberately represented by ``None``.  Callers must
defer to the real compile/link/synthesis stages instead of rejecting source.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


_DEFAULT_LIBCLANG = Path(
    "/data/Xilinx/Vitis/2023.2/tps/lnx64/clang-14.0.0/lib/libclang.so"
)
_LEGACY_LIBCLANG = Path(
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


class _CXToken(ctypes.Structure):
    _fields_ = [("int_data", ctypes.c_uint * 4), ("ptr_data", ctypes.c_void_p)]


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
    canonical_function_type: str | None = None
    linker_symbol: str | None = None


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
    semantic_types: bool = False,
) -> bool:
    if left.name != right.name:
        return False
    if semantic_types:
        # Function types include compiler-adjusted array parameters and omit
        # top-level parameter qualifiers that do not change the interface.
        if (
            not left.canonical_function_type
            or left.canonical_function_type != right.canonical_function_type
            or not left.linker_symbol
            or left.linker_symbol != right.linker_symbol
        ):
            return False
        return not require_parameter_names or tuple(
            item.name for item in left.parameters
        ) == tuple(item.name for item in right.parameters)
    if (
        left.language_linkage is not None
        and right.language_linkage is not None
        and left.language_linkage != right.language_linkage
    ):
        return False
    if left.result_type != right.result_type:
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
        self.inclusion_visitor_type = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.POINTER(_CXSourceLocation),
            ctypes.c_uint, ctypes.c_void_p,
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
        library.clang_getCursorUSR.argtypes = [_CXCursor]
        library.clang_getCursorUSR.restype = _CXString
        library.clang_Cursor_getMangling.argtypes = [_CXCursor]
        library.clang_Cursor_getMangling.restype = _CXString
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
        library.clang_getFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        library.clang_getFile.restype = ctypes.c_void_p
        library.clang_getLocationForOffset.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        library.clang_getLocationForOffset.restype = _CXSourceLocation
        library.clang_getRange.argtypes = [_CXSourceLocation, _CXSourceLocation]
        library.clang_getRange.restype = _CXSourceRange
        library.clang_tokenize.argtypes = [ctypes.c_void_p, _CXSourceRange, ctypes.POINTER(ctypes.POINTER(_CXToken)), ctypes.POINTER(ctypes.c_uint)]
        library.clang_disposeTokens.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CXToken), ctypes.c_uint]
        library.clang_getTokenKind.argtypes = [_CXToken]
        library.clang_getTokenKind.restype = ctypes.c_uint
        library.clang_getTokenSpelling.argtypes = [ctypes.c_void_p, _CXToken]
        library.clang_getTokenSpelling.restype = _CXString
        library.clang_getTokenExtent.argtypes = [ctypes.c_void_p, _CXToken]
        library.clang_getTokenExtent.restype = _CXSourceRange
        library.clang_getTypeDeclaration.argtypes = [_CXType]
        library.clang_getTypeDeclaration.restype = _CXCursor
        library.clang_getTypedefDeclUnderlyingType.argtypes = [_CXCursor]
        library.clang_getTypedefDeclUnderlyingType.restype = _CXType
        library.clang_getEnumDeclIntegerType.argtypes = [_CXCursor]
        library.clang_getEnumDeclIntegerType.restype = _CXType
        library.clang_getEnumConstantDeclValue.argtypes = [_CXCursor]
        library.clang_getEnumConstantDeclValue.restype = ctypes.c_longlong
        library.clang_getEnumConstantDeclUnsignedValue.argtypes = [_CXCursor]
        library.clang_getEnumConstantDeclUnsignedValue.restype = ctypes.c_ulonglong
        library.clang_Type_getSizeOf.argtypes = [_CXType]
        library.clang_Type_getSizeOf.restype = ctypes.c_longlong
        library.clang_Type_getAlignOf.argtypes = [_CXType]
        library.clang_Type_getAlignOf.restype = ctypes.c_longlong
        library.clang_Cursor_getOffsetOfField.argtypes = [_CXCursor]
        library.clang_Cursor_getOffsetOfField.restype = ctypes.c_longlong
        library.clang_Cursor_isBitField.argtypes = [_CXCursor]
        library.clang_Cursor_isBitField.restype = ctypes.c_uint
        library.clang_getFieldDeclBitWidth.argtypes = [_CXCursor]
        library.clang_getFieldDeclBitWidth.restype = ctypes.c_int
        library.clang_Location_isInSystemHeader.argtypes = [_CXSourceLocation]
        library.clang_Location_isInSystemHeader.restype = ctypes.c_int
        library.clang_getInclusions.argtypes = [ctypes.c_void_p, self.inclusion_visitor_type, ctypes.c_void_p]
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
    if configured:
        return Path(configured)
    return _DEFAULT_LIBCLANG if _DEFAULT_LIBCLANG.is_file() else _LEGACY_LIBCLANG


@lru_cache(maxsize=16)
def _compiler_include_context(
    compiler: str = "g++",
    compile_flags: tuple[str, ...] = (),
) -> dict:
    # Original-only qualification uses the host compiler. Mixing its libc
    # with an unrelated vendor C++ library can make valid artifacts unparsable.
    try:
        result = subprocess.run(
            [compiler, *compile_flags, "-E", "-dM", "-x", "c++", "-v", "-"],
            input="", capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "unknown", "flags": (), "reason": str(error)}
    flags: list[str] = []
    collecting = False
    for line in result.stderr.splitlines():
        if line.strip() == "#include <...> search starts here:":
            collecting = True
        elif line.strip() == "End of search list.":
            break
        elif collecting and Path(line.strip()).is_dir():
            flags.extend(("-isystem", line.strip()))
    language = re.search(r"^#define __cplusplus (\d+)L?\s*$", result.stdout, re.MULTILINE)
    cpp_versions = {199711: "98", 201103: "11", 201402: "14", 201703: "17", 202002: "20", 202100: "23", 202302: "23"}
    standard = cpp_versions.get(int(language.group(1))) if language else None
    if standard:
        strict = re.search(r"^#define __STRICT_ANSI__\s", result.stdout, re.MULTILINE)
        standard = ("c++" if strict else "gnu++") + standard
    complete = result.returncode == 0 and flags and standard
    return {
        "status": "confirmed" if complete else "unknown",
        "flags": tuple(flags), "return_code": result.returncode,
        "language_standard": standard,
        "reason": None if complete else result.stderr or "compiler language standard unavailable",
    }


def _compiler_include_flags(library_path, compiler="g++", compile_flags=()):
    return _compiler_include_context(compiler, tuple(compile_flags))["flags"]


def _vendor_include_directory():
    xilinx_hls = os.getenv("XILINX_HLS")
    return Path(xilinx_hls) / "include" if xilinx_hls else None


def _parse_flags(path, include_dirs, compile_flags, compiler):
    flags = ["-x", "c++"]
    context = _compiler_include_context(compiler, tuple(compile_flags))
    if context.get("language_standard"):
        flags.append("-std=" + context["language_standard"])
    compiler_includes = _compiler_include_flags(path, compiler, tuple(compile_flags))
    if compiler_includes:
        flags.extend(("-nostdinc++", *compiler_includes))
    # The qualification compiler adds this directory even when source-package
    # includes only name the user source root. Parse the same header context.
    directories = list(include_dirs)
    vendor_include = _vendor_include_directory()
    if vendor_include is not None:
        directories.append(str(vendor_include))
    flags.extend(f"-I{directory}" for directory in dict.fromkeys(directories))
    flags.extend(compile_flags)
    return flags


def extract_top_interface(
    source: str,
    function_name: str,
    *,
    require_definition: bool = True,
    source_path: str | Path | None = None,
    include_dirs: tuple[str, ...] = (),
    compile_flags: tuple[str, ...] = (),
    compiler: str = "g++",
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
            flags = _parse_flags(path, include_dirs, compile_flags, compiler)
            include_context = _compiler_include_context(compiler, tuple(compile_flags))
            if _entry_facts is not None:
                _entry_facts["parse_context"] = {
                    "libclang": str(path), "compiler": compiler,
                    "source_path": filename.decode("utf-8"), "arguments": flags,
                    "include_search_status": include_context["status"],
                    "include_search_reason": include_context.get("reason"),
                    "language_standard": include_context.get("language_standard"),
                }
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
                1,  # CXTranslationUnit_DetailedPreprocessingRecord
            )
            if not translation_unit:
                return None
            error_offsets: list[int] = []
            has_errors = include_context["status"] != "confirmed"
            diagnostic_evidence: list[dict] = []
            if has_errors:
                diagnostic_evidence.append({
                    "severity": 3, "message": "qualification compiler include context unavailable: " + str(include_context.get("reason")),
                    "file": None, "line": None, "column": None, "offset": None,
                    "source": "compiler_include_probe",
                })
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
                _entry_facts["translation_unit_complete"] = not has_errors
            if any(diagnostic["severity"] >= 4 for diagnostic in diagnostic_evidence):
                if _entry_facts is not None:
                    _entry_facts.update(status="unknown", entries=[], translation_unit_complete=False)
                return None

            matches: list[tuple[_CXCursor, dict]] = []
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
                        entry = {
                            "kind": {_FUNCTION_DECL: "function", _METHOD_DECL: "member", _FUNCTION_TEMPLATE: "template"}[cursor.kind],
                            "name": name,
                            "parent": clang.text(library.clang_getCursorSpelling(_parent)),
                            "linkage": linkage,
                            "linkage_name": _LINKAGE_NAMES.get(
                                linkage,
                                "unknown",
                            ),
                            "source_path": clang.text(library.clang_getFileName(entry_file)),
                            "usr": clang.text(library.clang_getCursorUSR(cursor)),
                            "canonical_function_type": clang.text(library.clang_getTypeSpelling(library.clang_getCanonicalType(library.clang_getCursorType(cursor)))),
                            "is_definition": bool(library.clang_isCursorDefinition(cursor)),
                            "signature_valid": declaration is not None and not any(start <= offset < start + len(declaration.encode("utf-8")) for offset in error_offsets),
                        }
                        entries.append(entry)
                        if cursor.kind == _FUNCTION_DECL and library.clang_Location_isFromMainFile(location):
                            matches.append((cursor, entry))
                return _CHILD_VISIT_RECURSE

            root = library.clang_getTranslationUnitCursor(translation_unit)
            library.clang_visitChildren(root, visit, None)
            valid_entries = [
                entry for entry in entries
                if entry["signature_valid"] or not has_errors
            ]
            # Only compiler-identical redeclarations share an entry. Matching
            # spelling alone cannot disambiguate overloads or namespaces.
            groups = {}
            for position, entry in enumerate(valid_entries):
                identity = (
                    entry["usr"] or position,
                    entry["kind"],
                    entry["canonical_function_type"],
                )
                groups.setdefault(identity, []).append(entry)
            duplicate_definition = any(
                sum(entry["is_definition"] for entry in group) > 1
                for group in groups.values()
            )
            if not entries:
                status = "unknown" if has_errors else "missing"
            elif len(valid_entries) != len(entries):
                status = "unknown"
            elif len(groups) != 1 or duplicate_definition:
                status = "ambiguous"
            else:
                status = "confirmed"
            if _entry_facts is not None:
                _entry_facts.update(status=status, entries=valid_entries)
                if status == "confirmed" and len(valid_entries) > 1:
                    _entry_facts["equivalent_redeclarations_collapsed"] = len(valid_entries)
                if status in {"missing", "ambiguous"}:
                    _entry_facts.setdefault("diagnostics", []).append({
                        "severity": 3,
                        "message": (
                            f"entry declaration `{function_name}` was not found"
                            if status == "missing" else
                            f"multiple non-equivalent `{function_name}` declarations or definitions are present"
                        ),
                        "file": str(source_path) if source_path else None,
                        "line": None, "column": None, "offset": None,
                        "source": "entry_contract",
                    })
            if status != "confirmed":
                return None
            resolved = []
            for cursor, entry in matches:
                if not entry["signature_valid"]:
                    continue
                interface = _interface_from_cursor(clang, cursor, source)
                if interface is not None and interface.source_declaration is not None:
                    resolved.append((cursor, entry, interface))
            if not resolved:
                return None
            representative, _entry, interface = next(
                (item for item in resolved if item[1]["is_definition"]),
                resolved[0],
            )
            if _entry_facts is not None:
                if not has_errors:
                    _entry_facts["reachable_calls"] = _reachable_call_facts(clang, representative)
                if _entry_facts.get("_include_type_contract"):
                    contract = _type_contract_from_cursor(clang, representative, source, translation_unit)
                    if has_errors and contract["has_user_types"]:
                        contract.update(status="unknown", fingerprint=None)
                        contract["unresolved"].append("translation_unit_has_errors")
                    _entry_facts["type_contract"] = contract
            return interface
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
        canonical_function_type=clang.text(
            library.clang_getTypeSpelling(
                library.clang_getCanonicalType(function_type)
            )
        ),
        linker_symbol=clang.text(library.clang_Cursor_getMangling(cursor)),
    )


def inspect_top_entry(source: str, function_name: str, **context) -> dict:
    facts: dict = {"status": "unknown", "entries": []}
    extract_top_interface(source, function_name, _entry_facts=facts, **context)
    return facts


def extract_cpp_comments(
    source: str,
    *,
    source_path: str | Path | None = None,
) -> dict:
    """Return compiler comment tokens with byte and Python source offsets.

    Tokenization does not require a semantically complete translation unit.
    The raw spelling is sliced from the original buffer, never reconstructed.
    """
    facts = {"status": "unknown", "comments": [], "diagnostics": []}
    path = _library_path()
    if not isinstance(source, str) or not path.is_file():
        facts["reason"] = "source_or_libclang_unavailable"
        return facts
    try:
        clang = _LibClang(path)
        library = clang.library
        index = library.clang_createIndex(0, 0)
        if not index:
            facts["reason"] = "index_creation_failed"
            return facts
        unit = None
        tokens = ctypes.POINTER(_CXToken)()
        count = ctypes.c_uint()
        try:
            filename = str(source_path or "agrefactor_comments.cpp").encode("utf-8")
            contents = source.encode("utf-8")
            unsaved = _CXUnsavedFile(filename, contents, len(contents))
            args = (ctypes.c_char_p * 3)(b"-x", b"c++", b"-std=c++14")
            unit = library.clang_parseTranslationUnit(
                index, filename, args, len(args), ctypes.pointer(unsaved), 1, 0,
            )
            if not unit:
                facts["reason"] = "translation_unit_unavailable"
                return facts
            main_file = library.clang_getFile(unit, filename)
            start = library.clang_getLocationForOffset(unit, main_file, 0)
            end = library.clang_getLocationForOffset(unit, main_file, len(contents))
            library.clang_tokenize(unit, library.clang_getRange(start, end), ctypes.byref(tokens), ctypes.byref(count))
            comments = []
            for token_index in range(count.value):
                token = tokens[token_index]
                if library.clang_getTokenKind(token) != 4:  # CXToken_Comment
                    continue
                extent = library.clang_getTokenExtent(unit, token)
                byte_offsets = []
                line = ctypes.c_uint()
                column = ctypes.c_uint()
                for location in (library.clang_getRangeStart(extent), library.clang_getRangeEnd(extent)):
                    offset = ctypes.c_uint()
                    library.clang_getSpellingLocation(location, None, ctypes.byref(line), ctypes.byref(column), ctypes.byref(offset))
                    byte_offsets.append(int(offset.value))
                byte_start, byte_end = byte_offsets
                raw = contents[byte_start:byte_end].decode("utf-8")
                char_start = len(contents[:byte_start].decode("utf-8"))
                char_end = len(contents[:byte_end].decode("utf-8"))
                token_line = source.count("\n", 0, char_start) + 1
                line_start = source.rfind("\n", 0, char_start) + 1
                comments.append({
                    "kind": "line" if raw.startswith("//") else "block",
                    "text": raw, "start": char_start, "end": char_end,
                    "byte_start": byte_start, "byte_end": byte_end,
                    "line": token_line, "column": char_start - line_start + 1,
                })
            facts.update(status="confirmed", comments=comments)
            return facts
        finally:
            if tokens:
                library.clang_disposeTokens(unit, tokens, count.value)
            if unit:
                library.clang_disposeTranslationUnit(unit)
            library.clang_disposeIndex(index)
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError) as error:
        facts["reason"] = f"comment_tokenization_failed: {error}"
        return facts


def extract_top_type_contract(
    source: str,
    function_name: str,
    *,
    require_definition: bool = False,
    **context,
) -> dict:
    """Compiler facts for user types reachable from the entry interface."""
    facts = {"status": "unknown", "entries": [], "_include_type_contract": True}
    extract_top_interface(
        source, function_name, require_definition=require_definition,
        _entry_facts=facts, **context,
    )
    contract = facts.get("type_contract") or {
        "status": "unknown", "types": [], "declaration_context": "",
        "fingerprint": None, "has_user_types": None,
        "required_type_names": [], "unresolved": ["entry_type_facts_unavailable"],
    }
    contract.update(
        diagnostics=facts.get("diagnostics", []),
        parse_context=facts.get("parse_context", {}),
        translation_unit_complete=facts.get("translation_unit_complete", False),
        entry_status=facts.get("status", "unknown"),
        entries=facts.get("entries", []),
        equivalent_redeclarations_collapsed=facts.get("equivalent_redeclarations_collapsed", 0),
    )
    return contract


def type_contract_layout_fingerprint(types):
    """Identity of compiler layout facts, independent of legal typedef spelling."""
    layout = []
    for item in types:
        if item["kind"] == "typedef":
            continue
        facts = dict(item)
        if "fields" in facts:
            facts["fields"] = [
                {key: value for key, value in field.items() if key != "type"}
                for field in facts["fields"]
            ]
        layout.append(facts)
    return hashlib.sha256(json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _type_contract_from_cursor(clang, top, source, translation_unit):
    library = clang.library
    types = {}
    declaration_spans = []
    visited = set()
    unresolved = []
    anonymous_field_names = {}
    required_headers = set()
    vendor_include = _vendor_include_directory()
    vendor_include = vendor_include.resolve() if vendor_include is not None else None
    main_filename = None
    top_file = ctypes.c_void_p()
    library.clang_getSpellingLocation(library.clang_getCursorLocation(top), ctypes.byref(top_file), None, None, None)
    if top_file.value:
        main_filename = clang.text(library.clang_getFileName(top_file))
    top_start, top_end = _source_offsets(clang, top)
    top_declaration = _declaration_text(source, top_start, top_end) or ""
    top_fragment = source.encode("utf-8")[top_start:top_end]
    top_start += len(top_fragment) - len(top_fragment.lstrip())
    top_declaration_end = top_start + len(top_declaration.encode("utf-8"))

    def spelling(value):
        return clang.text(library.clang_getTypeSpelling(value))

    def library_header(cursor):
        location = library.clang_getCursorLocation(cursor)
        header = ctypes.c_void_p()
        library.clang_getSpellingLocation(location, ctypes.byref(header), None, None, None)
        filename = clang.text(library.clang_getFileName(header)) if header.value else ""
        if filename and (
            library.clang_Location_isInSystemHeader(location)
            or vendor_include is not None and Path(filename).resolve().is_relative_to(vendor_include)
        ):
            return filename
        return None

    def children(cursor):
        found = []
        @clang.visitor_type
        def visit(child, _parent, _data):
            found.append(child)
            return 1  # CXChildVisit_Continue: direct fields only.
        library.clang_visitChildren(cursor, visit, None)
        return found

    def cursor_name(cursor):
        names = [clang.text(library.clang_getCursorSpelling(cursor))]
        parent = library.clang_getCursorSemanticParent(cursor)
        while parent.kind in {2, 3, 4, 22}:
            name = clang.text(library.clang_getCursorSpelling(parent))
            if name:
                names.insert(0, name)
            parent = library.clang_getCursorSemanticParent(parent)
        return "::".join(name for name in names if name)

    def declaration(cursor):
        location = library.clang_getCursorLocation(cursor)
        file_handle = ctypes.c_void_p()
        library.clang_getSpellingLocation(location, ctypes.byref(file_handle), None, None, None)
        filename = clang.text(library.clang_getFileName(file_handle)) if file_handle.value else ""
        start, end = _source_offsets(clang, cursor)
        if library.clang_Location_isFromMainFile(location):
            contents = source.encode("utf-8")
        else:
            try:
                contents = Path(filename).read_bytes()
            except OSError:
                unresolved.append(f"declaration_source_unavailable:{cursor_name(cursor)}")
                return
        if end < len(contents) and contents[end:end + 1] == b";":
            end += 1
        # Keep enclosing named namespaces in generated context without
        # including unrelated declarations from the namespace body.
        namespaces = []
        parent = library.clang_getCursorSemanticParent(cursor)
        while parent.kind == 22:
            namespace = clang.text(library.clang_getCursorSpelling(parent))
            if namespace:
                namespaces.insert(0, namespace)
            parent = library.clang_getCursorSemanticParent(parent)
        text = contents[start:end].decode("utf-8", errors="replace")
        if text and not text.rstrip().endswith(";"):
            text += ";"
        if filename != main_filename:
            required_headers.add(filename)
        declaration_spans.append((filename, start, end, text, namespaces))

    def shape(value):
        canonical = library.clang_getCanonicalType(value)
        result = {"type": spelling(value), "canonical_type": spelling(canonical)}
        layers = []
        element = canonical
        while element.kind in {_POINTER_TYPE, *_REFERENCE_TYPES, *_ARRAY_TYPES}:
            if element.kind in _ARRAY_TYPES:
                layers.append({"kind": "array", "size": int(library.clang_getArraySize(element))})
                element = library.clang_getArrayElementType(element)
            else:
                layers.append({"kind": "pointer" if element.kind == _POINTER_TYPE else "lvalue_reference" if element.kind == 103 else "rvalue_reference"})
                element = library.clang_getPointeeType(element)
            element = library.clang_getCanonicalType(element)
        result["layers"] = layers
        leaf_decl = library.clang_getTypeDeclaration(element)
        stable_name = anonymous_field_names.get(library.clang_hashCursor(leaf_decl))
        if stable_name:
            leaf_spelling = spelling(element)
            for prefix in (leaf_spelling, "struct " + leaf_spelling, "union " + leaf_spelling, "class " + leaf_spelling):
                result["type"] = result["type"].replace(prefix, stable_name)
                result["canonical_type"] = result["canonical_type"].replace(prefix, stable_name)
            if "unnamed" in result["type"] or "anonymous" in result["type"]:
                result["type"] = result["canonical_type"]
        return result

    anonymous_owners = {}
    @clang.visitor_type
    def find_anonymous_owner(cursor, _parent, _data):
        if cursor.kind in {20, 36}:
            target = library.clang_getCanonicalType(library.clang_getTypedefDeclUnderlyingType(cursor))
            target_decl = library.clang_getTypeDeclaration(target)
            if target_decl.kind in {2, 3, 4, 5} and not clang.text(library.clang_getCursorSpelling(target_decl)):
                anonymous_owners[library.clang_hashCursor(target_decl)] = cursor
        return _CHILD_VISIT_RECURSE
    library.clang_visitChildren(library.clang_getTranslationUnitCursor(translation_unit), find_anonymous_owner, None)

    def walk(value):
        canonical = library.clang_getCanonicalType(value)
        # Preserve typedef declarations as facts before following their
        # canonical record/array target. The typedef itself is part of ABI
        # spelling and must remain independently comparable.
        type_decl = library.clang_getTypeDeclaration(value)
        type_header = library_header(type_decl) if type_decl.kind in {2, 3, 4, 5, 20, 36} else None
        if type_header is not None:
            required_headers.add(type_header)
            return
        if type_decl.kind in {20, 36}:
            identity = library.clang_hashCursor(type_decl)
            if identity not in visited:
                visited.add(identity)
                target = library.clang_getTypedefDeclUnderlyingType(type_decl)
                name = cursor_name(type_decl) or spelling(value)
                types[name] = {"kind": "typedef", "name": name, "underlying_type": shape(target)}
                declaration(type_decl)
            walk(library.clang_getTypedefDeclUnderlyingType(type_decl))
            return
        if canonical.kind in {_POINTER_TYPE, *_REFERENCE_TYPES}:
            walk(library.clang_getPointeeType(value))
            return
        if canonical.kind in _ARRAY_TYPES:
            walk(library.clang_getArrayElementType(value))
            return
        cursor = library.clang_getTypeDeclaration(value)
        if cursor.kind not in {2, 3, 4, 5, 20, 36}:
            cursor = library.clang_getTypeDeclaration(canonical)
        if cursor.kind not in {2, 3, 4, 5, 20, 36}:
            return
        type_header = library_header(cursor)
        if type_header is not None:
            required_headers.add(type_header)
            return
        identity = library.clang_hashCursor(cursor)
        if identity in visited:
            return
        visited.add(identity)
        owner = anonymous_owners.get(identity)
        name = anonymous_field_names.get(identity) or cursor_name(cursor) or (
            {2: "struct", 3: "union", 4: "class", 5: "enum"}[cursor.kind] + " " + cursor_name(owner)
            if owner is not None else spelling(value)
        )
        if owner is not None:
            declaration(owner)
        if cursor.kind == 5:
            underlying = library.clang_getEnumDeclIntegerType(cursor)
            unsigned = library.clang_getCanonicalType(underlying).kind in {4, 5, 6, 7, 8, 9, 10, 11, 12}
            facts = {
                "kind": "enum", "name": name, "underlying_type": spelling(underlying),
                "enum_values": [{"name": clang.text(library.clang_getCursorSpelling(child)), "value": int(library.clang_getEnumConstantDeclUnsignedValue(child) if unsigned else library.clang_getEnumConstantDeclValue(child))} for child in children(cursor) if child.kind == 7],
            }
        else:
            definition = library.clang_getCursorDefinition(cursor)
            if definition.kind in {2, 3, 4}:
                cursor = definition
            record_type = library.clang_getCursorType(cursor)
            size = int(library.clang_Type_getSizeOf(record_type))
            align = int(library.clang_Type_getAlignOf(record_type))
            fields = []
            @clang.visitor_type
            def referenced_extent(child, _parent, _data):
                if child.kind == 101:  # CXCursor_DeclRefExpr
                    referenced = library.clang_getCursorReferenced(child)
                    if referenced.kind == 7:  # CXCursor_EnumConstantDecl
                        walk(library.clang_getCursorType(referenced))
                    elif referenced.kind == _VARIABLE_DECL and library.clang_isConstQualifiedType(library.clang_getCursorType(referenced)):
                        declaration(referenced)
                return _CHILD_VISIT_RECURSE
            library.clang_visitChildren(cursor, referenced_extent, None)
            for child in children(cursor):
                if child.kind == 6:
                    field_type = library.clang_getCursorType(child)
                    leaf_type = library.clang_getCanonicalType(field_type)
                    while leaf_type.kind in {_POINTER_TYPE, *_REFERENCE_TYPES, *_ARRAY_TYPES}:
                        leaf_type = library.clang_getArrayElementType(leaf_type) if leaf_type.kind in _ARRAY_TYPES else library.clang_getPointeeType(leaf_type)
                    leaf_decl = library.clang_getTypeDeclaration(leaf_type)
                    if leaf_decl.kind in {2, 3, 4, 5} and not clang.text(library.clang_getCursorSpelling(leaf_decl)):
                        anonymous_field_names[library.clang_hashCursor(leaf_decl)] = name + "::" + clang.text(library.clang_getCursorSpelling(child))
                    walk(field_type)
                    field = {"name": clang.text(library.clang_getCursorSpelling(child)), **shape(field_type), "bit_offset": int(library.clang_Cursor_getOffsetOfField(child))}
                    if library.clang_Cursor_isBitField(child):
                        field["bit_width"] = int(library.clang_getFieldDeclBitWidth(child))
                    fields.append(field)
                elif child.kind == 44:  # C++ base layouts need explicit facts.
                    unresolved.append(f"base_layout_unavailable:{name}")
            if size < 0 or align < 0 or any(field["bit_offset"] < 0 for field in fields):
                unresolved.append(f"record_layout_unavailable:{name}")
            facts = {"kind": {2: "struct", 3: "union", 4: "class"}[cursor.kind], "name": name, "fields": fields, "size_bytes": size, "align_bytes": align}
        types[name] = facts
        declaration(cursor)

    function_type = library.clang_getCursorType(top)
    walk(library.clang_getResultType(function_type))
    for argument_index in range(library.clang_Cursor_getNumArguments(top)):
        walk(library.clang_getCursorType(library.clang_Cursor_getArgument(top, argument_index)))
    required_include_lines = set()
    @clang.inclusion_visitor_type
    def included(file_handle, stack, length, _data):
        filename = clang.text(library.clang_getFileName(file_handle))
        if filename not in required_headers or not length:
            return
        line = ctypes.c_uint()
        library.clang_getSpellingLocation(stack[length - 1], None, ctypes.byref(line), None, None)
        required_include_lines.add(int(line.value))
    library.clang_getInclusions(translation_unit, included, None)
    # Macro expansions inside the required declarations are compile context,
    # not testcase inputs. Preserve only the definitions actually referenced
    # there (and their macro dependencies), never every main-file define.
    macro_definitions = {}
    required_macros = []
    @clang.visitor_type
    def collect_macro(cursor, _parent, _data):
        if cursor.kind not in {501, 502}:
            return _CHILD_VISIT_RECURSE
        name = clang.text(library.clang_getCursorSpelling(cursor))
        location = library.clang_getCursorLocation(cursor)
        file_handle = ctypes.c_void_p()
        library.clang_getSpellingLocation(location, ctypes.byref(file_handle), None, None, None)
        filename = clang.text(library.clang_getFileName(file_handle)) if file_handle.value else ""
        start, end = _source_offsets(clang, cursor)
        if cursor.kind == 501:
            macro_definitions[name] = (filename, start, end, cursor)
        elif any(
            file == filename and left <= start < right
            for file, left, right, _text, _namespaces in declaration_spans
        ) or (
            filename == main_filename
            and top_start <= start < top_declaration_end
        ):
            referenced = library.clang_getCursorReferenced(cursor)
            definition_file = ctypes.c_void_p()
            library.clang_getSpellingLocation(library.clang_getCursorLocation(referenced), ctypes.byref(definition_file), None, None, None)
            definition_filename = clang.text(library.clang_getFileName(definition_file)) if definition_file.value else ""
            definition_start, definition_end = _source_offsets(clang, referenced)
            required_macros.append((name, (definition_filename, definition_start, definition_end, referenced), dict(macro_definitions)))
        return _CHILD_VISIT_RECURSE
    library.clang_visitChildren(library.clang_getTranslationUnitCursor(translation_unit), collect_macro, None)
    macro_items = []
    pending_macros = list(required_macros)
    visited_macros = {}
    while pending_macros:
        name, definition, available_definitions = pending_macros.pop()
        filename, start, end, cursor = definition
        identity = (filename, start, end)
        if name in visited_macros:
            if visited_macros[name] != identity:
                unresolved.append(f"macro_definition_conflict:{name}")
            continue
        visited_macros[name] = identity
        if not filename or filename.startswith("<"):
            # Command-line and builtin definitions are already represented
            # by the qualification compiler's parse context.
            continue
        if filename != main_filename:
            required_headers.add(filename)
            continue
        fragment = source.encode("utf-8")[start:end].decode("utf-8")
        macro_items.append((start, "#define " + fragment))
        tokens = ctypes.POINTER(_CXToken)()
        count = ctypes.c_uint()
        library.clang_tokenize(translation_unit, library.clang_getCursorExtent(cursor), ctypes.byref(tokens), ctypes.byref(count))
        try:
            spellings = [clang.text(library.clang_getTokenSpelling(translation_unit, tokens[index])) for index in range(count.value)]
            body_start = 1
            parameters = set()
            if fragment[len(name):].startswith("(") and ")" in spellings:
                body_start = spellings.index(")") + 1
                parameters = set(spellings[2:body_start - 1])
            dependencies = {
                spellings[index] for index in range(body_start, count.value)
                if library.clang_getTokenKind(tokens[index]) == 2
                and spellings[index] not in parameters
                and spellings[index] in available_definitions
            }
            pending_macros.extend((dependency, available_definitions[dependency], available_definitions) for dependency in dependencies)
        finally:
            if tokens:
                library.clang_disposeTokens(translation_unit, tokens, count.value)
    # Macro-only header dependencies may have been discovered after the first
    # inclusion pass; the same recorded inclusion identities resolve them.
    library.clang_getInclusions(translation_unit, included, None)
    semantic = sorted(types.values(), key=lambda item: (item["name"], item["kind"]))
    # Source order and surrounding packing directives matter for replay.
    # Tokenized directives exclude examples embedded in strings/comments.
    main_items = list(macro_items)
    encoded = source.encode("utf-8")
    main_file = library.clang_getFile(translation_unit, main_filename.encode("utf-8"))
    start_location = library.clang_getLocationForOffset(translation_unit, main_file, 0)
    end_location = library.clang_getLocationForOffset(translation_unit, main_file, len(encoded))
    tokens = ctypes.POINTER(_CXToken)()
    token_count = ctypes.c_uint()
    library.clang_tokenize(translation_unit, library.clang_getRange(start_location, end_location), ctypes.byref(tokens), ctypes.byref(token_count))
    try:
        token_facts = []
        for token_index in range(token_count.value):
            token = tokens[token_index]
            extent = library.clang_getTokenExtent(translation_unit, token)
            offsets = []
            line = ctypes.c_uint()
            for location in (library.clang_getRangeStart(extent), library.clang_getRangeEnd(extent)):
                offset = ctypes.c_uint()
                library.clang_getSpellingLocation(location, None, ctypes.byref(line), None, ctypes.byref(offset))
                offsets.append(int(offset.value))
            token_facts.append((offsets[0], offsets[1], encoded[offsets[0]:offsets[1]].decode("utf-8"), int(line.value)))
        for token_index, (start, _end, text, line) in enumerate(token_facts):
            if text != "#" or token_index + 2 >= len(token_facts):
                continue
            line_start = encoded.rfind(b"\n", 0, start) + 1
            if encoded[line_start:start].strip():
                continue
            directive = token_facts[token_index + 1][2]
            argument = token_facts[token_index + 2][2]
            if not (directive == "pragma" and argument == "pack" or directive == "include" and line in required_include_lines):
                continue
            line_end = encoded.find(b"\n", start)
            if line_end < 0:
                line_end = len(encoded)
            while encoded[start:line_end].rstrip(b"\r").endswith(b"\\") and line_end < len(encoded):
                following_end = encoded.find(b"\n", line_end + 1)
                line_end = len(encoded) if following_end < 0 else following_end
            main_items.append((start, encoded[start:line_end].decode("utf-8")))
    finally:
        library.clang_disposeTokens(translation_unit, tokens, token_count.value)
    for filename, start, end, text, namespaces in declaration_spans:
        if filename != main_filename:
            continue
        if any(other_file == filename and other_start <= start and end <= other_end and (other_start, other_end) != (start, end) for other_file, other_start, other_end, _text, _scope in declaration_spans):
            continue
        if namespaces:
            text = " ".join(f"namespace {name} {{" for name in namespaces) + "\n" + text + "\n" + "}" * len(namespaces)
        main_items.append((start, text))
    main_items = sorted(set(main_items), key=lambda item: item[0])
    context = "\n".join(text for _offset, text in main_items).strip()
    return {
        "status": "unknown" if unresolved else "confirmed", "types": semantic,
        "has_user_types": bool(semantic), "required_type_names": sorted(types),
        "declaration_context": context,
        "fingerprint": None if unresolved else type_contract_layout_fingerprint(semantic),
        "unresolved": unresolved,
    }


def _reachable_call_facts(clang: _LibClang, top: _CXCursor) -> list[dict]:
    """Compiler identities reachable from a function, without semantic claims."""
    library = clang.library
    visited: set[int] = set()
    calls: dict[int, dict] = {}

    def walk(function: _CXCursor) -> None:
        key = library.clang_hashCursor(function)
        if key in visited:
            return
        visited.add(key)

        @clang.visitor_type
        def visit(cursor, _parent, _data):
            if cursor.kind == 103:  # CXCursor_CallExpr
                declaration = library.clang_getCursorReferenced(cursor)
                if declaration.kind == _FUNCTION_DECL:
                    calls[library.clang_hashCursor(declaration)] = {
                        "name": clang.text(library.clang_getCursorSpelling(declaration)),
                        "linker_symbol": clang.text(library.clang_Cursor_getMangling(declaration)),
                    }
                    definition = library.clang_getCursorDefinition(declaration)
                    if library.clang_Location_isFromMainFile(library.clang_getCursorLocation(definition)):
                        walk(definition)
            return _CHILD_VISIT_RECURSE

        library.clang_visitChildren(function, visit, None)

    walk(top)
    return list(calls.values())


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
