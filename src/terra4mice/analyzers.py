"""
Tree-sitter based AST analysis for terra4mice.

Provides deep code analysis to verify spec attributes against actual code:
- Functions/methods defined
- Classes/interfaces/contracts declared
- Exports and imports
- Decorators (Python)

Falls back gracefully when tree-sitter is not installed.
"""

import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, List, Any

_TREE_SITTER_AVAILABLE = False
_get_parser = None
_get_language = None
_Query = None
_QueryCursor = None

if sys.version_info >= (3, 10):
    try:
        from tree_sitter_language_pack import get_parser, get_language
        from tree_sitter import Query as _Q, QueryCursor as _QC

        _get_parser = get_parser
        _get_language = get_language
        _Query = _Q
        _QueryCursor = _QC
        _TREE_SITTER_AVAILABLE = True
    except ImportError:
        pass


def is_available() -> bool:
    """Check if tree-sitter analysis is available."""
    return _TREE_SITTER_AVAILABLE


@dataclass
class SymbolInfo:
    """Metadata for a single symbol (function, class, method) found in source."""

    name: str
    kind: str  # "function", "class", "method", "interface", "type", "enum"
    line_start: int
    line_end: int
    parent: str = ""  # "ClassName" for methods, empty for top-level
    file: str = ""

    @property
    def qualified_name(self) -> str:
        """Class.method or just function_name."""
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


@dataclass
class AnalysisResult:
    """Result of analyzing a source file with tree-sitter."""

    functions: Set[str] = field(default_factory=set)
    classes: Set[str] = field(default_factory=set)
    exports: Set[str] = field(default_factory=set)
    imports: Set[str] = field(default_factory=set)
    entities: Set[str] = field(default_factory=set)
    decorators: Set[str] = field(default_factory=set)
    has_errors: bool = False
    symbols: List[SymbolInfo] = field(default_factory=list)

    @property
    def all_names(self) -> Set[str]:
        """Union of all discovered names."""
        return self.functions | self.classes | self.exports | self.imports | self.entities


# ---------------------------------------------------------------------------
# Parser / language cache
# ---------------------------------------------------------------------------
_parser_cache: Dict[str, Any] = {}
_language_cache: Dict[str, Any] = {}


def _cached_parser(lang_name: str):
    """Get or create a cached parser for a language."""
    if lang_name not in _parser_cache:
        _parser_cache[lang_name] = _get_parser(lang_name)
    return _parser_cache[lang_name]


def _cached_language(lang_name: str):
    """Get or create a cached language for queries."""
    if lang_name not in _language_cache:
        _language_cache[lang_name] = _get_language(lang_name)
    return _language_cache[lang_name]


# ---------------------------------------------------------------------------
# Safe query helper
# ---------------------------------------------------------------------------
def _safe_query(language, query_str: str, root_node) -> Dict[str, list]:
    """Run a tree-sitter query via Query+QueryCursor, returning empty dict on failure."""
    try:
        query = _Query(language, query_str)
        cursor = _QueryCursor(query)
        return cursor.captures(root_node)
    except Exception:
        return {}


def _extract_names(captures: Dict[str, list], name_key: str) -> Set[str]:
    """Extract text from captured nodes matching a key. Captures is dict[str, list[Node]]."""
    names = set()
    nodes = captures.get(name_key, [])
    for node in nodes:
        text = node.text
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        names.add(text)
    return names


def _extract_symbols(
    captures: Dict[str, list], name_key: str, kind: str, file: str = ""
) -> List[SymbolInfo]:
    """Extract SymbolInfo with line numbers from captured nodes."""
    symbols = []
    for node in captures.get(name_key, []):
        text = node.text
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        # Use the parent node (e.g. function_definition) for line range
        def_node = node.parent
        if def_node:
            line_start = def_node.start_point[0] + 1
            line_end = def_node.end_point[0] + 1
        else:
            line_start = node.start_point[0] + 1
            line_end = line_start
        symbols.append(SymbolInfo(
            name=text, kind=kind,
            line_start=line_start, line_end=line_end,
            file=file,
        ))
    return symbols


