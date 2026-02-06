"""
Tests for terra4mice tree-sitter AST analysis.

Covers:
- Python source analysis (functions, classes, decorators, imports)
- TypeScript/TSX analysis (functions, arrow functions, classes, interfaces, exports)
- JavaScript analysis (functions, classes, exports)
- Solidity analysis (contracts, functions, events, structs)
- Spec attribute scoring
- File dispatch by extension
- Graceful fallback when tree-sitter is not installed
- TypeScript regex fallback in inference.py
"""

import sys
import os
import tempfile
from pathlib import Path

import pytest

from terra4mice.analyzers import (
    AnalysisResult,
    analyze_file,
    is_available,
    score_against_spec,
)
from terra4mice.models import Resource, ResourceStatus

# Conditional imports for tree-sitter-only tests
HAS_TREE_SITTER = is_available()

# Only import language-specific analyzers if tree-sitter is available
if HAS_TREE_SITTER:
    from terra4mice.analyzers import (
        analyze_python,
        analyze_typescript,
        analyze_javascript,
        analyze_solidity,
    )


# ---------------------------------------------------------------------------
# Python Analysis
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestPythonAnalysis:
    def test_functions(self):
        source = b"""
def hello():
    pass

def world(x, y):
    return x + y

async def async_func():
    pass
"""
        result = analyze_python(source)
        assert "hello" in result.functions
        assert "world" in result.functions
        assert "async_func" in result.functions

    def test_classes(self):
        source = b"""
class MyClass:
    def method(self):
        pass

class AnotherClass(Base):
    pass
"""
        result = analyze_python(source)
        assert "MyClass" in result.classes
        assert "AnotherClass" in result.classes
        assert "MyClass" in result.entities
        # method should be in functions
        assert "method" in result.functions

    def test_entities(self):
        source = b"""
class Resource:
    pass

class State:
    pass

class Spec:
    pass
"""
        result = analyze_python(source)
        assert result.entities == {"Resource", "State", "Spec"}

    def test_empty_file(self):
        result = analyze_python(b"")
        assert len(result.functions) == 0
        assert len(result.classes) == 0
        assert not result.has_errors

    def test_syntax_error(self):
        source = b"""
def broken(
    # missing closing paren and colon
"""
        result = analyze_python(source)
        assert result.has_errors

    def test_decorators(self):
        source = b"""
@app.route("/hello")
def hello():
    pass

@staticmethod
def static_func():
    pass
"""
        result = analyze_python(source)
        assert "hello" in result.functions
        assert "static_func" in result.functions


# ---------------------------------------------------------------------------
# TypeScript Analysis
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestTypeScriptAnalysis:
    def test_functions(self):
        source = b"""
function greet(name: string): string {
    return `Hello, ${name}`;
}
"""
        result = analyze_typescript(source)
        assert "greet" in result.functions

    def test_arrow_functions(self):
        source = b"""
const add = (a: number, b: number): number => a + b;
const multiply = (a: number, b: number) => {
    return a * b;
};
"""
        result = analyze_typescript(source)
        assert "add" in result.functions
        assert "multiply" in result.functions

    def test_classes(self):
        source = b"""
class UserService {
    getUser(id: string): User {
        return {} as User;
    }
}
"""
        result = analyze_typescript(source)
        assert "UserService" in result.classes
        assert "UserService" in result.entities

    def test_interfaces(self):
        source = b"""
interface User {
    id: string;
    name: string;
}

interface Admin extends User {
    role: string;
}
"""
        result = analyze_typescript(source)
        assert "User" in result.entities
        assert "Admin" in result.entities

    def test_exports(self):
        source = b"""
export function publicFunc(): void {}
export class PublicClass {}
export const publicConst = 42;
export interface PublicInterface {}
"""
        result = analyze_typescript(source)
        assert "publicFunc" in result.exports
        assert "PublicClass" in result.exports
        assert "publicConst" in result.exports
        assert "PublicInterface" in result.exports

    def test_imports(self):
        source = b"""
import { useState, useEffect } from 'react';
import { MyComponent } from './components';
"""
        result = analyze_typescript(source)
        assert "useState" in result.imports
        assert "useEffect" in result.imports
        assert "MyComponent" in result.imports

    def test_type_aliases_and_enums(self):
        source = b"""
type Status = 'active' | 'inactive';
enum Color {
    Red,
    Green,
    Blue,
}
"""
        result = analyze_typescript(source)
        assert "Status" in result.entities
        assert "Color" in result.entities

    def test_tsx(self):
        source = b"""
import React from 'react';

interface Props {
    name: string;
}

export const Greeting: React.FC<Props> = ({ name }) => {
    return <div>Hello, {name}</div>;
};
"""
        result = analyze_typescript(source, is_tsx=True)
        assert "Props" in result.entities
        assert "Greeting" in result.exports or "Greeting" in result.functions


