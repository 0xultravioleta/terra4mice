"""
Tests for terra4mice symbol-level tracking.

Covers:
- SymbolInfo dataclass (qualified_name, properties)
- SymbolStatus dataclass (serialization, properties)
- _extract_symbols helper (line numbers, parent detection)
- _assign_parents helper (method assignment by line ranges)
- State serialization round-trip with symbols
- Backward compatibility (state without symbols)
- Inference symbol population
- CLI/planner display formatting
"""

import json
import tempfile
from pathlib import Path

import pytest

from terra4mice.analyzers import (
    AnalysisResult,
    SymbolInfo,
    analyze_file,
    is_available,
)
from terra4mice.models import Resource, ResourceStatus, SymbolStatus
from terra4mice.state_manager import StateManager
from terra4mice.inference import InferenceEngine, InferenceConfig, InferenceResult
from terra4mice.planner import generate_plan, format_plan

HAS_TREE_SITTER = is_available()

if HAS_TREE_SITTER:
    from terra4mice.analyzers import (
        analyze_python,
        analyze_typescript,
        analyze_javascript,
        _extract_symbols,
        _assign_parents,
    )


# ---------------------------------------------------------------------------
# SymbolInfo
# ---------------------------------------------------------------------------
class TestSymbolInfo:
    def test_qualified_name_top_level(self):
        sym = SymbolInfo(name="my_func", kind="function", line_start=1, line_end=5)
        assert sym.qualified_name == "my_func"

    def test_qualified_name_with_parent(self):
        sym = SymbolInfo(
            name="my_method", kind="method", line_start=10, line_end=15,
            parent="MyClass",
        )
        assert sym.qualified_name == "MyClass.my_method"

    def test_file_field(self):
        sym = SymbolInfo(
            name="f", kind="function", line_start=1, line_end=1,
            file="src/foo.py",
        )
        assert sym.file == "src/foo.py"

    def test_default_parent_empty(self):
        sym = SymbolInfo(name="f", kind="function", line_start=1, line_end=1)
        assert sym.parent == ""


# ---------------------------------------------------------------------------
# SymbolStatus
# ---------------------------------------------------------------------------
class TestSymbolStatus:
    def test_qualified_name_top_level(self):
        ss = SymbolStatus(name="load", kind="function")
        assert ss.qualified_name == "load"

    def test_qualified_name_with_parent(self):
        ss = SymbolStatus(name="save", kind="method", parent="StateManager")
        assert ss.qualified_name == "StateManager.save"

    def test_default_status_implemented(self):
        ss = SymbolStatus(name="f", kind="function")
        assert ss.status == "implemented"

    def test_missing_status(self):
        ss = SymbolStatus(name="f", kind="function", status="missing")
        assert ss.status == "missing"


# ---------------------------------------------------------------------------
# _extract_symbols (requires tree-sitter)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestExtractSymbols:
    def test_python_functions_have_lines(self):
        source = b"""def hello():
    pass

def world():
    return 42
"""
        result = analyze_python(source, file_path="test.py")
        assert len(result.symbols) >= 2
        func_names = {s.name for s in result.symbols}
        assert "hello" in func_names
        assert "world" in func_names

        # Check line numbers
        hello_sym = next(s for s in result.symbols if s.name == "hello")
        assert hello_sym.line_start == 1
        assert hello_sym.line_end == 2
        assert hello_sym.kind == "function"
        assert hello_sym.file == "test.py"

    def test_python_classes_have_lines(self):
        source = b"""class Foo:
    def bar(self):
        pass

    def baz(self):
        pass
"""
        result = analyze_python(source, file_path="mod.py")
        class_syms = [s for s in result.symbols if s.kind == "class"]
        assert len(class_syms) == 1
        assert class_syms[0].name == "Foo"
        assert class_syms[0].line_start == 1

    def test_file_path_propagated(self):
        source = b"def f(): pass"
        result = analyze_python(source, file_path="src/terra4mice/cli.py")
        assert all(s.file == "src/terra4mice/cli.py" for s in result.symbols)

    def test_empty_file_path(self):
        source = b"def f(): pass"
        result = analyze_python(source)
        assert all(s.file == "" for s in result.symbols)


