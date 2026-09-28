"""Best-effort C/C++ interface facts from the compiler AST.

Extraction failure is deliberately represented by ``None``.  Callers must
defer to the real compile/link/synthesis stages instead of rejecting source.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_DEFAULT_LIBCLANG = Path(
    "/data/Xilinx/Vitis_HLS/2023.2/lnx64/tools/clang-3.9/lib/libclang.so"
)
_FUNCTION_DECL = 8
_VARIABLE_DECL = 9
_TRANSLATION_UNIT = 300
_CHILD_VISIT_RECURSE = 2
_POINTER_TYPE = 101
_REFERENCE_TYPES = frozenset({103, 104})
_ARRAY_TYPES = frozenset({112, 113, 114, 115})
_FUNCTION_TYPES = frozenset({110, 111})


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


@dataclass(frozen=True, slots=True)
class CppFunctionInterface:
    name: str
    result_type: str
    parameters: tuple[CppParameter, ...]
    source_declaration: str | None = None
    canonical_result_type: str | None = None


@dataclass(frozen=True, slots=True)
class CppVariable:
    name: str
    type_spelling: str
    canonical_type: str
    pointer_like: bool


def interfaces_equivalent(
    left: CppFunctionInterface,
    right: CppFunctionInterface,
    *,
    require_parameter_names: bool = True,
) -> bool:
    if left.name != right.name or left.result_type != right.result_type:
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


def extract_top_interface(
    source: str,
    function_name: str,
    *,
    require_definition: bool = True,
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
            filename = b"agrefactor_interface.cpp"
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
                return None

            matches: list[_CXCursor] = []

            @clang.visitor_type
            def visit(
                cursor: _CXCursor,
                _parent: _CXCursor,
                _client_data: ctypes.c_void_p,
            ) -> int:
                if cursor.kind == _FUNCTION_DECL:
                    name = clang.text(library.clang_getCursorSpelling(cursor))
                    if name == function_name and (
                        not require_definition
                        or library.clang_isCursorDefinition(cursor)
                    ):
                        matches.append(cursor)
                return _CHILD_VISIT_RECURSE

            root = library.clang_getTranslationUnitCursor(translation_unit)
            library.clang_visitChildren(root, visit, None)
            interfaces = [
                _interface_from_cursor(clang, cursor, source)
                for cursor in matches
            ]
            interfaces = [item for item in interfaces if item is not None]
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
        if not name:
            return None
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
            )
        )
    start, end = _source_offsets(clang, cursor)
    return CppFunctionInterface(
        name=clang.text(library.clang_getCursorSpelling(cursor)),
        result_type=result_type,
        parameters=tuple(parameters),
        source_declaration=_declaration_text(source, start, end),
        canonical_result_type=canonical_result_type,
    )


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