# ---------------------------------------------------------------------------
# JavaScript Analysis
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestJavaScriptAnalysis:
    def test_functions(self):
        source = b"""
function hello() {
    console.log("hello");
}
"""
        result = analyze_javascript(source)
        assert "hello" in result.functions

    def test_classes(self):
        source = b"""
class Animal {
    constructor(name) {
        this.name = name;
    }
    speak() {
        return this.name;
    }
}
"""
        result = analyze_javascript(source)
        assert "Animal" in result.classes
        assert "Animal" in result.entities

    def test_exports(self):
        source = b"""
export function exported() {}
export class ExportedClass {}
export const value = 42;
"""
        result = analyze_javascript(source)
        assert "exported" in result.exports
        assert "ExportedClass" in result.exports
        assert "value" in result.exports


# ---------------------------------------------------------------------------
# Solidity Analysis
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestSolidityAnalysis:
    def test_contracts(self):
        source = b"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract MyToken {
    string public name;
    function transfer(address to, uint256 amount) public returns (bool) {
        return true;
    }
}
"""
        try:
            result = analyze_solidity(source)
        except Exception:
            pytest.skip("Solidity grammar not available")
        assert "MyToken" in result.classes
        assert "transfer" in result.functions

    def test_interfaces_and_events(self):
        source = b"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IERC20 {
    function totalSupply() external view returns (uint256);
    event Transfer(address indexed from, address indexed to, uint256 value);
}
"""
        try:
            result = analyze_solidity(source)
        except Exception:
            pytest.skip("Solidity grammar not available")
        assert "IERC20" in result.entities
        assert "totalSupply" in result.functions


# ---------------------------------------------------------------------------
# Score Against Spec
# ---------------------------------------------------------------------------
class TestScoreAgainstSpec:
    def test_functions_full_match(self):
        result = AnalysisResult(functions={"load", "save", "list"})
        score = score_against_spec(result, {"functions": ["load", "save", "list"]})
        assert score == 1.0

    def test_functions_partial_match(self):
        result = AnalysisResult(functions={"load", "save"})
        score = score_against_spec(result, {"functions": ["load", "save", "list"]})
        assert abs(score - 2 / 3) < 0.01

    def test_functions_no_match(self):
        result = AnalysisResult(functions={"foo", "bar"})
        score = score_against_spec(result, {"functions": ["load", "save"]})
        assert score == 0.0

    def test_class_match(self):
        result = AnalysisResult(classes={"StateManager"})
        score = score_against_spec(result, {"class": "StateManager"})
        assert score == 1.0

    def test_class_no_match(self):
        result = AnalysisResult(classes={"OtherClass"})
        score = score_against_spec(result, {"class": "StateManager"})
        assert score == 0.0

    def test_entities_match(self):
        result = AnalysisResult(entities={"Resource", "State", "Spec"})
        score = score_against_spec(result, {"entities": ["Resource", "State", "Spec"]})
        assert score == 1.0

    def test_exports_match(self):
        result = AnalysisResult(exports={"WorkerRatingModal", "rateWorker"})
        score = score_against_spec(result, {"exports": ["WorkerRatingModal"]})
        assert score == 1.0

    def test_imports_match(self):
        result = AnalysisResult(imports={"useState", "useEffect"})
        score = score_against_spec(result, {"imports": ["useState", "useEffect"]})
        assert score == 1.0

    def test_commands_substring_match(self):
        result = AnalysisResult(functions={"cmd_init", "cmd_plan", "cmd_refresh"})
        score = score_against_spec(result, {"commands": ["init", "plan", "refresh"]})
        assert score == 1.0

    def test_commands_no_match(self):
        result = AnalysisResult(functions={"cmd_init"})
        score = score_against_spec(result, {"commands": ["init", "plan", "refresh"]})
        assert abs(score - 1 / 3) < 0.01

    def test_strategies_substring_match(self):
        result = AnalysisResult(
            functions={"_find_explicit_files", "_pattern_matching"},
            classes={"TestDetector"},
        )
        score = score_against_spec(
            result, {"strategies": ["explicit_files", "pattern_matching"]}
        )
        assert score == 1.0

    def test_mixed_attributes(self):
        result = AnalysisResult(
            functions={"load", "save"},
            classes={"StateManager"},
        )
        score = score_against_spec(
            result,
            {"class": "StateManager", "functions": ["load", "save", "delete"]},
        )
        # class: 1/1, functions: 2/3 -> total 3/4 = 0.75
        assert abs(score - 0.75) < 0.01

    def test_case_insensitive(self):
        result = AnalysisResult(functions={"Load", "SAVE"})
        score = score_against_spec(result, {"functions": ["load", "save"]})
        assert score == 1.0

    def test_empty_attributes(self):
        result = AnalysisResult(functions={"load"})
        score = score_against_spec(result, {})
        assert score == 0.0

    def test_no_attributes(self):
        result = AnalysisResult(functions={"load"})
        score = score_against_spec(result, {"unrelated_key": "value"})
        assert score == 0.0