def _assign_parents(
    functions: List[SymbolInfo], classes: List[SymbolInfo]
) -> List[SymbolInfo]:
    """Assign parent class to methods based on line ranges."""
    for func in functions:
        for cls in classes:
            if cls.line_start <= func.line_start <= cls.line_end:
                func.parent = cls.name
                func.kind = "method"
                break
    return functions


# ---------------------------------------------------------------------------
# Python analysis
# ---------------------------------------------------------------------------
_PYTHON_FUNC_QUERY = "(function_definition name: (identifier) @name)"
_PYTHON_CLASS_QUERY = "(class_definition name: (identifier) @name)"
_PYTHON_IMPORT_QUERY = """[
  (import_from_statement name: (dotted_name) @name)
  (import_statement name: (dotted_name) @name)
]"""
_PYTHON_DECORATOR_QUERY = "(decorator (identifier) @name)"


def analyze_python(source: bytes, file_path: str = "") -> AnalysisResult:
    """Analyze Python source code with tree-sitter."""
    if not _TREE_SITTER_AVAILABLE:
        return AnalysisResult()

    parser = _cached_parser("python")
    language = _cached_language("python")
    tree = parser.parse(source)
    root = tree.root_node

    result = AnalysisResult()
    result.has_errors = tree.root_node.has_error

    func_captures = _safe_query(language, _PYTHON_FUNC_QUERY, root)
    class_captures = _safe_query(language, _PYTHON_CLASS_QUERY, root)

    result.functions = _extract_names(func_captures, "name")
    result.classes = _extract_names(class_captures, "name")
    result.entities = set(result.classes)

    # Symbol extraction with line numbers
    func_symbols = _extract_symbols(func_captures, "name", "function", file=file_path)
    class_symbols = _extract_symbols(class_captures, "name", "class", file=file_path)
    _assign_parents(func_symbols, class_symbols)
    result.symbols = class_symbols + func_symbols

    # imports - try to get individual imported names
    import_captures = _safe_query(language, _PYTHON_IMPORT_QUERY, root)
    result.imports = _extract_names(import_captures, "name")

    # Also try to capture 'from X import a, b, c' individual names
    _PYTHON_IMPORT_NAMES_QUERY = (
        "(import_from_statement name: (dotted_name) @mod_name)"
    )
    # Fallback: parse import names from aliased_import or dotted_name inside import_from
    try:
        alias_query = "(aliased_import name: (identifier) @alias_name)"
        alias_captures = _safe_query(language, alias_query, root)
        alias_names = _extract_names(alias_captures, "alias_name")
        result.imports |= alias_names
    except Exception:
        pass

    result.decorators = _extract_names(
        _safe_query(language, _PYTHON_DECORATOR_QUERY, root), "name"
    )

    return result


# ---------------------------------------------------------------------------
# TypeScript / TSX analysis
# ---------------------------------------------------------------------------
_TS_FUNC_QUERY = """[
  (function_declaration name: (identifier) @name)
  (method_definition name: (property_identifier) @name)
]"""

_TS_ARROW_QUERY = """(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (arrow_function)))"""

_TS_CLASS_QUERY = "(class_declaration name: (type_identifier) @name)"

_TS_INTERFACE_QUERY = "(interface_declaration name: (type_identifier) @name)"

_TS_TYPE_ALIAS_QUERY = "(type_alias_declaration name: (type_identifier) @name)"

_TS_ENUM_QUERY = "(enum_declaration name: (identifier) @name)"

_TS_EXPORT_QUERY = """[
  (export_statement
    declaration: (function_declaration name: (identifier) @name))
  (export_statement
    declaration: (class_declaration name: (type_identifier) @name))
  (export_statement
    declaration: (lexical_declaration
      (variable_declarator name: (identifier) @name)))
  (export_statement
    declaration: (interface_declaration name: (type_identifier) @name))
  (export_statement
    declaration: (type_alias_declaration name: (type_identifier) @name))
  (export_statement
    declaration: (enum_declaration name: (identifier) @name))
]"""