# ---------------------------------------------------------------------------
# _assign_parents
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestAssignParents:
    def test_methods_get_parent(self):
        source = b"""class Engine:
    def start(self):
        pass

    def stop(self):
        pass

def standalone():
    pass
"""
        result = analyze_python(source)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 2
        assert all(m.parent == "Engine" for m in methods)

        standalone = next(s for s in result.symbols if s.name == "standalone")
        assert standalone.kind == "function"
        assert standalone.parent == ""

    def test_nested_classes(self):
        source = b"""class Outer:
    def outer_method(self):
        pass

    class Inner:
        def inner_method(self):
            pass

def top_level():
    pass
"""
        result = analyze_python(source)
        top = next(s for s in result.symbols if s.name == "top_level")
        assert top.parent == ""
        assert top.kind == "function"

    def test_assign_parents_direct(self):
        funcs = [
            SymbolInfo(name="method1", kind="function", line_start=3, line_end=5),
            SymbolInfo(name="standalone", kind="function", line_start=10, line_end=12),
        ]
        classes = [
            SymbolInfo(name="MyClass", kind="class", line_start=1, line_end=8),
        ]
        _assign_parents(funcs, classes)
        assert funcs[0].parent == "MyClass"
        assert funcs[0].kind == "method"
        assert funcs[1].parent == ""
        assert funcs[1].kind == "function"


# ---------------------------------------------------------------------------
# TypeScript symbols
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestTypescriptSymbols:
    def test_ts_symbols_populated(self):
        source = b"""
class UserService {
    getUser(id: string): User {
        return {} as User;
    }
}

function helper(): void {}

const arrowFn = () => {};

interface Config {
    port: number;
}
"""
        result = analyze_typescript(source, file_path="service.ts")
        names = {s.name for s in result.symbols}
        assert "UserService" in names
        assert "getUser" in names
        assert "helper" in names
        assert "arrowFn" in names
        assert "Config" in names

    def test_ts_methods_have_parent(self):
        source = b"""
class Api {
    fetch(): void {}
    save(): void {}
}
"""
        result = analyze_typescript(source)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 2
        assert all(m.parent == "Api" for m in methods)


# ---------------------------------------------------------------------------
# JavaScript symbols
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestJavascriptSymbols:
    def test_js_symbols_populated(self):
        source = b"""
class Router {
    handle(req) {}
}

function dispatch() {}

const middleware = () => {};
"""
        result = analyze_javascript(source, file_path="router.js")
        names = {s.name for s in result.symbols}
        assert "Router" in names
        assert "handle" in names
        assert "dispatch" in names
        assert "middleware" in names


# ---------------------------------------------------------------------------
# analyze_file passes file_path
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestAnalyzeFileSymbols:
    def test_file_path_in_symbols(self):
        source = b"def process(): pass"
        result = analyze_file("src/worker.py", source)
        assert result is not None
        assert len(result.symbols) >= 1
        assert result.symbols[0].file == "src/worker.py"


# ---------------------------------------------------------------------------
# State serialization round-trip
# ---------------------------------------------------------------------------
class TestSymbolStateSerialization:
    def test_round_trip(self, tmp_path):
        state_file = tmp_path / "test.state.json"
        sm = StateManager(state_file)

        resource = Resource(
            type="module", name="inference",
            status=ResourceStatus.IMPLEMENTED,
        )
        resource.symbols = {
            "InferenceEngine": SymbolStatus(
                name="InferenceEngine", kind="class",
                status="implemented", line_start=94, line_end=686,
                file="src/terra4mice/inference.py",
            ),
            "InferenceEngine.infer_all": SymbolStatus(
                name="infer_all", kind="method",
                status="implemented", line_start=154, line_end=178,
                parent="InferenceEngine",
                file="src/terra4mice/inference.py",
            ),
            "format_report": SymbolStatus(
                name="format_report", kind="function",
                status="missing",
            ),
        }
        sm.state.set(resource)
        sm.save()

        # Reload
        sm2 = StateManager(state_file)
        sm2.load()
        loaded = sm2.state.get("module.inference")
        assert loaded is not None
        assert len(loaded.symbols) == 3
        assert loaded.symbols["InferenceEngine"].kind == "class"
        assert loaded.symbols["InferenceEngine"].line_start == 94
        assert loaded.symbols["InferenceEngine.infer_all"].parent == "InferenceEngine"
        assert loaded.symbols["InferenceEngine.infer_all"].kind == "method"
        assert loaded.symbols["format_report"].status == "missing"

    def test_backward_compat_no_symbols(self, tmp_path):
        """State files without 'symbols' key should load with empty dict."""
        state_file = tmp_path / "old.state.json"
        state_data = {
            "version": "1",
            "serial": 1,
            "last_updated": None,
            "resources": [
                {
                    "type": "module",
                    "name": "cli",
                    "status": "implemented",
                    "attributes": {},
                    "depends_on": [],
                    "files": ["src/cli.py"],
                    "tests": [],
                    "created_at": None,
                    "updated_at": None,
                }
            ],
        }
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        sm = StateManager(state_file)
        sm.load()
        resource = sm.state.get("module.cli")
        assert resource is not None
        assert resource.symbols == {}

    def test_symbols_not_serialized_when_empty(self, tmp_path):
        """Empty symbols dict should not appear in JSON output."""
        state_file = tmp_path / "clean.state.json"
        sm = StateManager(state_file)
        resource = Resource(
            type="module", name="planner",
            status=ResourceStatus.IMPLEMENTED,
        )
        sm.state.set(resource)
        sm.save()

        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # symbols key should not be present when empty
        for r in data["resources"]:
            assert "symbols" not in r