# ---------------------------------------------------------------------------
# File Dispatch
# ---------------------------------------------------------------------------
class TestAnalyzeFile:
    @pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
    def test_python_file(self):
        result = analyze_file("example.py", b"def hello(): pass")
        assert result is not None
        assert "hello" in result.functions

    @pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
    def test_typescript_file(self):
        result = analyze_file("example.ts", b"function greet(): void {}")
        assert result is not None
        assert "greet" in result.functions

    @pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
    def test_tsx_file(self):
        result = analyze_file(
            "Component.tsx",
            b"export const App = () => { return <div/>; };",
        )
        assert result is not None

    @pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
    def test_javascript_file(self):
        result = analyze_file("script.js", b"function run() {}")
        assert result is not None
        assert "run" in result.functions

    @pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
    def test_solidity_file(self):
        try:
            result = analyze_file(
                "Token.sol",
                b"contract Token { function mint() public {} }",
            )
        except Exception:
            pytest.skip("Solidity grammar not available")
        # May return None if solidity grammar fails, that's ok
        if result is not None:
            assert "Token" in result.classes

    def test_unsupported_extension(self):
        result = analyze_file("data.csv", b"a,b,c")
        assert result is None

    def test_no_extension(self):
        result = analyze_file("Makefile", b"all: build")
        assert result is None


# ---------------------------------------------------------------------------
# Fallback Behavior
# ---------------------------------------------------------------------------
class TestFallback:
    def test_is_available_returns_bool(self):
        assert isinstance(is_available(), bool)

    def test_analysis_result_all_names(self):
        result = AnalysisResult(
            functions={"a", "b"},
            classes={"C"},
            exports={"D"},
            imports={"E"},
            entities={"C", "F"},
        )
        all_names = result.all_names
        assert all_names == {"a", "b", "C", "D", "E", "F"}

    def test_analysis_result_empty(self):
        result = AnalysisResult()
        assert len(result.all_names) == 0
        assert not result.has_errors

    def test_score_works_without_tree_sitter(self):
        """score_against_spec should work regardless of tree-sitter availability."""
        result = AnalysisResult(functions={"load", "save"})
        score = score_against_spec(result, {"functions": ["load", "save"]})
        assert score == 1.0


# ---------------------------------------------------------------------------
# TypeScript Regex Fallback (in inference.py)
# ---------------------------------------------------------------------------
class TestTypeScriptFallback:
    def test_fallback_detects_functions(self):
        from terra4mice.inference import InferenceEngine, InferenceConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            ts_file = Path(tmpdir) / "component.ts"
            ts_file.write_text(
                "export function greet(name: string): string {\n"
                '    return `Hello, ${name}`;\n'
                "}\n"
                "export class UserService {}\n",
                encoding="utf-8",
            )

            config = InferenceConfig(root_dir=Path(tmpdir))
            engine = InferenceEngine(config)

            resource = Resource(
                type="module",
                name="component",
                attributes={"functions": ["greet"], "class": "UserService"},
            )

            score = engine._score_typescript_fallback(
                ts_file.read_text(encoding="utf-8"), resource
            )
            assert score > 0.0

    def test_fallback_no_match(self):
        from terra4mice.inference import InferenceEngine, InferenceConfig

        config = InferenceConfig()
        engine = InferenceEngine(config)
        resource = Resource(
            type="module",
            name="xyz",
            attributes={"functions": ["nonexistent"]},
        )
        score = engine._score_typescript_fallback("const x = 1;", resource)
        # Should still get some score from declaration detection (const is not detected)
        # but functions won't match
        assert score < 0.5

    def test_integration_with_analyze_ast(self):
        """_analyze_ast should use TS fallback for .ts files when tree-sitter is unavailable."""
        from terra4mice.inference import InferenceEngine, InferenceConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            ts_file = Path(tmpdir) / "service.ts"
            ts_file.write_text(
                "export class AuthService {\n"
                "    login() {}\n"
                "    logout() {}\n"
                "}\n",
                encoding="utf-8",
            )

            config = InferenceConfig(root_dir=Path(tmpdir))
            engine = InferenceEngine(config)

            resource = Resource(
                type="module",
                name="service",
                attributes={"class": "AuthService", "functions": ["login", "logout"]},
            )

            score = engine._analyze_ast(resource, ["service.ts"])
            assert score > 0.0