_TS_IMPORT_QUERY = """(import_statement
  (import_clause
    (named_imports
      (import_specifier name: (identifier) @name))))"""


def analyze_typescript(
    source: bytes, is_tsx: bool = False, file_path: str = ""
) -> AnalysisResult:
    """Analyze TypeScript/TSX source code with tree-sitter."""
    if not _TREE_SITTER_AVAILABLE:
        return AnalysisResult()

    lang_name = "tsx" if is_tsx else "typescript"
    parser = _cached_parser(lang_name)
    language = _cached_language(lang_name)
    tree = parser.parse(source)
    root = tree.root_node

    result = AnalysisResult()
    result.has_errors = tree.root_node.has_error

    func_captures = _safe_query(language, _TS_FUNC_QUERY, root)
    result.functions = _extract_names(func_captures, "name")

    # Arrow functions
    arrow_captures = _safe_query(language, _TS_ARROW_QUERY, root)
    arrow_names = _extract_names(arrow_captures, "name")
    result.functions |= arrow_names

    class_captures = _safe_query(language, _TS_CLASS_QUERY, root)
    result.classes = _extract_names(class_captures, "name")

    # Interfaces, type aliases, enums -> entities
    iface_captures = _safe_query(language, _TS_INTERFACE_QUERY, root)
    type_captures = _safe_query(language, _TS_TYPE_ALIAS_QUERY, root)
    enum_captures = _safe_query(language, _TS_ENUM_QUERY, root)
    interfaces = _extract_names(iface_captures, "name")
    type_aliases = _extract_names(type_captures, "name")
    enums = _extract_names(enum_captures, "name")
    result.entities = result.classes | interfaces | type_aliases | enums

    # Exports
    result.exports = _extract_names(_safe_query(language, _TS_EXPORT_QUERY, root), "name")

    # Imports
    result.imports = _extract_names(_safe_query(language, _TS_IMPORT_QUERY, root), "name")

    # Symbol extraction with line numbers
    func_symbols = _extract_symbols(func_captures, "name", "function", file=file_path)
    func_symbols += _extract_symbols(arrow_captures, "name", "function", file=file_path)
    class_symbols = _extract_symbols(class_captures, "name", "class", file=file_path)
    iface_symbols = _extract_symbols(iface_captures, "name", "interface", file=file_path)
    type_symbols = _extract_symbols(type_captures, "name", "type", file=file_path)
    enum_symbols = _extract_symbols(enum_captures, "name", "enum", file=file_path)
    _assign_parents(func_symbols, class_symbols)
    result.symbols = class_symbols + iface_symbols + type_symbols + enum_symbols + func_symbols

    return result


# ---------------------------------------------------------------------------
# JavaScript analysis
# ---------------------------------------------------------------------------
_JS_FUNC_QUERY = """[
  (function_declaration name: (identifier) @name)
  (method_definition name: (property_identifier) @name)
]"""

_JS_ARROW_QUERY = """(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (arrow_function)))"""

_JS_CLASS_QUERY = "(class_declaration name: (identifier) @name)"

_JS_EXPORT_QUERY = """[
  (export_statement
    declaration: (function_declaration name: (identifier) @name))
  (export_statement
    declaration: (class_declaration name: (identifier) @name))
  (export_statement
    declaration: (lexical_declaration
      (variable_declarator name: (identifier) @name)))
]"""

_JS_IMPORT_QUERY = """(import_statement
  (import_clause
    (named_imports
      (import_specifier name: (identifier) @name))))"""