# ---------------------------------------------------------------------------
# Inference populates symbols
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestSymbolInference:
    def test_inference_collects_symbols(self, tmp_path):
        """InferenceEngine should populate symbols from tree-sitter analysis."""
        # Create a Python source file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "engine.py").write_text(
            "class Engine:\n    def run(self):\n        pass\n\ndef helper():\n    pass\n",
            encoding="utf-8",
        )

        # Create spec with file reference
        from terra4mice.models import Spec
        spec = Spec()
        resource = Resource(
            type="module", name="engine",
            files=["src/engine.py"],
            attributes={"class": "Engine", "functions": ["run", "helper"]},
        )
        spec.add(resource)

        config = InferenceConfig()
        config.root_dir = tmp_path
        engine = InferenceEngine(config)
        results = engine.infer_all(spec)

        assert len(results) == 1
        result = results[0]
        assert len(result.symbols) > 0
        # Engine class should be there
        sym_names = {s.name for s in result.symbols.values()}
        assert "Engine" in sym_names
        assert "run" in sym_names
        assert "helper" in sym_names

    def test_inference_detects_missing_symbols(self, tmp_path):
        """Functions declared in spec but not in code should be marked missing."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "partial.py").write_text(
            "def existing():\n    pass\n",
            encoding="utf-8",
        )

        from terra4mice.models import Spec
        spec = Spec()
        resource = Resource(
            type="module", name="partial",
            files=["src/partial.py"],
            attributes={"functions": ["existing", "not_yet_written"]},
        )
        spec.add(resource)

        config = InferenceConfig()
        config.root_dir = tmp_path
        eng = InferenceEngine(config)
        results = eng.infer_all(spec)

        result = results[0]
        missing = [s for s in result.symbols.values() if s.status == "missing"]
        assert any(s.name == "not_yet_written" for s in missing)
        implemented = [s for s in result.symbols.values() if s.status == "implemented"]
        assert any(s.name == "existing" for s in implemented)

    def test_apply_to_state_copies_symbols(self, tmp_path):
        """apply_to_state should copy symbols to the resource in state."""
        from terra4mice.models import State
        result = InferenceResult(
            address="module.foo",
            status=ResourceStatus.IMPLEMENTED,
            confidence=0.9,
            symbols={
                "bar": SymbolStatus(name="bar", kind="function", status="implemented"),
                "baz": SymbolStatus(name="baz", kind="function", status="missing"),
            },
        )
        state = State()
        config = InferenceConfig()
        config.root_dir = tmp_path
        eng = InferenceEngine(config)
        eng.apply_to_state([result], state, only_missing=False)

        resource = state.get("module.foo")
        assert resource is not None
        assert len(resource.symbols) == 2
        assert resource.symbols["bar"].status == "implemented"
        assert resource.symbols["baz"].status == "missing"


# ---------------------------------------------------------------------------
# Plan display with symbols
# ---------------------------------------------------------------------------
class TestSymbolDisplay:
    def _make_spec_state(self):
        from terra4mice.models import Spec, State
        spec = Spec()
        state = State()

        # A resource with symbols in state
        r = Resource(
            type="module", name="inference",
            status=ResourceStatus.PARTIAL,
            symbols={
                "InferenceEngine": SymbolStatus(
                    name="InferenceEngine", kind="class", status="implemented",
                    line_start=94, line_end=686,
                ),
                "InferenceEngine.infer_all": SymbolStatus(
                    name="infer_all", kind="method", status="implemented",
                    parent="InferenceEngine", line_start=154, line_end=178,
                ),
                "format_report": SymbolStatus(
                    name="format_report", kind="function", status="missing",
                ),
            },
        )
        state.set(r)

        spec_r = Resource(type="module", name="inference")
        spec.add(spec_r)

        return spec, state

    def test_verbose_plan_shows_symbols(self):
        spec, state = self._make_spec_state()
        plan = generate_plan(spec, state)

        # The resource in state should have symbols; format_plan uses spec resource
        # but plan action references spec resource. We need to ensure the state resource
        # is used for symbols display. Actually, let's verify the plan output.
        # The plan action references spec_resource, not state_resource.
        # We need to set symbols on the spec resource too for the display.

        # Actually the planner uses spec_resource for create/update actions.
        # For partial status, it creates update action with spec_resource.
        # Since spec_resource has no symbols, let's add them:
        spec_r = spec.get("module.inference")
        spec_r.symbols = state.get("module.inference").symbols

        plan = generate_plan(spec, state)
        output = format_plan(plan, verbose=True)
        assert "Symbols:" in output
        assert "missing" in output

    def test_non_verbose_hides_symbols(self):
        spec, state = self._make_spec_state()
        plan = generate_plan(spec, state)
        output = format_plan(plan, verbose=False)
        assert "Symbols:" not in output

    def test_state_show_displays_symbols(self, tmp_path, capsys):
        """cmd_state_show should display symbol details."""
        import sys
        from terra4mice.cli import cmd_state_show

        state_file = tmp_path / "test.state.json"
        sm = StateManager(state_file)
        r = Resource(
            type="module", name="engine",
            status=ResourceStatus.IMPLEMENTED,
            symbols={
                "Engine": SymbolStatus(
                    name="Engine", kind="class", status="implemented",
                    line_start=1, line_end=50, file="engine.py",
                ),
                "Engine.run": SymbolStatus(
                    name="run", kind="method", status="implemented",
                    parent="Engine", line_start=5, line_end=20, file="engine.py",
                ),
                "missing_fn": SymbolStatus(
                    name="missing_fn", kind="function", status="missing",
                ),
            },
        )
        sm.state.set(r)
        sm.save()

        # Mock args
        class Args:
            state = str(state_file)
            address = "module.engine"

        ret = cmd_state_show(Args())
        assert ret == 0

        captured = capsys.readouterr()
        assert "symbols" in captured.out
        assert "3" in captured.out  # total count
        assert "Engine" in captured.out
        assert "MISSING" in captured.out


# ---------------------------------------------------------------------------
# Parallelism tests
# ---------------------------------------------------------------------------

class TestEffectiveParallelism:
    """Test _effective_parallelism resolution."""

    def test_explicit_value(self, tmp_path):
        config = InferenceConfig(root_dir=tmp_path, parallelism=4)
        engine = InferenceEngine(config)
        assert engine._effective_parallelism() == 4

    def test_sequential_mode(self, tmp_path):
        config = InferenceConfig(root_dir=tmp_path, parallelism=1)
        engine = InferenceEngine(config)
        assert engine._effective_parallelism() == 1

    def test_auto_mode(self, tmp_path):
        """parallelism=0 means auto: min(8, cpu_count)."""
        config = InferenceConfig(root_dir=tmp_path, parallelism=0)
        engine = InferenceEngine(config)
        import os
        expected = min(8, os.cpu_count() or 4)
        assert engine._effective_parallelism() == expected

    def test_negative_clamped(self, tmp_path):
        """Negative values get clamped to 1."""
        config = InferenceConfig(root_dir=tmp_path, parallelism=-5)
        engine = InferenceEngine(config)
        assert engine._effective_parallelism() == 1


class TestParallelInferAll:
    """Test that parallel infer_all produces same results as sequential."""

    def _make_test_project(self, tmp_path):
        """Create a small project with multiple resources."""
        from terra4mice.models import Spec
        # Create source files
        src = tmp_path / "src"
        src.mkdir()
        (src / "auth.py").write_text("class Auth:\n    def login(self):\n        pass\n")
        (src / "users.py").write_text("def list_users():\n    pass\ndef get_user():\n    pass\n")
        (src / "payments.py").write_text("class PaymentProcessor:\n    def charge(self):\n        pass\n")

        # Create spec
        spec = Spec()
        for name in ["auth", "users", "payments"]:
            r = Resource(type="module", name=name, status=ResourceStatus.MISSING)
            spec.add(r)
        return spec

    def test_parallel_same_as_sequential(self, tmp_path):
        """Parallel and sequential paths produce identical results."""
        spec = self._make_test_project(tmp_path)

        # Sequential
        config_seq = InferenceConfig(root_dir=tmp_path, parallelism=1)
        engine_seq = InferenceEngine(config_seq)
        results_seq = engine_seq.infer_all(spec)

        # Parallel
        config_par = InferenceConfig(root_dir=tmp_path, parallelism=4)
        engine_par = InferenceEngine(config_par)
        results_par = engine_par.infer_all(spec)

        # Same count
        assert len(results_seq) == len(results_par)

        # Same addresses in same order
        addrs_seq = [r.address for r in results_seq]
        addrs_par = [r.address for r in results_par]
        assert addrs_seq == addrs_par

        # Same statuses
        for rs, rp in zip(results_seq, results_par):
            assert rs.status == rp.status, f"{rs.address}: {rs.status} != {rp.status}"
            assert rs.confidence == rp.confidence

    def test_sequential_fallback_single_resource(self, tmp_path):
        """With only 1 resource, uses sequential path even with parallelism > 1."""
        from terra4mice.models import Spec
        spec = Spec()
        spec.add(Resource(type="module", name="only", status=ResourceStatus.MISSING))
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "only.py").write_text("x = 1\n")

        config = InferenceConfig(root_dir=tmp_path, parallelism=8)
        engine = InferenceEngine(config)
        results = engine.infer_all(spec)
        assert len(results) == 1

    def test_progress_callback_called(self, tmp_path):
        """Progress callback fires for each resource in parallel mode."""
        spec = self._make_test_project(tmp_path)
        calls = []

        def cb(current, total, resource):
            calls.append((current, total, resource.address))

        config = InferenceConfig(root_dir=tmp_path, parallelism=4)
        engine = InferenceEngine(config)
        engine.infer_all(spec, progress_callback=cb)

        # Should be called once per resource
        assert len(calls) == 3
        # All calls have total=3
        assert all(t == 3 for _, t, _ in calls)

    def test_empty_spec_no_crash(self, tmp_path):
        """Empty spec produces empty results without errors."""
        from terra4mice.models import Spec
        spec = Spec()
        config = InferenceConfig(root_dir=tmp_path, parallelism=4)
        engine = InferenceEngine(config)
        results = engine.infer_all(spec)
        assert results == []


@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter not installed")
class TestThreadSafeCache:
    """Test that tree-sitter cache is thread-safe."""

    def test_concurrent_parser_access(self):
        """Multiple threads requesting the same parser don't crash."""
        import threading
        from terra4mice.analyzers import _cached_parser

        errors = []

        def get_parser():
            try:
                p = _cached_parser("python")
                assert p is not None
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_parser) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_language_access(self):
        """Multiple threads requesting the same language don't crash."""
        import threading
        from terra4mice.analyzers import _cached_language

        errors = []

        def get_lang():
            try:
                lang = _cached_language("python")
                assert lang is not None
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_lang) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_analyze_file(self):
        """Multiple threads calling analyze_file in parallel don't crash."""
        import threading
        from terra4mice.analyzers import analyze_file

        source = b"def hello():\n    pass\n\nclass World:\n    def greet(self):\n        return 42\n"
        errors = []
        results = []

        def do_analyze():
            try:
                r = analyze_file("test.py", source)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_analyze) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(results) == 10
        # All results should be identical
        for r in results:
            assert "hello" in r.functions
            assert "World" in r.classes