def analyze_javascript(source: bytes, file_path: str = "") -> AnalysisResult:
    """Analyze JavaScript source code with tree-sitter."""
    if not _TREE_SITTER_AVAILABLE:
        return AnalysisResult()

    parser = _cached_parser("javascript")
    language = _cached_language("javascript")
    tree = parser.parse(source)
    root = tree.root_node

    result = AnalysisResult()
    result.has_errors = tree.root_node.has_error

    func_captures = _safe_query(language, _JS_FUNC_QUERY, root)
    result.functions = _extract_names(func_captures, "name")
    arrow_captures = _safe_query(language, _JS_ARROW_QUERY, root)
    arrow_names = _extract_names(arrow_captures, "name")
    result.functions |= arrow_names

    class_captures = _safe_query(language, _JS_CLASS_QUERY, root)
    result.classes = _extract_names(class_captures, "name")
    result.entities = set(result.classes)

    result.exports = _extract_names(_safe_query(language, _JS_EXPORT_QUERY, root), "name")
    result.imports = _extract_names(_safe_query(language, _JS_IMPORT_QUERY, root), "name")

    # Symbol extraction with line numbers
    func_symbols = _extract_symbols(func_captures, "name", "function", file=file_path)
    func_symbols += _extract_symbols(arrow_captures, "name", "function", file=file_path)
    class_symbols = _extract_symbols(class_captures, "name", "class", file=file_path)
    _assign_parents(func_symbols, class_symbols)
    result.symbols = class_symbols + func_symbols

    return result


# ---------------------------------------------------------------------------
# Solidity analysis
# ---------------------------------------------------------------------------
_SOL_CONTRACT_QUERY = """[
  (contract_declaration name: (identifier) @name)
  (interface_declaration name: (identifier) @name)
  (library_declaration name: (identifier) @name)
]"""

_SOL_FUNC_QUERY = "(function_definition name: (identifier) @name)"

_SOL_EVENT_QUERY = "(event_definition name: (identifier) @name)"

_SOL_MODIFIER_QUERY = "(modifier_definition name: (identifier) @name)"

_SOL_STRUCT_QUERY = "(struct_declaration name: (identifier) @name)"

_SOL_ENUM_QUERY = "(enum_declaration name: (identifier) @name)"


def analyze_solidity(source: bytes, file_path: str = "") -> AnalysisResult:
    """Analyze Solidity source code with tree-sitter."""
    if not _TREE_SITTER_AVAILABLE:
        return AnalysisResult()

    try:
        parser = _cached_parser("solidity")
        language = _cached_language("solidity")
    except Exception:
        # Solidity grammar may not be available in language-pack
        return AnalysisResult()

    tree = parser.parse(source)
    root = tree.root_node

    result = AnalysisResult()
    result.has_errors = tree.root_node.has_error

    # Contracts, interfaces, libraries
    contract_captures = _safe_query(language, _SOL_CONTRACT_QUERY, root)
    contracts = _extract_names(contract_captures, "name")
    result.classes = contracts
    result.entities = set(contracts)

    func_captures = _safe_query(language, _SOL_FUNC_QUERY, root)
    result.functions = _extract_names(func_captures, "name")

    # Events, modifiers, structs, enums -> entities
    events = _extract_names(_safe_query(language, _SOL_EVENT_QUERY, root), "name")
    modifiers = _extract_names(_safe_query(language, _SOL_MODIFIER_QUERY, root), "name")
    structs = _extract_names(_safe_query(language, _SOL_STRUCT_QUERY, root), "name")
    enums = _extract_names(_safe_query(language, _SOL_ENUM_QUERY, root), "name")
    result.entities |= events | modifiers | structs | enums

    # Symbol extraction with line numbers
    func_symbols = _extract_symbols(func_captures, "name", "function", file=file_path)
    class_symbols = _extract_symbols(contract_captures, "name", "class", file=file_path)
    _assign_parents(func_symbols, class_symbols)
    result.symbols = class_symbols + func_symbols

    return result


# ---------------------------------------------------------------------------
# File dispatch
# ---------------------------------------------------------------------------
_EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".sol": "solidity",
}


def analyze_file(file_path: str, source: bytes) -> Optional[AnalysisResult]:
    """
    Analyze a source file, detecting language by extension.

    Returns None for unsupported file types.
    """
    if not _TREE_SITTER_AVAILABLE:
        return None

    ext = ""
    dot_pos = file_path.rfind(".")
    if dot_pos >= 0:
        ext = file_path[dot_pos:].lower()

    lang = _EXTENSION_MAP.get(ext)
    if lang is None:
        return None

    if lang == "python":
        return analyze_python(source, file_path=file_path)
    elif lang == "typescript":
        return analyze_typescript(source, is_tsx=False, file_path=file_path)
    elif lang == "tsx":
        return analyze_typescript(source, is_tsx=True, file_path=file_path)
    elif lang == "javascript":
        return analyze_javascript(source, file_path=file_path)
    elif lang == "solidity":
        return analyze_solidity(source, file_path=file_path)

    return None


# ---------------------------------------------------------------------------
# Spec attribute scoring
# ---------------------------------------------------------------------------
def score_against_spec(result: AnalysisResult, attributes: Dict[str, Any]) -> float:
    """
    Score how well an AnalysisResult matches spec attributes.

    Supported attribute keys:
    - functions: list of expected function names
    - class: single expected class name
    - classes: list of expected class names
    - entities: list of expected entity names (classes, interfaces, types, enums)
    - exports: list of expected export names
    - imports: list of expected import names
    - commands: list of command names (matched as substrings in functions)
    - strategies: list of strategy names (matched in functions and classes)

    Returns ratio of matched items / total declared items (0.0 to 1.0).
    """
    if not attributes:
        return 0.0

    total_items = 0
    matched_items = 0

    # Helper for case-insensitive membership check
    def _in_set(name: str, names: Set[str]) -> bool:
        name_lower = name.lower()
        return any(n.lower() == name_lower for n in names)

    # Helper for substring match (for commands/strategies)
    def _substr_in_set(name: str, names: Set[str]) -> bool:
        name_lower = name.lower()
        return any(name_lower in n.lower() for n in names)

    # functions
    spec_functions = attributes.get("functions")
    if spec_functions and isinstance(spec_functions, list):
        for fn in spec_functions:
            total_items += 1
            if _in_set(fn, result.functions):
                matched_items += 1

    # class (single)
    spec_class = attributes.get("class")
    if spec_class and isinstance(spec_class, str):
        total_items += 1
        if _in_set(spec_class, result.classes):
            matched_items += 1

    # classes (list)
    spec_classes = attributes.get("classes")
    if spec_classes and isinstance(spec_classes, list):
        for cls in spec_classes:
            total_items += 1
            if _in_set(cls, result.classes):
                matched_items += 1

    # entities
    spec_entities = attributes.get("entities")
    if spec_entities and isinstance(spec_entities, list):
        for entity in spec_entities:
            total_items += 1
            if _in_set(entity, result.entities):
                matched_items += 1

    # exports
    spec_exports = attributes.get("exports")
    if spec_exports and isinstance(spec_exports, list):
        for exp in spec_exports:
            total_items += 1
            if _in_set(exp, result.exports):
                matched_items += 1

    # imports
    spec_imports = attributes.get("imports")
    if spec_imports and isinstance(spec_imports, list):
        for imp in spec_imports:
            total_items += 1
            if _in_set(imp, result.imports):
                matched_items += 1

    # commands (substring match in functions)
    spec_commands = attributes.get("commands")
    if spec_commands and isinstance(spec_commands, list):
        for cmd in spec_commands:
            total_items += 1
            if _substr_in_set(cmd, result.functions):
                matched_items += 1

    # strategies (substring match in functions + classes)
    spec_strategies = attributes.get("strategies")
    if spec_strategies and isinstance(spec_strategies, list):
        combined = result.functions | result.classes
        for strategy in spec_strategies:
            total_items += 1
            if _substr_in_set(strategy, combined):
                matched_items += 1

    if total_items == 0:
        return 0.0

    return matched_items / total_items
